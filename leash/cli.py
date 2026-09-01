import sys
import os
import subprocess
import shutil
import functools
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
from .targets import get_target, get_native_target, list_targets, TargetConfig, wsl_available
from .optimize import optimize_module, parse_opt_level
from .ast_optimize import optimize_ast
from .hoist_allocas import hoist_allocas
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
    # alias -> set(names) for rewriting qualified accesses like alias.item
    alias_maps = {}
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
                            # Conditional branches are resolved later (after
                            # _PLATFORM is known) by resolve_conditionals; do
                            # not merge every branch into `available`, or the
                            # winning branch's items get defined twice.
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
                # Handle alias cases
                alias = getattr(item, 'alias', None)
                if alias:
                    if item.imported_items is not None and len(item.imported_items) >= 1:
                        # Single-item alias rename: use orig as alias
                        # e.g. use hash::Hash alias MyHash;
                        if len(item.imported_items) != 1:
                            raise LeashError(f"Alias can only be used with a single imported item or whole-module import, got {item.imported_items}", node=item)
                        orig = item.imported_items[0]
                        if orig not in available:
                            raise LeashError(f"Imported item '{orig}' not found in module", node=item)
                        import copy as _copy
                        cloned = _copy.deepcopy(available[orig])
                        cloned.name = alias
                        # Preserve visibility of original
                        if hasattr(cloned, 'visibility'):
                            cloned.visibility = cloned.visibility
                        new_items.append(cloned)
                        # Also add internal/private types if needed
                        for name, mod_item in internal_types.items(): new_items.append(mod_item)
                        for mod_item in module_ast.items:
                            if isinstance(mod_item, NativeImport): new_items.append(mod_item)
                            if isinstance(mod_item, ConditionalDef): new_items.append(mod_item)
                        loaded_modules.add(module_file_abs)
                        continue
                    else:
                        # Module alias: use mod alias local; -> import all under namespace
                        # Record mapping for rewriting alias.item -> item
                        alias_maps[alias] = set(available.keys()) | set(internal_types.keys())
                        # Still import items flat so they are available (both qualified and unqualified work)
                        if is_priv_import:
                            for mod_item in module_ast.items: new_items.append(mod_item)
                        else:
                            for name, mod_item in available.items(): new_items.append(mod_item)
                            for name, mod_item in internal_types.items(): new_items.append(mod_item)
                            for mod_item in module_ast.items:
                                if isinstance(mod_item, NativeImport): new_items.append(mod_item)
                                if isinstance(mod_item, ConditionalDef): new_items.append(mod_item)
                        loaded_modules.add(module_file_abs)
                        continue
                if is_priv_import:
                    for mod_item in module_ast.items: new_items.append(mod_item)
                else:
                    if item.imported_items is not None:
                        for name in item.imported_items:
                            if name not in available: raise LeashError(f"Imported item '{name}' not found in module", node=item)
                        # If specific items requested, only add those (preserve old behavior of adding all? but respect request)
                        # For compatibility we still add all, but to be precise we add only requested plus internal types.
                        # To keep previous behavior (adds all), we add all; requested check ensures existence.
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
    expanded = _expand_items(program.items, base_path)
    if alias_maps:
        _rewrite_alias_accesses(expanded, alias_maps)
    return expanded

