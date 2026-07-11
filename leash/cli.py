import sys
import os
import subprocess
import shutil
from .lexer import Lexer
from .parser_l import Parser
from .codegen import CodeGen
from .typechecker import TypeChecker
from .lowlevel_checker import LowLevelChecker
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
    SpawnStatement,
    ThisWorkerExpr,
    TemplateDef,
    ImportStmt,
    ConditionalDef,
    BuiltinVarLiteral,
    StringLiteral,
    BoolLiteral,
    BinaryOp,
    UnaryOp,
    MacroDef,
    NativeImport,
    Call
)
from .targets import get_target, get_native_target, list_targets, TargetConfig
from .optimize import optimize_module, parse_opt_level
from .ast_optimize import optimize_ast
import llvmlite.binding as llvm


VERBOSE_MODE = False


def get_verbose_explanation(msg, code=None):
    c = str(code).upper() if code else ""
    m = msg.lower()

    # LEASH-E001: Redefinition of variable
    if c == "LEASH-E001" or "redefinition of variable" in m:
        return """
=========================================
Leash Masterclass: Redefinition of Variable
=========================================
In Leash, you cannot redeclare a variable with the same name in the same scope. 
This prevents accidental shadowing or re-declaration bugs.

If you want to modify a variable's value, simply assign to it using '=' 
without repeating the type declaration or using the ':=' operator.

[CORRECT EXAMPLES]:
fnc main() : void {
    x: int = 10;
    x = 20; // Reassign, do not redeclare
    show(x);
}
"""

    # LEASH-E002: void variable declaration
    elif c == "LEASH-E002" or "type 'void'" in m or "void variable" in m:
        return """
=========================================
Leash Masterclass: Void Variable Declaration
=========================================
In Leash, the 'void' type represents the absence of a value. 
It is exclusively used as a function return type to indicate the function 
does not return anything. You cannot declare a variable of type 'void'.

If you need a variable, declare it with a concrete type (like int, float, bool, or string).

[CORRECT EXAMPLES]:
fnc main() : void {
    x: int = 0; // Use a concrete type
    show(x);
}
"""

    # LEASH-E003: Unknown type
    elif c == "LEASH-E003" or "unknown type" in m or ("type" in m and "not been defined" in m):
        return """
=========================================
Leash Masterclass: Unknown Type
=========================================
In Leash, all custom types (structs, classes, unions, aliases, enums) 
must be declared using the 'def' keyword before they can be referenced in your code.

Double-check for typos or ensure you have defined the type.

[CORRECT EXAMPLES]:
def MyFloat : type float; // Define type alias
fnc main() : void {
    val: MyFloat = 3.14;
    show(val);
}
"""

    # LEASH-E004: := without initializer
    elif c == "LEASH-E004" or "without an initializer" in m:
        return """
=========================================
Leash Masterclass: Type Inference (:=)
=========================================
The type inference operator ':=' tells the Leash compiler to determine 
the variable's type based on its initial value. Because of this, 
you must provide a value on the right-hand side.

[CORRECT EXAMPLES]:
fnc main() : void {
    x := 42; // compiler infers x is int
    name := "Leash"; // compiler infers name is string
    show(x, " ", name);
}
"""

    # LEASH-E005: Immutable assignment / reassignment
    elif c == "LEASH-E005" or "cannot assign to immutable variable" in m:
        return """
=========================================
Leash Masterclass: Immutability (imut)
=========================================
Variables in Leash can be declared immutable using the 'imut' modifier. 
Additionally, if a variable receives a value from a function returning an 'imut' type, 
that variable automatically becomes immutable!

Once a variable is immutable, you cannot reassign a new value to it. 
To modify its value, remove the 'imut' keyword from its declaration and ensure 
functions returning values to it return standard, mutable types.

[CORRECT EXAMPLES]:
fnc main() : void {
    x: int = 10; // Declare without 'imut'
    x = 20; // Safe to modify
    show(x);
}
"""

    # LEASH-E006: Type mismatch in assignment
    elif c == "LEASH-E006" or ("cannot assign" in m and "to a variable of type" in m):
        return """
=========================================
Leash Masterclass: Type Mismatch & Casting
=========================================
Leash is a strongly and statically typed language. It does not perform implicit 
conversions between mismatched types. To assign a value of one type to a variable 
of a different type, you must use an explicit type cast.

Type cast syntax: (TargetType)value

[CORRECT EXAMPLES]:
fnc main() : void {
    x: int = 10;
    f: float = (float)x; // Explicit cast from int to float
    show(f);
}
"""

    # LEASH-E007: Case type mismatch
    elif c == "LEASH-E007" or ("case type" in m and "does not match" in m):
        return """
=========================================
Leash Masterclass: Switch Case Types
=========================================
In Leash switch-case statements, every 'case' expression must evaluate to 
the exact same type as the main 'switch' expression.

[CORRECT EXAMPLES]:
fnc main() : void {
    val: int = 2;
    switch val {
        case 1 { show("one"); }
        case 2 { show("two"); }
        default { show("default"); }
    }
}
"""

    # LEASH-E008: Duplicate case
    elif c == "LEASH-E008" or "duplicate case value" in m:
        return """
=========================================
Leash Masterclass: Duplicate Switch Case
=========================================
Each case value in a switch statement must be unique. Duplicate cases 
are redundant, unreachable, and will cause compiler errors.

[CORRECT EXAMPLES]:
fnc main() : void {
    val: int = 1;
    switch val {
        case 1 { show("One"); }
        case 2 { show("Two"); } // Unique case value
        default { show("Other"); }
    }
}
"""

    # LEASH-E009: Method not found / Type has no method named
    elif c == "LEASH-E009" or "has no method named" in m:
        return """
=========================================
Leash Masterclass: Member & Method Resolution
=========================================
You are calling a method that is not defined on the target struct, union, or class. 
Verify the spelling of the method name, class definition, or parameters.

[CORRECT EXAMPLES]:
def Calculator : class {
    pub name: string;
    pub fnc add(a int, b int) : int {
        return a + b;
    }
}
fnc main() : void {
    calc: Calculator = Calculator { name: "calc" };
    show(calc.add(5, 5));
}
"""

    # Undefined variable
    elif "undefined variable" in m:
        return """
=========================================
Leash Masterclass: Undefined Variable
=========================================
All variables in Leash must be defined before they are used. You can define a variable 
either explicitly by specifying its type (e.g., 'x: int = 10;') or implicitly 
via type inference (e.g., 'x := 10;').

[CORRECT EXAMPLES]:
fnc main() : void {
    x: int = 5; // Define x first
    show(x);
}
"""

    # Shadowing warning (LEASH-W001)
    elif c == "LEASH-W001" or "shadows a global variable" in m:
        return """
=========================================
Leash Masterclass: Variable Shadowing
=========================================
Shadowing happens when a local variable has the exact same name as a global variable. 
This is a warning because it can lead to subtle bugs where you modify the local variable 
while intending to modify the global one.

Consider renaming the local variable or the global variable to keep them distinct.

[CORRECT EXAMPLES]:
global_counter: int = 100;
fnc main() : void {
    local_counter: int = 200; // Distinct names prevent confusion
    show(global_counter + local_counter);
}
"""

    # Self-assignment warning (LEASH-W003)
    elif c == "LEASH-W003" or "self-assignment" in m:
        return """
=========================================
Leash Masterclass: Self Assignment
=========================================
Assigning a variable to itself (e.g., `x = x;`) has no functional effect. 
The compiler issues a warning to alert you of potential copy-paste or logic errors.

[CORRECT EXAMPLES]:
fnc main() : void {
    x: int = 10;
    x = 20; // Assign a new value instead
    show(x);
}
"""

    # Function body warning
    elif "empty body" in m and "function" in m:
        return """
=========================================
Leash Masterclass: Empty Function Body
=========================================
Defining a function with an empty body is allowed but generates a warning 
to ensure it wasn't left unimplemented by mistake. 

If you want a no-op function, add a simple return statement or comments inside.

[CORRECT EXAMPLES]:
fnc do_nothing() : void {
    // Explicitly empty
    ignore;
}
fnc main() : void {
    do_nothing();
}
"""

    # Function parameters warning
    elif ("parameters" in m and "has too many" in m) or "consider grouping" in m:
        return """
=========================================
Leash Masterclass: Parameter Grouping
=========================================
Functions with a high number of parameters (e.g., more than 8) are hard to read, 
test, and maintain. In Leash, it is recommended to group related parameters 
into a 'struct' and pass the struct instance instead.

[CORRECT EXAMPLES]:
def Config : struct {
    a: int; b: int; c: int; d: int;
    e: int; f: int; g: int; h: int; i: int;
};
fnc configure(cfg Config) : void {
    show(cfg.a);
}
fnc main() : void {
    c: Config = Config {a:1, b:2, c:3, d:4, e:5, f:6, g:7, h:8, i:9};
    configure(c);
}
"""

    return None


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

