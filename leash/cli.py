import sys
import os
import subprocess
import shutil
from .lexer import Lexer
from .parser_l import Parser
from .codegen import CodeGen
from .typechecker import TypeChecker
from .errors import LeashError
from .ast_nodes import (
    Program,
    StructDef,
    UnionDef,
    EnumDef,
    ErrorDef,
    TypeAlias,
    ClassDef,
    Function,
    GlobalVarDecl,
    TemplateDef,
    ImportStmt,
    ConditionalDef,
    BuiltinVarLiteral,
    StringLiteral,
    BoolLiteral,
    BinaryOp,
    UnaryOp,
    MacroDef,
    Call
)
from .targets import get_target, get_native_target, list_targets, TargetConfig
import llvmlite.binding as llvm


def install_libraries(paths):
    """Install library files or directories into the global libs directory (~/.leash/libs)."""
    libs_root = os.path.expanduser("~/.leash/libs")
    try:
        os.makedirs(libs_root, exist_ok=True)
    except OSError as e:
        print(f"Error: Could not create libs directory '{libs_root}': {e}")
        sys.exit(1)

    for p in paths:
        p_expanded = os.path.expanduser(p)
        abs_path = os.path.abspath(p_expanded)
        if not os.path.exists(abs_path):
            print(f"Error: Path does not exist: {abs_path}")
            sys.exit(1)

        if os.path.isfile(abs_path):
            dest = os.path.join(libs_root, os.path.basename(abs_path))
            if os.path.exists(dest):
                print(
                    f"Error: A file with the name '{os.path.basename(abs_path)} already exists in the libs directory."
                )
                sys.exit(1)
            try:
                shutil.copyfile(abs_path, dest)
                print(f"Installed library: {abs_path} -> {dest}")
            except Exception as e:
                print(f"Error copying file {abs_path}: {e}")
                sys.exit(1)
        elif os.path.isdir(abs_path):
            # Copy contents of the directory into libs_root, not the directory itself
            try:
                copied_count = 0
                for item in os.listdir(abs_path):
                    src = os.path.join(abs_path, item)
                    dest = os.path.join(libs_root, item)
                    if os.path.exists(dest):
                        print(f"Error: '{item}' already exists in the libs directory.")
                        sys.exit(1)
                    if os.path.isdir(src):
                        shutil.copytree(src, dest)
                    else:
                        shutil.copy2(src, dest)
                    copied_count += 1
                print(
                    f"Installed {copied_count} item(s) from {abs_path} -> {libs_root}"
                )
            except Exception as e:
                print(f"Error copying directory contents from {abs_path}: {e}")
                sys.exit(1)
        else:
            print(f"Error: Unsupported path type: {abs_path}")
            sys.exit(1)


def resolve_imports(program, base_path):
    """Recursively expand import statements in the program AST, searching both base_path and global libs."""
    loaded_modules = set()  # set of absolute file paths that have been expanded
    global_libs_dir = os.path.expanduser("~/.leash/libs")

    def find_module_file(module_path, search_path):
        """Find a module file given a path like ['subfolder', 'helpers'].
        Returns absolute path or None.
        """
        # Join path segments: ['subfolder', 'helpers'] -> 'subfolder/helpers'
        path_str = os.path.join(*module_path)
        module_name = module_path[-1]  # The actual module name (last segment)

        # Try direct .lsh in search_path
        direct = os.path.join(search_path, f"{path_str}.lsh")
        if os.path.exists(direct):
            return direct

        # Try in global libs directory
        global_path = os.path.join(global_libs_dir, f"{path_str}.lsh")
        if os.path.exists(global_path):
            return global_path

        # Fallback: search in global libs recursively for a file whose stem matches module_name
        # This handles the case where the module was installed without the folder structure
        if os.path.isdir(global_libs_dir):
            matches = []
            for root, dirs, files in os.walk(global_libs_dir):
                for f in files:
                    stem, _ = os.path.splitext(f)
                    if stem == module_name:
                        matches.append(os.path.join(root, f))
            if len(matches) == 1:
                return matches[0]
            elif len(matches) > 1:
                # Ambiguous; will be handled by caller
                return matches  # return list to indicate ambiguity
        return None

    def _expand_items(items, current_base_path):
        new_items = []
        for item in items:
            if isinstance(item, ImportStmt):
                module_file = find_module_file(item.module_path, current_base_path)
                module_name_str = "::".join(item.module_path)
                if not module_file:
                    raise LeashError(
                        f"Module '{module_name_str}' not found",
                        line=item.line,
                        col=item.col,
                    )
                if isinstance(module_file, list):
                    # Ambiguous
                    raise LeashError(
                        f"Module '{module_name_str}' is ambiguous in global libraries (multiple matches found)",
                        line=item.line,
                        col=item.col,
                    )
                module_file_abs = os.path.abspath(module_file)
                if module_file_abs in loaded_modules:
                    continue
                # Parse the module
                with open(module_file_abs, "r") as f:
                    code = f.read()
                try:
                    lexer = Lexer(code)
                    tokens = lexer.tokenize()
                    parser = Parser(tokens)
                    module_ast = parser.parse()
                except LeashError as e:
                    # Re-raise with file path if not already set
                    if e.file is None:
                        e.file = module_file_abs
                    raise
                # Recursively expand imports using the module's directory as base
                module_dir = os.path.dirname(module_file_abs) or "."
                module_ast = _expand_items(module_ast.items, module_dir)

                # Collect which items are publicly accessible (for external importers)
                is_priv_import = item.visibility == "priv"
                available = {}
                # Collect all templates for type-checking
                all_templates = {}
                for mod_item in module_ast.items:
                    if isinstance(mod_item, TemplateDef):
                        all_templates[mod_item.name] = mod_item
                