def _rewrite_alias_accesses(program, alias_maps):
    """Rewrite qualified accesses like alias.item -> item for module alias imports."""
    import copy
    from .ast_nodes import Identifier, MemberAccess, MethodCall, Call, GenericCall, EnumMemberAccess
    def rewrite_type_str(t):
        if not isinstance(t, str):
            return t
        # Handle alias prefix in type strings: "alias.Type" or "alias::Type" -> "Type"
        for alias, names in alias_maps.items():
            for prefix in (alias + ".", alias + "::"):
                if t.startswith(prefix):
                    rest = t[len(prefix):]
                    # rest may be "Type<..." - extract base
                    base = rest.split("<")[0].split("[")[0].strip()
                    if base in names:
                        t = rest
                        return t
            # Also handle generic wrapper like "vec<alias.Type>"
            for alias2 in alias_maps:
                if alias2 + "." in t or alias2 + "::" in t:
                    t = t.replace(alias2 + ".", "").replace(alias2 + "::", "")
        return t
    def rewrite_node(node):
        if node is None or isinstance(node, (str, int, float, bool)):
            return node
        if isinstance(node, (list, tuple)):
            return type(node)(rewrite_node(x) for x in node) if isinstance(node, tuple) else [rewrite_node(x) for x in node]
        if not hasattr(node, '__dict__'):
            return node
        # Rewrite MemberAccess alias.item -> Identifier(item)
        if isinstance(node, MemberAccess):
            if isinstance(node.expr, Identifier) and node.expr.name in alias_maps and node.member in alias_maps[node.expr.name]:
                return Identifier(node.member)
        if isinstance(node, MethodCall):
            if isinstance(node.expr, Identifier) and node.expr.name in alias_maps and node.method in alias_maps[node.expr.name]:
                # Convert to plain Call
                return Call(node.method, node.args)
        if isinstance(node, EnumMemberAccess):
            if node.enum_name in alias_maps and node.member_name in alias_maps[node.enum_name]:
                # Enum access via alias namespace: alias::MEMBER -> keep but enum_name should be resolved
                # EnumMemberAccess expects enum_name; if alias maps, we keep as is but type may be alias prefix?
                # Actually EnumMemberAccess(enum_name, member) - if enum_name is alias, it should become actual enum type?
                # For now, leave as is; type checker will resolve via alias mapping of types.
                pass
        # Rewrite type strings on nodes that carry them
        for attr in ('var_type', 'target_type', 'return_type', 'type_params'):
            if hasattr(node, attr):
                val = getattr(node, attr)
                if isinstance(val, str):
                    setattr(node, attr, rewrite_type_str(val))
                elif isinstance(val, list) and val and isinstance(val[0], str):
                    setattr(node, attr, [rewrite_type_str(v) if isinstance(v, str) else v for v in val])
        # Handle Function/OpDef/ClassField type strings inside args/fields tuples
        from .ast_nodes import Function as _Func, OpDef as _OpDef, ClassDef as _ClassDef, ClassField as _ClassField
        if isinstance(node, (_Func, _OpDef)) and hasattr(node, 'args') and isinstance(node.args, list):
            new_args = []
            for aname, atype, adefault in node.args:
                if isinstance(atype, str):
                    atype = rewrite_type_str(atype)
                new_args.append((aname, atype, rewrite_node(adefault) if adefault else adefault))
            node.args = new_args
        if hasattr(node, 'fields') and isinstance(node.fields, list):
            # For StructDef, ClassDef.fields, etc. may contain tuples (name, type, default)
            # Only rewrite if first element looks like a field tuple
            new_fields = []
            changed = False
            for f in node.fields:
                if isinstance(f, tuple) and len(f) == 3 and isinstance(f[1], str):
                    n, t, d = f
                    t = rewrite_type_str(t)
                    new_fields.append((n, t, rewrite_node(d) if d else d))
                    changed = True
                elif hasattr(f, 'var_type') and isinstance(getattr(f, 'var_type', None), str):
                    # ClassField object
                    f.var_type = rewrite_type_str(f.var_type)
                    if getattr(f, 'value', None):
                        f.value = rewrite_node(f.value)
                    new_fields.append(f)
                    changed = True
                else:
                    new_fields.append(f)
            if changed:
                node.fields = new_fields
        # Recursively walk children
        for attr_name in list(vars(node).keys()):
            if attr_name.startswith('_'):
                continue
            val = getattr(node, attr_name)
            if isinstance(val, list):
                setattr(node, attr_name, [rewrite_node(x) for x in val])
            elif isinstance(val, tuple):
                setattr(node, attr_name, tuple(rewrite_node(x) for x in val))
            elif hasattr(val, '__dict__') and not isinstance(val, (str, int, float, bool)):
                setattr(node, attr_name, rewrite_node(val))
        return node
    for it in program.items:
        rewrite_node(it)

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
        if isinstance(node, Identifier) and node.name in macros:
            m = macros[node.name]
            if len(m.params) == 0:
                if len(m.body) == 1:
                    s = m.body[0]
                    if isinstance(s, ExpressionStatement): return copy.deepcopy(s.expr)
                    elif isinstance(s, ReturnStatement): return copy.deepcopy(s.value)
                    return copy.deepcopy(s)
                return copy.deepcopy(m.body)
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
        # Handle AST nodes whose attributes contain tuples (e.g. IfStatement.also_blocks)
        from .ast_nodes import IfStatement
        if isinstance(node, IfStatement):
            node.condition = expand_expr(node.condition)
            node.then_block = expand_stmts(node.then_block)
            node.also_blocks = [(expand_expr(c), expand_stmts(b), inv) for c, b, inv in node.also_blocks]
            node.else_block = expand_stmts(node.else_block) if node.else_block else None
            return node
        for attr in list(vars(node)):
            if attr.startswith('_'): continue
            val = getattr(node, attr)
            if val is None: continue
            if isinstance(val, list): setattr(node, attr, [expand_expr(i) for i in val])
            elif isinstance(val, tuple): setattr(node, attr, tuple(expand_expr(i) for i in val))
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
            item.also_blocks = [(expand_expr(c), expand_stmts(b), inv) for c, b, inv in item.also_blocks]
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

def check_file(input_file, verbose=False, extra_import_dirs=None, opt_verbose=False):
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
        tc = TypeChecker(check_mode=True)
        warnings = tc.check(ast)
    except LeashError as e:
        errors.append(e)
    except Exception as e:
        if verbose: import traceback; print(f"error: Internal: {e}", file=sys.stderr); traceback.print_exc()
    ll_errors = LowLevelChecker().check(ast)
    errors.extend(ll_errors)
    errors.extend(tc.errors)
    if verbose:
        for err in errors:
            _print_error(err, input_file, code)
    return errors, warnings

# Cache target machines to avoid repeated LLVM Target creation (~16ms each)
_target_machine_cache = {}