def parse_lshc_config(path):
    """Parse a .lshc config file. Returns a dict of key-value pairs.

    Format:
      key: value  # optional comment
    Supports strings (double-quoted), dicts ({}), and lists ({} like sets).
    """
    config = {}
    with open(path, "r") as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            comment_idx = stripped.find(" #")
            if comment_idx >= 0:
                stripped = stripped[:comment_idx].strip()
            if ":" not in stripped:
                continue
            key, _, val = stripped.partition(":")
            key = key.strip()
            val = val.strip()
            if not key:
                continue
            if val.startswith('"') and val.endswith('"'):
                val = val[1:-1]
            elif val == "{}":
                val = {}
            elif val.startswith("{") and val.endswith("}"):
                inner = val[1:-1].strip()
                items = {}
                if inner:
                    for part in inner.split(","):
                        part = part.strip()
                        if not part:
                            continue
                        v = [x.strip().strip('"') for x in part.split(":")]
                        if len(v) == 1:
                            items[v[0]] = None
                        elif len(v) == 2:
                            items[v[0]] = v[1]
                val = items
            config[key] = val
    return config


def init_project(project_dir):
    """Scaffold a new leash project in the given directory."""
    project_dir = os.path.abspath(project_dir)
    if os.path.exists(project_dir) and os.listdir(project_dir):
        print(f"error: Directory '{project_dir}' is not empty", file=sys.stderr)
        sys.exit(1)
    os.makedirs(project_dir, exist_ok=True)
    src_dir = os.path.join(project_dir, "src")
    imports_dir = os.path.join(project_dir, "imports")
    out_dir = os.path.join(project_dir, "out")
    for d in [src_dir, imports_dir, out_dir]:
        os.makedirs(d, exist_ok=True)
    main_lsh = os.path.join(src_dir, "main.lsh")
    with open(main_lsh, "w") as f:
        f.write('fnc main |> show("Hello, World!");\n')
    config_path = os.path.join(project_dir, "config.lshc")
    default_out_name = os.path.basename(project_dir)
    with open(config_path, "w") as f:
        f.write('main: "src/main.lsh"\n')
        f.write("clibs: {}\n")
        f.write('imports: "imports/"\n')
        f.write('opt_level: "O3"\n')
        f.write(f'out_name: "{default_out_name}"\n')
    print(f"Initialized leash project in '{project_dir}'")
    print(f"  {main_lsh}")
    print(f"  {config_path}")
    print(f"  {imports_dir}/")
    print(f"  {out_dir}/")