# Build available items - skip private items in public imports
                for mod_item in module_ast.items:
                    # Skip private items for public imports (include for private imports)
                    if not is_priv_import and hasattr(mod_item, "visibility") and mod_item.visibility == "priv":
                        continue

                    if isinstance(mod_item, (StructDef, UnionDef, EnumDef, ErrorDef, TypeAlias, ClassDef, Function, TemplateDef, MacroDef)):
                        available[mod_item.name] = mod_item
                    elif isinstance(mod_item, GlobalVarDecl):
                        if mod_item.visibility == "pub" or is_priv_import:
                            available[mod_item.name] = mod_item

                # For public imports, add templates needed by public classes/functions
                if not is_priv_import:
                    for name, item in available.items():
                        if hasattr(item, 'template_params') and item.template_params:
                            for tp in item.template_params:
                                if tp in all_templates and tp not in available:
                                    tp_node = all_templates[tp]
                                    if getattr(tp_node, 'visibility', 'pub') == 'pub':
                                        available[tp] = tp_node

                # If this is a private import, add ALL items for internal type-checking
                if item.visibility == "priv":
                    for mod_item in module_ast.items:
                        new_items.append(mod_item)
                    loaded_modules.add(module_file_abs)
                    continue

# Verify requested items exist
                if isinstance(item, ImportStmt) and item.imported_items is not None:
                    for name in item.imported_items:
                        if name not in available:
                            raise LeashError(
                                f"Imported item '{name}' not found or not accessible in module '{module_name_str}'",
                                line=item.line,
                                col=item.col,
                            )

                # For public imports, add only available items (private ones filtered out)
                for name, mod_item in available.items():
                    new_items.append(mod_item)
                loaded_modules.add(module_file_abs)
            else:
                new_items.append(item)
        return Program(new_items)

    return _expand_items(program.items, base_path)


def expand_macros(program):
    """Expand macro definitions and calls in the program AST.

    Macros are textual substitution: a call like MAX(10, 20) where
    MAX is defined as 'def MAX : macro(a, b) |> a < b ? b : a;'
    is replaced by the expression '10 < 20 ? 20 : 10'.

    Returns a new Program with macros removed and calls expanded.
    """
    from .ast_nodes import Identifier, ExpressionStatement, ReturnStatement

    macros = {}
    for item in program.items:
        if isinstance(item, MacroDef):
            macros[item.name] = item

    if not macros:
        return program

    import copy

    def substitute(node, param_map):
        """Recursively substitute Identifier nodes that match macro params."""
        if node is None or isinstance(node, (str, int, float, bool)):
            return node

        if isinstance(node, Identifier) and node.name in param_map:
            return copy.deepcopy(param_map[node.name])

        if isinstance(node, list):
            return [substitute(item, param_map) for item in node]

        if not hasattr(node, '__dict__'):
            return node

        for attr_name in list(vars(node)):
            if attr_name.startswith('_'):
                continue
            attr_val = getattr(node, attr_name)
            if attr_val is None:
                continue
            if isinstance(attr_val, list):
                setattr(node, attr_name, [substitute(item, param_map) for item in attr_val])
            elif hasattr(attr_val, '__dict__') and not isinstance(attr_val, str):
                setattr(node, attr_name, substitute(attr_val, param_map))

        return node

    def expand_expr(node):
        """Expand macro calls within an expression tree."""
        if node is None or isinstance(node, (str, int, float, bool)):
            return node

        if not hasattr(node, '__dict__'):
            return node

        if isinstance(node, Call) and node.name in macros:
            macro_def = macros[node.name]
            if len(node.args) != len(macro_def.params):
                raise LeashError(
                    f"Macro '{node.name}' expects {len(macro_def.params)} argument(s), but got {len(node.args)}",
                    line=getattr(node, 'line', None),
                    col=getattr(node, 'col', None),
                )
            param_map = {}
            for param_name, arg_expr in zip(macro_def.params, node.args):
                param_map[param_name] = expand_expr(arg_expr)
            if len(macro_def.body) == 1:
                stmt = macro_def.body[0]
                if isinstance(stmt, ExpressionStatement):
                    return substitute(copy.deepcopy(stmt.expr), param_map)
                elif isinstance(stmt, ReturnStatement):
                    return substitute(copy.deepcopy(stmt.value), param_map)
                else:
                    return substitute(copy.deepcopy(stmt), param_map)
            else:
                return substitute(copy.deepcopy(macro_def.body), param_map)

        for attr_name in list(vars(node)):
            if attr_name.startswith('_'):
                continue
            attr_val = getattr(node, attr_name)
            if attr_val is None:
                continue
            if isinstance(attr_val, list):
                setattr(node, attr_name, [expand_expr(item) for item in attr_val])
            elif hasattr(attr_val, '__dict__') and not isinstance(attr_val, str):
                setattr(node, attr_name, expand_expr(attr_val))

        return node

    def expand_stmts(statements):
        result = []
        for stmt in statements:
            expanded = expand_expr(stmt)
            if isinstance(expanded, list):
                result.extend(expanded)
            else:
                result.append(expanded)
        return result

    new_items = []
    for item in program.items:
        if isinstance(item, MacroDef):
            continue
        if isinstance(item, Function):
            item.body = expand_stmts(item.body)
            new_items.append(item)
        elif isinstance(item, ClassDef):
            for m in item.methods:
                m.fnc.body = expand_stmts(m.fnc.body)
            new_items.append(item)
        elif isinstance(item, ConditionalDef):
            item.then_block = expand_stmts(item.then_block) if item.then_block else None
            item.also_blocks = [(c, expand_stmts(b), inv) for c, b, inv in item.also_blocks]
            item.else_block = expand_stmts(item.else_block) if item.else_block else None
            new_items.append(item)
        else:
            new_items.append(item)

    return Program(new_items)