def _get_target_machine(triple, reloc, opt_level):
    key = (triple, reloc, opt_level)
    if key not in _target_machine_cache:
        t = llvm.Target.from_triple(triple)
        _target_machine_cache[key] = t.create_target_machine(
            reloc=reloc, opt=opt_level
        )
    return _target_machine_cache[key]

def compile_file(input_file, output_name=None, output_type="executable", is_run_mode=False, target_name=None, check_mode=False, warnings_as_errors=False, extra_libs=None, opt_level=None, extra_import_dirs=None, opt_verbose=False, no_gc=False, autofree=False, static=False):
    with open(input_file, "r") as f: code = f.read()
    target_config = get_target(target_name) if target_name else get_native_target()
    try:
        lexer = Lexer(code); tokens = lexer.tokenize(); parser = Parser(tokens, input_file); ast = parser.parse()
        ast = resolve_imports(ast, os.path.dirname(os.path.abspath(input_file)) or ".", extra_import_dirs=extra_import_dirs)
        ast = resolve_conditionals(ast, target_config); ast = expand_macros(ast)
        tc = TypeChecker(check_mode=check_mode)
        warnings = tc.check(ast)
        for w in warnings: _print_warning(w, warnings_as_errors, code=code, input_file=input_file)
        if warnings_as_errors and warnings: sys.exit(1)
        if tc.errors:
            for err in tc.errors: _print_error(err, input_file, code)
            sys.exit(1)
        ll_errors = LowLevelChecker().check(ast)
        if ll_errors:
            for err in ll_errors: _print_error(err, input_file, code)
            sys.exit(1)
        parsed_opt, size_opt = parse_opt_level(opt_level)
        ast = optimize_ast(ast, opt_level=parsed_opt, opt_verbose=opt_verbose)
        llvm.initialize_native_target(); llvm.initialize_native_asmprinter()
        codegen = CodeGen(target_platform=target_config.name, no_gc=no_gc, autofree=autofree); codegen.generate_code(ast, input_file)
        hoist_allocas(codegen.module)
        mod = llvm.parse_assembly(codegen.get_ir()); mod.verify()
    except LeashError as e: _print_error(e, input_file, code); sys.exit(1)
    except Exception as e: import traceback; print(f"error: Internal: {e}"); traceback.print_exc(); sys.exit(1)
    triple = target_config.llvm_triple
    if target_config.name == "win64" and os.name == "nt":
        triple = triple + "-elf"
    # Static (non-PIC) reloc model: safe because Linux targets always link -no-pie
    use_static_reloc = (target_config.name == "win64" and os.name == "nt") or target_config.name in ("linux64", "linux32")
    tm = _get_target_machine(triple, "static" if use_static_reloc else "default",
                            min(parsed_opt, 3))
    mod.triple = triple

    if output_name is None: output_name = input_file[:-4] if input_file.endswith(".lsh") else "out"
    optimize_module(mod, opt_level=parsed_opt, size_opt=size_opt, target_machine=tm, opt_verbose=opt_verbose)
    obj_name = output_name + ".o"
    with open(obj_name, "wb") as f: f.write(tm.emit_object(mod))
    return _link_native(obj_name, output_name, target_config, is_run_mode, output_type, codegen, extra_libs, no_gc=no_gc, autofree=autofree, size_opt=bool(size_opt), static=static)

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
    "pthread_create": "pthread", "pthread_join": "pthread",
    "pthread_mutex_init": "pthread", "pthread_mutex_lock": "pthread",
    "pthread_cond_wait": "pthread",
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


def _flatten_cc(cc):
    """Return a compiler invocation as a flat command list (handles WSL cmds)."""
    return list(cc) if isinstance(cc, (list, tuple)) else [cc]


# Cache compiled runtime stubs to avoid recompiling gc.c + stubs every link
_runtime_stub_cache = {}