def resolve_imports(program, base_path, extra_import_dirs=None):
    loaded_modules = set()
    global_libs_dir = os.path.expanduser("~/.leash/libs")
    extra_dirs = extra_import_dirs or []
    def find_module_file(module_path, search_path):
        path_str = os.path.join(*module_path)
        module_name = module_path[-1]
        search_dirs = [search_path] + [d for d in extra_dirs if os.path.isdir(d)]
        for sd in search_dirs:
            direct = os.path.join(sd, f"{path_str}.lsh")
            if os.path.exists(direct):
                return direct
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
                    raise LeashError(f"Module '{'::'.join(item.module_path)}' not found or ambiguous", node=item)
                module_file_abs = os.path.abspath(module_file)
                if module_file_abs in loaded_modules: continue
                with open(module_file_abs, "r") as f: code = f.read()
                try:
                    lexer = Lexer(code); tokens = lexer.tokenize(); parser = Parser(tokens, module_file_abs); module_ast = parser.parse()
                except LeashError as e:
                    if e.file is None: e.file = module_file_abs
                    raise
                module_dir = os.path.dirname(module_file_abs) or "."
                module_ast = _expand_items(module_ast.items, module_dir)
                is_priv_import = item.visibility == "priv"
                available = {}
                all_templates = {i.name: i for i in module_ast.items if isinstance(i, TemplateDef)}
                internal_types = {}
                def _collect_items(mod_items):
                    for mod_item in mod_items:
                        if isinstance(mod_item, ConditionalDef):
                            _collect_items(mod_item.then_block)
                            for c, b, inv in getattr(mod_item, 'also_blocks', []):
                                _collect_items(b)
                            if mod_item.else_block:
                                _collect_items(mod_item.else_block)
                            continue
                        if not is_priv_import and hasattr(mod_item, "visibility") and mod_item.visibility == "priv":
                            if isinstance(mod_item, (StructDef, UnionDef, EnumDef, ClassDef, TypeAlias, ErrorDef)): internal_types[mod_item.name] = mod_item
                            continue
                        if isinstance(mod_item, (StructDef, UnionDef, EnumDef, ErrorDef, TypeAlias, ClassDef, Function, TemplateDef, MacroDef)): available[mod_item.name] = mod_item
                        elif isinstance(mod_item, GlobalVarDecl) and (mod_item.visibility == "pub" or is_priv_import): available[mod_item.name] = mod_item
                _collect_items(module_ast.items)
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
                            if name not in available: raise LeashError(f"Imported item '{name}' not found in module", node=item)
                    for name, mod_item in available.items(): new_items.append(mod_item)
                    for name, mod_item in internal_types.items(): new_items.append(mod_item)
                    for mod_item in module_ast.items:
                        if isinstance(mod_item, NativeImport): new_items.append(mod_item)
                        if isinstance(mod_item, ConditionalDef): new_items.append(mod_item)
                loaded_modules.add(module_file_abs)
            elif isinstance(item, ConditionalDef):
                if item.then_block: item.then_block = _expand_items(item.then_block, current_base_path).items
                if item.also_blocks:
                    item.also_blocks = [(c, _expand_items(b, current_base_path).items, inv) for c, b, inv in item.also_blocks]
                if item.else_block: item.else_block = _expand_items(item.else_block, current_base_path).items
                new_items.append(item)
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
            if len(node.args) != len(m.params): raise LeashError(f"Macro '{node.name}' expects {len(m.params)} args, got {len(node.args)}", node=node)
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
            raise LeashError(f"Unsupported builtin '{expr.name}'", node=expr)
        elif isinstance(expr, StringLiteral): return expr.value
        elif isinstance(expr, BoolLiteral): return expr.value
        elif isinstance(expr, BinaryOp):
            l, r = eval_expr(expr.left), eval_expr(expr.right)
            if expr.op == "==": return l == r
            if expr.op == "!=": return l != r
            if expr.op == "&&": return l and r
            if expr.op == "||": return l or r
            raise LeashError(f"Operator '{expr.op}' not supported in conditional", node=expr)
        elif isinstance(expr, UnaryOp):
            if expr.op == "!": return not eval_expr(expr.expr)
            raise LeashError(f"Unary '{expr.op}' not supported", node=expr)
        raise LeashError(f"Unsupported expr {type(expr).__name__}", node=expr)
    if cond_def.invert:
        if not eval_expr(cond_def.condition): return cond_def.then_block
    elif eval_expr(cond_def.condition): return cond_def.then_block
    for c, b, inv in cond_def.also_blocks:
        if inv:
            if not eval_expr(c): return b
        elif eval_expr(c): return b
    return cond_def.else_block

