"""JavaScript code generation backend for the Leash language."""

import os
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
    VariableDecl,
    Assignment,
    IfStatement,
    WhileStatement,
    ForStatement,
    DoWhileStatement,
    ForeachStructStatement,
    ForeachArrayStatement,
    ForeachStringStatement,
    ForeachVectorStatement,
    ReturnStatement,
    StopStatement,
    ContinueStatement,
    ExpressionStatement,
    ShowStatement,
    WorksOtherwiseStatement,
    BinaryOp,
    UnaryOp,
    Call,
    MethodCall,
    GenericCall,
    Identifier,
    MemberAccess,
    PointerMemberAccess,
    EnumMemberAccess,
    CastExpr,
    AsExpr,
    IndexAccess,
    StructInit,
    ArrayInit,
    NumberLiteral,
    FloatLiteral,
    StringLiteral,
    CharLiteral,
    BoolLiteral,
    NullLiteral,
    FilePathLiteral,
    BuiltinVarLiteral,
    ThisExpr,
    TypeConvExpr,
    TernaryOp,
    NativeImport,
    ClassField,
    ClassMethod,
    Block,
    ASTNode,
    SwitchStatement,
)


class JSCodeGen:
    """Generate JavaScript code from Leash AST."""

    def __init__(self, is_browser=False, target_name=None):
        self.indent_level = 0
        self.output = []
        self.struct_symtab = {}
        self.union_symtab = {}
        self.enum_symtab = {}
        self.class_symtab = {}
        self.type_aliases = {}
        self.var_symtab = {}
        self.func_symtab = {}
        self.global_vars = []
        self.in_function = False
        self.current_func_ret_type = None
        self.loop_stack = []
        self.native_libs = []
        self.has_native_imports = False
        self.uses_get = False
        self.uses_file = False
        self.uses_exec = False
        self.uses_cstr = False
        self.uses_rand = False
        self.uses_wait = False
        self.uses_timepass = False
        self.uses_choose = False
        self.needs_runtime = False
        self.async_methods = set()
        self.is_browser = is_browser
        self.target_name = target_name

    def generate_code(self, node):
        """Generate JavaScript code from the AST and return as string."""
        self._codegen(node)

        # Build runtime preamble if needed
        preamble = self._build_runtime_preamble()

        if preamble:
            all_output = preamble + [""] + self.output
        else:
            all_output = self.output

        code = "\n".join(all_output)

        # Post-process fixes
        import re

        # 1. Redirect base generic class static calls to instantiated versions
        from .typechecker import TypeChecker

        for mangled_name in TypeChecker.instantiated_class_nodes:
            for base_name in self.class_symtab:
                if (
                    mangled_name.startswith(base_name + "_")
                    and "_T" not in mangled_name
                ):
                    pattern = rf"(?<![.\w]){re.escape(base_name)}\."
                    replacement = f"{mangled_name}."
                    code = re.sub(pattern, replacement, code)
                    break

        # 2. Fix vector methods in generated code (pushb -> push, etc.)
        code = code.replace(".pushb(", ".push(")
        code = code.replace(".popb(", ".pop(")
        code = code.replace(".popf(", ".shift(")

        # 3. Fix vector .get(idx) and .set(idx, val) -> bracket notation
        # Only target specific patterns, exclude _leash_memory and other runtime functions
        def replace_get(m):
            var_name = m.group(1)
            if var_name in ("_leash_memory",):
                return m.group(0)  # Don't modify
            return f"{var_name}[{m.group(2)}]"

        code = re.sub(r"(\w+)\.get\((\w+)\)", replace_get, code)
        code = re.sub(r"(\w+)\.set\((\w+),\s*(\w+)\)", r"\1[\2] = \3", code)
        code = re.sub(r"(\w+)\.remove\((\w+)\)", r"\1.splice(\2, 1)", code)
        code = re.sub(r"(\w+)\.insert\((\w+),\s*(\w+)\)", r"\1.splice(\2, 0, \3)", code)
        code = re.sub(r"(\w+)\.clear\(\)", r"\1.length = 0", code)
        code = re.sub(r"(\w+)\.size\(\)", r"\1.length", code)

        return code

    def _build_runtime_preamble(self):
        """Build the runtime preamble based on what features are used."""
        self.needs_runtime = True

        if self.is_browser:
            runtime_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "runtime_html.js"
            )
        else:
            runtime_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "runtime_js.js"
            )
        try:
            with open(runtime_path, "r") as f:
                runtime_code = f.read()
        except FileNotFoundError:
            return ["// Runtime not found - system features may not work"]

        lines = [
            "// ============================================================",
            "// Leash Runtime - Auto-generated system simulation",
            "// ============================================================",
            "",
        ]

        # Only include the parts that are needed
        if self.uses_file:
            lines.append("// File I/O support enabled")
        if self.uses_exec:
            lines.append("// exec() support enabled")
        if self.uses_get:
            lines.append("// input/get() support enabled")
        if self.uses_cstr:
            lines.append("// cstr/lstr support enabled")
        if self.uses_rand:
            lines.append("// Random number support enabled")
        if self.uses_wait:
            lines.append("// wait() support enabled")
        if self.uses_timepass:
            lines.append("// timepass() support enabled")
        if self.uses_choose:
            lines.append("// choose() support enabled")

        lines.append("")
        lines.append(runtime_code)
        lines.append("")
        lines.append("// ============================================================")
        lines.append("// End of Leash Runtime")
        lines.append("// ============================================================")
        lines.append("")

        return lines

    def _indent(self):
        return "    " * self.indent_level

    def _emit(self, line=""):
        self.output.append(f"{self._indent()}{line}")

    def _emit_raw(self, line):
        self.output.append(line)

    def _codegen(self, node):
        method_name = f"_codegen_{type(node).__name__}"
        method = getattr(self, method_name, None)
        if method:
            return method(node)
        else:
            raise NotImplementedError(f"No JS codegen for {type(node).__name__}")

    def _expr(self, node):
        """Generate code for an expression and return the JS expression string."""
        method_name = f"_expr_{type(node).__name__}"
        method = getattr(self, method_name, None)
        if method:
            return method(node)
        else:
            raise NotImplementedError(f"No JS expr codegen for {type(node).__name__}")

    def _resolve_type(self, type_name):
        """Resolve type aliases."""
        if isinstance(type_name, str) and type_name.startswith("imut "):
            type_name = type_name[5:]
        visited = set()
        while type_name in self.type_aliases and type_name not in visited:
            visited.add(type_name)
            type_name = self.type_aliases[type_name]
        return type_name

    def _is_pointer_type(self, type_name):
        """Check if a type name represents a pointer type."""
        if isinstance(type_name, str):
            return (
                type_name.startswith("*")
                or type_name.startswith("&")
                or type_name.startswith("fnc(")
            )
        return False

    def _is_reference_type(self, type_name):
        """Check if a type name represents a reference type (&T)."""
        return isinstance(type_name, str) and type_name.startswith("&")

    def _type_to_runtime_type(self, type_name):
        """Map a Leash type to a runtime type string for JS arithmetic functions."""
        if type_name == "int":
            return "int32"
        elif type_name == "uint":
            return "uint32"
        elif type_name == "char":
            return "uint8"
        elif type_name == "bool":
            return "int"
        elif type_name == "float":
            return "float"
        elif type_name == "string":
            return None  # Strings don't use runtime arithmetic functions
        elif type_name.startswith("int<") or type_name.startswith("uint<"):
            signed = type_name.startswith("int<")
            bits = int(type_name.split("<")[1].rstrip(">"))
            if bits <= 8:
                return "int8" if signed else "uint8"
            elif bits <= 16:
                return "int16" if signed else "uint16"
            elif bits <= 32:
                return "int32" if signed else "uint32"
            elif bits <= 64:
                return "int64" if signed else "uint64"
            else:
                return f"int{bits}" if signed else f"uint{bits}"
        elif type_name.startswith("float<"):
            return "float"
        return "int32"

    def _get_pointee_type(self, type_name):
        """Get the type that a pointer points to."""
        if isinstance(type_name, str):
            if type_name.startswith("*"):
                return type_name[1:]
            elif type_name.startswith("&"):
                return type_name[1:]
        return type_name

    def _sizeof_type(self, type_name):
        """Get the size of a type in bytes (for pointer arithmetic)."""
        resolved = self._resolve_type(type_name)
        if resolved in ("int", "uint", "float", "char", "bool"):
            return 8  # JS uses 64-bit numbers
        elif resolved.startswith("int<") or resolved.startswith("uint<"):
            return 8
        elif resolved.startswith("vec<") or resolved.endswith("]"):
            return 8  # Array reference
        elif resolved in self.struct_symtab:
            return 8  # Struct reference
        return 8

    def _is_struct_type(self, type_name):
        return type_name in self.struct_symtab

    def _is_class_type(self, type_name):
        return type_name in self.class_symtab

    def _is_union_type(self, type_name):
        return type_name in self.union_symtab

    def _is_vec_type(self, type_name):
        return isinstance(type_name, str) and type_name.startswith("vec<")

    def _is_array_type(self, type_name):
        return (
            isinstance(type_name, str) and type_name.endswith("]") and "[" in type_name
        )

    def _is_string_type(self, type_name):
        return type_name == "string"

    def _is_numeric_type(self, type_name):
        return (
            type_name in ("int", "uint", "float", "char", "bool")
            or type_name.startswith("int<")
            or type_name.startswith("uint<")
            or type_name.startswith("float<")
        )

    def _get_vec_inner_type(self, type_name):
        if type_name.startswith("vec<") and type_name.endswith(">"):
            return type_name[4:-1]
        return "int"

    def _get_array_inner_type(self, type_name):
        if "[" in type_name and type_name.endswith("]"):
            return type_name.split("[")[0]
        return "int"

    def _js_type_check(self, type_name):
        """Generate a runtime type check expression for JS."""
        resolved = self._resolve_type(type_name)
        if resolved in ("int", "uint", "char", "bool"):
            return "typeof x === 'number'"
        elif resolved == "float" or resolved.startswith("float<"):
            return "typeof x === 'number'"
        elif resolved == "string":
            return "typeof x === 'string'"
        elif resolved == "bool":
            return "typeof x === 'boolean'"
        return ""

    def _default_value(self, type_name):
        """Get default value for a type in JS."""
        resolved = self._resolve_type(type_name)
        if resolved in ("int", "uint", "float", "char", "bool"):
            return "0"
        elif (
            resolved.startswith("int<")
            or resolved.startswith("uint<")
            or resolved.startswith("float<")
        ):
            return "0"
        elif resolved == "string":
            return '""'
        elif resolved == "bool":
            return "false"
        elif resolved.startswith("vec<"):
            return "[]"
        elif resolved.endswith("]") and "[" in resolved:
            return "[]"
        elif resolved in self.struct_symtab:
            return self._struct_default(resolved)
        elif resolved in self.class_symtab:
            return "null"
        elif resolved in self.union_symtab:
            return self._union_default(resolved)
        return "null"

    def _struct_default(self, struct_name):
        info = self.struct_symtab[struct_name]
        fields = ", ".join(
            f"{fname}: {self._default_value(ftype)}" for fname, ftype in info["fields"]
        )
        return f"{{ {fields} }}"

    def _union_default(self, union_name):
        info = self.union_symtab[union_name]
        if info["variants"]:
            first_name, first_type = info["variants"][0]
            return (
                f'{{ _tag: "{first_name}", _value: {self._default_value(first_type)} }}'
            )
        return "{ _tag: null, _value: null }"

    # ========== Program ==========

    def _scan_features(self, node):
        """Pre-scan AST to detect which runtime features are needed."""

        def scan_node(n):
            if isinstance(n, Call):
                if n.name == "get":
                    self.uses_get = True
                    self.needs_runtime = True
                elif n.name in ("rand", "randf", "seed"):
                    self.uses_rand = True
                    self.needs_runtime = True
                elif n.name == "wait":
                    self.uses_wait = True
                    self.needs_runtime = True
                elif n.name == "timepass":
                    self.uses_timepass = True
                    self.needs_runtime = True
                elif n.name == "choose":
                    self.uses_choose = True
                    self.needs_runtime = True
                elif n.name in ("cstr", "lstr"):
                    self.uses_cstr = True
                    self.needs_runtime = True
                elif n.name == "exec":
                    self.uses_exec = True
                    self.needs_runtime = True
                elif n.name in ("tostring", "toint", "tofloat", "sizeof"):
                    self.needs_runtime = True
            elif isinstance(n, MethodCall):
                if isinstance(n.expr, Identifier) and n.expr.name == "File":
                    self.uses_file = True
                    self.needs_runtime = True

            # Recurse into children
            for attr_name in dir(n):
                if attr_name.startswith("_"):
                    continue
                try:
                    attr = getattr(n, attr_name)
                    if isinstance(attr, ASTNode):
                        scan_node(attr)
                    elif isinstance(attr, (list, tuple)):
                        for item in attr:
                            if isinstance(item, ASTNode):
                                scan_node(item)
                except:
                    pass

        scan_node(node)

    def _codegen_Program(self, node):
        # Pre-scan: detect feature usage before code generation
        self._scan_features(node)

        # First pass: type aliases, structs, unions, enums, classes
        for item in node.items:
            if isinstance(item, TypeAlias):
                self._codegen(item)
            elif isinstance(item, StructDef):
                self._codegen(item)
            elif isinstance(item, UnionDef):
                self._codegen(item)
            elif isinstance(item, EnumDef):
                self._codegen(item)
            elif isinstance(item, ClassDef):
                self._codegen(item)
            elif isinstance(item, NativeImport):
                self._codegen(item)

        # Second pass: global vars
        for item in node.items:
            if isinstance(item, GlobalVarDecl):
                self._codegen(item)

        # Third pass: functions
        has_main = False
        for item in node.items:
            if isinstance(item, Function):
                if item.name == "main":
                    has_main = True
                self._codegen(item)

        # Fourth pass: instantiated generic functions
        from .typechecker import TypeChecker

        for mangled_name, func_node in TypeChecker.instantiated_func_nodes.items():
            if mangled_name not in self.func_symtab:
                self._codegen(func_node)

        # Fifth pass: instantiated generic classes
        for mangled_name, class_node in TypeChecker.instantiated_class_nodes.items():
            if mangled_name not in self.class_symtab:
                # Skip placeholder classes
                parts = mangled_name.split("_")
                is_placeholder = any(
                    p.startswith("T") and p[1:].isalnum()
                    for p in parts[1:]
                    if len(p) > 1
                )
                if not is_placeholder:
                    self._codegen(class_node)

        # Call main() if it exists
        if has_main:
            self._emit("")
            self._emit("// Entry point")
            # Find the main function to check if it takes args
            main_func = None
            for item in node.items:
                if isinstance(item, Function) and item.name == "main":
                    main_func = item
                    break

            main_has_args = main_func and len(main_func.args) > 0

            if self.is_browser:
                # Browser mode: no process.argv, use empty array
                main_args_str = "[]"
                if self.uses_get or self.uses_wait:
                    self._emit("(async () => {")
                    self._emit(f"    await main({main_args_str});")
                    self._emit("})();")
                else:
                    self._emit(f"main({main_args_str});")
            else:
                # Node.js mode
                main_args_str = "process.argv" if main_has_args else ""

                if self.uses_get or self.uses_wait:
                    # main() is async because it uses get() or wait()
                    self._emit("(async () => {")
                    self._emit(f"    await main({main_args_str});")
                    self._emit("})();")
                else:
                    self._emit(f"main({main_args_str});")

    # ========== Type Definitions ==========

    def _codegen_TypeAlias(self, node):
        self.type_aliases[node.name] = node.target_type

    def _codegen_StructDef(self, node):
        fields = [(f[0], f[1]) for f in node.fields]
        self.struct_symtab[node.name] = {
            "fields": fields,
            "field_types": {f[0]: f[1] for f in fields},
        }
        # Generate JS factory function
        self._emit(f"// Struct: {node.name}")
        self._emit(f"function create_{node.name}(")
        for i, (fname, ftype) in enumerate(fields):
            comma = "," if i < len(fields) - 1 else ""
            self._emit(f"    {fname} = {self._default_value(ftype)}{comma}")
        self._emit(f") {{")
        self.indent_level += 1
        field_strs = ", ".join(f"{fname}: {fname}" for fname, _ in fields)
        self._emit(f"return {{ {field_strs} }};")
        self.indent_level -= 1
        self._emit(f"}}")
        self._emit("")

    def _codegen_UnionDef(self, node):
        variants = [(v[0], v[1]) for v in node.variants]
        self.union_symtab[node.name] = {
            "variants": variants,
            "variant_types": {v[0]: v[1] for v in variants},
        }
        # Generate JS factory functions for each variant
        self._emit(f"// Union: {node.name}")
        variant_names = [v[0] for v in variants]
        has_float = any(v[1].startswith("float") for v in variants)
        has_int64 = any(v[1] in ("int<64>", "uint<64>") for v in variants)
        needs_bit_reinterpret = has_float and has_int64

        if needs_bit_reinterpret:
            self._emit(f"const _{node.name}_buf = new ArrayBuffer(8);")
            self._emit(f"const _{node.name}_f64 = new Float64Array(_{node.name}_buf);")
            self._emit(
                f"const _{node.name}_u64 = new BigUint64Array(_{node.name}_buf);"
            )
            self._emit(f"const _{node.name}_i64 = new BigInt64Array(_{node.name}_buf);")

        for vname, vtype in variants:
            self._emit(
                f"function {node.name}_{vname}(value = {self._default_value(vtype)}) {{"
            )
            self.indent_level += 1
            if needs_bit_reinterpret:
                if vtype.startswith("float"):
                    self._emit(f"_{node.name}_f64[0] = Number(value);")
                    raw_i = f"_{node.name}_i64[0]"
                elif vtype == "int<64>":
                    self._emit(f"_{node.name}_i64[0] = BigInt(value);")
                    raw_i = f"_{node.name}_i64[0]"
                elif vtype == "uint<64>":
                    self._emit(f"_{node.name}_u64[0] = BigInt(value);")
                    raw_i = f"_{node.name}_u64[0]"
                else:
                    raw_i = "BigInt(0)"

                float_val = f"_{node.name}_f64[0]"
                int64_val = f"_{node.name}_i64[0]"
                uint64_val = f"_{node.name}_u64[0]"

                fields = []
                for vn, vt in variants:
                    if vt.startswith("float"):
                        fields.append(f"{vn}: {float_val}")
                    elif vt == "int<64>":
                        fields.append(f"{vn}: {int64_val}")
                    elif vt == "uint<64>":
                        fields.append(f"{vn}: {uint64_val}")
                    else:
                        fields.append(f"{vn}: value")

                self._emit(
                    f'return {{ _tag: "{vname}", _value: value, {", ".join(fields)} }};'
                )
            else:
                fields = ", ".join(f"{vn}: value" for vn in variant_names)
                self._emit(f'return {{ _tag: "{vname}", _value: value, {fields} }};')
            self.indent_level -= 1
            self._emit(f"}}")
        self._emit("")

    def _codegen_EnumDef(self, node):
        self.enum_symtab[node.name] = {"members": node.members}
        self._emit(f"// Enum: {node.name}")
        self._emit(f"const {node.name} = {{")
        self.indent_level += 1
        for i, member in enumerate(node.members):
            comma = "," if i < len(node.members) - 1 else ""
            self._emit(f"{member}: {i}{comma}")
        self.indent_level -= 1
        self._emit(f"}};")
        # Generate helper to get enum name from value
        self._emit(f"function {node.name}_name(v) {{")
        self.indent_level += 1
        names_str = ", ".join(f'"{m}"' for m in node.members)
        self._emit(f"return [{names_str}][v];")
        self.indent_level -= 1
        self._emit(f"}}")
        self._emit("")

    def _codegen_NativeImport(self, node):
        self.has_native_imports = True
        self.native_libs.append(node.lib_path)
        # Generate stubs for native functions
        for name, args, return_type in node.func_declarations:
            arg_names = ", ".join(a[0] for a in args)
            self._emit(f"// Native function: {name} (from {node.lib_path})")
            self._emit(f"function {name}({arg_names}) {{")
            self.indent_level += 1
            self._emit(
                f"throw new Error(\"Native function '{name}' is not available in JavaScript target\");"
            )
            self.indent_level -= 1
            self._emit(f"}}")
        self._emit("")

    # ========== Classes ==========

    def _uses_async_in_body(self, body):
        """Check if a function/method body uses async features like wait() or get()."""
        from .ast_nodes import Call, ASTNode

        for stmt in body:
            if isinstance(stmt, Call):
                if stmt.name in ("wait", "get"):
                    return True
            # Recurse into children
            for attr_name in dir(stmt):
                if attr_name.startswith("_"):
                    continue
                try:
                    attr = getattr(stmt, attr_name)
                    if isinstance(attr, ASTNode):
                        if self._uses_async_in_body([attr]):
                            return True
                    elif isinstance(attr, (list, tuple)):
                        for item in attr:
                            if isinstance(item, ASTNode):
                                if self._uses_async_in_body([item]):
                                    return True
                except:
                    pass
        return False

    def _codegen_ClassDef(self, node):
        parent_name = node.parent

        static_fields = {}
        instance_fields = []
        for f in node.fields:
            if getattr(f, "is_static", False):
                static_fields[f.name] = {
                    "type": f.var_type,
                    "value": f.value,
                }
            else:
                instance_fields.append(f)

        # Register class in symtab BEFORE generating methods so StructInit can find it
        self.class_symtab[node.name] = {
            "fields": [
                (
                    f.name if isinstance(f, ClassField) else f[0],
                    f.var_type if isinstance(f, ClassField) else f[1],
                )
                for f in node.fields
            ],
            "static_fields": static_fields,
            "methods": {m.fnc.name: m for m in node.methods},
            "parent": parent_name,
        }

        if parent_name:
            self._emit(f"// Class: {node.name} (extends {parent_name})")
            self._emit(f"class {node.name} extends {parent_name} {{")
        else:
            self._emit(f"// Class: {node.name}")
            self._emit(f"class {node.name} {{")

        self.indent_level += 1

        # Emit static fields inside the class
        if static_fields:
            for fname, finfo in static_fields.items():
                ftype = finfo["type"]
                if finfo["value"] is not None:
                    val = self._expr(finfo["value"])
                    self._emit(f"static {fname} = {val};")
                else:
                    default_val = self._default_value(ftype)
                    self._emit(f"static {fname} = {default_val};")

        # Constructor
        all_fields = []
        if parent_name and parent_name in self.class_symtab:
            all_fields.extend(self.class_symtab[parent_name].get("fields", []))
        all_fields.extend(node.fields)

        if all_fields:
            self._emit("constructor(")
            for i, field in enumerate(all_fields):
                fname = field.name if isinstance(field, ClassField) else field[0]
                ftype = field.var_type if isinstance(field, ClassField) else field[1]
                comma = "," if i < len(all_fields) - 1 else ""
                self._emit(f"    {fname} = {self._default_value(ftype)}{comma}")
            self._emit(") {")
            self.indent_level += 1
            if parent_name:
                parent_fields = self.class_symtab[parent_name].get("fields", [])
                parent_arg_names = [
                    f.name if isinstance(f, ClassField) else f[0] for f in parent_fields
                ]
                if parent_arg_names:
                    self._emit(f"super({', '.join(parent_arg_names)});")
            for field in all_fields:
                fname = field.name if isinstance(field, ClassField) else field[0]
                self._emit(f"this.{fname} = {fname};")
            self.indent_level -= 1
            self._emit("}")
        else:
            self._emit("constructor() {")
            self.indent_level += 1
            if parent_name:
                self._emit("super();")
            self.indent_level -= 1
            self._emit("}")

        self._emit("")

        # Methods
        for method in node.methods:
            func_node = method.fnc
            mangled_name = func_node.name
            self._emit(f"// Method: {mangled_name}")

            method_is_async = self._uses_async_in_body(func_node.body)
            async_kw = "async " if method_is_async else ""

            if method_is_async:
                self.async_methods.add(f"{node.name}.{mangled_name}")

            if method.is_static:
                self._emit(f"static {async_kw}{mangled_name}(")
            else:
                self._emit(f"{async_kw}{mangled_name}(")

            for i, (arg_name, arg_type) in enumerate(func_node.args):
                comma = "," if i < len(func_node.args) - 1 else ""
                self._emit(f"    {arg_name}{comma}")
            self._emit(") {")

            self.indent_level += 1
            self.in_function = True
            self.current_func_ret_type = func_node.return_type
            self.var_symtab = {}

            # Add args to symtab
            self._value_params = set()
            for arg_name, arg_type in func_node.args:
                self.var_symtab[arg_name] = arg_type
                resolved = self._resolve_type(arg_type)
                if not self._is_pointer_type(resolved):
                    self._value_params.add(arg_name)

            if not method.is_static:
                self.var_symtab["this"] = node.name

            for stmt in func_node.body:
                self._codegen(stmt)

            self.in_function = False
            self.indent_level -= 1
            self._emit("}")
            self._emit("")

        self.indent_level -= 1
        self._emit("}")
        self._emit("")

        self.class_symtab[node.name] = {
            "fields": [
                (
                    f.name if isinstance(f, ClassField) else f[0],
                    f.var_type if isinstance(f, ClassField) else f[1],
                )
                for f in node.fields
            ],
            "methods": {m.fnc.name: m for m in node.methods},
            "parent": parent_name,
        }

    # ========== Global Variables ==========

    def _codegen_GlobalVarDecl(self, node):
        js_name = f"global_{node.name}"
        self.var_symtab[node.name] = node.var_type
        self.global_vars.append(js_name)

        if node.value is not None:
            value_expr = self._expr(node.value)
            self._emit(f"let {js_name} = _leash_alloc({value_expr}, 8);")
        else:
            self._emit(
                f"let {js_name} = _leash_alloc({self._default_value(node.var_type)}, 8);"
            )

    # ========== Functions ==========

    def _codegen_Function(self, node):
        self._emit(f"// Function: {node.name}")

        is_main = node.name == "main"

        # Check if main uses get() or wait() - if so, make it async
        main_uses_async = is_main and (self.uses_get or self.uses_wait)

        if is_main and len(node.args) == 1 and node.args[0][1] == "string[]":
            # main(args) - JS doesn't have argc/argv, just pass args array
            async_kw = "async " if main_uses_async else ""
            self._emit(f"{async_kw}function {node.name}(args = []) {{")
        else:
            async_kw = "async " if main_uses_async else ""
            self._emit(f"{async_kw}function {node.name}(")
            for i, (arg_name, arg_type) in enumerate(node.args):
                comma = "," if i < len(node.args) - 1 else ""
                self._emit(f"    {arg_name}{comma}")
            self._emit(") {")

        self.indent_level += 1
        self.in_function = True
        self.current_func_ret_type = node.return_type
        self.var_symtab = {}

        # Add args to symtab
        # Track which params are value types (not pointers) - these receive values directly
        self._value_params = set()
        for arg_name, arg_type in node.args:
            self.var_symtab[arg_name] = arg_type
            resolved = self._resolve_type(arg_type)
            if not self._is_pointer_type(resolved):
                self._value_params.add(arg_name)

        # Generate statements
        for stmt in node.body:
            self._codegen(stmt)

        # Add implicit return for main
        if is_main and node.return_type == "void":
            # Check if last statement is not a return
            pass  # JS doesn't need explicit return for void

        self.in_function = False
        self.indent_level -= 1
        self._emit("}")
        self._emit("")

        self.func_symtab[node.name] = node

    # ========== Statements ==========

    def _codegen_VariableDecl(self, node):
        resolved_type = self._resolve_type(node.var_type)
        var_name = node.name
        self.var_symtab[var_name] = node.var_type

        # ALL variables are addresses in virtual memory
        if node.value is not None:
            value_expr = self._expr(node.value)
            # For pointer types, the value IS already an address (e.g., from cstr(), &var)
            # Don't wrap in another _leash_alloc
            # Exception: function pointer types where value is a bare function name
            if self._is_pointer_type(resolved_type):
                if resolved_type.startswith("fnc(") and value_expr in self.func_symtab:
                    self._emit(f"let {var_name} = _leash_alloc({value_expr}, 8);")
                else:
                    self._emit(f"let {var_name} = {value_expr};")
            elif resolved_type in self.union_symtab:
                # Union: wrap value in the appropriate variant
                union_info = self.union_symtab[resolved_type]
                wrap_expr = self._union_auto_wrap(
                    value_expr, node.value, union_info, resolved_type
                )
                self._emit(f"let {var_name} = _leash_alloc({wrap_expr}, 8);")
            else:
                self._emit(f"let {var_name} = _leash_alloc({value_expr}, 8);")
        else:
            default = self._default_value(node.var_type)
            self._emit(f"let {var_name} = _leash_alloc({default}, 8);")

    def _union_auto_wrap(self, value_expr, value_node, union_info, union_name):
        """Wrap a value in the appropriate union variant."""
        value_type = self._get_expr_type(value_node)
        resolved_value_type = self._resolve_type(value_type)

        # Try to find matching variant by type
        for vname, vtype in union_info["variants"]:
            if self._resolve_type(vtype) == resolved_value_type:
                return f"{union_name}_{vname}({value_expr})"

        # Fallback: try numeric type matching
        for vname, vtype in union_info["variants"]:
            rt = self._resolve_type(vtype)
            if rt in ("int", "uint", "float") and resolved_value_type in (
                "int",
                "uint",
                "float",
            ):
                return f"{union_name}_{vname}({value_expr})"

        # Last resort: use first variant
        first_name = union_info["variants"][0][0]
        return f"{union_name}_{first_name}({value_expr})"

    def _codegen_Assignment(self, node):
        from .ast_nodes import UnaryOp, MemberAccess, Identifier

        # *ptr = val (pointer dereference assignment)
        if isinstance(node.target, UnaryOp) and node.target.op == "*":
            ptr_expr = self._expr(node.target.expr)
            value_expr = self._expr(node.value)
            self._emit(f"_leash_store({ptr_expr}, {value_expr});")
            return

        # Regular variable assignment: var = value
        # All variables are addresses, so store through the address
        if isinstance(node.target, Identifier) and node.target.name in self.var_symtab:
            var_type = self.var_symtab[node.target.name]
            resolved = self._resolve_type(var_type)
            value_expr = self._expr(node.value)

            # For reference types (&T), store through the pointer
            # For raw pointer types (*T), also store through the pointer
            if self._is_pointer_type(resolved):
                self._emit(f"_leash_store({node.target.name}, {value_expr});")
            else:
                # For normal types, store the value at the variable's address
                self._emit(f"_leash_store({node.target.name}, {value_expr});")
            return

        # Member access assignment (struct.field = val, union.variant = val)
        if isinstance(node.target, MemberAccess):
            base_type = self._get_expr_type(node.target.expr)
            resolved = self._resolve_type(base_type)
            if resolved in self.union_symtab:
                union_info = self.union_symtab[resolved]
                variant_name = node.target.member
                value_expr = self._expr(node.value)
                if variant_name in union_info.get("variant_types", {}):
                    # Union variant assignment: f.i = 10
                    # f is an address, need to store the new union object
                    self._emit(
                        f"_leash_store({node.target.expr.name}, {resolved}_{variant_name}({value_expr}));"
                    )
                    return
                elif variant_name == "cur":
                    wrap_expr = self._union_auto_wrap(
                        value_expr, node.value, union_info, resolved
                    )
                    self._emit(f"_leash_store({node.target.expr.name}, {wrap_expr});")
                    return
            # Struct field assignment
            value_expr = self._expr(node.value)
            self._emit(
                f"{self._expr(node.target.expr)}.{node.target.member} = {value_expr};"
            )
            return

        # Fallback
        target_expr = self._expr(node.target)
        value_expr = self._expr(node.value)
        self._emit(f"{target_expr} = {value_expr};")

    def _codegen_IfStatement(self, node):
        cond_expr = self._expr(node.condition)
        self._emit(f"if ({self._wrap_condition(cond_expr)}) {{")
        self.indent_level += 1
        for stmt in node.then_block:
            self._codegen(stmt)
        self.indent_level -= 1
        self._emit("}")

        for also_cond, also_body in node.also_blocks:
            also_expr = self._expr(also_cond)
            self._emit(f"else if ({self._wrap_condition(also_expr)}) {{")
            self.indent_level += 1
            for stmt in also_body:
                self._codegen(stmt)
            self.indent_level -= 1
            self._emit("}")

        if node.else_block:
            self._emit("else {")
            self.indent_level += 1
            for stmt in node.else_block:
                self._codegen(stmt)
            self.indent_level -= 1
            self._emit("}")

    def _codegen_SwitchStatement(self, node):
        switch_expr = self._expr(node.expression)
        self._emit(f"switch ({switch_expr}) {{")
        self.indent_level += 1
        for case_expr, case_body in node.cases:
            case_val = self._expr(case_expr)
            self._emit(f"case {case_val}: {{")
            self.indent_level += 1
            for stmt in case_body:
                self._codegen(stmt)
            self.indent_level -= 1
            self._emit("break;")
            self._emit("}")
        if node.default_block is not None:
            self._emit("default: {")
            self.indent_level += 1
            for stmt in node.default_block:
                self._codegen(stmt)
            self.indent_level -= 1
            self._emit("break;")
            self._emit("}")
        self.indent_level -= 1
        self._emit("}")

    def _wrap_condition(self, expr):
        """Wrap condition to ensure boolean evaluation."""
        return expr

    def _codegen_WhileStatement(self, node):
        cond_expr = self._expr(node.condition)
        self._emit(f"while ({self._wrap_condition(cond_expr)}) {{")
        self.indent_level += 1
        self._emit("_leash_maybe_yield();")
        self.loop_stack.append({"break": True, "continue": True})
        for stmt in node.body:
            self._codegen(stmt)
        self.loop_stack.pop()
        self.indent_level -= 1
        self._emit("}")

    def _codegen_ForStatement(self, node):
        self._codegen(node.init)
        cond_expr = self._expr(node.condition)

        from .ast_nodes import Assignment

        if isinstance(node.step, Assignment):
            step_label = f"_step_{id(node)}"
            self._emit(f"for (; {self._wrap_condition(cond_expr)}; ) {{")
            self.indent_level += 1
            self._emit("_leash_maybe_yield();")
            self.loop_stack.append({"break": True, "continue": step_label})
            for stmt in node.body:
                self._codegen(stmt)
            self._emit(f"{step_label}:")
            self.indent_level += 1
            self._codegen(node.step)
            self.indent_level -= 1
            self.loop_stack.pop()
            self.indent_level -= 1
            self._emit("}")
        else:
            step_expr = self._expr(node.step)
            self._emit(f"for (; {self._wrap_condition(cond_expr)}; {step_expr}) {{")
            self.indent_level += 1
            self._emit("_leash_maybe_yield();")
            self.loop_stack.append({"break": True, "continue": True})
            for stmt in node.body:
                self._codegen(stmt)
            self.loop_stack.pop()
            self.indent_level -= 1
            self._emit("}")

    def _codegen_DoWhileStatement(self, node):
        self._emit("do {")
        self.indent_level += 1
        self._emit("_leash_maybe_yield();")
        self.loop_stack.append({"break": True, "continue": True})
        for stmt in node.body:
            self._codegen(stmt)
        self.loop_stack.pop()
        self.indent_level -= 1
        cond_expr = self._expr(node.condition)
        self._emit(f"}} while ({self._wrap_condition(cond_expr)});")

    def _codegen_ForeachArrayStatement(self, node):
        array_expr = self._expr(node.array_expr)
        # Try to determine element type from array expression
        full_type = self._get_expr_type(node.array_expr)
        resolved = self._resolve_type(full_type)
        if resolved.endswith("]") and "[" in resolved:
            elem_type = self._get_array_inner_type(resolved)
        else:
            elem_type = "int"

        self._emit(
            f"for (let {node.index_var} = 0; {node.index_var} < {array_expr}.length; {node.index_var}++) {{"
        )
        self.indent_level += 1
        self._emit("_leash_maybe_yield();")
        # All variables are addresses - wrap array element in _leash_alloc
        self._emit(
            f"let {node.value_var} = _leash_alloc({array_expr}[{node.index_var}], 8);"
        )
        self.var_symtab[node.value_var] = elem_type
        self.loop_stack.append({"break": True, "continue": True})
        for stmt in node.body:
            self._codegen(stmt)
        self.loop_stack.pop()
        self.indent_level -= 1
        self._emit("}")

    def _codegen_ForeachStringStatement(self, node):
        str_expr = self._expr(node.string_expr)
        self._emit(
            f"for (let {node.index_var} = 0; {node.index_var} < {str_expr}.length; {node.index_var}++) {{"
        )
        self.indent_level += 1
        # Get char code to match native behavior where chars are numeric
        self._emit(f"let {node.char_var} = {str_expr}.charCodeAt({node.index_var});")
        self.var_symtab[node.char_var] = "char"
        # Mark as value param so _expr_Identifier doesn't wrap in _leash_load
        if not hasattr(self, "_value_params"):
            self._value_params = set()
        self._value_params.add(node.char_var)
        self.loop_stack.append({"break": True, "continue": True})
        for stmt in node.body:
            self._codegen(stmt)
        self.loop_stack.pop()
        self.indent_level -= 1
        self._emit("}")

    def _codegen_ForeachVectorStatement(self, node):
        vec_expr = self._expr(node.vector_expr)
        # Determine element type from vector expression
        full_type = self._get_expr_type(node.vector_expr)
        resolved = self._resolve_type(full_type)
        if resolved.startswith("vec<"):
            elem_type = self._get_vec_inner_type(resolved)
        else:
            elem_type = "int"

        self._emit(
            f"for (let {node.index_var} = 0; {node.index_var} < {vec_expr}.length; {node.index_var}++) {{"
        )
        self.indent_level += 1
        # All variables are addresses - wrap vector element in _leash_alloc
        self._emit(
            f"let {node.value_var} = _leash_alloc({vec_expr}[{node.index_var}], 8);"
        )
        self.var_symtab[node.value_var] = elem_type
        self.loop_stack.append({"break": True, "continue": True})
        for stmt in node.body:
            self._codegen(stmt)
        self.loop_stack.pop()
        self.indent_level -= 1
        self._emit("}")

    def _codegen_ForeachStructStatement(self, node):
        struct_expr = self._expr(node.struct_expr)
        resolved = self._resolve_type(self._get_expr_type(node.struct_expr))
        if resolved in self.struct_symtab:
            fields = self.struct_symtab[resolved]["fields"]
            self._emit("{")
            self.indent_level += 1
            self._emit(f"let {node.name_var};")
            self._emit(f"let {node.value_var};")
            self.var_symtab[node.name_var] = "string"
            self.var_symtab[node.value_var] = "int"
            # Mark as value params so _expr_Identifier doesn't wrap in _leash_load
            if not hasattr(self, "_value_params"):
                self._value_params = set()
            self._value_params.add(node.name_var)
            self._value_params.add(node.value_var)
            for fname, ftype in fields:
                self._emit(f"{node.name_var} = {self._emit_const_str(fname)};")
                self._emit(f"{node.value_var} = {struct_expr}.{fname};")
                for stmt in node.body:
                    self._codegen(stmt)
            self.indent_level -= 1
            self._emit("}")

    def _codegen_ReturnStatement(self, node):
        if node.value is not None:
            value_expr = self._expr(node.value)
            self._emit(f"return {value_expr};")
        else:
            self._emit("return;")

    def _codegen_StopStatement(self, node):
        self._emit("break;")

    def _codegen_ContinueStatement(self, node):
        loop_info = self.loop_stack[-1] if self.loop_stack else None
        if loop_info and isinstance(loop_info.get("continue"), str):
            # Labeled continue - jump to step label
            self._emit(f"continue {loop_info['continue']};")
        else:
            self._emit("continue;")

    def _codegen_ExpressionStatement(self, node):
        expr_str = self._expr(node.expr)
        self._emit(f"{expr_str};")

    def _codegen_ShowStatement(self, node):
        parts = []
        for arg in node.args:
            arg_type = self._get_expr_type(arg)
            resolved = self._resolve_type(arg_type)

            from .ast_nodes import MemberAccess, Identifier, UnaryOp, BinaryOp

            is_union_cur = False
            is_enum_name = False
            if isinstance(arg, MemberAccess):
                base_type = self._get_expr_type(arg.expr)
                base_resolved = self._resolve_type(base_type)
                if arg.member == "cur" and base_resolved in self.union_symtab:
                    is_union_cur = True
                elif arg.member == "name" and base_resolved in self.enum_symtab:
                    is_enum_name = True

            # For pointer types, display the raw address
            if self._is_pointer_type(resolved) and isinstance(arg, Identifier):
                expr_str = f"_leash_display({arg.name})"
            elif is_union_cur:
                expr_str = self._expr(arg.expr)
                expr_str = f"_leash_display_smart({expr_str})"
            elif is_enum_name:
                expr_str = self._expr(arg)
            elif resolved in self.union_symtab:
                expr_str = self._expr(arg)
                expr_str = f"_leash_display({expr_str})"
            elif resolved == "char[]":
                expr_str = self._expr(arg)
                expr_str = f"_leash_display_char_array({expr_str})"
            elif resolved.endswith("]") and "[" in resolved:
                inner = resolved.split("[")[0]
                expr_str = self._expr(arg)
                if inner == "char":
                    expr_str = f"_leash_display_char_array({expr_str})"
                else:
                    expr_str = f"_leash_display_array({expr_str})"
            elif resolved in ("float",) or resolved.startswith("float<"):
                expr_str = self._expr(arg)
                expr_str = f"_leash_display_float({expr_str})"
            elif resolved.startswith("uint<8") or resolved.startswith("int<8"):
                expr_str = self._expr(arg)
                expr_str = f"_leash_display_uint8({expr_str})"
            elif (
                resolved in ("int", "uint")
                or resolved.startswith("int<")
                or resolved.startswith("uint<")
            ):
                expr_str = self._expr(arg)
                # For complex expressions (derefs, arithmetic), use smart display
                if isinstance(arg, (UnaryOp, BinaryOp)):
                    expr_str = f"_leash_display_smart({expr_str})"
                else:
                    expr_str = f"_leash_display_int({expr_str})"
            elif resolved == "char":
                expr_str = self._expr(arg)
                # For complex char expressions (like dereferenced char pointers), use smart display
                if isinstance(arg, (UnaryOp, BinaryOp)):
                    expr_str = f"_leash_display_smart({expr_str})"
                else:
                    expr_str = f"_leash_display_char({expr_str})"
            elif resolved == "bool":
                expr_str = self._expr(arg)
                expr_str = f"_leash_display_bool({expr_str})"
            elif resolved == "string":
                expr_str = self._expr(arg)
                expr_str = f"_leash_display_string({expr_str})"
            else:
                expr_str = self._expr(arg)
                expr_str = f"_leash_display_smart({expr_str})"
            parts.append(expr_str)
        if parts:
            join_expr = " + ".join(parts)
            self._emit(f"console.log({join_expr});")
        else:
            self._emit("console.log();")

    def _codegen_WorksOtherwiseStatement(self, node):
        self._emit("try {")
        self.indent_level += 1
        for stmt in node.body:
            self._codegen(stmt)
        self.indent_level -= 1
        self._emit(f"}} catch ({node.err_var}) {{")
        self.indent_level += 1
        self.var_symtab[node.err_var] = "string"
        # Mark as value param so _expr_Identifier doesn't wrap in _leash_load
        if not hasattr(self, "_value_params"):
            self._value_params = set()
        self._value_params.add(node.err_var)
        # Normalize JS error messages to match Leash native format
        self._emit(f"{node.err_var} = String({node.err_var});")
        self._emit(
            f"{node.err_var} = {node.err_var}.replace(/^ReferenceError: /, '').replace(/ is not defined$/, '');"
        )
        self._emit(
            f'if ({node.err_var}.includes(": ")) {{ {node.err_var} = {node.err_var}.split(": ").slice(1).join(": "); }}'
        )
        self._emit(
            f'else {{ {node.err_var} = "Undefined variable: \'" + {node.err_var} + "\'"; }}'
        )
        for stmt in node.otherwise_block:
            self._codegen(stmt)
        self.indent_level -= 1
        self._emit("}")

    # ========== Expressions ==========

    def _expr_BinaryOp(self, node):
        left = self._expr(node.left)
        right = self._expr(node.right)

        op_map = {
            "+": "+",
            "-": "-",
            "*": "*",
            "/": "/",
            "%": "%",
            "==": "===",
            "!=": "!==",
            "<": "<",
            ">": ">",
            "<=": "<=",
            ">=": ">=",
            "&&": "&&",
            "||": "||",
            "&": "&",
            "|": "|",
            "^": "^",
            "<<": "<<",
            ">>": ">>",
        }

        js_op = op_map.get(node.op, node.op)

        # Handle bitwise operators with BigInt: if either operand is BigInt, convert both
        if node.op in ("&", "|", "^", "<<", ">>"):
            left_is_bigint = left.endswith("n")
            right_is_bigint = right.endswith("n")
            if left_is_bigint and not right_is_bigint:
                right = f"BigInt({right})"
            elif right_is_bigint and not left_is_bigint:
                left = f"BigInt({left})"

        # Handle boolean operators to return 0/1 instead of truthy values
        if node.op == "&&":
            return f"Number(Boolean({left}) && Boolean({right}))"
        elif node.op == "||":
            return f"Number(Boolean({left}) || Boolean({right}))"

        # Handle string operations
        if node.op == "+":
            left_type = self._get_expr_type(node.left)
            right_type = self._get_expr_type(node.right)
            left_resolved = self._resolve_type(left_type)
            right_resolved = self._resolve_type(right_type)

            # string + char -> convert char to string
            if left_resolved == "string" and right_resolved == "char":
                right = f"String.fromCharCode({right})"

            # char + string -> convert char to string
            if left_resolved == "char" and right_resolved == "string":
                left = f"String.fromCharCode({left})"

            # string + char[] -> convert char[] to string
            if left_resolved == "string" and right_resolved.endswith("]"):
                inner = right_resolved.split("[")[0]
                if inner == "char":
                    # If right is not already a load expression, wrap it
                    if not right.startswith("_leash_load("):
                        right = f"_leash_load({right})"
                    right = f"String.fromCharCode(...{right})"

            # char[] + string -> convert char[] to string
            if left_resolved.endswith("]") and right_resolved == "string":
                inner = left_resolved.split("[")[0]
                if inner == "char":
                    # If left is not already a load expression, wrap it
                    if not left.startswith("_leash_load("):
                        left = f"_leash_load({left})"
                    left = f"String.fromCharCode(...{left})"

            # char[] + char[] -> concatenate as strings
            if left_resolved.endswith("]") and right_resolved.endswith("]"):
                left_inner = left_resolved.split("[")[0]
                right_inner = right_resolved.split("[")[0]
                if left_inner == "char" and right_inner == "char":
                    if not left.startswith("_leash_load("):
                        left = f"_leash_load({left})"
                    if not right.startswith("_leash_load("):
                        right = f"_leash_load({right})"
                    left = f"String.fromCharCode(...{left})"
                    right = f"String.fromCharCode(...{right})"

        # Handle string - string (remove substring)
        if node.op == "-":
            left_type = self._get_expr_type(node.left)
            right_type = self._get_expr_type(node.right)
            left_resolved = self._resolve_type(left_type)
            right_resolved = self._resolve_type(right_type)
            if left_resolved == "string" and right_resolved == "string":
                return f"_leash_str_sub({left}, {right})"

        # Handle is-in operator: value <> array
        if node.op == "<>":
            right_type = self._get_expr_type(node.right)
            right_resolved = self._resolve_type(right_type)
            if right_resolved.endswith("]") and "[" in right_resolved:
                return f"{right}.includes({left})"
            else:
                raise LeashError(
                    f"Operator '<>' is only supported for array types in JS target, got '{right_resolved}'",
                    node=node,
                )

        # Handle pointer arithmetic (ptr + n, ptr - n)
        # This only applies when the LEFT operand is a raw pointer variable (not a loaded value)
        if node.op in ("+", "-"):
            from .ast_nodes import Identifier

            left_type = self._get_expr_type(node.left)
            right_type = self._get_expr_type(node.right)
            left_resolved = self._resolve_type(left_type)
            right_resolved = self._resolve_type(right_type)

            # Check if left is a raw pointer variable (not dereferenced)
            if (
                isinstance(node.left, Identifier)
                and self._is_pointer_type(left_resolved)
                and not self._is_reference_type(left_resolved)
                and right_resolved in ("int", "uint", "char", "bool")
            ):
                # This is pointer arithmetic: ptr + n
                pointee = self._get_pointee_type(left_resolved)
                size = self._sizeof_type(pointee)
                # For char pointers, use size 1 and special load
                if pointee == "char":
                    size = 1
                    if node.op == "+":
                        return f"_leash_load_char({node.left.name}, {right})"
                    else:
                        return f"_leash_load_char({node.left.name}, -{right})"
                if node.op == "+":
                    return f"({node.left.name} + {right} * {size})"
                else:
                    return f"({node.left.name} - {right} * {size})"

            # ptr - ptr (difference)
            if isinstance(node.left, Identifier) and isinstance(node.right, Identifier):
                if self._is_pointer_type(left_resolved) and self._is_pointer_type(
                    right_resolved
                ):
                    if not self._is_reference_type(
                        left_resolved
                    ) and not self._is_reference_type(right_resolved):
                        pointee = self._get_pointee_type(left_resolved)
                        size = self._sizeof_type(pointee)
                        return f"Math.trunc(({node.left.name} - {node.right.name}) / {size})"

        # Use runtime type-constrained operations for integer types
        left_type = self._get_expr_type(node.left)
        right_type = self._get_expr_type(node.right)
        left_resolved = self._resolve_type(left_type)
        right_resolved = self._resolve_type(right_type)

        # Determine the common type for the operation
        common_type = None
        if left_resolved == right_resolved:
            common_type = left_resolved
        elif left_resolved in ("int", "uint", "char", "bool") and right_resolved in (
            "int",
            "uint",
            "char",
            "bool",
        ):
            common_type = "int"
        elif left_resolved == "float" or right_resolved == "float":
            common_type = "float"
        elif (
            left_resolved.startswith("int<") or left_resolved.startswith("uint<")
        ) and (right_resolved.startswith("int<") or right_resolved.startswith("uint<")):
            # Both are sized integers - use the larger bit width
            left_signed = left_resolved.startswith("int<")
            right_signed = right_resolved.startswith("int<")
            left_bits = int(left_resolved.split("<")[1].rstrip(">"))
            right_bits = int(right_resolved.split("<")[1].rstrip(">"))
            max_bits = max(left_bits, right_bits)
            prefix = "int" if (left_signed or right_signed) else "uint"
            common_type = f"{prefix}<{max_bits}>"
        elif (
            left_resolved.startswith("int<") or left_resolved.startswith("uint<")
        ) and right_resolved in ("int", "uint", "char", "bool"):
            # Sized int + plain int -> use the sized int type
            common_type = left_resolved
        elif (
            right_resolved.startswith("int<") or right_resolved.startswith("uint<")
        ) and left_resolved in ("int", "uint", "char", "bool"):
            # Plain int + sized int -> use the sized int type
            common_type = right_resolved

        if common_type:
            # Map common type to runtime type string
            runtime_type = self._type_to_runtime_type(common_type)

            op_to_func = {
                "+": "_leash_add",
                "-": "_leash_sub",
                "*": "_leash_mul",
                "/": "_leash_div",
                "%": "_leash_mod",
            }

            if runtime_type and node.op in op_to_func:
                func_name = op_to_func[node.op]
                return f"{func_name}({left}, {right}, '{runtime_type}')"
            elif runtime_type == "int32" and node.op == "~":
                return f"_leash_not({left}, 'int32')"

        if node.op in ("+", "-", "*", "/", "%", "&", "|", "^", "<<", ">>"):
            return f"({left} {js_op} {right})"
        elif node.op in ("==", "!=", "<", ">", "<=", ">="):
            return f"({left} {js_op} {right})"
        elif node.op in ("&&", "||"):
            return f"({left} {js_op} {right})"
        else:
            return f"({left} {js_op} {right})"

    def _expr_UnaryOp(self, node):
        expr = self._expr(node.expr)
        op_map = {
            "-": "-",
            "!": "!",
            "~": "~",
            "&": "/* address-of */ ",
            "*": "/* deref */ ",
        }
        js_op = op_map.get(node.op, node.op)

        # Address-of operator (&)
        if node.op == "&":
            from .ast_nodes import Identifier, MemberAccess

            if isinstance(node.expr, Identifier):
                var_name = node.expr.name
                # In the virtual memory model, variables ARE addresses
                # &var just returns the address directly
                return var_name
            elif isinstance(node.expr, MemberAccess):
                return expr
            return expr

        # Dereference operator (*)
        if node.op == "*":
            # If the inner expression is already a load function, don't wrap again
            if expr.startswith("_leash_load_char(") or expr.startswith("_leash_load("):
                return expr
            return f"_leash_load({expr})"

        # Use runtime for bitwise NOT on integers
        if node.op == "~":
            expr_type = self._get_expr_type(node.expr)
            resolved = self._resolve_type(expr_type)
            if (
                resolved in ("int", "uint", "char", "bool")
                or resolved.startswith("int<")
                or resolved.startswith("uint<")
            ):
                runtime_type = self._type_to_runtime_type(resolved)
                return f"_leash_not({expr}, '{runtime_type}')"

        if node.op == "!":
            return f"Number(!({expr}))"

        return f"({js_op}{expr})"

    def _expr_Identifier(self, node):
        if node.name in self.var_symtab:
            var_type = self.var_symtab[node.name]
            resolved = self._resolve_type(var_type)
            # Reference types (&T) auto-deref when used in expressions
            # Raw pointer types (*T) return the address directly
            # Value types (non-pointer params) are already values, no load needed
            if self._is_reference_type(resolved):
                return f"_leash_load({node.name})"
            elif resolved.startswith("*"):
                return node.name
            elif node.name in getattr(self, "_value_params", set()):
                # Value parameter - already a value, no load needed
                return node.name
            return f"_leash_load({node.name})"
        elif node.name in self.global_vars:
            return f"_leash_load({node.name})"
        elif node.name in self.class_symtab:
            return node.name
        elif node.name in self.enum_symtab:
            return node.name
        elif node.name in self.func_symtab:
            return node.name
        else:
            if f"global_{node.name}" in self.global_vars:
                return f"_leash_load(global_{node.name})"
            return node.name

    def _expr_NumberLiteral(self, node):
        val = node.value
        # JS Number.MAX_SAFE_INTEGER is 2^53 - 1
        # For values outside safe range, emit as BigInt literal
        if isinstance(val, int) and (val > 9007199254740991 or val < -9007199254740991):
            return f"{val}n"
        return str(val)

    def _expr_FloatLiteral(self, node):
        return str(node.value)

    def _expr_StringLiteral(self, node):
        escaped = (
            node.value.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\n", "\\n")
            .replace("\t", "\\t")
        )
        return f'"{escaped}"'

    def _expr_CharLiteral(self, node):
        return f"'{node.value}'.charCodeAt(0)"

    def _expr_BoolLiteral(self, node):
        return "true" if node.value else "false"

    def _expr_NullLiteral(self, node):
        return "null"

    def _expr_FilePathLiteral(self, node):
        if node.name == "_FILEPATH":
            return f'"{node.source_file or ""}"'
        elif node.name == "_FILENAME":
            import os

            return f'"{os.path.basename(node.source_file) if node.source_file else ""}"'
        return '""'

    def _expr_BuiltinVarLiteral(self, node):
        if node.name == "_PLATFORM":
            platform_name = (
                self.target_name
                if self.target_name
                else ("html-js" if self.is_browser else "js")
            )
            return f'"{platform_name}"'
        return '""'

    def _expr_ThisExpr(self, node):
        return "this"

    def _expr_MemberAccess(self, node):
        from .ast_nodes import Identifier

        # Handle static class field access (e.g., idkMath.PI)
        if isinstance(node.expr, Identifier) and node.expr.name in self.class_symtab:
            cls_info = self.class_symtab[node.expr.name]
            static_fields = cls_info.get("static_fields", {})
            if node.member in static_fields:
                # Static field access - generate ClassName.field
                return f"{node.expr.name}.{node.member}"

        expr = self._expr(node.expr)
        expr_type = self._get_expr_type(node.expr)
        resolved = self._resolve_type(expr_type)

        # If the expression is a raw pointer to a struct, dereference first
        if (
            isinstance(node.expr, Identifier)
            and resolved.startswith("*")
            and resolved[1:] in self.struct_symtab
        ):
            expr = f"_leash_load({node.expr.name})"
            resolved = resolved[1:]

        # Handle union .cur access
        if node.member == "cur":
            if resolved in self.union_symtab:
                return f"{expr}._value"
        # Handle enum .name access
        if node.member == "name" and resolved in self.enum_symtab:
            return f"{resolved}_name({expr})"
        # Handle .size on strings, arrays, vectors
        if node.member == "size":
            if resolved == "string":
                return f"{expr}.length"
            elif resolved.endswith("]") and "[" in resolved:
                return f"{expr}.length"
            elif resolved.startswith("vec<"):
                return f"{expr}.length"
        return f"{expr}.{node.member}"

    def _expr_PointerMemberAccess(self, node):
        # Pointer member access (p->x) - dereference the pointer first
        from .ast_nodes import Identifier

        expr_type = self._get_expr_type(node.expr)
        resolved = self._resolve_type(expr_type)

        # Get the raw address of the pointer variable
        if isinstance(node.expr, Identifier):
            addr = node.expr.name
        else:
            addr = self._expr(node.expr)

        # Dereference and access member
        return f"_leash_load({addr}).{node.member}"

    def _expr_EnumMemberAccess(self, node):
        return f"{node.enum_name}.{node.member_name}"

    def _expr_IndexAccess(self, node):
        expr = self._expr(node.expr)
        index = self._expr(node.index)
        return f"{expr}[{index}]"

    def _expr_Call(self, node):
        args = ", ".join(self._expr(arg) for arg in node.args)

        # Built-in functions
        if node.name == "tostring":
            return f"tostring({args})"
        elif node.name == "toint":
            return f"toint({args})"
        elif node.name == "tofloat":
            return f"tofloat({args})"
        elif node.name == "show":
            return f"console.log({args})"
        elif node.name == "get":
            self.uses_get = True
            self.needs_runtime = True
            # get() returns a Promise in Node.js, caller must await
            return f"await get({args})"
        elif node.name == "rand":
            self.uses_rand = True
            self.needs_runtime = True
            return f"_leash_rand({args})"
        elif node.name == "randf":
            self.uses_rand = True
            self.needs_runtime = True
            return f"_leash_randf({args})"
        elif node.name == "seed":
            self.uses_rand = True
            self.needs_runtime = True
            return f"_leash_seed_rand({args})"
        elif node.name == "wait":
            self.uses_wait = True
            self.needs_runtime = True
            return f"await _leash_wait({args})"
        elif node.name == "timepass":
            self.uses_timepass = True
            self.needs_runtime = True
            return f"_leash_timepass()"
        elif node.name == "choose":
            self.uses_choose = True
            self.needs_runtime = True
            return f"choose({args})"
        elif node.name == "sizeof":
            return f"sizeof({args})"
        elif node.name == "cstr":
            self.uses_cstr = True
            self.needs_runtime = True
            return f"cstr({args})"
        elif node.name == "lstr":
            self.uses_cstr = True
            self.needs_runtime = True
            return f"lstr({args})"
        elif node.name == "exec":
            self.uses_exec = True
            self.needs_runtime = True
            return f"exec({args})"

        # Regular function call - handle pointer/reference parameters
        from .typechecker import TypeChecker
        from .ast_nodes import (
            Identifier,
            NumberLiteral,
            StringLiteral,
            BoolLiteral,
            CharLiteral,
            FloatLiteral,
        )

        func_info = self.func_symtab.get(node.name)
        if func_info:
            processed_args = []
            for i, arg_expr in enumerate(node.args):
                if i < len(func_info.args):
                    param_name, param_type = func_info.args[i]
                    resolved_param = self._resolve_type(param_type)
                    # For pointer/reference types (including function pointers):
                    # pass the ADDRESS of the argument
                    if self._is_pointer_type(resolved_param):
                        from .ast_nodes import UnaryOp as UnaryOpNode

                        if isinstance(arg_expr, Identifier):
                            var_type = self.var_symtab.get(arg_expr.name, "int")
                            resolved_var = self._resolve_type(var_type)
                            # If the variable is itself a function pointer type, pass address directly
                            if resolved_var.startswith("fnc("):
                                processed_args.append(arg_expr.name)
                            # If the variable is a function name (not a var), wrap in alloc
                            elif (
                                arg_expr.name in self.func_symtab
                                and arg_expr.name not in self.var_symtab
                            ):
                                processed_args.append(
                                    f"_leash_alloc({arg_expr.name}, 8)"
                                )
                            else:
                                # Variable is already an address, pass it directly
                                processed_args.append(arg_expr.name)
                        elif isinstance(arg_expr, UnaryOpNode) and arg_expr.op == "&":
                            # &var - check if it's a function name
                            if isinstance(arg_expr.expr, Identifier):
                                if (
                                    arg_expr.expr.name in self.func_symtab
                                    and arg_expr.expr.name not in self.var_symtab
                                ):
                                    # It's a function name, wrap in alloc
                                    processed_args.append(
                                        f"_leash_alloc({arg_expr.expr.name}, 8)"
                                    )
                                else:
                                    # Regular variable, already an address
                                    processed_args.append(arg_expr.expr.name)
                            else:
                                processed_args.append(self._expr(arg_expr.expr))
                        else:
                            # For expressions, evaluate and wrap in alloc
                            expr_str = self._expr(arg_expr)
                            # If it's a bare function name (from &func), wrap in alloc
                            if expr_str in self.func_symtab:
                                processed_args.append(f"_leash_alloc({expr_str}, 8)")
                            else:
                                processed_args.append(f"_leash_alloc({expr_str}, 8)")
                        continue
                    # For non-pointer params: pass the VALUE directly
                    if isinstance(arg_expr, Identifier):
                        var_type = self.var_symtab.get(arg_expr.name, "int")
                        resolved_var = self._resolve_type(var_type)
                        if self._is_pointer_type(resolved_var):
                            # Pointer arg to non-pointer param: load the pointed value
                            processed_args.append(f"_leash_load({arg_expr.name})")
                        else:
                            # Non-pointer arg to non-pointer param: load the value from address
                            processed_args.append(f"_leash_load({arg_expr.name})")
                        continue
                    # For non-Identifier args (literals, expressions), pass value
                processed_args.append(self._expr(arg_expr))
            args = ", ".join(processed_args)

        # Check if this is a ClassName.method() pattern where ClassName is a generic base
        if "." in node.name:
            parts = node.name.split(".", 1)
            class_name = parts[0]
            method_name = parts[1]
            target = getattr(self, "current_target_type", None)
            if target:
                resolved_target = self._resolve_type(target)
                if resolved_target in TypeChecker.instantiated_class_nodes:
                    return f"{resolved_target}.{method_name}({args})"
            for mangled_name in TypeChecker.instantiated_class_nodes:
                if (
                    mangled_name.startswith(class_name + "_")
                    and "_T" not in mangled_name
                ):
                    return f"{mangled_name}.{method_name}({args})"

        # Check if this is a function pointer call (variable storing a function reference)
        if node.name in self.var_symtab:
            var_type = self.var_symtab[node.name]
            resolved = self._resolve_type(var_type)
            if resolved.startswith("fnc("):
                return f"_leash_load({node.name})({args})"

        return f"{node.name}({args})"

    def _expr_GenericCall(self, node):
        type_args_str = "_".join(
            t.replace("<", "_").replace(">", "_").replace(",", "_").replace(" ", "")
            for t in node.type_args
        )
        mangled_name = f"{node.name}_{type_args_str}"
        args = ", ".join(self._expr(arg) for arg in node.args)
        return f"{mangled_name}({args})"

    def _expr_MethodCall(self, node):
        expr = self._expr(node.expr)
        args = ", ".join(self._expr(arg) for arg in node.args)

        # Built-in type methods
        expr_type = self._get_expr_type(node.expr)
        resolved = self._resolve_type(expr_type)

        # Vector methods - check both resolved and raw type
        is_vec = (
            resolved.startswith("vec<")
            or resolved.startswith("vec_")
            or expr_type.startswith("vec<")
            or expr_type.startswith("vec_")
        )
        if is_vec:
            if node.method == "push" or node.method == "pushb":
                return f"{expr}.push({args})"
            elif node.method == "popf":
                return f"{expr}.shift()"
            elif node.method == "popb":
                return f"{expr}.pop()"
            elif node.method == "get":
                return f"{expr}[{args}]"
            elif node.method == "set":
                return f"{expr}[{args}]"
            elif node.method == "size":
                return f"{expr}.length"
            elif node.method == "insert":
                return f"{expr}.splice({args})"
            elif node.method == "remove":
                return f"{expr}.splice({args}, 1)"
            elif node.method == "clear":
                return f"{expr}.length = 0"
            elif node.method == "isin":
                return f"{expr}.includes({args})"

        # String methods
        if resolved == "string":
            if node.method == "size":
                return f"{expr}.length"

        # Array/slice methods
        if resolved.endswith("]") and "[" in resolved:
            if node.method == "size":
                return f"{expr}.length"

        # Class methods
        if resolved in self.class_symtab:
            # Check if this is a generic class with instantiations
            from .typechecker import TypeChecker

            method_key = f"{resolved}.{node.method}"
            is_async = method_key in self.async_methods
            await_kw = "await " if is_async else ""

            # Look for instantiated versions of this class
            target = getattr(self, "current_target_type", None)
            if target:
                resolved_target = self._resolve_type(target)
                if resolved_target in TypeChecker.instantiated_class_nodes:
                    return f"{await_kw}{resolved_target}.{node.method}({args})"
            # Check for any instantiated class matching this base name
            for mangled_name in TypeChecker.instantiated_class_nodes:
                if mangled_name.startswith(resolved + "_") and "_T" not in mangled_name:
                    return f"{await_kw}{mangled_name}.{node.method}({args})"
            return f"{await_kw}{expr}.{node.method}({args})"

        # File methods
        if resolved == "File":
            self.uses_file = True
            self.needs_runtime = True
            return f"{expr}.{node.method}({args})"

        # File static method calls (File.open, File.rename, File.delete)
        from .ast_nodes import Identifier

        if isinstance(node.expr, Identifier) and node.expr.name == "File":
            self.uses_file = True
            self.needs_runtime = True
            if node.method == "open":
                return f"File.open({args})"
            elif node.method == "rename":
                return f"File.rename({args})"
            elif node.method == "delete":
                return f"File.delete({args})"

        # Fallback
        return f"{expr}.{node.method}({args})"

    def _expr_StructInit(self, node):
        if node.name in self.class_symtab:
            kwargs = {}
            for fname, fexpr in node.kwargs:
                kwargs[fname] = self._expr(fexpr)
            args_str = ", ".join(kwargs.values())
            return f"new {node.name}({args_str})"
        elif node.name in self.struct_symtab:
            kwargs = {}
            for fname, fexpr in node.kwargs:
                kwargs[fname] = self._expr(fexpr)
            parts = ", ".join(f"{k}: {v}" for k, v in kwargs.items())
            return f"{{ {parts} }}"
        return "{}"

    def _expr_ArrayInit(self, node):
        elements = ", ".join(self._expr(e) for e in node.elements)
        return f"[{elements}]"

    def _expr_Assignment(self, node):
        target_expr = self._expr(node.target)
        value_expr = self._expr(node.value)
        return f"({target_expr} = {value_expr})"

    def _expr_TernaryOp(self, node):
        cond = self._expr(node.condition)
        true_val = self._expr(node.true_expr)
        false_val = self._expr(node.false_expr)
        return f"({cond} ? {true_val} : {false_val})"

    def _expr_CastExpr(self, node):
        expr = self._expr(node.expr)
        target = self._resolve_type(node.target_type)
        if target in ("int", "uint"):
            return f"parseInt({expr})"
        elif target == "float":
            return f"parseFloat({expr})"
        elif target == "string":
            return f"String({expr})"
        elif target == "bool":
            return f"Boolean({expr})"
        elif target == "char":
            return f"String.fromCharCode({expr})"
        elif target.startswith("uint<") or target.startswith("int<"):
            bits = int(target.split("<")[1].rstrip(">"))
            if bits <= 64:
                # Standard JS number range - use bitwise mask
                return f"({expr} & 0x{'f' * (bits // 4)})"
            else:
                # Arbitrary precision - use BigInt masking
                signed = target.startswith("int<")
                if signed:
                    return f"_leash_intN(BigInt({expr}), {bits})"
                else:
                    return f"_leash_uintN(BigInt({expr}), {bits})"
        return expr

    def _expr_AsExpr(self, node):
        expr = self._expr(node.expr)
        target = self._resolve_type(node.target_type)
        if target in ("int", "uint"):
            return f"parseInt({expr})"
        elif target == "float":
            return f"parseFloat({expr})"
        elif target == "string":
            return f"String({expr})"
        elif target == "bool":
            return f"Boolean({expr})"
        elif target == "char":
            return f"String.fromCharCode({expr})"
        elif target.startswith("uint<") or target.startswith("int<"):
            bits = int(target.split("<")[1].rstrip(">"))
            if bits <= 64:
                return f"({expr} & 0x{'f' * (bits // 4)})"
            else:
                signed = target.startswith("int<")
                if signed:
                    return f"_leash_intN(BigInt({expr}), {bits})"
                else:
                    return f"_leash_uintN(BigInt({expr}), {bits})"
        elif target.startswith("float<"):
            return f"parseFloat({expr})"
        return expr

    def _expr_TypeConvExpr(self, node):
        expr = self._expr(node.expr)
        target = self._resolve_type(node.target_type)
        if node.target_type == "int":
            return f"parseInt({expr})"
        elif node.target_type == "float":
            return f"parseFloat({expr})"
        elif node.target_type == "string":
            return f"String({expr})"
        elif node.target_type == "bool":
            return f"Boolean({expr})"
        elif target.startswith("int<") or target.startswith("uint<"):
            bits = int(target.split("<")[1].rstrip(">"))
            if bits > 64:
                signed = target.startswith("int<")
                if signed:
                    return f"_leash_intN(BigInt({expr}), {bits})"
                else:
                    return f"_leash_uintN(BigInt({expr}), {bits})"
        return expr

    def _emit_const_str(self, s):
        escaped = (
            s.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\n", "\\n")
            .replace("\t", "\\t")
        )
        return f'"{escaped}"'

    def _get_expr_type(self, node):
        """Try to determine the Leash type of an expression."""
        from .ast_nodes import (
            Identifier,
            StringLiteral,
            NumberLiteral,
            FloatLiteral,
            BoolLiteral,
            CharLiteral,
            NullLiteral,
            FilePathLiteral,
            BuiltinVarLiteral,
            ArrayInit,
            Call,
            MethodCall,
            BinaryOp,
            UnaryOp,
        )

        if isinstance(node, Identifier):
            return self.var_symtab.get(node.name, "int")
        elif isinstance(node, StringLiteral):
            return "string"
        elif isinstance(node, NumberLiteral):
            return "int"
        elif isinstance(node, FloatLiteral):
            return "float"
        elif isinstance(node, BoolLiteral):
            return "bool"
        elif isinstance(node, CharLiteral):
            return "char"
        elif isinstance(node, NullLiteral):
            return "int"
        elif isinstance(node, FilePathLiteral):
            return "string"
        elif isinstance(node, BuiltinVarLiteral):
            return "string"
        elif isinstance(node, ArrayInit):
            return "int[]"
        elif isinstance(node, Call):
            if node.name == "tostring":
                return "string"
            elif node.name in ("toint", "rand"):
                return "int"
            elif node.name in ("tofloat", "randf"):
                return "float"
            elif node.name == "get":
                return "string"
            elif node.name == "cstr":
                return "char[]"
            elif node.name == "lstr":
                return "string"
            # Look up user-defined function return type
            if node.name in self.func_symtab:
                f = self.func_symtab[node.name]
                if hasattr(f, "return_type"):
                    return f.return_type
            return "int"
        elif isinstance(node, MethodCall):
            if node.method == "readb" or node.method == "readlnb":
                return "char[]"
            elif node.method == "read":
                return "string"
            elif node.method == "size":
                return "int"
            elif node.method in ("popb", "popf", "get"):
                base_type = self._get_expr_type(node.expr)
                if base_type.startswith("vec<"):
                    return self._get_vec_inner_type(base_type)
                if base_type.endswith("]"):
                    return self._get_array_inner_type(base_type)
                return "int"
            # Try to look up the method's return type from the class
            base_type = self._get_expr_type(node.expr)
            resolved = self._resolve_type(base_type)
            if resolved in self.class_symtab:
                methods = self.class_symtab[resolved].get("methods", {})
                if node.method in methods:
                    method_node = methods[node.method]
                    return method_node.fnc.return_type
            # Handle static method calls: ClassName.method() where ClassName is a class
            from .ast_nodes import Identifier

            if (
                isinstance(node.expr, Identifier)
                and node.expr.name in self.class_symtab
            ):
                methods = self.class_symtab[node.expr.name].get("methods", {})
                if node.method in methods:
                    method_node = methods[node.method]
                    return method_node.fnc.return_type
            return "int"
        elif isinstance(node, BinaryOp):
            if node.op == "+":
                left_type = self._get_expr_type(node.left)
                right_type = self._get_expr_type(node.right)
                if left_type == "string" or right_type == "string":
                    return "string"
                # Pointer arithmetic: *T + int -> T
                left_resolved = self._resolve_type(left_type)
                if left_resolved.startswith("*"):
                    return left_resolved[1:]  # Return pointee type
            elif node.op == "-":
                left_type = self._get_expr_type(node.left)
                right_type = self._get_expr_type(node.right)
                if left_type == "string" and right_type == "string":
                    return "string"
                # Pointer arithmetic: *T - int -> T
                left_resolved = self._resolve_type(left_type)
                if left_resolved.startswith("*"):
                    return left_resolved[1:]
            if node.op in ("==", "!=", "<", ">", "<=", ">=", "&&", "||"):
                return "bool"
            return "int"
        elif isinstance(node, UnaryOp):
            if node.op == "!":
                return "bool"
            elif node.op == "*":
                # Dereference: return the pointee type
                inner_type = self._get_expr_type(node.expr)
                resolved = self._resolve_type(inner_type)
                if resolved.startswith("*"):
                    return resolved[1:]
            return "int"
        elif isinstance(node, MemberAccess):
            base_type = self._get_expr_type(node.expr)
            resolved = self._resolve_type(base_type)
            if resolved in self.struct_symtab:
                return self.struct_symtab[resolved]["field_types"].get(
                    node.member, "int"
                )
            if resolved in self.class_symtab:
                fields = self.class_symtab[resolved].get("fields", [])
                for f in fields:
                    fname = f[0] if isinstance(f, tuple) else f.name
                    ftype = f[1] if isinstance(f, tuple) else f.var_type
                    if fname == node.member:
                        return ftype
                # Also check static fields
                static_fields = self.class_symtab[resolved].get("static_fields", {})
                if node.member in static_fields:
                    return static_fields[node.member]["type"]
            return "int"
        elif isinstance(node, ThisExpr):
            return self.var_symtab.get("this", "int")
        elif isinstance(node, IndexAccess):
            base_type = self._get_expr_type(node.expr)
            if base_type.startswith("vec<"):
                return self._get_vec_inner_type(base_type)
            if base_type.endswith("]"):
                return self._get_array_inner_type(base_type)
            if base_type == "string":
                return "char"
            return "int"
        elif isinstance(node, TernaryOp):
            return self._get_expr_type(node.true_expr)
        elif isinstance(node, CastExpr):
            return node.target_type
        elif isinstance(node, AsExpr):
            return node.target_type
        elif isinstance(node, TypeConvExpr):
            return node.target_type
        return "int"