def _get_runtime_stubs(cc, target_config, no_gc=False, autofree=False, size_opt=False):
    """Return list of compiled .o paths for runtime C files, cached by (cc, target, mtime).

    Runtime files are compiled with -O2 (or -Os in size mode) plus
    -ffunction-sections/-fdata-sections so the linker's --gc-sections can
    drop unused functions (matrix ops, thread pools, etc.) from the binary.

    The OS-specific stub file is chosen by the *target* platform (not the host),
    so cross-compiling win64 from Linux uses the Windows stubs and compiling
    linux64 from Windows (via WSL) uses the POSIX stubs.
    """
    leash_dir = os.path.dirname(os.path.abspath(__file__))
    stub_files = []
    base_cflags = ["-Os" if size_opt else "-O2", "-ffunction-sections", "-fdata-sections"]
    # Always compile gc.c; in no-gc/autofree mode compile with -DNO_GC (stub mode)
    gc_cflags = list(base_cflags)
    if no_gc or autofree:
        gc_cflags.append("-DNO_GC")
    stub_files.append(("gc.c", gc_cflags))
    if target_config.name == "win64":
        stub_files.append(("windows_stubs.c", base_cflags))
    else:
        stub_files.append(("cross_compile_stubs.c", base_cflags))

    cc_key = tuple(cc) if isinstance(cc, (list, tuple)) else cc
    cc_cmd = _flatten_cc(cc)
    import hashlib
    cc_tag = hashlib.sha1(repr(cc_key).encode("utf-8")).hexdigest()[:10]
    result = []
    for sfile, cflags in stub_files:
        spath = os.path.join(leash_dir, sfile)
        if not os.path.exists(spath):
            continue
        mtime = os.path.getmtime(spath)
        key = (cc_key, target_config.name, sfile, tuple(cflags), mtime)
        if key in _runtime_stub_cache:
            result.append(_runtime_stub_cache[key])
            continue
        cached_o = os.path.join(
            leash_dir, f".cached_{target_config.name}_{cc_tag}_{sfile}_{'_'.join(cflags)}.o")
        if os.path.exists(cached_o):
            # If the cached .o is older than the source, recompile
            if os.path.getmtime(cached_o) >= mtime:
                _runtime_stub_cache[key] = cached_o
                result.append(cached_o)
                continue
        cmd = cc_cmd + ["-c", spath, "-o", cached_o] + cflags
        res = subprocess.run(cmd,
                             stderr=subprocess.PIPE, stdout=subprocess.PIPE)
        if res.returncode != 0:
            err = res.stderr.decode("utf-8", errors="replace").strip()
            print(f"warning: failed to compile {sfile}: {err}", file=sys.stderr)
            continue
        _runtime_stub_cache[key] = cached_o
        result.append(cached_o)
    return result


def _win_to_wsl_path(path):
    """Convert a Windows path to the corresponding WSL path (e.g. C:\\x -> /mnt/c/x)."""
    path_abs = os.path.abspath(path)
    try:
        res = subprocess.run(
            ["wsl", "wslpath", "-a", path_abs],
            capture_output=True, text=True, timeout=30)
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    p = path_abs.replace("\\", "/")
    if len(p) >= 2 and p[1] == ":":
        drive = p[0].lower()
        return "/mnt/" + drive + p[2:]
    return p


def _link_native(obj_name, output_name, target_config, is_run_mode, output_type, codegen, extra_libs=None, no_gc=False, autofree=False, size_opt=False, static=False):
    nlib_args = [l[0] for l in codegen.native_libs]
    if extra_libs: nlib_args.extend([f"-l{l}" for l in extra_libs])
    cc = os.environ.get("CC")
    if not cc:
        if target_config.uses_wsl():
            # Linux target from a Windows host: link through WSL.
            if not wsl_available():
                print("error: Cross-compiling to Linux on Windows requires WSL with a C compiler.", file=sys.stderr)
                print("  Install WSL (wsl --install) and run: sudo apt install gcc", file=sys.stderr)
                sys.exit(1)
            cc = ["wsl", "gcc"]
            if not is_run_mode:
                print(f"Using WSL cross-compiler for '{target_config.name}' target", file=sys.stderr)
        else:
            cc = target_config.detect_cross_linker()
            if cc and not is_run_mode:
                print(f"Using cross-compiler: {cc}", file=sys.stderr)
    if not cc:
        if os.name == "nt":
            cc = "gcc" if shutil.which("gcc") else "clang"
        else:
            cc = shutil.which("gcc") or shutil.which("clang")
            if not cc:
                print("error: No C compiler found (install gcc or clang, or set CC env var)", file=sys.stderr)
                sys.exit(1)

    static_flags = []
    if static:
        if target_config.name not in ("linux64", "linux32"):
            print("error: --static is only supported for linux64/linux32 targets", file=sys.stderr)
            sys.exit(1)
        if not os.environ.get("CC"):
            if isinstance(cc, list) and cc[0] == "wsl":
                # Probe for a musl toolchain inside the WSL distro.
                def _wsl_which(name):
                    r = subprocess.run(cc + ["which", name], capture_output=True, text=True)
                    return r.returncode == 0 and bool(r.stdout.strip())
                if _wsl_which("musl-gcc"):
                    cc = ["wsl", "musl-gcc"]
                elif _wsl_which("musl-clang"):
                    cc = ["wsl", "musl-clang"]
                elif _wsl_which("clang"):
                    cc = ["wsl", "clang"]
                    arch = target_config.llvm_triple.split("-")[0]
                    static_flags.append(f"--target={arch}-linux-musl")
                else:
                    print("error: --static requires musl-gcc, musl-clang, or clang installed inside WSL", file=sys.stderr)
                    sys.exit(1)
            else:
                musl_cc = shutil.which("musl-gcc") or shutil.which("musl-clang")
                if musl_cc:
                    cc = musl_cc
                elif shutil.which("clang"):
                    cc = "clang"
                    arch = target_config.llvm_triple.split("-")[0]
                    static_flags.append(f"--target={arch}-linux-musl")
                else:
                    print("error: --static requires musl-gcc, musl-clang, or clang (with musl target support)", file=sys.stderr)
                    sys.exit(1)
        static_flags.append("-static")

    size_flags = list(target_config.size_flags)
    if size_opt:
        size_flags.extend(target_config.size_only_flags)

    stubs = _get_runtime_stubs(cc, target_config, no_gc=no_gc, autofree=autofree, size_opt=size_opt)

    out = None
    retried = False
    while True:
        try:
            if output_type == "executable":
                out = target_config.get_output_name(output_name)
                result = subprocess.run(_flatten_cc(cc) + [obj_name] + stubs + ["-o", out] + target_config.linker_flags + size_flags + static_flags + nlib_args, stderr=subprocess.PIPE)
                if result.returncode != 0:
                    raise subprocess.CalledProcessError(result.returncode, result.args, stderr=result.stderr)
            elif output_type == "dynamic":
                out = output_name + (".dll" if os.name == "nt" else ".so")
                subprocess.run(_flatten_cc(cc) + ["-shared", obj_name, "-o", out, "-fPIC"] + nlib_args, stderr=subprocess.PIPE, check=True)
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

    for f in [obj_name]:
        try:
            if os.path.exists(f):
                os.remove(f)
        except OSError:
            pass
    if not is_run_mode: print(f"Successfully compiled to '{out}'")
    return out