def _print_error(e, input_file, code):
    f = e.file or input_file
    loc = f"{f}:{e.line}:{e.col or 0}" if e.line else f
    code_str = f" [{e.code}]" if e.code else ""
    print(f"error{code_str}: {e.msg}", file=sys.stderr)
    print(f"  --> {loc}", file=sys.stderr)
    if e.line:
        c = code
        if e.file and e.file != input_file:
            try:
                with open(e.file, "r") as fh: c = fh.read()
            except: c = code
        lines = c.splitlines(); idx = e.line - 1
        if 0 <= idx < len(lines):
            p = " " * (len(str(e.line)) + 1)
            print(f"{p}|", file=sys.stderr)
            print(f"{e.line} | {lines[idx]}", file=sys.stderr)
            if e.col is not None:
                print(f"{p}| {' '*e.col}^", file=sys.stderr)
            print(f"{p}|", file=sys.stderr)
    if e.tip:
        p = " " * (len(str(e.line)) + 1) if e.line else "  "
        print(f"{p}= tip: {e.tip}", file=sys.stderr)
    if VERBOSE_MODE:
        explanation = get_verbose_explanation(e.msg, e.code)
        if explanation:
            print(explanation, file=sys.stderr)

def _print_warning(w, warnings_as_errors=False, code=None, input_file=None):
    print(f"{'error:' if warnings_as_errors else 'warning:'} {w['msg']}", file=sys.stderr)
    f = w.get('file') or input_file or 'unknown'
    if w.get("line"):
        print(f"  --> {f}:{w['line']}:{w.get('col',0)}{' ['+w['code']+']' if w.get('code') else ''}", file=sys.stderr)
        if code:
            c = code
            if w.get('file') and w['file'] != input_file:
                try:
                    with open(w['file'], "r") as fh:
                        c = fh.read()
                except:
                    pass
            lines = c.splitlines(); idx = w['line'] - 1
            if 0 <= idx < len(lines):
                p = " " * (len(str(w['line'])) + 1)
                print(f"{p}|", file=sys.stderr)
                print(f"{w['line']} | {lines[idx]}", file=sys.stderr)
                if w.get('col') is not None:
                    print(f"{p}| {' '*w['col']}^", file=sys.stderr)
                print(f"{p}|", file=sys.stderr)
    if w.get("tip"):
        p = " " * (len(str(w['line'])) + 1) if w.get('line') else "  "
        print(f"{p}= tip: {w['tip']}", file=sys.stderr)
    if VERBOSE_MODE:
        explanation = get_verbose_explanation(w['msg'], w.get('code'))
        if explanation:
            print(explanation, file=sys.stderr)

def check_file(input_file, verbose=False, extra_import_dirs=None):
    with open(input_file, "r") as f: code = f.read()
    errors, warnings = [], []
    try:
        lexer = Lexer(code); tokens = lexer.tokenize(); parser = Parser(tokens, input_file); ast = parser.parse()
        ast = resolve_imports(ast, os.path.dirname(os.path.abspath(input_file)) or ".", extra_import_dirs=extra_import_dirs or [])
        ast = resolve_conditionals(ast, get_native_target()); ast = expand_macros(ast)
    except LeashError as e:
        if verbose: _print_error(e, input_file, code)
        errors.append(e); return errors, warnings
    except Exception as e:
        if verbose: import traceback; print(f"error: Internal: {e}", file=sys.stderr); traceback.print_exc()
        return errors, warnings
    try:
        warnings = TypeChecker(check_mode=True).check(ast)
        ll_errors = LowLevelChecker().check(ast)
        if ll_errors:
            for err in ll_errors:
                if verbose: _print_error(err, input_file, code)
            errors.extend(ll_errors)
    except LeashError as e:
        if verbose: _print_error(e, input_file, code)
        errors.append(e)
    except Exception as e:
        if verbose: import traceback; print(f"error: Internal: {e}", file=sys.stderr); traceback.print_exc()
    return errors, warnings

def compile_file(input_file, output_name=None, output_type="executable", is_run_mode=False, target_name=None, check_mode=False, warnings_as_errors=False, extra_libs=None, opt_level=None, extra_import_dirs=None):
    with open(input_file, "r") as f: code = f.read()
    target_config = get_target(target_name) if target_name else get_native_target()
    try:
        lexer = Lexer(code); tokens = lexer.tokenize(); parser = Parser(tokens, input_file); ast = parser.parse()
        ast = resolve_imports(ast, os.path.dirname(os.path.abspath(input_file)) or ".", extra_import_dirs=extra_import_dirs)
        ast = resolve_conditionals(ast, target_config); ast = expand_macros(ast)
        warnings = TypeChecker(check_mode=check_mode).check(ast)
        for w in warnings: _print_warning(w, warnings_as_errors, code=code, input_file=input_file)
        if warnings_as_errors and warnings: sys.exit(1)
        ll_errors = LowLevelChecker().check(ast)
        if ll_errors:
            for err in ll_errors: _print_error(err, input_file, code)
            sys.exit(1)
        ast = optimize_ast(ast)
        parsed_opt, size_opt = parse_opt_level(opt_level)
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
    return _link_native(obj_name, output_name, target_config, is_run_mode, output_type, codegen, extra_libs)

def _parse_undefined_symbols(stderr):
    """Parse undefined reference symbols from linker error output."""
    import re
    symbols = set()
    # MinGW/ld: "undefined reference to `__imp_timeEndPeriod'"
    for m in re.finditer(r"undefined reference to [`']([^`']+)", stderr):
        sym = m.group(1)
        sym = sym.lstrip("_")
        if sym.startswith("imp_"):
            sym = sym[4:]
        if sym:
            symbols.add(sym)
    return symbols