def resolve_conditionals(program, target_config):
    """Resolve top-level ConditionalDef nodes based on the target platform, recursively."""
    # Determine platform string for the target
    platform = target_config.name

    def resolve_items(items):
        resolved = []
        for item in items:
            if isinstance(item, ConditionalDef):
                selected = _evaluate_conditional(item, platform)
                if selected:
                    # Recursively resolve nested conditionals in the selected branch
                    resolved.extend(resolve_items(selected))
            else:
                resolved.append(item)
        return resolved

    resolved_items = resolve_items(program.items)
    return Program(resolved_items)


def _evaluate_conditional(cond_def, platform):
    """Evaluate a ConditionalDef's condition chain and return items for first true branch."""

    def eval_expr(expr):
        if isinstance(expr, BuiltinVarLiteral):
            if expr.name == "_PLATFORM":
                return platform
            else:
                raise LeashError(
                    f"Unsupported builtin variable '{expr.name}' in compile-time condition",
                    expr.line,
                    expr.col,
                )
        elif isinstance(expr, StringLiteral):
            return expr.value
        elif isinstance(expr, BoolLiteral):
            return expr.value
        elif isinstance(expr, BinaryOp):
            left = eval_expr(expr.left)
            right = eval_expr(expr.right)
            if expr.op == "==":
                return left == right
            elif expr.op == "!=":
                return left != right
            elif expr.op == "&&":
                return left and right
            elif expr.op == "||":
                return left or right
            else:
                raise LeashError(
                    f"Operator '{expr.op}' not supported in conditional expression",
                    expr.line,
                    expr.col,
                )
        elif isinstance(expr, UnaryOp):
            if expr.op == "!":
                return not eval_expr(expr.expr)
            else:
                raise LeashError(
                    f"Unary operator '{expr.op}' not supported in conditional expression",
                    expr.line,
                    expr.col,
                )
        else:
            raise LeashError(
                f"Expression type {type(expr).__name__} cannot be used in compile-time conditional",
                getattr(expr, "line", None),
                getattr(expr, "col", None),
            )

    # Then branch
    cond_result = eval_expr(cond_def.condition)
    if cond_def.invert:
        cond_result = not cond_result
    if cond_result:
        return cond_def.then_block
    # Also branches
    for also_cond, also_block in cond_def.also_blocks:
        if eval_expr(also_cond):
            return also_block
    # Else branch
    if cond_def.else_block:
        return cond_def.else_block
    return None


def _print_error(e, input_file, code):
    print(f"error: {e.msg}", file=sys.stderr)

    error_file = e.file if e.file else input_file

    if e.line is not None:
        error_code = code
        if e.file and e.file != input_file:
            try:
                with open(e.file, "r") as f:
                    error_code = f.read()
            except Exception:
                error_code = code

        lines = error_code.splitlines()
        line_idx = e.line - 1

        if e.code:
            print(
                f"  --> {error_file}:{e.line}:{e.col if e.col is not None else 0} [{e.code}]",
                file=sys.stderr,
            )
        else:
            print(
                f"  --> {error_file}:{e.line}:{e.col if e.col is not None else 0}",
                file=sys.stderr,
            )

        if 0 <= line_idx < len(lines):
            gutter_width = len(str(e.line)) + 1
            padding = " " * gutter_width

            error_line = lines[line_idx]
            print(f"{padding}|", file=sys.stderr)
            print(f"{e.line} | {error_line}", file=sys.stderr)

            if e.col is not None:
                pointer = " " * e.col + "^"
                print(f"{padding}| {pointer}", file=sys.stderr)

            print(f"{padding}|", file=sys.stderr)

    if e.tip:
        print(f"tip: {e.tip}", file=sys.stderr)