def dump_file(input_file, output_name=None, target_name=None, check_mode=False, warnings_as_errors=False, extra_libs=None, opt_level=None, extra_import_dirs=None, opt_verbose=False, no_gc=False, autofree=False):
    with open(input_file, "r") as f: code = f.read()
    target_config = get_target(target_name) if target_name else get_native_target()
    try:
        lexer = Lexer(code); tokens = lexer.tokenize(); parser = Parser(tokens, input_file); ast = parser.parse()
        ast = resolve_imports(ast, os.path.dirname(os.path.abspath(input_file)) or ".", extra_import_dirs=extra_import_dirs)
        ast = resolve_conditionals(ast, target_config); ast = expand_macros(ast)
        tc = TypeChecker(check_mode=check_mode)
        warnings = tc.check(ast)
        for w in warnings: _print_warning(w, warnings_as_errors, code=code, input_file=input_file)
        if warnings_as_errors and warnings: sys.exit(1)
        if tc.errors:
            for err in tc.errors: _print_error(err, input_file, code)
            sys.exit(1)
        ll_errors = LowLevelChecker().check(ast)
        if ll_errors:
            for err in ll_errors: _print_error(err, input_file, code)
            sys.exit(1)
        popt, sopt = parse_opt_level(opt_level)
        ast = optimize_ast(ast, opt_level=popt, opt_verbose=opt_verbose)
        llvm.initialize_all_targets()
        codegen = CodeGen(target_platform=target_config.name, no_gc=no_gc, autofree=autofree); codegen.generate_code(ast, input_file)
        hoist_allocas(codegen.module)
        mod = llvm.parse_assembly(codegen.get_ir()); mod.verify()
    except LeashError as e: _print_error(e, input_file, code); sys.exit(1)
    except Exception as e: import traceback; print(f"error: Internal: {e}"); traceback.print_exc(); sys.exit(1)
    popt, sopt = parse_opt_level(opt_level)
    optimize_module(mod, opt_level=popt, size_opt=sopt, opt_verbose=opt_verbose)
    if output_name is None: output_name = input_file[:-4] if input_file.endswith(".lsh") else "out"
    if not output_name.endswith(".ll"): output_name += ".ll"
    with open(output_name, "w") as f: f.write(str(mod))
    print(f"Dumped LLVM IR to '{output_name}'"); return output_name

def run_file(input_file, args=[], target_name=None, check_mode=False, warnings_as_errors=False, extra_libs=None, opt_level=None, extra_import_dirs=None, opt_verbose=False, no_gc=False, autofree=False, static=False):
    import platform, time, uuid, stat, signal
    tcfg = get_target(target_name) if target_name else get_native_target()
    tmp = f".__temp_run_leash_exe_{uuid.uuid4().hex}"
    out = compile_file(input_file, output_name=tmp, is_run_mode=True, target_name=target_name, check_mode=check_mode, warnings_as_errors=warnings_as_errors, extra_libs=extra_libs, opt_level=opt_level, extra_import_dirs=extra_import_dirs, opt_verbose=opt_verbose, no_gc=no_gc, autofree=autofree, static=static)
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
    elif tcfg.name in ("linux64", "linux32") and sys_name == "windows":
        # Linux target on a Windows host: run through WSL.
        if not wsl_available():
            print("error: Cannot run Linux binary on Windows without WSL"); sys.exit(1)
        out_wsl = _win_to_wsl_path(out_abs)
        try:
            subprocess.run(["wsl", "chmod", "+x", out_wsl], capture_output=True, timeout=30)
        except OSError:
            pass
        cmd = ["wsl", out_wsl] + args
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
    autofree = config.get("autofree", "false").lower() in ("true", "yes", "1")
    static = config.get("static", "false").lower() in ("true", "yes", "1")
    return all_extra_dirs, extra_libs, opt_level, autofree, static