_WIN32_SYMBOL_LIBS = {
    "CreateRectRgn": "gdi32", "DeleteObject": "gdi32", "SwapBuffers": "gdi32",
    "CreateDCW": "gdi32", "GetDeviceGammaRamp": "gdi32", "DeleteDC": "gdi32",
    "GetDeviceCaps": "gdi32", "ChoosePixelFormat": "gdi32", "SetPixelFormat": "gdi32",
    "DescribePixelFormat": "gdi32", "CreateDIBSection": "gdi32", "CreateBitmap": "gdi32",
    "SetDeviceGammaRamp": "gdi32",
    "timeEndPeriod": "winmm", "timeBeginPeriod": "winmm",
    "glClear": "opengl32", "glBegin": "opengl32", "glEnd": "opengl32",
    "glMatrixMode": "opengl32", "glLoadIdentity": "opengl32",
    "glOrtho": "opengl32", "glViewport": "opengl32",
}


_LINUX_SYMBOL_LIBS = {
    "XOpenDisplay": "X11", "XCloseDisplay": "X11", "XCreateWindow": "X11",
    "XMapWindow": "X11", "XFlush": "X11",
    "glXSwapBuffers": "GL", "glXMakeCurrent": "GL",
    "dlopen": "dl", "dlsym": "dl", "dlclose": "dl",
    "sincosf": "m", "sincos": "m",
    "fmaxf": "m", "fmax": "m", "fminf": "m", "fmin": "m",
    "atan2f": "m", "atan2": "m",
    "sinf": "m", "sin": "m", "cosf": "m", "cos": "m",
    "sqrtf": "m", "sqrt": "m",
    "powf": "m", "pow": "m",
    "fmodf": "m", "fmod": "m",
    "roundf": "m", "round": "m",
    "hypotf": "m", "hypot": "m",
    "logf": "m", "log": "m",
    "acosf": "m", "acos": "m",
    "asinf": "m", "asin": "m",
    "tanf": "m", "tan": "m",
}


_MACOS_SYMBOL_LIBS = {
    "objc_msgSend": "objc", "objc_getClass": "objc",
    "sel_registerName": "objc",
}


def _match_symbols_to_libs(symbols, target_name):
    """Match undefined symbols to system library names by target platform."""
    deps = set()
    mapping = {}
    if target_name == "win64":
        mapping = _WIN32_SYMBOL_LIBS
        # On Windows MinGW, also try to check if gdi32/winmm/opengl32 exist as
        # import libraries and add them proactively when many Win32 symbols are seen
        win32_count = sum(1 for s in symbols if s in _WIN32_SYMBOL_LIBS)
    elif target_name in ("linux64", "linux32"):
        mapping = _LINUX_SYMBOL_LIBS
    elif target_name in ("macos", "macos-arm"):
        mapping = _MACOS_SYMBOL_LIBS
    for sym in symbols:
        lib = mapping.get(sym)
        if lib:
            deps.add(lib)
    return deps


def _link_native(obj_name, output_name, target_config, is_run_mode, output_type, codegen, extra_libs=None):
    nlib_args = [l[0] for l in codegen.native_libs]
    if extra_libs: nlib_args.extend([f"-l{l}" for l in extra_libs])
    cc = os.environ.get("CC") or target_config.detect_cross_linker()
    if not cc:
        if os.name == "nt":
            cc = "gcc" if shutil.which("gcc") else "clang"
        else:
            cc = shutil.which("gcc") or shutil.which("clang")
            if not cc:
                print("error: No C compiler found (install gcc or clang, or set CC env var)", file=sys.stderr)
                sys.exit(1)

    stubs = []
    for sfile in ["gc.c", "cross_compile_stubs.c" if not (os.name == "nt" and target_config.name == "win64") else "windows_stubs.c"]:
        spath = os.path.join(os.path.dirname(os.path.abspath(__file__)), sfile)
        if os.path.exists(spath):
            oname = f"{obj_name}_{sfile}.o"
            res = subprocess.run([cc, "-c", spath, "-o", oname], stderr=subprocess.PIPE, stdout=subprocess.PIPE)
            if res.returncode != 0:
                err = res.stderr.decode("utf-8", errors="replace").strip()
                print(f"warning: failed to compile {sfile}: {err}", file=sys.stderr)
            else:
                stubs.append(oname)

    out = None
    retried = False
    while True:
        try:
            if output_type == "executable":
                out = target_config.get_output_name(output_name)
                result = subprocess.run([cc, obj_name] + stubs + ["-o", out] + target_config.linker_flags + nlib_args, stderr=subprocess.PIPE)
                if result.returncode != 0:
                    raise subprocess.CalledProcessError(result.returncode, result.args, stderr=result.stderr)
            elif output_type == "dynamic":
                out = output_name + (".dll" if os.name == "nt" else ".so")
                subprocess.run([cc, "-shared", obj_name, "-o", out, "-fPIC"] + nlib_args, stderr=subprocess.PIPE, check=True)
            elif output_type == "static":
                out = output_name + (".lib" if os.name == "nt" else ".a")
                subprocess.run(["ar", "rcs", out, obj_name], check=True)
            break
        except subprocess.CalledProcessError as e:
            stderr_text = e.stderr.decode("utf-8", errors="replace") if e.stderr else ""
            if output_type == "executable" and stderr_text:
                symbols = _parse_undefined_symbols(stderr_text)
                detected = _match_symbols_to_libs(symbols, target_config.name)
                if detected:
                    to_add = [f"-l{l}" for l in sorted(detected) if f"-l{l}" not in nlib_args]
                    if to_add:
                        nlib_args.extend(to_add)
                        print(f"Auto-detected missing system libraries: {', '.join(sorted(detected))}", file=sys.stderr)
                        continue
            if stderr_text:
                print(stderr_text.strip(), file=sys.stderr)
            else:
                print(f"error: Linker failed with exit code {e.returncode}", file=sys.stderr)
            sys.exit(1)
        except FileNotFoundError:
            print(f"error: C compiler '{cc}' not found", file=sys.stderr)
            sys.exit(1)

    for f in [obj_name] + stubs:
        try:
            if os.path.exists(f):
                os.remove(f)
        except OSError:
            pass
    if not is_run_mode: print(f"Successfully compiled to '{out}'")
    return out