def _print_warning(w, warnings_as_errors=False):
    prefix = "error:" if warnings_as_errors else "warning:"
    print(f"{prefix} {w['msg']}", file=sys.stderr)
    if w.get("line") is not None:
        code_str = ""
        if w.get("code"):
            code_str = f" [{w['code']}]"
        print(
            f"  --> {w.get('file', 'unknown')}:{w['line']}:{w.get('col', 0)}{code_str}",
            file=sys.stderr,
        )
    if w.get("tip"):
        print(f"tip: {w['tip']}", file=sys.stderr)


def install_clang_on_windows():
    print("Attempting to install clang...")

    installers = [
        (
            "winget",
            [
                "winget",
                "install",
                "-e",
                "--accept-package-agreements",
                "--accept-source-agreements",
                "LLVM.LLVM",
            ],
        ),
        ("choco", ["choco", "install", "llvm", "-y"]),
        ("scoop", ["scoop", "install", "llvm"]),
    ]

    for name, cmd in installers:
        print(f"Trying {name}...")
        try:
            result = subprocess.run(cmd, capture_output=True)
            if result.returncode == 0:
                print(f"Successfully installed clang via {name}.")
                return True
        except FileNotFoundError:
            continue

    print("Trying direct download via Python urllib...")
    try:
        import urllib.request
        import json

        api_url = "https://api.github.com/repos/llvm/llvm-project/releases/latest"
        req = urllib.request.Request(api_url, headers={"User-Agent": "Python"})
        with urllib.request.urlopen(req) as response:
            releases = json.loads(response.read().decode())
        assets = [a["name"] for a in releases["assets"] if a["name"].endswith(".exe")]
        print(f"Found {len(assets)} exe assets")
        asset = next(
            (
                a
                for a in releases["assets"]
                if a["name"].lower().startswith("llvm") and a["name"].endswith(".exe")
            ),
            None,
        )
        if not asset:
            asset = next(
                (a for a in releases["assets"] if a["name"].endswith(".exe")), None
            )
        if not asset:
            raise Exception("No LLVM x64 installer found in latest release")
        url = asset["browser_download_url"]
        dest = os.path.join(os.environ.get("TEMP", "."), "llvm-installer.exe")
        print(f"Downloading {asset['name']}...")
        urllib.request.urlretrieve(url, dest)
        print(f"Downloaded installer to {dest}")
        print("To install clang, please run the installer manually:")
        print(f"  wine {dest}")
        return True
    except Exception as e:
        print(f"Download failed: {e}")

    return False


def check_file(input_file, verbose=False):
    """Run thorough checking on a leash file without compiling. Returns (errors, warnings)."""
    with open(input_file, "r") as f:
        code = f.read()

    errors = []
    all_warnings = []

    try:
        lexer = Lexer(code)
        tokens = lexer.tokenize()

        parser = Parser(tokens, input_file)
        ast = parser.parse()

        base_path = os.path.dirname(os.path.abspath(input_file)) or "."
        ast = resolve_imports(ast, base_path)
        # Resolve conditionals using native target
        target_config = get_native_target()
        ast = resolve_conditionals(ast, target_config)
        ast = expand_macros(ast)
    except LeashError as e:
        _print_error(e, input_file, code)
        errors.append(e)
        return errors, all_warnings
    except Exception as e:
        import traceback

        print(f"error: Internal compiler error: {e}", file=sys.stderr)
        traceback.print_exc()
        sys.exit(1)

    try:
        checker = TypeChecker(check_mode=True)
        warnings = checker.check(ast)
        all_warnings = warnings
    except LeashError as e:
        _print_error(e, input_file, code)
        errors.append(e)
        return errors, all_warnings
    except Exception as e:
        import traceback

        print(f"error: Internal type checker error: {e}", file=sys.stderr)
        traceback.print_exc()
        sys.exit(1)

    return errors, all_warnings


