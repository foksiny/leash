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
    TypeAlias,
    ClassDef,
    Function,
    GlobalVarDecl,
    TemplateDef,
    ImportStmt,
)
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
                # Collect definitions
                available = {}
                for mod_item in module_ast.items:
                    if isinstance(
                        mod_item,
                        (
                            StructDef,
                            UnionDef,
                            EnumDef,
                            TypeAlias,
                            ClassDef,
                            Function,
                            TemplateDef,
                        ),
                    ):
                        available[mod_item.name] = mod_item
                    elif isinstance(mod_item, GlobalVarDecl):
                        if mod_item.visibility == "pub":
                            available[mod_item.name] = mod_item
                # Verify requested items exist
                if item.imported_items is not None:
                    for name in item.imported_items:
                        if name not in available:
                            raise LeashError(
                                f"Imported item '{name}' not found or not accessible in module '{module_name_str}'",
                                line=item.line,
                                col=item.col,
                            )
                # Add all available items
                for def_item in available.values():
                    new_items.append(def_item)
                loaded_modules.add(module_file_abs)
            else:
                new_items.append(item)
        return Program(new_items)

    return _expand_items(program.items, base_path)


def _print_error(e, input_file, code):
    print(f"error: {e.msg}")

    # Use the error's file path if available, otherwise use the input_file
    error_file = e.file if e.file else input_file

    if e.line is not None:
        # If error has a file attribute, read that file's code for the snippet
        error_code = code
        if e.file and e.file != input_file:
            try:
                with open(e.file, "r") as f:
                    error_code = f.read()
            except Exception:
                error_code = code  # Fall back to original code

        lines = error_code.splitlines()
        line_idx = e.line - 1

        # File location
        print(f"  --> {error_file}:{e.line}:{e.col if e.col is not None else 0}")

        # Line snippet
        if 0 <= line_idx < len(lines):
            gutter_width = len(str(e.line)) + 1
            padding = " " * gutter_width

            error_line = lines[line_idx]
            print(f"{padding}|")
            print(f"{e.line} | {error_line}")

            if e.col is not None:
                # Pointer
                pointer = " " * e.col + "^"
                print(f"{padding}| {pointer}")

            print(f"{padding}|")

    if e.tip:
        print(f"tip: {e.tip}")


def _print_warning(w):
    print(f"warning: {w['msg']}", file=sys.stderr)
    if w.get("line") is not None:
        print(
            f"  --> {w.get('file', 'unknown')}:{w['line']}:{w.get('col', 0)}",
            file=sys.stderr,
        )
    if w.get("tip"):
        print(f"tip: {w['tip']}", file=sys.stderr)


def compile_file(input_file, output_name=None, is_run_mode=False):
    with open(input_file, "r") as f:
        code = f.read()

    try:
        # 1. Lexical Analysis
        lexer = Lexer(code)
        tokens = lexer.tokenize()

        # 2. Parsing
        parser = Parser(tokens)
        ast = parser.parse()

        # 2.5. Resolve imports (expand them into the AST)
        base_path = os.path.dirname(os.path.abspath(input_file)) or "."
        ast = resolve_imports(ast, base_path)

        # 3. Static Type Checking
        checker = TypeChecker()
        warnings = checker.check(ast)
        for w in warnings:
            _print_warning(w)

        # 4. LLVM Initialization
        llvm.initialize_native_target()
        llvm.initialize_native_asmprinter()

        # 5. Code Generation
        codegen = CodeGen()
        codegen.generate_code(ast)

        llvm_ir = codegen.get_ir()

        # Parse IR
        mod = llvm.parse_assembly(llvm_ir)
        mod.verify()
    except LeashError as e:
        _print_error(e, input_file, code)
        sys.exit(1)
    except Exception as e:
        print(f"error: Internal compiler error: {e}")
        # import traceback; traceback.print_exc()
        sys.exit(1)

    # Create target machine
    target = llvm.Target.from_default_triple()
    target_machine = target.create_target_machine()

    # Optional Output name
    if output_name is None:
        if input_file.endswith(".lsh"):
            output_name = input_file[:-4]
        else:
            output_name = "out"

    if os.name == "nt":
        output_name += ".exe"

    obj_name = output_name + ".o"

    # Compile to object file
    with open(obj_name, "wb") as f:
        f.write(target_machine.emit_object(mod))

    # Link to executable using system compiler
    cc = os.environ.get("CC", "gcc")

    if os.name == "nt":
        # On Windows, try calling clang or gcc. If ms linker is preferred, would be `link`.
        # MSYS2/MinGW gcc works too.
        link_cmd = [cc, obj_name, "-o", output_name, "-l:libgc.so.1"]
    else:
        # Linux / Mac
        link_cmd = [cc, obj_name, "-o", output_name, "-no-pie", "-l:libgc.so.1"]

    try:
        subprocess.run(link_cmd, check=True)
    except FileNotFoundError:
        print(f"Error: A C compiler ('{cc}') is required to link the executable.")
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        print(f"Linking failed: {e}")
        sys.exit(1)

    # Finally, cleanup object file
    if os.path.exists(obj_name):
        os.remove(obj_name)

    if not is_run_mode:
        print(f"Successfully compiled '{input_file}' to '{output_name}'")
    return output_name


def run_file(input_file, args=[]):
    output_name = compile_file(
        input_file, output_name=".__temp_run_leash_exe", is_run_mode=True
    )
    executable_path = f"./{output_name}" if os.name != "nt" else output_name

    try:
        result = subprocess.run([executable_path] + args)
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
        print("Usage: leash <compile|run|install> ...")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd in ("compile", "run"):
        if len(sys.argv) < 3:
            print(f"Usage: leash {cmd} <file.lsh> [to <outname>]")
            sys.exit(1)
        input_file = sys.argv[2]
        if cmd == "run":
            run_file(input_file, sys.argv[3:])
        else:
            output_name = None
            if len(sys.argv) == 5 and sys.argv[3] == "to":
                output_name = sys.argv[4]
            compile_file(input_file, output_name)
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