def dump_file(input_file, output_name=None, target_name=None, check_mode=False, warnings_as_errors=False, extra_libs=None, opt_level=None, extra_import_dirs=None):
    with open(input_file, "r") as f: code = f.read()
    target_config = get_target(target_name) if target_name else get_native_target()
    try:
        lexer = Lexer(code); tokens = lexer.tokenize(); parser = Parser(tokens, input_file); ast = parser.parse()
        ast = resolve_imports(ast, os.path.dirname(os.path.abspath(input_file)) or ".", extra_import_dirs=extra_import_dirs)
        ast = resolve_conditionals(ast, target_config); ast = expand_macros(ast)
        warnings = TypeChecker(check_mode=check_mode).check(ast)
        for w in warnings: _print_warning(w, warnings_as_errors, code=code, input_file=input_file)
        if warnings_as_errors and warnings: sys.exit(1)
        ll_errors = LowLevelChecker().check(ast)
        if ll_errors:
            for err in ll_errors: _print_error(err, input_file, code)
            sys.exit(1)
        ast = optimize_ast(ast)
        popt, sopt = parse_opt_level(opt_level)
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

def run_file(input_file, args=[], target_name=None, check_mode=False, warnings_as_errors=False, extra_libs=None, opt_level=None, extra_import_dirs=None):
    import platform, time, uuid, stat, signal
    tcfg = get_target(target_name) if target_name else get_native_target()
    tmp = f".__temp_run_leash_exe_{uuid.uuid4().hex}"
    out = compile_file(input_file, output_name=tmp, is_run_mode=True, target_name=target_name, check_mode=check_mode, warnings_as_errors=warnings_as_errors, extra_libs=extra_libs, opt_level=opt_level, extra_import_dirs=extra_import_dirs)
    # Use absolute path to avoid working directory issues
    out_abs = os.path.abspath(out)
    # Ensure binary is executable (important on filesystems like WSL DrvFs)
    try: os.chmod(out_abs, stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)
    except: pass
    sys_name = platform.system().lower()
    cmd = [out_abs] + args
    if tcfg.name == "win64" and sys_name != "windows":
        res = subprocess.run(["wine", "--version"], stderr=subprocess.PIPE, stdout=subprocess.PIPE)
        if res.returncode != 0:
            print("error: Cannot run Win64 binary on non-Windows without wine"); sys.exit(1)
        cmd = ["wine", out] + args
    elif tcfg.name in ("macos", "macos-arm") and sys_name != "darwin":
        print("error: Cannot run macOS binary on non-macOS"); sys.exit(1)
    proc = None
    old_handler = signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        print(f"--- Executed at {time.strftime('%Y-%m-%d %H:%M:%S')} ---")
        proc = subprocess.Popen(cmd)
        proc.wait()
        if proc.returncode != 0:
            if sys_name == "windows" and proc.returncode == 0xC000013A:
                print("\n(Interrupted)")
            elif proc.returncode < 0:
                print(f"\n(Interrupted by signal {-proc.returncode})")
            sys.exit(proc.returncode)
    except FileNotFoundError:
        exists = os.path.exists(out_abs)
        print(f"error: Could not execute '{out_abs}'", file=sys.stderr)
        if not exists:
            print(f"  -> file does not exist", file=sys.stderr)
        else:
            try:
                perms = oct(os.stat(out_abs).st_mode & 0o777)
                print(f"  -> exists, permissions: {perms}", file=sys.stderr)
                import subprocess as _sp
                res2 = _sp.run(["file", out_abs], stdout=_sp.PIPE, stderr=_sp.PIPE)
                print(f"  -> type: {res2.stdout.decode('utf-8', errors='replace').strip()}", file=sys.stderr)
            except Exception as _e:
                print(f"  -> diagnostic error: {_e}", file=sys.stderr)
        sys.exit(1)
    except OSError as e:
        print(f"error: Could not execute '{out_abs}': {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n(Interrupted)")
        if proc and proc.poll() is None:
            proc.terminate()
            try: proc.wait(timeout=5)
            except: proc.kill(); proc.wait()
        sys.exit(1)
    finally:
        signal.signal(signal.SIGINT, old_handler)
        if os.path.exists(out_abs):
            for _ in range(10):
                try:
                    os.remove(out_abs)
                    break
                except OSError:
                    time.sleep(0.1)