def build_project(extra_import_dirs=None):
    """Build the project using config.lshc in the current directory."""
    project_dir = os.getcwd()
    config, project_dir, main_path = read_project_config(project_dir)
    all_extra_dirs, extra_libs, opt_level, autofree, static = resolve_project_deps(config, project_dir, extra_import_dirs)
    out_basename = config.get("out_name") or os.path.basename(project_dir)
    out_name = os.path.join(project_dir, "out", out_basename)
    compile_file(main_path, output_name=out_name, extra_import_dirs=all_extra_dirs, extra_libs=extra_libs, opt_level=opt_level, autofree=autofree, static=static)


def run_project(prog_args=None, extra_import_dirs=None):
    """Build and run the project using config.lshc in the current directory."""
    import platform, time, uuid, stat
    project_dir = os.getcwd()
    config, project_dir, main_path = read_project_config(project_dir)
    all_extra_dirs, extra_libs, opt_level, autofree, static = resolve_project_deps(config, project_dir, extra_import_dirs)
    tmp = f".__temp_run_leash_exe_{uuid.uuid4().hex}"
    out = compile_file(main_path, output_name=tmp, is_run_mode=True, extra_import_dirs=all_extra_dirs, extra_libs=extra_libs, opt_level=opt_level, autofree=autofree, static=static)
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
    print("Current version: 0.23.4 Beta\n")
    
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


VERSION_STRING = "v0.23.4 Beta"

MAIN_HELP = f"""Leash {VERSION_STRING} - LLVM-powered compiled programming language

Usage:
  leash <command> [arguments] [options]

Commands:
  compile <file.lsh>    Compile to an executable binary
  run [<file.lsh>]      Compile and immediately execute (file or project)
  dump <file.lsh>       Dump generated LLVM IR instead of linking
  check <file.lsh>      Type-check only; report errors and warnings
  init [dir]            Scaffold a new Leash project (default: .)
  build                 Compile the project described by config.lshc
  install <path>...     Install libraries into ~/.leash/libs/
  update                Check for updates and pull the latest source
  help [command]        Show help, optionally for one command

Global Options:
  --target <target>                Cross-compile target: linux64, linux32, win64, macos, macos-arm
  --check                          Type-check only, do not produce output
  --warnings-as-errors             Treat warnings as errors
  --opt <level> / -O<level>        Optimization level: 0, 1, 2, 3, 4, s (size), z (aggressive size)
  -l<lib>                          Link a native library (repeatable), e.g. -lraylib
  --other-imports / -oi <folder>   Extra module search directory (repeatable)
  --no-garbage-collector / -ngc    Disable garbage collector (use C malloc/free everywhere)
  --autofree / -af                 Smart auto-free mode (tracks allocations, frees on scope exit; no GC needed)
  --static / -static               Fully-static musl-linked binary (linux64/linux32 only)
  --verbose / -vb                  Highly detailed error and warning explanations
  --optimization-verbosity / -ov   Show optimization pass details
  --version / -v                   Print version information
  --help / -h                      Show this help, or 'leash help <command>' for details

Run 'leash <command> --help' for command-specific options.
Note: 'runp' is deprecated -- use 'leash run' without a file to run the current project."""

_SHARED_COMPILE_OPTIONS = """  --target <target>                Cross-compile target: linux64, linux32, win64, macos, macos-arm
  --check                          Type-check only, do not produce output
  --warnings-as-errors             Treat warnings as errors
  --opt <level> / -O<level>        Optimization level: 0, 1, 2, 3, 4, s (size), z (aggressive size)
  -l<lib>                          Link a native library (repeatable), e.g. -lraylib
  --other-imports / -oi <folder>   Extra module search directory (repeatable)
  --no-garbage-collector / -ngc    Disable garbage collector (use C malloc/free everywhere)
  --autofree / -af                 Smart auto-free mode (tracks allocations, frees on scope exit; no GC needed)
  --static / -static               Fully-static musl-linked binary (linux64/linux32 only)
  --verbose / -vb                  Highly detailed error and warning explanations
  --optimization-verbosity / -ov   Show optimization pass details"""