def compile_file(
    input_file,
    output_name=None,
    output_type="executable",
    is_run_mode=False,
    target_name=None,
    check_mode=False,
    warnings_as_errors=False,
    extra_libs=None,
):
    with open(input_file, "r") as f:
        code = f.read()

    # Resolve target configuration
    if target_name:
        target_config = get_target(target_name)
    else:
        target_config = get_native_target()

    try:
        # 1. Lexical Analysis
        lexer = Lexer(code)
        tokens = lexer.tokenize()

        # 2. Parsing
        parser = Parser(tokens, input_file)
        ast = parser.parse()

        # 2.5. Resolve imports (expand them into the AST)
        base_path = os.path.dirname(os.path.abspath(input_file)) or "."
        ast = resolve_imports(ast, base_path)
        # 2.75. Resolve top-level conditionals based on target
        ast = resolve_conditionals(ast, target_config)
        # 2.8. Expand macros
        ast = expand_macros(ast)

        # 3. Static Type Checking
        checker = TypeChecker(check_mode=check_mode)
        warnings = checker.check(ast)
        for w in warnings:
            _print_warning(w, warnings_as_errors=warnings_as_errors)
        if warnings_as_errors and warnings:
            sys.exit(1)

        # 4. LLVM Initialization
        llvm.initialize_native_target()
        llvm.initialize_native_asmprinter()

        # 5. Code Generation
        codegen = CodeGen()
        codegen.generate_code(ast, input_file)

        llvm_ir = codegen.get_ir()

        # Parse IR
        mod = llvm.parse_assembly(llvm_ir)
        mod.verify()
    except LeashError as e:
        _print_error(e, input_file, code)
        sys.exit(1)
    except Exception as e:
        import traceback

        print(f"error: Internal compiler error: {e}")
        traceback.print_exc()
        sys.exit(1)

    # Get target and create target machine
    try:
        target = llvm.Target.from_triple(target_config.llvm_triple)
    except Exception:
        target = llvm.Target.from_default_triple()

    # Use static reloc model to avoid PIC/GOT references on Windows
    reloc_model = "static" if target_config.name == "win64" and os.name == "nt" else "default"
    target_machine = target.create_target_machine(reloc=reloc_model)

    # Optional Output name
    if output_name is None:
        if input_file.endswith(".lsh"):
            output_name = input_file[:-4]
        else:
            output_name = "out"

    obj_name = output_name + ".o"

    # Compile to object file
    with open(obj_name, "wb") as f:
        f.write(target_machine.emit_object(mod))

    # Native/cross-compilation targets - link with C compiler
    return _link_native(
        obj_name,
        output_name,
        target_config,
        is_run_mode,
        output_type,
        codegen,
        base_path,
        extra_libs,
    )


def _link_native(
    obj_name,
    output_name,
    target_config,
    is_run_mode,
    output_type,
    codegen,
    base_path,
    extra_libs=None,
):
    """Link native object file with appropriate cross-compiler."""
    # Collect native library paths
    native_libs = codegen.native_libs
    native_lib_args = []
    for native_lib in native_libs:
        lib_path = native_lib[0]
        if lib_path.startswith("."):
            lib_path = os.path.join(base_path, lib_path)
        if os.name == "nt":
            native_lib_args.append(lib_path)
        else:
            native_lib_args.append(lib_path)

    # Add extra libs specified via -l flag
    if extra_libs:
        for lib in extra_libs:
            native_lib_args.append(f"-l{lib}")

    # Determine the C compiler to use
    cc = os.environ.get("CC")
    is_cross_compile = False

    # Try cross-compiler detection if target differs from host
    if cc is None:
        cross_linker = target_config.detect_cross_linker()
        if cross_linker:
            cc = cross_linker
            is_cross_compile = True
            print(f"Using cross-compiler: {cc}")

    # Platform-specific compiler detection
    if cc is None and os.name == "nt":
        try:
            subprocess.run(["gcc", "--version"], capture_output=True, check=True)
            cc = "gcc"
        except (FileNotFoundError, subprocess.CalledProcessError):
            try:
                subprocess.run(["clang", "--version"], capture_output=True, check=True)
                cc = "clang"
            except (FileNotFoundError, subprocess.CalledProcessError):
                print("Error: No C compiler found on Windows.")
                try:
                    response = (
                        input("Would you like to install clang? [Y/n]: ")
                        .strip()
                        .lower()
                    )
                except EOFError:
                    response = ""
                if response in ("", "y", "yes"):
                    if install_clang_on_windows():
                        try:
                            subprocess.run(
                                ["clang", "--version"], capture_output=True, check=True
                            )
                            cc = "clang"
                        except (FileNotFoundError, subprocess.CalledProcessError):
                            print(
                                "Clang installed but not found in PATH. Please restart your terminal or add LLVM to PATH."
                            )
                            print(
                                'Then run: setx PATH "%PATH%;C:\\Program Files\\LLVM\\bin"'
                            )
                            sys.exit(1)
                    else:
                        print("Could not install clang automatically.")
                        print("To install manually, run: winget install LLVM.LLVM")
                        print("Or download from: https://clang.llvm.org/")
                        sys.exit(1)
                else:
                    sys.exit(1)
    elif cc is None:
        cc = "gcc"

    # Add built-in stubs (GC, clock, stdout helpers)
    cross_stubs = []
    stubs_obj = None
    
    stubs_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "cross_compile_stubs.c"
    )
    
    # On native Windows, use windows_stubs.c instead
    if os.name == "nt" and target_config.name == "win64":
        stubs_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "windows_stubs.c"
        )
    
    if os.path.exists(stubs_path):
        # Compile stubs to unique object file to avoid race conditions
        stubs_obj = f"{obj_name}_stubs.o"
        try:
            compile_cmd = [cc, "-c", stubs_path, "-o", stubs_obj]
            subprocess.run(compile_cmd, check=True, capture_output=True)
            cross_stubs = [stubs_obj]
        except subprocess.CalledProcessError as e:
            print(f"warning: Failed to compile built-in stubs: {e}")
    else:
        print(f"warning: Built-in stubs not found at {stubs_path}")
        print("tip: Compilation may fail without internal GC, clock and I/O helpers.")

    # Determine output name and link command
    try:
        if output_type == "executable":
            output_name_final = target_config.get_output_name(output_name)
            link_cmd = [cc, obj_name] + cross_stubs + ["-o", output_name_final]
            link_cmd.extend(target_config.linker_flags)
            link_cmd.extend(native_lib_args)
            subprocess.run(link_cmd, check=True)
        elif output_type == "dynamic":
            if os.name == "nt":
                output_name_final = output_name + ".dll"
            else:
                output_name_final = "lib" + output_name + ".so"
            link_cmd = [cc, "-shared", obj_name, "-o", output_name_final, "-fPIC"]
            link_cmd.extend(native_lib_args)
            subprocess.run(link_cmd, check=True)
        elif output_type == "static":
            if os.name == "nt":
                output_name_final = output_name + ".lib"
            else:
                output_name_final = "lib" + output_name + ".a"
            ar_cmd = ["ar", "rcs", output_name_final, obj_name]
            subprocess.run(ar_cmd, check=True)
        else:
            output_name_final = output_name
    except FileNotFoundError:
        print(f"Error: C compiler '{cc}' not found for target '{target_config.name}'.")
        print(
            f"tip: Install appropriate cross-compiler or set CC environment variable."
        )
        print(f"Object file available at: {obj_name}")
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        print(f"Linking failed: {e}")
        sys.exit(1)

    # Cleanup object files
    if os.path.exists(obj_name):
        os.remove(obj_name)
    if stubs_obj and os.path.exists(stubs_obj):
        os.remove(stubs_obj)

    if not is_run_mode:
        print(f"Successfully compiled to '{output_name_final}'")
    return output_name_final