def read_project_config(project_dir):
    """Read config.lshc and return (config, project_dir)."""
    config_path = os.path.join(project_dir, "config.lshc")
    if not os.path.exists(config_path):
        print(f"error: No config.lshc found in '{project_dir}'", file=sys.stderr)
        print("  Run 'leash init' to create a project", file=sys.stderr)
        sys.exit(1)
    config = parse_lshc_config(config_path)
    main_file = config.get("main")
    if not main_file:
        print("error: 'main' not set in config.lshc", file=sys.stderr)
        sys.exit(1)
    main_path = os.path.join(project_dir, main_file)
    if not os.path.exists(main_path):
        print(f"error: Main file '{main_path}' not found", file=sys.stderr)
        sys.exit(1)
    return config, project_dir, main_path


def resolve_project_deps(config, project_dir, extra_import_dirs=None):
    """Resolve imports dir and clibs from config."""
    imports_dir = config.get("imports")
    all_extra_dirs = list(extra_import_dirs or [])
    if imports_dir:
        abs_imports = os.path.join(project_dir, imports_dir)
        if os.path.isdir(abs_imports):
            all_extra_dirs.append(abs_imports)
    clibs = config.get("clibs", {})
    extra_libs = list(clibs.keys()) if isinstance(clibs, dict) else []
    opt_level = config.get("opt_level", "2")
    if opt_level.startswith("O"):
        opt_level = opt_level[1:]
    return all_extra_dirs, extra_libs, opt_level


def build_project(extra_import_dirs=None):
    """Build the project using config.lshc in the current directory."""
    project_dir = os.getcwd()
    config, project_dir, main_path = read_project_config(project_dir)
    all_extra_dirs, extra_libs, opt_level = resolve_project_deps(config, project_dir, extra_import_dirs)
    out_basename = config.get("out_name") or os.path.basename(project_dir)
    out_name = os.path.join(project_dir, "out", out_basename)
    compile_file(main_path, output_name=out_name, extra_import_dirs=all_extra_dirs, extra_libs=extra_libs, opt_level=opt_level)