COMMAND_HELP = {
    "compile": f"""Compile a Leash source file into an executable binary.

Usage:
  leash compile <file.lsh> [to <name>] [to-dynamic | to-static] [options]

Output options:
  to <name>                        Write output to <name>
  to-dynamic                       Produce a dynamically-linked binary (default)
  to-static                        Produce a statically-linked binary

Options:
{_SHARED_COMPILE_OPTIONS}""",
    "run": f"""Compile and immediately execute a Leash program (file or project).

Usage:
  leash run [<file.lsh>] [options] [-- program-args]

  With <file.lsh>: compile and run that file (file mode).
    Any arguments after '--' are passed to the program.

  Without <file.lsh>: run the project described by config.lshc
    in the current directory (project mode, same as deprecated 'runp').
    Project options are read from config.lshc.

File mode options:
{_SHARED_COMPILE_OPTIONS}

Project mode options:
  --other-imports / -oi <folder>   Extra module search directory (repeatable)

Arguments after '--' are passed through to the running program.

Examples:
  leash run program.lsh
  leash run program.lsh -- arg1 arg2
  leash run                        # run project (config.lshc)
  leash run -- arg1 arg2           # run project with args
  leash run --other-imports mylibs/ -- arg1""",
    "dump": f"""Compile a Leash source file and dump the generated LLVM IR
instead of producing a linked binary.

Usage:
  leash dump <file.lsh> [to <name>] [options]

Options:
{_SHARED_COMPILE_OPTIONS}""",
    "check": """Type-check a Leash source file without compiling. Reports all
errors and warnings found in the file.

Usage:
  leash check <file.lsh> [options]

Options:
  --other-imports / -oi <folder>   Extra module search directory (repeatable)
  --verbose / -vb                  Highly detailed error and warning explanations""",
    "init": """Scaffold a new Leash project with a standard directory layout:
src/main.lsh entry point, imports/ directory, out/ directory and a
config.lshc project file.

Usage:
  leash init [project_dir]     (default: current directory)""",
    "build": """Compile the project described by config.lshc in the current
directory. Run 'leash init' first to create a project.

Usage:
  leash build [options]

Options:
  --other-imports / -oi <folder>   Extra module search directory (repeatable)""",
    "runp": """Build the project from config.lshc and run its output binary.

Usage:
  leash runp [options] [-- program-args]

Deprecated: use 'leash run' without a file argument instead.
This alias will be removed in a future version.

Options:
  --other-imports / -oi <folder>   Extra module search directory (repeatable)

Arguments after '--' are passed through to the running program.""",
    "install": """Install one or more libraries (files or directories of .lsh files)
into ~/.leash/libs/ so they can be imported from any project.

Usage:
  leash install <path> [path...]

Example:
  leash install installthis/""",
    "update": """Check GitHub for a newer Leash release and pull the latest
source into the current repository.

Usage:
  leash update""",
}

KNOWN_COMMANDS = tuple(COMMAND_HELP.keys())


def _print_main_help():
    print(MAIN_HELP)