def dump_file(
    input_file,
    output_name=None,
    target_name=None,
    check_mode=False,
    warnings_as_errors=False,
    extra_libs=None,
):
    with open(input_file, "r") as f:
        code = f.read()

    if target_name:
        target_config = get_target(target_name)
    else:
        target_config = get_native_target()

    try:
        lexer = Lexer(code)
        tokens = lexer.tokenize()

        parser = Parser(tokens, input_file)
        ast = parser.parse()

        base_path = os.path.dirname(os.path.abspath(input_file)) or "."
        ast = resolve_imports(ast, base_path)
        ast = resolve_conditionals(ast, target_config)
        ast = expand_macros(ast)

        checker = TypeChecker(check_mode=check_mode)
        warnings = checker.check(ast)
        for w in warnings:
            _print_warning(w, warnings_as_errors=warnings_as_errors)
        if warnings_as_errors and warnings:
            sys.exit(1)

        llvm.initialize_all_targets()

        codegen = CodeGen()
        codegen.generate_code(ast, input_file)

        llvm_ir = codegen.get_ir()

        mod = llvm.parse_assembly(llvm_ir)
        mod.verify()
    except LeashError as e:
        _print_error(e, input_file, code)
        sys.exit(1)
    except Exception as e:
        import traceback

        print(f"error: Internal compiler error: {e}")
        traceback.print_exc()
        sys.exit(1)

    if output_name is None:
        if input_file.endswith(".lsh"):
            output_name = input_file[:-4]
        else:
            output_name = "out"

    if not output_name.endswith(".ll"):
        output_name = output_name + ".ll"

    with open(output_name, "w") as f:
        f.write(llvm_ir)

    print(f"Dumped LLVM IR to '{output_name}'")
    return output_name


def run_file(
    input_file,
    args=[],
    target_name=None,
    check_mode=False,
    warnings_as_errors=False,
    extra_libs=None,
):
    from .targets import get_target, get_native_target
    import platform

    target_config = get_target(target_name) if target_name else get_native_target()

    # Use a fixed name for the temporary executable
    temp_name = ".__temp_run_leash_exe"

    output_name = compile_file(
        input_file,
        output_name=temp_name,
        is_run_mode=True,
        target_name=target_name,
        check_mode=check_mode,
        warnings_as_errors=warnings_as_errors,
        extra_libs=extra_libs,
    )

    # Check if this is a cross-compiled target that can't run natively
    system = platform.system().lower()
    machine = platform.machine().lower()
    can_run = True

    if target_config.name == "win64" and system != "windows":
        can_run = False
        runner = None
        try:
            subprocess.run(["wine", "--version"], capture_output=True, check=True)
            runner = "wine"
            can_run = True
        except (FileNotFoundError, subprocess.CalledProcessError):
            pass

        if can_run and runner:
            executable_path = output_name
            cmd = [runner, executable_path] + args
        else:
            print(f"error: Cannot run Windows executable on {system}.")
            print(f"tip: Install 'wine' to run Windows binaries, or run on Windows.")
            print(f"Binary compiled at: {output_name}")
            if os.path.exists(output_name):
                os.remove(output_name)
            sys.exit(1)
    elif target_config.name in ("macos", "macos-arm") and system != "darwin":
        can_run = False
        print(f"error: Cannot run macOS binary on {system}.")
        print(f"tip: Compile on macOS or transfer the binary to a Mac.")
        print(f"Binary compiled at: {output_name}")
        if os.path.exists(output_name):
            os.remove(output_name)
        sys.exit(1)
    elif target_config.name == "linux32" and machine not in ("i386", "i686", "x86"):
        can_run = True
        executable_path = f"./{output_name}" if os.name != "nt" else output_name
        cmd = [executable_path] + args
    else:
        executable_path = f"./{output_name}" if os.name != "nt" else output_name
        cmd = [executable_path] + args

    try:
        import datetime

        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"--- Executed at {now} ---")
        result = subprocess.run(cmd)
        if result.returncode != 0:
            sys.exit(result.returncode)
    except Exception as e:
        print(f"Execution failed: {e}")
        sys.exit(1)
    finally:
        if os.path.exists(output_name):
            try:
                os.remove(output_name)
            except:
                pass