def run_project(prog_args=None, extra_import_dirs=None):
    """Build and run the project using config.lshc in the current directory."""
    import platform, time, uuid, stat
    project_dir = os.getcwd()
    config, project_dir, main_path = read_project_config(project_dir)
    all_extra_dirs, extra_libs, opt_level = resolve_project_deps(config, project_dir, extra_import_dirs)
    tmp = f".__temp_run_leash_exe_{uuid.uuid4().hex}"
    out = compile_file(main_path, output_name=tmp, is_run_mode=True, extra_import_dirs=all_extra_dirs, extra_libs=extra_libs, opt_level=opt_level)
    out_abs = os.path.abspath(out)
    try:
        os.chmod(out_abs, stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)
    except:
        pass
    sys_name = platform.system().lower()
    target_config = get_native_target()
    cmd = [out_abs] + (prog_args or [])
    if target_config.name == "win64" and sys_name != "windows":
        res = subprocess.run(["wine", "--version"], stderr=subprocess.PIPE, stdout=subprocess.PIPE)
        if res.returncode != 0:
            print("error: Cannot run Win64 binary on non-Windows without wine")
            sys.exit(1)
        cmd = ["wine", out] + (prog_args or [])
    elif target_config.name in ("macos", "macos-arm") and sys_name != "darwin":
        print("error: Cannot run macOS binary on non-macOS")
        sys.exit(1)
    try:
        print(f"--- Executed at {time.strftime('%Y-%m-%d %H:%M:%S')} ---")
        res = subprocess.run(cmd)
        if res.returncode != 0:
            sys.exit(res.returncode)
    except FileNotFoundError:
        print(f"error: Could not execute '{out_abs}'", file=sys.stderr)
        sys.exit(1)
    except OSError as e:
        print(f"error: Could not execute '{out_abs}': {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        if os.path.exists(out_abs):
            for _ in range(10):
                try:
                    os.remove(out_abs)
                    break
                except OSError:
                    time.sleep(0.1)


def update_leash():
    import urllib.request
    import json
    
    print("Leash Update Checker")
    print("Current version: 0.19.0b0\n")
    
    try:
        req = urllib.request.Request(
            "https://api.github.com/repos/foksiny/leash/releases/latest",
            headers={"User-Agent": "leash-update"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            latest_tag = data.get("tag_name", "").lstrip("v")
            print(f"Latest remote version: {latest_tag}")
    except Exception as e:
        print(f"Could not fetch latest version from GitHub: {e}")
        print("Proceeding with git pull anyway...\n")
    
    print("\nPulling latest changes from GitHub...")
    result = subprocess.run(["git", "pull"], capture_output=True, text=True)
    print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="")
    if result.returncode == 0:
        print("Update complete.")
    else:
        print("Update failed.")


def main():
    global VERBOSE_MODE
    VERBOSE_MODE = False
    
    # Extract --verbose / -vb globally and remove from sys.argv
    for arg in list(sys.argv):
        if arg in ("--verbose", "-vb"):
            VERBOSE_MODE = True
            try:
                sys.argv.remove(arg)
            except ValueError:
                pass

    if len(sys.argv) < 2:
        print("Usage: leash <compile|run|dump|check|install|init|build|runp|update> ... [options]")
        print("Global Options:\n  --verbose/-vb        Enable highly detailed masterclass error and warning explanations.")
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd in ("--help", "-h"):
        print("Leash v0.19.0b0\nUsage: leash <command> [options]\nCommands: compile, run, dump, check, install, init, build, runp, update\nRun 'leash <command> --help' for details.\n\nGlobal Options:\n  --verbose/-vb        Enable highly detailed masterclass error and warning explanations.")
        sys.exit(0)
    if cmd in ("--version", "-v"):
        print("Leash v0.19.0b0\nBuilt on LLVM with custom GC"); sys.exit(0)
    if cmd == "check":
        if len(sys.argv) < 3:
            print("Usage: leash check <file.lsh> [options]")
            sys.exit(1)
        if sys.argv[2] in ("--help", "-h"):
            print("Options for check:\n  --other-imports/-oi <folder>\n  --verbose/-vb        Enable highly detailed masterclass error and warning explanations.")
            sys.exit(0)
        if not os.path.exists(sys.argv[2]):
            print(f"error: Not found: {sys.argv[2]}", file=sys.stderr)
            sys.exit(1)
        extra_import_dirs = []
        i = 3
        while i < len(sys.argv):
            if sys.argv[i] in ("--other-imports", "-oi") and i + 1 < len(sys.argv):
                extra_import_dirs.append(os.path.abspath(sys.argv[i + 1]))
                i += 2
            else:
                i += 1
        with open(sys.argv[2], "r") as f: check_code = f.read()
        print(f"Checking '{sys.argv[2]}'...\n")
        errs, warns = check_file(sys.argv[2], verbose=True, extra_import_dirs=extra_import_dirs)
        if warns:
            print(f"Found {len(warns)} warning(s):\n")
            for w in warns:
                _print_warning(w, code=check_code, input_file=sys.argv[2])
                print()
        if not errs:
            print("No issues found." if not warns else f"Summary: 0 errors, {len(warns)} warnings.")
            sys.exit(0)
        print(f"Summary: {len(errs)} errors, {len(warns)} warnings.")
        sys.exit(1)
    if cmd == "init":
        project_dir = sys.argv[2] if len(sys.argv) > 2 else "."
        init_project(project_dir)
        sys.exit(0)
    if cmd == "build":
        extra_import_dirs = []
        i = 2
        while i < len(sys.argv):
            if sys.argv[i] in ("--other-imports", "-oi") and i + 1 < len(sys.argv):
                extra_import_dirs.append(os.path.abspath(sys.argv[i + 1]))
                i += 2
            else:
                i += 1
        build_project(extra_import_dirs)
        sys.exit(0)
    if cmd == "runp":
        extra_import_dirs = []
        prog_args = []
        i = 2
        found_sep = False
        while i < len(sys.argv):
            if sys.argv[i] == "--":
                found_sep = True
                i += 1
                break
            if sys.argv[i] in ("--other-imports", "-oi") and i + 1 < len(sys.argv):
                extra_import_dirs.append(os.path.abspath(sys.argv[i + 1]))
                i += 2
            else:
                i += 1
        if found_sep:
            prog_args = sys.argv[i:]
        run_project(prog_args, extra_import_dirs)
        sys.exit(0)
    if cmd in ("compile", "run", "dump"):
        if len(sys.argv) < 3:
            print(f"Usage: leash {cmd} <file.lsh> [options]")
            sys.exit(1)
        if sys.argv[2] in ("--help", "-h"):
            print(f"Options for {cmd}:\n  --target <target>\n  --check\n  --warnings-as-errors\n  --opt <0,1,2,3,s,z>\n  -l<lib>\n  --other-imports/-oi <folder>\n  --verbose/-vb        Enable highly detailed masterclass error and warning explanations.")
            sys.exit(0)
        infile = sys.argv[2]
        target, outname, outtype, check, warnerr, elibs, opt = None, None, "executable", False, False, [], "2"
        extra_import_dirs = []
        i = 3
        while i < len(sys.argv):
            if sys.argv[i] == "--target" and i + 1 < len(sys.argv):
                target = sys.argv[i + 1]
                i += 2
            elif sys.argv[i] == "--check":
                check = True
                i += 1
            elif sys.argv[i] == "--warnings-as-errors":
                warnerr = True
                i += 1
            elif (sys.argv[i] == "--opt" or sys.argv[i] == "-O") and i + 1 < len(sys.argv):
                opt = sys.argv[i + 1]
                i += 2
            elif sys.argv[i].startswith("-l"):
                elibs.append(sys.argv[i][2:])
                i += 1
            elif sys.argv[i] == "to" and i + 1 < len(sys.argv):
                outname = sys.argv[i + 1]
                i += 2
            elif sys.argv[i] == "to-dynamic":
                outtype = "dynamic"
                i += 1
            elif sys.argv[i] == "to-static":
                outtype = "static"
                i += 1
            elif sys.argv[i] in ("--other-imports", "-oi") and i + 1 < len(sys.argv):
                extra_import_dirs.append(os.path.abspath(sys.argv[i + 1]))
                i += 2
            else:
                i += 1
        if cmd == "run":
            run_file(infile, sys.argv[i:], target, check, warnerr, elibs, opt, extra_import_dirs=extra_import_dirs)
        elif cmd == "dump":
            dump_file(infile, outname, target, check, warnerr, elibs, opt, extra_import_dirs=extra_import_dirs)
        else:
            compile_file(infile, outname, outtype, False, target, check, warnerr, elibs, opt, extra_import_dirs=extra_import_dirs)
    elif cmd == "update":
        update_leash()
        sys.exit(0)
    elif cmd == "install":
        if len(sys.argv) < 3:
            print("Usage: leash install <path> ...")
            sys.exit(1)
        install_libraries(sys.argv[2:])
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)

if __name__ == "__main__": main()