def main():
    global VERBOSE_MODE, OPT_VERBOSE_MODE
    VERBOSE_MODE = False
    OPT_VERBOSE_MODE = False
    
    # Extract --verbose / -vb globally and remove from sys.argv
    for arg in list(sys.argv):
        if arg in ("--verbose", "-vb"):
            VERBOSE_MODE = True
            try:
                sys.argv.remove(arg)
            except ValueError:
                pass
    
    # Extract --optimization-verbosity / -ov globally and remove from sys.argv
    for arg in list(sys.argv):
        if arg in ("--optimization-verbosity", "-ov"):
            OPT_VERBOSE_MODE = True
            try:
                sys.argv.remove(arg)
            except ValueError:
                pass

    if len(sys.argv) < 2:
        _print_main_help()
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd in ("--help", "-h", "help"):
        if cmd == "help" and len(sys.argv) > 2:
            topic = sys.argv[2]
            if topic in COMMAND_HELP:
                print(COMMAND_HELP[topic])
                sys.exit(0)
            print(f"Unknown command: '{topic}'. Run 'leash --help' for a list of commands.")
            sys.exit(1)
        _print_main_help()
        sys.exit(0)
    if cmd in ("--version", "-v"):
        print(f"Leash {VERSION_STRING}\nBuilt on LLVM with custom GC (use --autofree for GC-free auto-free mode)"); sys.exit(0)
    # Per-command help: intercept '-h'/'--help'/'help' as the first argument
    # after the command name, before any command tries to interpret it.
    if cmd in KNOWN_COMMANDS and len(sys.argv) > 2 and sys.argv[2] in ("-h", "--help", "help"):
        print(COMMAND_HELP[cmd])
        sys.exit(0)
    if cmd == "check":
        if len(sys.argv) < 3:
            print(COMMAND_HELP["check"])
            sys.exit(1)
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
        print("warning: 'runp' is deprecated; use 'leash run' without a file instead", file=sys.stderr)
        extra_import_dirs = []
        prog_args = []
        if "--" in sys.argv:
            sep = sys.argv.index("--")
            opts = sys.argv[2:sep]
            prog_args = sys.argv[sep + 1:]
            i = 0
            while i < len(opts):
                if opts[i] in ("--other-imports", "-oi") and i + 1 < len(opts):
                    extra_import_dirs.append(os.path.abspath(opts[i + 1]))
                    i += 2
                else:
                    i += 1
        else:
            i = 2
            while i < len(sys.argv):
                if sys.argv[i] in ("--other-imports", "-oi") and i + 1 < len(sys.argv):
                    extra_import_dirs.append(os.path.abspath(sys.argv[i + 1]))
                    i += 2
                else:
                    i += 1
        run_project(prog_args, extra_import_dirs)
        sys.exit(0)
    if cmd == "run":
        # Merged: 'leash run [<file.lsh>]' -- file mode if a file is given, otherwise project mode
        if len(sys.argv) < 3:
            # No file -> project mode (like old runp)
            run_project([], [])
            sys.exit(0)
        first = sys.argv[2]
        # 'run --help' already handled above, but keep for safety
        if first in ("-h", "--help", "help"):
            print(COMMAND_HELP["run"])
            sys.exit(0)
        is_project = False
        if first == "--":
            is_project = True
        elif first.startswith("-"):
            is_project = True
        elif first.endswith(".lsh") or os.path.isfile(first):
            is_project = False
        else:
            # Heuristic: if config.lshc exists in cwd, treat bare non-file arg as project invocation
            if os.path.exists(os.path.join(os.getcwd(), "config.lshc")):
                is_project = True
            else:
                is_project = False
        if is_project:
            extra_import_dirs = []
            prog_args = []
            try:
                sep_idx = sys.argv.index("--", 2)
                opts_part = sys.argv[2:sep_idx]
                prog_args = sys.argv[sep_idx + 1:]
            except ValueError:
                opts_part = sys.argv[2:]
                prog_args = []
            i = 0
            while i < len(opts_part):
                if opts_part[i] in ("--other-imports", "-oi") and i + 1 < len(opts_part):
                    extra_import_dirs.append(os.path.abspath(opts_part[i + 1]))
                    i += 2
                else:
                    i += 1
            run_project(prog_args, extra_import_dirs)
            sys.exit(0)
        # File mode
        infile = sys.argv[2]
        if "--" in sys.argv:
            sep_idx = sys.argv.index("--", 3)
            compile_argv = sys.argv[3:sep_idx]
            prog_args = sys.argv[sep_idx + 1:]
        else:
            compile_argv = sys.argv[3:]
            prog_args = []
        target, outname, outtype, check, warnerr, elibs, opt = None, None, "executable", False, False, [], "2"
        no_gc = False
        autofree = False
        static = False
        extra_import_dirs = []
        i = 0
        while i < len(compile_argv):
            arg = compile_argv[i]
            if arg == "--target" and i + 1 < len(compile_argv):
                target = compile_argv[i + 1]
                i += 2
            elif arg == "--check":
                check = True
                i += 1
            elif arg == "--warnings-as-errors":
                warnerr = True
                i += 1
            elif arg in ("--static", "-static"):
                static = True
                i += 1
            elif arg.startswith("-O") and len(arg) > 2:
                opt = arg[2:]
                i += 1
            elif arg.startswith("--opt="):
                opt = arg[6:]
                i += 1
            elif (arg == "--opt" or arg == "-O") and i + 1 < len(compile_argv):
                opt = compile_argv[i + 1]
                i += 2
            elif arg.startswith("-l"):
                elibs.append(arg[2:])
                i += 1
            elif arg == "to" and i + 1 < len(compile_argv):
                outname = compile_argv[i + 1]
                i += 2
            elif arg == "to-dynamic":
                outtype = "dynamic"
                i += 1
            elif arg == "to-static":
                outtype = "static"
                i += 1
            elif arg in ("--other-imports", "-oi") and i + 1 < len(compile_argv):
                extra_import_dirs.append(os.path.abspath(compile_argv[i + 1]))
                i += 2
            elif arg in ("--no-garbage-collector", "-ngc"):
                no_gc = True
                i += 1
            elif arg in ("--autofree", "-af"):
                autofree = True
                i += 1
            else:
                i += 1
        run_file(infile, prog_args, target, check, warnerr, elibs, opt, extra_import_dirs=extra_import_dirs, opt_verbose=OPT_VERBOSE_MODE, no_gc=no_gc, autofree=autofree, static=static)
        sys.exit(0)
    if cmd in ("compile", "dump"):
        if len(sys.argv) < 3:
            print(COMMAND_HELP[cmd])
            sys.exit(1)
        infile = sys.argv[2]
        target, outname, outtype, check, warnerr, elibs, opt = None, None, "executable", False, False, [], "2"
        no_gc = False
        autofree = False
        static = False
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
            elif sys.argv[i] in ("--static", "-static"):
                static = True
                i += 1
            elif sys.argv[i].startswith("-O") and len(sys.argv[i]) > 2:
                opt = sys.argv[i][2:]  # -O4, -Os, -Oz
                i += 1
            elif sys.argv[i].startswith("--opt="):
                opt = sys.argv[i][6:]  # --opt=4
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
            elif sys.argv[i] in ("--no-garbage-collector", "-ngc"):
                no_gc = True
                i += 1
            elif sys.argv[i] in ("--autofree", "-af"):
                autofree = True
                i += 1
            else:
                i += 1
        if cmd == "dump":
            dump_file(infile, outname, target, check, warnerr, elibs, opt, extra_import_dirs=extra_import_dirs, opt_verbose=OPT_VERBOSE_MODE, no_gc=no_gc, autofree=autofree)
        else:
            compile_file(infile, outname, outtype, False, target, check, warnerr, elibs, opt, extra_import_dirs=extra_import_dirs, opt_verbose=OPT_VERBOSE_MODE, no_gc=no_gc, autofree=autofree, static=static)
    elif cmd == "update":
        update_leash()
        sys.exit(0)
    elif cmd == "install":
        if len(sys.argv) < 3:
            print(COMMAND_HELP["install"])
            sys.exit(1)
        install_libraries(sys.argv[2:])
    else:
        print(f"Unknown command: {cmd}")
        print("Run 'leash --help' for a list of commands.")
        sys.exit(1)

if __name__ == "__main__": main()