def main():
    if len(sys.argv) < 2:
        print("Usage: leash <compile|run|dump|install> ...")
        print("       leash compile <file.lsh> [to <outname>] [--target <target>]")
        print(
            "       leash compile <file.lsh> to-dynamic [<outname>] [--target <target>]"
        )
        print(
            "       leash compile <file.lsh> to-static [<outname>] [--target <target>]"
        )
        print("       leash dump <file.lsh> [to <outname.ll|outname>] [--target <target>]")
        print("       leash compile <file.lsh> --target <target>")
        print("")
        print("Supported targets:")
        for name, desc in list_targets():
            print(f"  {name:12s} - {desc}")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd in ("--help", "-h"):
        print("Leash Programming Language v1.0")
        print("")
        print("Usage: leash <command> [options]")
        print("")
        print("Commands:")
        print("  compile <file.lsh> [to <outname>] [--target <target>]")
        print("                            Compile a Leash file to an executable")
        print("  compile <file.lsh> to-dynamic [<outname>] [--target <target>]")
        print("                            Compile to a shared library (.so)")
        print("  compile <file.lsh> to-static [<outname>] [--target <target>]")
        print("                            Compile to a static library (.a)")
        print("  run <file.lsh> [args...] [--target <target>]")
        print("                            Run a Leash file directly")
        print("  check <file.lsh>          Check a file for errors and warnings")
        print("                            (more verbose, includes safety analysis)")
        print("  install <path> [<path> ...]")
        print(
            "                            Install libraries to the global libs directory"
        )
        print("")
        print("Options:")
        print(
            "  --target <target>         Specify compilation target (win64, macos, etc.)"
        )
        print("  --list-targets            List all supported compilation targets")
        print(
            "  --check                   Run thorough safety checks while compiling/running"
        )
        print("  --warnings-as-errors      Treat all warnings as errors")
        print("  --help, -h                Show this help message")
        print("  --version, -v             Show version information")
        print("")
        print("Supported targets:")
        for name, desc in list_targets():
            print(f"  {name:12s} - {desc}")
        sys.exit(0)

    if cmd in ("--version", "-v"):
        print("Leash Programming Language v1.0")
        print("Copyright (c) 2026 Leash Project")
        print("")
        print("Built on LLVM with Boehm Garbage Collection")
        print("Targets: linux64, linux32, win64, macos, macos-arm, ")
        sys.exit(0)

    if cmd == "check":
        if len(sys.argv) < 3:
            print("Usage: leash check <file.lsh>")
            sys.exit(1)
        input_file = sys.argv[2]
        if not os.path.exists(input_file):
            print(f"error: File not found: {input_file}", file=sys.stderr)
            sys.exit(1)

        print(f"Checking '{input_file}'...")
        print()

        errors, warnings = check_file(input_file, verbose=True)

        if warnings:
            print(f"Found {len(warnings)} warning(s):")
            print()
            for w in warnings:
                _print_warning(w)
                print()

        if not errors and not warnings:
            print("No issues found.")
            sys.exit(0)
        elif not errors:
            print(f"Summary: 0 errors, {len(warnings)} warning(s).")
            sys.exit(0)
        else:
            print(f"Summary: {len(errors)} error(s), {len(warnings)} warning(s).")
            sys.exit(1)

    cmd = sys.argv[1]

    if cmd in ("--help", "-h"):
        print("Leash Programming Language v1.0")
        print("")
        print("Usage: leash <command> [options]")
        print("")
        print("Commands:")
        print("  compile <file.lsh> [to <outname>] [--target <target>]")
        print("                            Compile a Leash file to an executable")
        print("  compile <file.lsh> to-dynamic [<outname>] [--target <target>]")
        print("                            Compile to a shared library (.so)")
        print("  compile <file.lsh> to-static [<outname>] [--target <target>]")
        print("                            Compile to a static library (.a)")
        print("  dump <file.lsh> [to <outname.ll|outname>] [--target <target>]")
        print("                            Dump LLVM IR to a .ll file")
        print("  run <file.lsh> [args...] [--target <target>]")
        print("                            Run a Leash file directly")
        print("  check <file.lsh>          Check a file for errors and warnings")
        print("                            (more verbose, includes safety analysis)")
        print("  install <path> [<path> ...]")
        print(
            "                            Install libraries to the global libs directory"
        )
        print("")
        print("Options:")
        print(
            "  --target <target>         Specify compilation target (win64, macos, etc.)"
        )
        print("  --list-targets            List all supported compilation targets")
        print(
            "  --check                   Run thorough safety checks while compiling/running"
        )
        print("  --warnings-as-errors      Treat all warnings as errors")
        print(
            "  -l<name>                 Link with system library (e.g., -lm for libm)"
        )
        print("  --help, -h                Show this help message")
        print("  --version, -v             Show version information")
        print("")
        print("Supported targets:")
        for name, desc in list_targets():
            print(f"  {name:12s} - {desc}")
        sys.exit(0)

    if cmd in ("--version", "-v"):
        print("Leash Programming Language v1.0")
        print("Copyright (c) 2026 Leash Project")
        print("")
        print("Built on LLVM with Boehm Garbage Collection")
        print("Targets: linux64, linux32, win64, macos, macos-arm, ")
        sys.exit(0)

    if cmd == "check":
        if len(sys.argv) < 3:
            print("Usage: leash check <file.lsh>")
            sys.exit(1)
        input_file = sys.argv[2]
        if not os.path.exists(input_file):
            print(
                f"File not found: {input_file}",
                file=sys.stderr,
            )
            sys.exit(1)

        print(f"Checking '{input_file}'...")
        print()

        errors, warnings = check_file(input_file, verbose=True)

        if warnings:
            print(f"Found {len(warnings)} warning(s):")
            print()
            for w in warnings:
                _print_warning(w)
                print()

        if not errors and not warnings:
            print("No issues found.")
            sys.exit(0)
        elif not errors:
            print(f"Summary: 0 errors, {len(warnings)} warning(s).")
            sys.exit(0)
        else:
            print(f"Summary: {len(errors)} error(s), {len(warnings)} warning(s).")
            sys.exit(1)

    cmd = sys.argv[1]
    if cmd in ("compile", "run", "dump"):
        if len(sys.argv) < 3:
            print(f"Usage: leash {cmd} <file.lsh> [to <outname>] [--target <target>]")
            sys.exit(1)
        input_file = sys.argv[2]

        remaining_args = sys.argv[3:]
        target_name = None
        output_name = None
        output_type = "executable"
        check_mode = False
        warnings_as_errors = False
        extra_libs = []

        i = 0
        positional = []
        while i < len(remaining_args):
            if remaining_args[i] == "--target":
                if i + 1 >= len(remaining_args):
                    print("error: --target requires a value")
                    sys.exit(1)
                target_name = remaining_args[i + 1]
                i += 2
            elif remaining_args[i] == "--list-targets":
                print("Supported targets:")
                for name, desc in list_targets():
                    print(f"  {name:12s} - {desc}")
                sys.exit(0)
            elif remaining_args[i] == "--check":
                check_mode = True
                i += 1
            elif remaining_args[i] == "--warnings-as-errors":
                warnings_as_errors = True
                i += 1
            elif remaining_args[i].startswith("-l"):
                extra_libs.append(remaining_args[i][2:])
                i += 1
            else:
                positional.append(remaining_args[i])
                i += 1

        if cmd == "run":
            run_file(
                input_file,
                positional,
                target_name,
                check_mode=check_mode,
                warnings_as_errors=warnings_as_errors,
                extra_libs=extra_libs,
            )
        elif cmd == "dump":
            if len(positional) >= 1:
                if positional[0] == "to":
                    if len(positional) >= 2:
                        output_name = positional[1]
                else:
                    output_name = positional[0]

            dump_file(
                input_file,
                output_name,
                target_name=target_name,
                check_mode=check_mode,
                warnings_as_errors=warnings_as_errors,
                extra_libs=extra_libs,
            )
        else:
            if len(positional) >= 1:
                if positional[0] == "to":
                    if len(positional) >= 2:
                        output_name = positional[1]
                elif positional[0] == "to-dynamic":
                    output_type = "dynamic"
                    if len(positional) >= 2:
                        output_name = positional[1]
                elif positional[0] == "to-static":
                    output_type = "static"
                    if len(positional) >= 2:
                        output_name = positional[1]
                else:
                    output_name = positional[0]

            compile_file(
                input_file,
                output_name,
                output_type,
                target_name=target_name,
                check_mode=check_mode,
                warnings_as_errors=warnings_as_errors,
                extra_libs=extra_libs,
            )
    elif cmd == "install":
        if len(sys.argv) < 3:
            print("Usage: leash install <path> [<path> ...]")
            sys.exit(1)
        paths = sys.argv[2:]
        install_libraries(paths)
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
