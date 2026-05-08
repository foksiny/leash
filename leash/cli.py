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
from .optimize import optimize_module, parse_opt_level
import llvmlite.binding as llvm


def file_hash(filepath):
    """Calculate MD5 hash of a file."""
    import hashlib
    h = hashlib.md5()
    try:
        with open(filepath, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None

def sync_directory(src_root, dest_root):
    """Recursively sync files from src_root to dest_root, only updating changed files."""
    copied_count = 0
    updated_count = 0
    skipped_count = 0

    for root, dirs, files in os.walk(src_root):
        rel_path = os.path.relpath(root, src_root)
        if rel_path == '.':
            rel_path = ''
        dest_dir = os.path.join(dest_root, rel_path) if rel_path else dest_root
        if not os.path.exists(dest_dir):
            os.makedirs(dest_dir, exist_ok=True)
        for file in files:
            src_file = os.path.join(root, file)
            dest_file = os.path.join(dest_dir, file)
            if os.path.exists(dest_file):
                src_hash = file_hash(src_file)
                dest_hash = file_hash(dest_file)
                if src_hash == dest_hash:
                    skipped_count += 1
                else:
                    shutil.copy2(src_file, dest_file)
                    updated_count += 1
            else:
                shutil.copy2(src_file, dest_file)
                copied_count += 1
    return copied_count, updated_count, skipped_count

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
                if file_hash(abs_path) == file_hash(dest):
                    print(f"Skipped (unchanged): {os.path.basename(abs_path)}")
                else:
                    shutil.copyfile(abs_path, dest)
                    print(f"Updated library: {abs_path} -> {dest}")
            else:
                shutil.copyfile(abs_path, dest)
                print(f"Installed library: {abs_path} -> {dest}")
        elif os.path.isdir(abs_path):
            copied, updated, skipped = sync_directory(abs_path, libs_root)
            print(f"Installed {copied}, updated {updated}, skipped {skipped} item(s) from {abs_path} -> {libs_root}")
        else:
            print(f"Error: Unsupported path type: {abs_path}")
            sys.exit(1)

def resolve_imports(program, base_path):
    loaded_modules = set()
    global_libs_dir = os.path.expanduser("~/.leash/libs")
    def find_module_file(module_path, search_path):
        path_str = os.path.join(*module_path)
        module_name = module_path[-1]
        direct = os.path.join(search_path, f"{path_str}.lsh")
        if os.path.exists(direct): return direct
        global_path = os.path.join(global_libs_dir, f"{path_str}.lsh")
        if os.path.exists(global_path): return global_path
        if os.path.isdir(global_libs_dir):
            matches = []
            for root, _, files in os.walk(global_libs_dir):
                for f in files:
                    if os.path.splitext(f)[0] == module_name: matches.append(os.path.join(root, f))
            if len(matches) == 1: return matches[0]
            elif len(matches) > 1: return matches
        return None
    def _expand_items(items, current_base_path):
        new_items = []
        for item in items:
            if isinstance(item, ImportStmt):
                module_file = find_module_file(item.module_path, current_base_path)
                if not module_file or isinstance(module_file, list):
                    raise LeashError(f"Module '{'::'.join(item.module_path)}' not found or ambiguous", line=item.line, col=item.col)
                module_file_abs = os.path.abspath(module_file)
                if module_file_abs in loaded_modules: continue
                with open(module_file_abs, "r") as f: code = f.read()
                try:
                    lexer = Lexer(code); tokens = lexer.tokenize(); parser = Parser(tokens); module_ast = parser.parse()
                except LeashError as e:
                    if e.file is None: e.file = module_file_abs
                    raise
                module_dir = os.path.dirname(module_file_abs) or "."
                module_ast = _expand_items(module_ast.items, module_dir)
                is_priv_import = item.visibility == "priv"
                available = {}
                all_templates = {i.name: i for i in module_ast.items if isinstance(i, TemplateDef)}
                internal_types = {}
                for mod_item in module_ast.items:
                    if not is_priv_import and hasattr(mod_item, "visibility") and mod_item.visibility == "priv":
                        if isinstance(mod_item, (StructDef, UnionDef, EnumDef, ClassDef, TypeAlias, ErrorDef)): internal_types[mod_item.name] = mod_item
                        continue
                    if isinstance(mod_item, (StructDef, UnionDef, EnumDef, ErrorDef, TypeAlias, ClassDef, Function, TemplateDef, MacroDef)): available[mod_item.name] = mod_item
                    elif isinstance(mod_item, GlobalVarDecl) and (mod_item.visibility == "pub" or is_priv_import): available[mod_item.name] = mod_item
                if not is_priv_import:
                    for name, it in list(available.items()):
                        if hasattr(it, 'type_params') and it.type_params:
                            for tp in it.type_params:
                                if tp in all_templates and tp not in available: available[tp] = all_templates[tp]
                if is_priv_import:
                    for mod_item in module_ast.items: new_items.append(mod_item)
                else:
                    if item.imported_items is not None:
                        for name in item.imported_items:
                            if name not in available: raise LeashError(f"Imported item '{name}' not found in module", line=item.line, col=item.col)
                    for name, mod_item in available.items(): new_items.append(mod_item)
                    for name, mod_item in internal_types.items(): new_items.append(mod_item)
                loaded_modules.add(module_file_abs)
            else: new_items.append(item)
        return Program(new_items)
    return _expand_items(program.items, base_path)

def expand_macros(program):
    from .ast_nodes import Identifier, ExpressionStatement, ReturnStatement
    macros = {item.name: item for item in program.items if isinstance(item, MacroDef)}
    if not macros: return program
    import copy
    def substitute(node, param_map):
        if node is None or isinstance(node, (str, int, float, bool)): return node
        if isinstance(node, Identifier) and node.name in param_map: return copy.deepcopy(param_map[node.name])
        if isinstance(node, list): return [substitute(item, param_map) for item in node]
        if not hasattr(node, '__dict__'): return node
        for attr in list(vars(node)):
            if attr.startswith('_'): continue
            val = getattr(node, attr)
            if val is None: continue
            if isinstance(val, list): setattr(node, attr, [substitute(i, param_map) for i in val])
            elif hasattr(val, '__dict__') and not isinstance(val, str): setattr(node, attr, substitute(val, param_map))
        return node
    def expand_expr(node):
        if node is None or isinstance(node, (str, int, float, bool)) or not hasattr(node, '__dict__'): return node
        if isinstance(node, Call) and node.name in macros:
            m = macros[node.name]
            if len(node.args) != len(m.params): raise LeashError(f"Macro '{node.name}' expects {len(m.params)} args, got {len(node.args)}")
            pmap = {name: expand_expr(arg) for name, arg in zip(m.params, node.args)}
            if len(m.body) == 1:
                s = m.body[0]
                if isinstance(s, ExpressionStatement): return substitute(copy.deepcopy(s.expr), pmap)
                elif isinstance(s, ReturnStatement): return substitute(copy.deepcopy(s.value), pmap)
                return substitute(copy.deepcopy(s), pmap)
            return substitute(copy.deepcopy(m.body), pmap)
        for attr in list(vars(node)):
            if attr.startswith('_'): continue
            val = getattr(node, attr)
            if val is None: continue
            if isinstance(val, list): setattr(node, attr, [expand_expr(i) for i in val])
            elif hasattr(val, '__dict__') and not isinstance(val, str): setattr(node, attr, expand_expr(val))
        return node
    def expand_stmts(stmts):
        res = []
        for s in stmts:
            ex = expand_expr(s)
            if isinstance(ex, list): res.extend(ex)
            else: res.append(ex)
        return res
    new_items = []
    for item in program.items:
        if isinstance(item, MacroDef): continue
        if isinstance(item, Function): item.body = expand_stmts(item.body)
        elif isinstance(item, ClassDef):
            for m in item.methods: m.fnc.body = expand_stmts(m.fnc.body)
        elif isinstance(item, ConditionalDef):
            item.then_block = expand_stmts(item.then_block) if item.then_block else None
            item.also_blocks = [(c, expand_stmts(b), inv) for c, b, inv in item.also_blocks]
            item.else_block = expand_stmts(item.else_block) if item.else_block else None
        new_items.append(item)
    return Program(new_items)

def resolve_conditionals(program, target_config):
    def resolve_items(items):
        res = []
        for i in items:
            if isinstance(i, ConditionalDef):
                sel = _evaluate_conditional(i, target_config.name)
                if sel: res.extend(resolve_items(sel))
            else: res.append(i)
        return res
    return Program(resolve_items(program.items))

def _evaluate_conditional(cond_def, platform):
    def eval_expr(expr):
        if isinstance(expr, BuiltinVarLiteral):
            if expr.name == "_PLATFORM": return platform
            raise LeashError(f"Unsupported builtin '{expr.name}'", expr.line, expr.col)
        elif isinstance(expr, StringLiteral): return expr.value
        elif isinstance(expr, BoolLiteral): return expr.value
        elif isinstance(expr, BinaryOp):
            l, r = eval_expr(expr.left), eval_expr(expr.right)
            if expr.op == "==": return l == r
            if expr.op == "!=": return l != r
            if expr.op == "&&": return l and r
            if expr.op == "||": return l or r
            raise LeashError(f"Operator '{expr.op}' not supported in conditional", expr.line, expr.col)
        elif isinstance(expr, UnaryOp):
            if expr.op == "!": return not eval_expr(expr.expr)
            raise LeashError(f"Unary '{expr.op}' not supported", expr.line, expr.col)
        raise LeashError(f"Unsupported expr {type(expr).__name__}", getattr(expr, "line"), getattr(expr, "col"))
    if cond_def.invert:
        if not eval_expr(cond_def.condition): return cond_def.then_block
    elif eval_expr(cond_def.condition): return cond_def.then_block
    for c, b in cond_def.also_blocks:
        if eval_expr(c): return b
    return cond_def.else_block

def _print_error(e, input_file, code):
    print(f"error: {e.msg}", file=sys.stderr)
    f = e.file or input_file
    if e.line:
        c = code
        if e.file and e.file != input_file:
            try:
                with open(e.file, "r") as fh: c = f.read()
            except: c = code
        lines = c.splitlines(); idx = e.line - 1
        print(f"  --> {f}:{e.line}:{e.col or 0}{' ['+e.code+']' if e.code else ''}", file=sys.stderr)
        if 0 <= idx < len(lines):
            p = " " * (len(str(e.line)) + 1)
            print(f"{p}|", file=sys.stderr); print(f"{e.line} | {lines[idx]}", file=sys.stderr)
            if e.col is not None: print(f"{p}| {' '*e.col}^", file=sys.stderr)
            print(f"{p}|", file=sys.stderr)
    if e.tip: print(f"tip: {e.tip}", file=sys.stderr)

def _print_warning(w, warnings_as_errors=False):
    print(f"{'error:' if warnings_as_errors else 'warning:'} {w['msg']}", file=sys.stderr)
    if w.get("line"): print(f"  --> {w.get('file','unknown')}:{w['line']}:{w.get('col',0)}{' ['+w['code']+']' if w.get('code') else ''}", file=sys.stderr)
    if w.get("tip"): print(f"tip: {w['tip']}", file=sys.stderr)

def check_file(input_file, verbose=False):
    with open(input_file, "r") as f: code = f.read()
    errors, warnings = [], []
    try:
        lexer = Lexer(code); tokens = lexer.tokenize(); parser = Parser(tokens, input_file); ast = parser.parse()
        ast = resolve_imports(ast, os.path.dirname(os.path.abspath(input_file)) or ".")
        ast = resolve_conditionals(ast, get_native_target()); ast = expand_macros(ast)
    except LeashError as e:
        if verbose: _print_error(e, input_file, code)
        errors.append(e); return errors, warnings
    except Exception as e:
        if verbose: import traceback; print(f"error: Internal: {e}", file=sys.stderr); traceback.print_exc()
        return errors, warnings
    try:
        warnings = TypeChecker(check_mode=True).check(ast)
    except LeashError as e:
        if verbose: _print_error(e, input_file, code)
        errors.append(e)
    except Exception as e:
        if verbose: import traceback; print(f"error: Internal: {e}", file=sys.stderr); traceback.print_exc()
    return errors, warnings

def compile_file(input_file, output_name=None, output_type="executable", is_run_mode=False, target_name=None, check_mode=False, warnings_as_errors=False, extra_libs=None, opt_level=None):
    with open(input_file, "r") as f: code = f.read()
    target_config = get_target(target_name) if target_name else get_native_target()
    try:
        lexer = Lexer(code); tokens = lexer.tokenize(); parser = Parser(tokens, input_file); ast = parser.parse()
        ast = resolve_imports(ast, os.path.dirname(os.path.abspath(input_file)) or ".")
        ast = resolve_conditionals(ast, target_config); ast = expand_macros(ast)
        warnings = TypeChecker(check_mode=check_mode).check(ast)
        for w in warnings: _print_warning(w, warnings_as_errors)
        if warnings_as_errors and warnings: sys.exit(1)
        llvm.initialize_native_target(); llvm.initialize_native_asmprinter()
        codegen = CodeGen(target_platform=target_config.name); codegen.generate_code(ast, input_file)
        mod = llvm.parse_assembly(codegen.get_ir()); mod.verify()
    except LeashError as e: _print_error(e, input_file, code); sys.exit(1)
    except Exception as e: import traceback; print(f"error: Internal: {e}"); traceback.print_exc(); sys.exit(1)
    try: target = llvm.Target.from_triple(target_config.llvm_triple)
    except: target = llvm.Target.from_default_triple()
    reloc = "static" if target_config.name == "win64" and os.name == "nt" else "default"
    parsed_opt, size_opt = parse_opt_level(opt_level)
    tm = target.create_target_machine(reloc=reloc, opt=parsed_opt)
    if output_name is None: output_name = input_file[:-4] if input_file.endswith(".lsh") else "out"
    if parsed_opt > 0 or size_opt: optimize_module(mod, opt_level=parsed_opt, size_opt=size_opt, target_machine=tm)
    obj_name = output_name + ".o"
    with open(obj_name, "wb") as f: f.write(tm.emit_object(mod))
    return _link_native(obj_name, output_name, target_config, is_run_mode, output_type, codegen, os.path.dirname(os.path.abspath(input_file)) or ".", extra_libs)

def _link_native(obj_name, output_name, target_config, is_run_mode, output_type, codegen, base_path, extra_libs=None):
    nlib_args = [os.path.join(base_path, l[0]) if l[0].startswith(".") else l[0] for l in codegen.native_libs]
    if extra_libs: nlib_args.extend([f"-l{l}" for l in extra_libs])
    cc = os.environ.get("CC") or target_config.detect_cross_linker()
    if not cc:
        if os.name == "nt":
            # On Windows, prefer gcc (MinGW) over clang since clang often
            # requires a Visual Studio installation for linking.
            import shutil
            cc = "gcc" if shutil.which("gcc") else "clang"
        else:
            cc = "gcc"
    stubs = []
    for sfile in ["gc.c", "cross_compile_stubs.c" if not (os.name == "nt" and target_config.name == "win64") else "windows_stubs.c"]:
        spath = os.path.join(os.path.dirname(os.path.abspath(__file__)), sfile)
        if os.path.exists(spath):
            oname = f"{obj_name}_{sfile}.o"
            try: subprocess.run([cc, "-c", spath, "-o", oname], check=True, capture_output=True); stubs.append(oname)
            except: pass
    try:
        if output_type == "executable":
            out = target_config.get_output_name(output_name)
            subprocess.run([cc, obj_name] + stubs + ["-o", out] + target_config.linker_flags + nlib_args, check=True)
        elif output_type == "dynamic":
            out = output_name + (".dll" if os.name == "nt" else ".so")
            subprocess.run([cc, "-shared", obj_name, "-o", out, "-fPIC"] + nlib_args, check=True)
        elif output_type == "static":
            out = output_name + (".lib" if os.name == "nt" else ".a")
            subprocess.run(["ar", "rcs", out, obj_name], check=True)
    except: sys.exit(1)
    finally:
        for f in [obj_name] + stubs:
            if os.path.exists(f): os.remove(f)
    if not is_run_mode: print(f"Successfully compiled to '{out}'")
    return out

def dump_file(input_file, output_name=None, target_name=None, check_mode=False, warnings_as_errors=False, extra_libs=None, opt_level=None):
    with open(input_file, "r") as f: code = f.read()
    target_config = get_target(target_name) if target_name else get_native_target()
    try:
        lexer = Lexer(code); tokens = lexer.tokenize(); parser = Parser(tokens, input_file); ast = parser.parse()
        ast = resolve_imports(ast, os.path.dirname(os.path.abspath(input_file)) or ".")
        ast = resolve_conditionals(ast, target_config); ast = expand_macros(ast)
        warnings = TypeChecker(check_mode=check_mode).check(ast)
        for w in warnings: _print_warning(w, warnings_as_errors)
        if warnings_as_errors and warnings: sys.exit(1)
        llvm.initialize_all_targets()
        codegen = CodeGen(target_platform=target_config.name); codegen.generate_code(ast, input_file)
        mod = llvm.parse_assembly(codegen.get_ir()); mod.verify()
    except LeashError as e: _print_error(e, input_file, code); sys.exit(1)
    except Exception as e: import traceback; print(f"error: Internal: {e}"); traceback.print_exc(); sys.exit(1)
    popt, sopt = parse_opt_level(opt_level)
    if popt > 0 or sopt: optimize_module(mod, opt_level=popt, size_opt=sopt)
    if output_name is None: output_name = input_file[:-4] if input_file.endswith(".lsh") else "out"
    if not output_name.endswith(".ll"): output_name += ".ll"
    with open(output_name, "w") as f: f.write(str(mod))
    print(f"Dumped LLVM IR to '{output_name}'"); return output_name

def run_file(input_file, args=[], target_name=None, check_mode=False, warnings_as_errors=False, extra_libs=None, opt_level=None):
    import platform, time, uuid
    tcfg = get_target(target_name) if target_name else get_native_target()
    tmp = f".__temp_run_leash_exe_{uuid.uuid4().hex}"
    out = compile_file(input_file, output_name=tmp, is_run_mode=True, target_name=target_name, check_mode=check_mode, warnings_as_errors=warnings_as_errors, extra_libs=extra_libs, opt_level=opt_level)
    sys_name = platform.system().lower()
    cmd = [out] + args
    if tcfg.name == "win64" and sys_name != "windows":
        try: subprocess.run(["wine", "--version"], check=True, capture_output=True); cmd = ["wine", out] + args
        except: print("error: Cannot run Win64 binary on non-Windows without wine"); sys.exit(1)
    elif tcfg.name in ("macos", "macos-arm") and sys_name != "darwin":
        print("error: Cannot run macOS binary on non-macOS"); sys.exit(1)
    try:
        print(f"--- Executed at {time.strftime('%Y-%m-%d %H:%M:%S')} ---")
        res = subprocess.run(cmd)
        if res.returncode != 0: sys.exit(res.returncode)
    except: sys.exit(1)
    finally:
        if os.path.exists(out): os.remove(out)

def main():
    if len(sys.argv) < 2:
        print("Usage: leash <compile|run|dump|check|install> ...")
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd in ("--help", "-h"):
        print("Leash v1.0\nUsage: leash <command> [options]\nCommands: compile, run, dump, check, install\nRun 'leash <command> --help' for details.")
        sys.exit(0)
    if cmd in ("--version", "-v"):
        print("Leash v1.0\nBuilt on LLVM with custom GC"); sys.exit(0)
    if cmd == "check":
        if len(sys.argv) < 3: print("Usage: leash check <file.lsh>"); sys.exit(1)
        if not os.path.exists(sys.argv[2]): print(f"error: Not found: {sys.argv[2]}", file=sys.stderr); sys.exit(1)
        print(f"Checking '{sys.argv[2]}'...\n")
        errs, warns = check_file(sys.argv[2], verbose=True)
        if warns:
            print(f"Found {len(warns)} warning(s):\n")
            for w in warns: _print_warning(w); print()
        if not errs: print("No issues found." if not warns else f"Summary: 0 errors, {len(warns)} warnings."); sys.exit(0)
        print(f"Summary: {len(errs)} errors, {len(warns)} warnings."); sys.exit(1)
    if cmd in ("compile", "run", "dump"):
        if len(sys.argv) < 3: print(f"Usage: leash {cmd} <file.lsh> [options]"); sys.exit(1)
        if sys.argv[2] in ("--help", "-h"):
            print(f"Options for {cmd}:\n  --target <target>\n  --check\n  --warnings-as-errors\n  --opt <0,1,2,3,s>\n  -l<lib>")
            sys.exit(0)
        infile = sys.argv[2]; target, outname, outtype, check, warnerr, elibs, opt = None, None, "executable", False, False, [], "2"
        i = 3
        while i < len(sys.argv):
            if sys.argv[i] == "--target" and i+1 < len(sys.argv): target = sys.argv[i+1]; i += 2
            elif sys.argv[i] == "--check": check = True; i += 1
            elif sys.argv[i] == "--warnings-as-errors": warnerr = True; i += 1
            elif (sys.argv[i] == "--opt" or sys.argv[i] == "-O") and i+1 < len(sys.argv): opt = sys.argv[i+1]; i += 2
            elif sys.argv[i].startswith("-l"): elibs.append(sys.argv[i][2:]); i += 1
            elif sys.argv[i] == "to" and i+1 < len(sys.argv): outname = sys.argv[i+1]; i += 2
            elif sys.argv[i] == "to-dynamic": outtype = "dynamic"; i += 1
            elif sys.argv[i] == "to-static": outtype = "static"; i += 1
            else: i += 1
        if cmd == "run": run_file(infile, sys.argv[i:], target, check, warnerr, elibs, opt)
        elif cmd == "dump": dump_file(infile, outname, target, check, warnerr, elibs, opt)
        else: compile_file(infile, outname, outtype, False, target, check, warnerr, elibs, opt)
    elif cmd == "install":
        if len(sys.argv) < 3: print("Usage: leash install <path> ..."); sys.exit(1)
        install_libraries(sys.argv[2:])
    else: print(f"Unknown command: {cmd}"); sys.exit(1)

if __name__ == "__main__": main()
