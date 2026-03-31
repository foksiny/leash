import llvmlite.ir as ir
import llvmlite.binding as llvm
from .errors import LeashError
from .ast_nodes import (
    TypeAlias,
    StructDef,
    EnumDef,
    UnionDef,
    ClassDef,
    Function,
    GenericCall,
    TemplateDef,
    GlobalVarDecl,
)


class CodeGen:
    def __init__(self):
        self.module = ir.Module(name="leash_module")
        self.builder = None
        self.func_symtab = {}
        self.var_symtab = {}
        self.struct_symtab = {}
        self.type_aliases = {}  # name -> resolved type string
        self.union_symtab = {}  # name -> { 'type': ir_type, 'variants': [...], 'variant_types': {...}, 'max_size': int }
        self.enum_symtab = {}  # name -> { 'members': [names], 'names_arr': ir.GlobalVariable }
        self.class_symtab = {}  # name -> { 'type': ir_type, 'fields': {...}, 'methods': {...} }
        self.global_var_ptrs = {}  # name -> (ir.GlobalVariable, leash_type_string) for module-level variables
        self.global_init_list = []  # list of (gv, init_expr, leash_type) for globals with initializers
        self.init_func = None  # The _leash_init_globals function if any globals need init
        self.printf = None
        self.current_target_type = None
        self.loop_stack = []  # Stack of (break_bb, continue_bb) for nested loops

        # stderr - declared as external global
        self.stderr_var = ir.GlobalVariable(
            self.module, ir.IntType(8).as_pointer(), name="stderr"
        )
        self.stderr_var.linkage = "external"

        # Boehm GC configuration (replacing legacy SAMM)
        self.current_func_alloc_limit = 0  # Not used with GC

        self.setup_builtins()

    def setup_builtins(self):
        printf_ty = ir.FunctionType(
            ir.IntType(32), [ir.IntType(8).as_pointer()], var_arg=True
        )
        self.printf = ir.Function(self.module, printf_ty, name="printf")

        strlen_ty = ir.FunctionType(ir.IntType(64), [ir.IntType(8).as_pointer()])
        self.strlen = ir.Function(self.module, strlen_ty, name="strlen")

        strcpy_ty = ir.FunctionType(
            ir.IntType(8).as_pointer(),
            [ir.IntType(8).as_pointer(), ir.IntType(8).as_pointer()],
        )
        self.strcpy = ir.Function(self.module, strcpy_ty, name="strcpy")

        strncpy_ty = ir.FunctionType(
            ir.IntType(8).as_pointer(),
            [ir.IntType(8).as_pointer(), ir.IntType(8).as_pointer(), ir.IntType(64)],
        )
        self.strncpy = ir.Function(self.module, strncpy_ty, name="strncpy")

        strcat_ty = ir.FunctionType(
            ir.IntType(8).as_pointer(),
            [ir.IntType(8).as_pointer(), ir.IntType(8).as_pointer()],
        )
        self.strcat = ir.Function(self.module, strcat_ty, name="strcat")

        strcmp_ty = ir.FunctionType(
            ir.IntType(32), [ir.IntType(8).as_pointer(), ir.IntType(8).as_pointer()]
        )
        self.strcmp = ir.Function(self.module, strcmp_ty, name="strcmp")

        strstr_ty = ir.FunctionType(
            ir.IntType(8).as_pointer(),
            [ir.IntType(8).as_pointer(), ir.IntType(8).as_pointer()],
        )
        self.strstr = ir.Function(self.module, strstr_ty, name="strstr")

        exit_ty = ir.FunctionType(ir.VoidType(), [ir.IntType(32)])
        self.exit_fn = ir.Function(self.module, exit_ty, name="exit")

        fprintf_ty = ir.FunctionType(
            ir.IntType(32),
            [ir.IntType(8).as_pointer(), ir.IntType(8).as_pointer()],
            var_arg=True,
        )
        self.fprintf = ir.Function(self.module, fprintf_ty, name="fprintf")

        # Boehm GC functions
        gc_init_ty = ir.FunctionType(ir.VoidType(), [])
        self.gc_init = ir.Function(self.module, gc_init_ty, name="GC_init")

        gc_malloc_ty = ir.FunctionType(ir.IntType(8).as_pointer(), [ir.IntType(64)])
        self.malloc = ir.Function(self.module, gc_malloc_ty, name="GC_malloc")

        gc_realloc_ty = ir.FunctionType(
            ir.IntType(8).as_pointer(), [ir.IntType(8).as_pointer(), ir.IntType(64)]
        )
        self.realloc = ir.Function(self.module, gc_realloc_ty, name="GC_realloc")

        # Free is still declared just in case, but GC_malloc doesn't need it.
        free_ty = ir.FunctionType(ir.VoidType(), [ir.IntType(8).as_pointer()])
        self.free = ir.Function(self.module, free_ty, name="free")

        getchar_ty = ir.FunctionType(ir.IntType(32), [])
        self.getchar = ir.Function(self.module, getchar_ty, name="getchar")

        atoll_ty = ir.FunctionType(ir.IntType(64), [ir.IntType(8).as_pointer()])
        self.atoll = ir.Function(self.module, atoll_ty, name="atoll")

        atof_ty = ir.FunctionType(ir.DoubleType(), [ir.IntType(8).as_pointer()])
        self.atof = ir.Function(self.module, atof_ty, name="atof")

        sprintf_ty = ir.FunctionType(
            ir.IntType(32),
            [ir.IntType(8).as_pointer(), ir.IntType(8).as_pointer()],
            var_arg=True,
        )
        self.sprintf = ir.Function(self.module, sprintf_ty, name="sprintf")

        memmove_ty = ir.FunctionType(
            ir.IntType(8).as_pointer(),
            [ir.IntType(8).as_pointer(), ir.IntType(8).as_pointer(), ir.IntType(64)],
        )
        self.memmove = ir.Function(self.module, memmove_ty, name="memmove")

    def _emit_const_str(self, string_val):
        """Create a global string constant and return a pointer to it (i8*)."""
        # Search for existing constant
        const_name = f"const_str_{hash(string_val)}"
        for g in self.module.global_values:
            if g.name == const_name:
                return self.builder.bitcast(g, ir.IntType(8).as_pointer())

        fmt_bytes = bytearray(string_val.encode("utf8") + b"\0")
        c_fmt = ir.Constant(ir.ArrayType(ir.IntType(8), len(fmt_bytes)), fmt_bytes)
        global_fmt = ir.GlobalVariable(self.module, c_fmt.type, name=const_name)
        global_fmt.linkage = "internal"
        global_fmt.global_constant = True
        global_fmt.initializer = c_fmt
        return self.builder.bitcast(global_fmt, ir.IntType(8).as_pointer())

    def _emit_tostring(self, val, llvm_ty):
        """Convert any basic value to a Leash string (i8*). Allocation via GC_malloc."""
        # Buffer for conversion (64 bytes is plenty for any numeric)
        buf = self.builder.call(self.malloc, [ir.Constant(ir.IntType(64), 64)])
        self._track_alloc(buf)

        fmt = ""
        casted_val = val
        if isinstance(llvm_ty, ir.IntType):
            if llvm_ty.width == 1:  # bool
                # Special case for bool: "true" or "false"
                true_str = self._emit_const_str("true")
                false_str = self._emit_const_str("false")
                return self.builder.select(val, true_str, false_str)
            elif llvm_ty.width == 8:  # char
                # Create a string of length 1
                self.builder.store(
                    val, self.builder.gep(buf, [ir.Constant(ir.IntType(32), 0)])
                )
                self.builder.store(
                    ir.Constant(ir.IntType(8), 0),
                    self.builder.gep(buf, [ir.Constant(ir.IntType(32), 1)]),
                )
                return buf
            elif llvm_ty.width == 64:
                fmt = "%lld"
            else:
                fmt = "%d"
                if llvm_ty.width < 32:
                    casted_val = self.builder.sext(val, ir.IntType(32))
        elif isinstance(llvm_ty, (ir.FloatType, ir.DoubleType)):
            fmt = "%f"
            if isinstance(llvm_ty, ir.FloatType):
                casted_val = self.builder.fpext(val, ir.DoubleType())
        else:
            return val  # already a string?

        fmt_ptr = self._emit_const_str(fmt)
        self.builder.call(self.sprintf, [buf, fmt_ptr, casted_val])
        return buf

    def _track_alloc(self, ptr):
        """No-op since we are using Boeing GC."""
        return ptr

    def _emit_cleanup(self, ret_val=None):
        """No-op since we are using Boeing GC."""
        pass

    def _emit_default_value(self, type_name):
        resolved = self._resolve_type_name(type_name)
        llvm_type = self._get_llvm_type(resolved)

        if (
            resolved in ("int", "uint")
            or resolved.startswith("int<")
            or resolved.startswith("uint<")
        ):
            return ir.Constant(llvm_type, 0)
        if resolved == "float" or resolved.startswith("float<"):
            return ir.Constant(llvm_type, 0.0)
        if resolved == "bool" or resolved == "char":
            return ir.Constant(llvm_type, 0)
        if resolved == "string":
            return self._emit_const_str("")
        if resolved.startswith("vec<"):
            # Empty vector: { null, 0, 0 }
            # First element's element type's pointer type
            ptr_ty = llvm_type.elements[0]
            return ir.Constant(
                llvm_type,
                [
                    ir.Constant(ptr_ty, None),
                    ir.Constant(ir.IntType(64), 0),
                    ir.Constant(ir.IntType(64), 0),
                ],
            )
        if resolved.endswith("]") and "[" in resolved:
            # Empty array/slice: { 0, null }
            ptr_ty = llvm_type.elements[1]
            return ir.Constant(
                llvm_type, [ir.Constant(ir.IntType(64), 0), ir.Constant(ptr_ty, None)]
            )

        return ir.Constant(
            llvm_type,
            [
                ir.Constant(e, 0) if isinstance(e, ir.IntType) else ir.Constant(e, None)
                for e in llvm_type.elements
            ],
        )

    def generate_code(self, node):
        return self._codegen(node)

    def _codegen(self, node):
        method_name = f"_codegen_{type(node).__name__}"
        method = getattr(self, method_name, None)
        if method:
            return method(node)
        else:
            raise NotImplementedError(f"No codegen for {type(node).__name__}")

    def _get_leash_type_name(self, node):
        """Helper to try and get the Leash type name for an AST node during codegen."""
        from .ast_nodes import (
            Identifier,
            MemberAccess,
            IndexAccess,
            Call,
            GenericCall,
            EnumMemberAccess,
            CastExpr,
            TypeConvExpr,
            MethodCall,
            ThisExpr,
            UnaryOp,
            PointerMemberAccess,
        )

        if isinstance(node, ThisExpr):
            if "this" in self.var_symtab:
                return self.var_symtab["this"][1]
            return "int"
        if isinstance(node, Identifier):
            if node.name in self.var_symtab:
                return self.var_symtab[node.name][1]
        elif isinstance(node, MemberAccess):
            base_type = self._get_leash_type_name(node.expr)
            resolved = self._resolve_type_name(base_type)
            if resolved.startswith("&"):
                resolved = self._resolve_type_name(resolved[1:])
            if resolved in self.struct_symtab:
                return self.struct_symtab[resolved]["field_types"].get(
                    node.member, "int"
                )
            if resolved in self.class_symtab:
                return self.class_symtab[resolved]["field_types"].get(
                    node.member, "int"
                )
            if resolved in self.union_symtab:
                return (
                    self.union_symtab[resolved]["variants"]
                    .get(node.member, {})
                    .get("type_name", "int")
                )
        elif isinstance(node, PointerMemberAccess):
            base_type = self._get_leash_type_name(node.expr)
            resolved = self._resolve_type_name(base_type)
            underlying = self._resolve_type_name(resolved[1:])
            if underlying in self.struct_symtab:
                return self.struct_symtab[underlying]["field_types"].get(
                    node.member, "int"
                )
            if underlying in self.class_symtab:
                return self.class_symtab[underlying]["field_types"].get(
                    node.member, "int"
                )
        elif isinstance(node, UnaryOp):
            if node.op == "*":
                t = self._get_leash_type_name(node.expr)
                res = self._resolve_type_name(t)
                return res[1:]
            if node.op == "&":
                t = self._get_leash_type_name(node.expr)
                return f"*{t}"
        elif isinstance(node, IndexAccess):
            base_type = self._get_leash_type_name(node.expr)
            if "[" in base_type:
                return base_type.split("[")[0]
        elif isinstance(node, EnumMemberAccess):
            return node.enum_name
        elif isinstance(node, CastExpr):
            return node.target_type
        elif isinstance(node, TypeConvExpr):
            return node.target_type
        elif isinstance(node, Call):
            if node.name == "tostring":
                return "string"
            if node.name == "get":
                return "string"
            if node.name in self.func_symtab:
                # We don't store return types in func_symtab currently...
                pass
        elif isinstance(node, GenericCall):
            # Return type is stored in the return type of the mangled function
            type_args_str = "_".join(
                t.replace("<", "_").replace(">", "_").replace(",", "_").replace(" ", "")
                for t in node.type_args
            )
            mangled_name = f"{node.name}_{type_args_str}"
            # Try to get return type from func_symtab
            if mangled_name in self.func_symtab:
                func = self.func_symtab[mangled_name]
                ret_type = func.function_type.return_type
                # Map LLVM type to Leash type name
                return self._llvm_type_to_leash_name(ret_type)
            return "int"  # fallback
        elif isinstance(node, MethodCall):
            base_type = self._get_leash_type_name(node.expr)
            resolved = self._resolve_type_name(base_type)
            if resolved.startswith("&"):
                resolved = self._resolve_type_name(resolved[1:])
            if resolved.startswith("vec<"):
                inner = resolved[4:-1]
                if node.method in ("get", "popb", "popf"):
                    return inner
                if node.method == "size":
                    return "int"
            if resolved == "string" or resolved.endswith("]"):
                if node.method == "size":
                    return "int"
        return "int"

    def _llvm_type_to_leash_name(self, llvm_type):
        """Convert an LLVM type to a Leash type name."""
        if isinstance(llvm_type, ir.IntType):
            if llvm_type.width == 1:
                return "bool"
            elif llvm_type.width == 8:
                return "char"
            elif llvm_type.width == 32:
                return "int"
            elif llvm_type.width == 64:
                return "int<64>"
        elif isinstance(llvm_type, (ir.FloatType, ir.DoubleType)):
            return "float"
        elif isinstance(llvm_type, ir.PointerType):
            if llvm_type.pointee == ir.IntType(8):
                return "string"
            return "ptr"
        elif isinstance(llvm_type, ir.VoidType):
            return "void"
        return "int"  # fallback

    def _resolve_type_name(self, type_name):
        """Resolve type aliases to their underlying type (strips imut qualifier).
        Also resolves generic type names like Hash<string, int> to Hash_string_int.
        """
        # Strip imut qualifier — it's a compile-time-only concept
        if isinstance(type_name, str) and type_name.startswith("imut "):
            type_name = type_name[5:]

        prefix = ""
        if isinstance(type_name, str):
            while type_name.startswith("*") or type_name.startswith("&"):
                prefix += type_name[0]
                type_name = type_name[1:]

        visited = set()
        while (
            isinstance(type_name, str)
            and type_name in self.type_aliases
            and type_name not in visited
        ):
            visited.add(type_name)
            type_name = self.type_aliases[type_name]

        # Handle generic type names like Hash<string, int> -> Hash_string_int
        # But don't mangle vec<T> types - they are handled specially
        if isinstance(type_name, str) and "<" in type_name and type_name.endswith(">"):
            base_class = type_name.split("<")[0]
            # Don't mangle vec types or built-in sized types (int<>, uint<>, float<>)
            if base_class not in ("vec", "int", "uint", "float"):
                type_args_str = type_name[len(base_class) + 1 : -1]
                type_args = [a.strip() for a in type_args_str.split(",")]
                mangled_name = f"{base_class}_{'_'.join(t.replace('<', '_').replace('>', '_').replace(',', '_').replace(' ', '') for t in type_args)}"
                type_name = mangled_name

        if prefix:
            return f"{prefix}{type_name}"
        return type_name

    def _codegen_Program(self, node):
        # First pass: register type aliases and struct/enum definitions
        for item in node.items:
            if isinstance(item, TypeAlias):
                self._codegen(item)
            elif isinstance(item, StructDef):
                self._codegen(item)
            elif isinstance(item, EnumDef):
                self._codegen(item)
            elif isinstance(item, UnionDef):
                self._codegen(item)

        # Second pass: global variable declarations
        # Initialize storage for globals (reset in case CodeGen is reused)
        self.global_var_ptrs = {}
        self.global_init_list = []
        self.init_func = None
        for item in node.items:
            if isinstance(item, GlobalVarDecl):
                self._codegen(item)

        # Third pass: generate non-generic classes (which may reference structs)
        for item in node.items:
            if isinstance(item, ClassDef):
                # Skip generic classes - they will be generated when instantiated
                if not item.type_params:
                    self._codegen(item)

        # Fourth pass: generate generic class instantiations
        self._codegen_instantiated_generics()

        # Fifth pass: generate init function for globals if needed
        if self.global_init_list:
            self._codegen_global_init_function()

        # Sixth pass: generate functions
        for item in node.items:
            if isinstance(item, Function):
                self._codegen(item)

    def _codegen_GlobalVarDecl(self, node):
        """Generate LLVM global variable for a top-level pub/priv declaration."""
        # Resolve the Leash type to LLVM type
        llvm_type = self._get_llvm_type(node.var_type)
        # Create the global variable
        gv = ir.GlobalVariable(self.module, llvm_type, name=node.name)
        gv.linkage = "internal"  # module-local; could use external for pub but not needed
        # Store in global symbol table for visibility in functions
        self.global_var_ptrs[node.name] = (gv, node.var_type)
        # If there is an initializer, schedule it for runtime initialization
        if node.value is not None:
            self.global_init_list.append((gv, node.value, node.var_type))

    def _codegen_global_init_function(self):
        """Generate a function that initializes all globals with initializers."""
        init_func = ir.Function(
            self.module, ir.FunctionType(ir.VoidType(), []), name="_leash_init_globals"
        )
        block = init_func.append_basic_block("entry")
        self.builder = ir.IRBuilder(block)
        for gv, init_expr, leash_type in self.global_init_list:
            init_val = self._codegen(init_expr)
            self.builder.store(init_val, gv)
        self.builder.ret_void()
        self.builder = None
        self.init_func = init_func

    def _codegen_instantiated_generics(self):
        """Generate code for all instantiated generic classes and functions.
        This is called after type checking has created the instantiations.
        """
        from .typechecker import TypeChecker

        # Generate code for instantiated generic classes
        # Skip placeholder classes (those with underscore-prefixed type params like _T1)
        for mangled_name, class_node in TypeChecker.instantiated_class_nodes.items():
            if mangled_name not in self.class_symtab:
                # Skip placeholder classes (e.g., Hash__T1__T2)
                # These have underscore-prefixed type parameters
                parts = mangled_name.split("_")
                is_placeholder = any(
                    p.startswith("T") and p[1:].isalnum()
                    for p in parts[1:]
                    if len(p) > 1
                )
                if not is_placeholder:
                    self._codegen(class_node)

        # Generate code for instantiated generic functions
        for mangled_name, func_node in TypeChecker.instantiated_func_nodes.items():
            if mangled_name not in self.func_symtab:
                self._codegen(func_node)

    def _get_llvm_type(self, type_name, is_return=False):
        # Resolve aliases first
        type_name = self._resolve_type_name(type_name)

        if type_name.startswith("*") or type_name.startswith("&"):
            return self._get_llvm_type(type_name[1:]).as_pointer()

        if type_name.endswith("]") and "[" in type_name:
            base = type_name.split("[")[0]
            base_type = self._get_llvm_type(base)
            return ir.LiteralStructType([ir.IntType(64), base_type.as_pointer()])

        if type_name.startswith("vec<") and type_name.endswith(">"):
            inner = type_name[4:-1]
            inner_llvm = self._get_llvm_type(inner)
            return ir.LiteralStructType(
                [inner_llvm.as_pointer(), ir.IntType(64), ir.IntType(64)]
            )

        if type_name in ("int", "uint"):
            return ir.IntType(32)
        elif type_name.startswith("int<"):
            size = int(type_name[4:-1])
            return ir.IntType(size)
        elif type_name.startswith("uint<"):
            size = int(type_name[5:-1])
            return ir.IntType(size)
        elif type_name == "void":
            return ir.VoidType() if is_return else ir.IntType(8).as_pointer()
        elif type_name == "string":
            return ir.IntType(8).as_pointer()
        elif type_name == "char":
            return ir.IntType(8)
        elif type_name == "bool":
            return ir.IntType(1)
        elif type_name == "float":
            return (
                ir.DoubleType()
            )  # standard float maps to standard float literals size
        elif type_name.startswith("float<"):
            size = int(type_name[6:-1])
            if size == 16:
                return ir.HalfType()
            elif size == 32:
                return ir.FloatType()
            elif size == 64:
                return ir.DoubleType()
            elif size == 128:
                return ir.FP128Type()
            else:
                return ir.DoubleType()
        elif type_name in self.union_symtab:
            return self.union_symtab[type_name]["type"]
        elif type_name in self.struct_symtab:
            return self.struct_symtab[type_name]["type"]
        elif type_name in self.enum_symtab:
            return ir.IntType(32)
        elif type_name in self.class_symtab:
            return self.class_symtab[type_name]["type"].as_pointer()
        return ir.IntType(32)  # default fallback

    def _codegen_StructDef(self, node):
        llvm_types = []
        fields = {}
        field_types = {}
        for idx, (fname, ftype) in enumerate(node.fields):
            fields[fname] = idx
            field_types[fname] = ftype
            llvm_types.append(self._get_llvm_type(ftype))

        struct_type = ir.LiteralStructType(llvm_types)
        self.struct_symtab[node.name] = {
            "type": struct_type,
            "fields": fields,
            "field_types": field_types,
        }

    def _codegen_TypeAlias(self, node):
        self.type_aliases[node.name] = node.target_type

    def _codegen_ClassDef(self, node):
        # Vtable pointer type (i8*)
        vtable_ptr_type = ir.IntType(8).as_pointer()

        llvm_types = []  # Will build from parent or start fresh
        fields = {}
        field_types = {}

        # Inherit parent struct type (includes vtable pointer and parent fields)
        if node.parent and node.parent in self.class_symtab:
            parent_info = self.class_symtab[node.parent]
            # Copy all parent types (vtable pointer + parent fields)
            for i, elem_type in enumerate(parent_info["type"].elements):
                llvm_types.append(elem_type)
            # Copy parent field indices (they already include vtable offset)
            for fname, fidx in parent_info["fields"].items():
                fields[fname] = fidx
                field_types[fname] = parent_info["field_types"][fname]

        # If no parent, start with vtable pointer
        if not node.parent:
            llvm_types.append(vtable_ptr_type)

        # Add child's own fields
        idx = len(llvm_types)
        for f in node.fields:
            fields[f.name] = idx
            field_types[f.name] = f.var_type
            llvm_types.append(self._get_llvm_type(f.var_type))
            idx += 1

        struct_type = ir.LiteralStructType(llvm_types)
        self.class_symtab[node.name] = {
            "type": struct_type,
            "fields": fields,
            "field_types": field_types,
            "methods": {},
            "method_static": {},
            "method_imut": {},  # Track which methods are imut (non-overridable)
            "method_order": [],  # Order of virtual methods for vtable
            "vtable_type": None,
            "vtable_global": None,
            "vtable_indices": {},
            "parent": node.parent,
        }

        # Build method order - inherit from parent first
        if node.parent and node.parent in self.class_symtab:
            parent_info = self.class_symtab[node.parent]
            # Copy parent's method order
            for mname in parent_info.get("method_order", []):
                if not parent_info["method_static"].get(mname, False):
                    if mname not in self.class_symtab[node.name]["method_order"]:
                        self.class_symtab[node.name]["method_order"].append(mname)
            # Inherit parent methods
            for mname, mfunc in parent_info["methods"].items():
                self.class_symtab[node.name]["methods"][mname] = mfunc
                self.class_symtab[node.name]["method_static"][mname] = parent_info[
                    "method_static"
                ].get(mname, False)
                self.class_symtab[node.name]["method_imut"][mname] = parent_info[
                    "method_imut"
                ].get(mname, False)

        # Add new methods to method order BEFORE codegen
        for m in node.methods:
            if (
                not m.is_static
                and m.fnc.name not in self.class_symtab[node.name]["method_order"]
            ):
                self.class_symtab[node.name]["method_order"].append(m.fnc.name)

        # Create vtable type and placeholder global BEFORE codegen methods
        # so StructInit can store the vtable pointer
        self._create_vtable_placeholder(node.name)

        # Codegen methods
        for m in node.methods:
            orig_name = m.fnc.name
            m.fnc.name = f"{node.name}_{orig_name}"

            # If not static, prepend 'this' to args
            if not m.is_static:
                new_args = [("this", node.name)] + list(m.fnc.args)
                m.fnc.args = tuple(new_args)

            func = self._codegen_Function(m.fnc)
            self.class_symtab[node.name]["methods"][orig_name] = func
            self.class_symtab[node.name]["method_static"][orig_name] = m.is_static
            self.class_symtab[node.name]["method_imut"][orig_name] = m.is_imut

        # Update vtable with actual function pointers
        self._update_vtable(node.name)

    def _create_vtable_placeholder(self, class_name):
        """Create a vtable type and placeholder global for the given class.
        This is called before methods are codegen'd so StructInit can use it.
        The actual function pointers will be filled in later by _update_vtable.
        """
        cls_info = self.class_symtab[class_name]
        method_order = cls_info.get("method_order", [])

        if not method_order:
            # No virtual methods, no vtable needed
            cls_info["vtable_type"] = None
            cls_info["vtable_global"] = None
            cls_info["vtable_indices"] = {}
            return

        # Build vtable type: struct of function pointers
        ptr_type = ir.IntType(8).as_pointer()
        vtable_elements = []
        vtable_indices = {}

        for i, method_name in enumerate(method_order):
            vtable_indices[method_name] = i
            vtable_elements.append(ptr_type)

        vtable_type = ir.LiteralStructType(vtable_elements)

        # Create placeholder vtable with null pointers
        vtable_values = [ir.Constant(ptr_type, None) for _ in method_order]
        vtable_const = ir.Constant(vtable_type, vtable_values)
        vtable_global = ir.GlobalVariable(
            self.module, vtable_type, f"{class_name}_vtable"
        )
        vtable_global.linkage = "internal"
        vtable_global.global_constant = True
        vtable_global.initializer = vtable_const

        cls_info["vtable_type"] = vtable_type
        cls_info["vtable_global"] = vtable_global
        cls_info["vtable_indices"] = vtable_indices

    def _update_vtable(self, class_name):
        """Update the vtable with actual function pointers after methods are codegen'd."""
        cls_info = self.class_symtab[class_name]
        method_order = cls_info.get("method_order", [])

        if not method_order or not cls_info.get("vtable_global"):
            return

        ptr_type = ir.IntType(8).as_pointer()
        vtable_values = []

        for method_name in method_order:
            # Get the function for this class (could be inherited or overridden)
            func = cls_info["methods"].get(method_name)
            if func:
                func_ptr = func.bitcast(ptr_type)
                vtable_values.append(func_ptr)
            else:
                vtable_values.append(ir.Constant(ptr_type, None))

        # Update the vtable initializer
        vtable_const = ir.Constant(cls_info["vtable_type"], vtable_values)
        cls_info["vtable_global"].initializer = vtable_const

    def _create_vtable(self, class_name):
        """Create a vtable for the given class (combined placeholder + update)."""
        self._create_vtable_placeholder(class_name)
        self._update_vtable(class_name)

    def _codegen_ThisExpr(self, node):
        if "this" not in self.var_symtab:
            raise LeashError("'this' is not available in the current context")
        ptr, type_name = self.var_symtab["this"]
        return self.builder.load(ptr)

    def _codegen_UnionDef(self, node):
        # Compute the max size needed for any variant
        variant_info = {}
        max_size = 0
        for idx, (vname, vtype) in enumerate(node.variants):
            llvm_ty = self._get_llvm_type(vtype)
            # Estimate byte size from LLVM type
            size = self._type_byte_size(llvm_ty)
            if size > max_size:
                max_size = size
            variant_info[vname] = {
                "index": idx,
                "type_name": vtype,
                "llvm_type": llvm_ty,
                "size": size,
            }

        # Union layout: { i32 tag, [max_size x i8] }
        if max_size < 8:
            max_size = 8  # minimum 8 bytes for pointer-sized data
        union_type = ir.LiteralStructType(
            [ir.IntType(32), ir.ArrayType(ir.IntType(8), max_size)]
        )

        self.union_symtab[node.name] = {
            "type": union_type,
            "variants": variant_info,
            "max_size": max_size,
        }

    def _codegen_EnumDef(self, node):
        # Create a global array of strings for member names
        names = []
        for mname in node.members:
            # Create a global string for this member name
            s = bytearray(mname.encode("utf8") + b"\0")
            c_str = ir.Constant(ir.ArrayType(ir.IntType(8), len(s)), s)
            g = ir.GlobalVariable(
                self.module,
                c_str.type,
                name=self.module.get_unique_name(f"enum_{node.name}_{mname}"),
            )
            g.linkage = "internal"
            g.global_constant = True
            g.initializer = c_str
            # Store pointer to this global string
            names.append(
                self.builder.bitcast(g, ir.IntType(8).as_pointer())
                if self.builder
                else g.bitcast(ir.IntType(8).as_pointer())
            )

        # Now create an array of these pointers
        ptr_type = ir.IntType(8).as_pointer()
        arr_type = ir.ArrayType(ptr_type, len(names))
        c_names_arr = ir.Constant(arr_type, names)

        g_names = ir.GlobalVariable(
            self.module, arr_type, name=f"enum_names_{node.name}"
        )
        g_names.linkage = "internal"
        g_names.global_constant = True
        g_names.initializer = c_names_arr

        self.enum_symtab[node.name] = {"members": node.members, "names_arr": g_names}

    def _type_byte_size(self, llvm_ty):
        """Estimate byte size of an LLVM type."""
        if isinstance(llvm_ty, ir.IntType):
            return max(1, llvm_ty.width // 8)
        elif isinstance(llvm_ty, ir.FloatType):
            return 4
        elif isinstance(llvm_ty, ir.DoubleType):
            return 8
        elif isinstance(llvm_ty, ir.HalfType):
            return 2
        elif isinstance(llvm_ty, ir.PointerType):
            return 8  # 64-bit
        elif isinstance(llvm_ty, ir.LiteralStructType):
            return sum(self._type_byte_size(e) for e in llvm_ty.elements)
        elif isinstance(llvm_ty, ir.ArrayType):
            return self._type_byte_size(llvm_ty.element) * llvm_ty.count
        return 8  # assume pointer size as fallback

    def _codegen_Function(self, node):
        # Start with globals in scope (they can be shadowed by locals)
        self.var_symtab = self.global_var_ptrs.copy() if hasattr(self, 'global_var_ptrs') else {}
        self.current_func_ret_type_name = node.return_type

        # Determine return type
        ret_type = self._get_llvm_type(node.return_type, is_return=True)

        # Determine argument types
        arg_types = []
        for name, typ in node.args:
            arg_types.append(self._get_llvm_type(typ, is_return=False))

        func_type = ir.FunctionType(ret_type, arg_types)

        # Main function usually needs to be i32 main() in standard C compilation
        name = node.name

        is_main_with_args = False
        if name == "main":
            if len(node.args) == 1 and node.args[0][1] == "string[]":
                is_main_with_args = True
                arg_types = [ir.IntType(32), ir.IntType(8).as_pointer().as_pointer()]

            if node.return_type == "void":
                ret_type = ir.IntType(32)

            func_type = ir.FunctionType(ret_type, arg_types)

        func = ir.Function(self.module, func_type, name=name)
        self.func_symtab[name] = func

        block = func.append_basic_block(name="entry")
        self.builder = ir.IRBuilder(block)

        if name == "main":
            self.builder.call(self.gc_init, [])
            # Call global initializer if any globals need runtime init
            if self.init_func:
                self.builder.call(self.init_func, [])

        # Allocate args
        if name == "main" and len(node.args) == 1 and node.args[0][1] == "string[]":
            # We have i32 argc, i8** argv
            argc_val = func.args[0]
            argv_val = func.args[1]
            argc_val.name = "argc"
            argv_val.name = "argv"

            # Create a string[] slice struct [i64 len, i8** ptr]
            leash_arg_name = node.args[0][0]
            slice_type = ir.LiteralStructType(
                [ir.IntType(64), ir.IntType(8).as_pointer().as_pointer()]
            )
            slice_val = ir.Constant(slice_type, ir.Undefined)
            slice_val = self.builder.insert_value(
                slice_val, self.builder.zext(argc_val, ir.IntType(64)), 0
            )
            slice_val = self.builder.insert_value(slice_val, argv_val, 1)

            ptr = self.builder.alloca(slice_type)
            self.builder.store(slice_val, ptr)
            self.var_symtab[leash_arg_name] = (ptr, "string[]")
        else:
            for i, (arg_name, arg_type_name) in enumerate(node.args):
                func.args[i].name = arg_name
                ptr = self.builder.alloca(func.args[i].type)
                self.builder.store(func.args[i], ptr)
                self.var_symtab[arg_name] = (ptr, arg_type_name)

        # SAMM initialization removed (Boeing GC is now used)

        for stmt in node.body:
            self._codegen(stmt)
            if self.builder.block.is_terminated:
                break

        if not self.builder.block.is_terminated:
            self._emit_cleanup()  # Auto-free everything
            if name == "main":
                self.builder.ret(ir.Constant(ir.IntType(32), 0))
            elif node.return_type == "void":
                self.builder.ret_void()
            else:
                # If a non-void function reaches the end without a return,
                # it's technically undefined behavior in Leash, but for
                # valid LLVM IR we must terminate the block.
                # 'unreachable' is appropriate here.
                self.builder.unreachable()

        # Reset tracking
        self.current_func_alloc_counter = None
        return func

    def _codegen_ReturnStatement(self, node):
        old_target = self.current_target_type
        self.current_target_type = getattr(self, "current_func_ret_type_name", None)
        val = self._codegen(node.value)
        self.current_target_type = old_target

        # Cast to return type
        ret_type = self.builder.function.type.pointee.return_type
        if not isinstance(ret_type, ir.VoidType):
            val = self._emit_cast(val, ret_type)

        self._emit_cleanup(ret_val=val)  # Auto-free everything EXCEPT the return value
        if isinstance(ret_type, ir.VoidType):
            self.builder.ret_void()
        else:
            self.builder.ret(val)

    def _codegen_StopStatement(self, node):
        # stop (break) - jump to the loop's merge block
        if not self.loop_stack:
            raise LeashError(
                "`stop` can only be used inside a loop",
                node.line,
                node.col,
                tip="`stop` (break) is used to exit a loop early. It can only be used within `while`, `for`, `do-while`, or `foreach` loops.",
            )
        break_bb, _ = self.loop_stack[-1]
        self.builder.branch(break_bb)

    def _codegen_ContinueStatement(self, node):
        # continue - jump to the loop's continue block (usually condition or update)
        if not self.loop_stack:
            raise LeashError(
                "`continue` can only be used inside a loop",
                node.line,
                node.col,
                tip="`continue` skips to the next iteration of a loop. It can only be used within `while`, `for`, `do-while`, or `foreach` loops.",
            )
        _, continue_bb = self.loop_stack[-1]
        self.builder.branch(continue_bb)

    def _codegen_VariableDecl(self, node):
        resolved_type = self._resolve_type_name(node.var_type)

        # Check if the variable type is a union
        if resolved_type in self.union_symtab:
            union_info = self.union_symtab[resolved_type]
            union_type = union_info["type"]
            ptr = self.builder.alloca(union_type)
            self.var_symtab[node.name] = (ptr, resolved_type)
            # Auto-assign the value into the union
            val = self._codegen(node.value)
            self._union_auto_store(ptr, val, union_info)
            return

        # Check if we should pass the target type to the expression (useful for ArrayInit)
        old_target = self.current_target_type
        self.current_target_type = node.var_type
        if node.value is None:
            val = self._emit_default_value(node.var_type)
        else:
            val = self._codegen(node.value)
        self.current_target_type = old_target

        # If the declared type maps to a different LLVM type, cast
        target_llvm = self._get_llvm_type(node.var_type)
        val = self._emit_cast(val, target_llvm)
        ptr = self.builder.alloca(val.type)
        self.builder.store(val, ptr)
        self.var_symtab[node.name] = (ptr, node.var_type)

    def _union_auto_store(self, union_ptr, val, union_info):
        """Store a value into a union, auto-detecting the matching variant by LLVM type."""
        matched_idx = None
        for vname, vdata in union_info["variants"].items():
            if vdata["llvm_type"] == val.type:
                matched_idx = vdata["index"]
                break
        # Fallback: try to match int types by checking if both are IntType
        if matched_idx is None:
            for vname, vdata in union_info["variants"].items():
                if isinstance(vdata["llvm_type"], ir.IntType) and isinstance(
                    val.type, ir.IntType
                ):
                    val = self._emit_cast(val, vdata["llvm_type"])
                    matched_idx = vdata["index"]
                    break
        # Fallback: try to match float types
        if matched_idx is None:
            for vname, vdata in union_info["variants"].items():
                if isinstance(
                    vdata["llvm_type"], (ir.FloatType, ir.DoubleType)
                ) and isinstance(val.type, (ir.FloatType, ir.DoubleType)):
                    val = self._emit_cast(val, vdata["llvm_type"])
                    matched_idx = vdata["index"]
                    break
        if matched_idx is None:
            raise LeashError(f"Cannot store value of this type into the union")

        # Store tag
        tag_ptr = self.builder.gep(
            union_ptr,
            [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 0)],
            inbounds=True,
        )
        self.builder.store(ir.Constant(ir.IntType(32), matched_idx), tag_ptr)
        # Store value into data region
        data_ptr = self.builder.gep(
            union_ptr,
            [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 1)],
            inbounds=True,
        )
        typed_ptr = self.builder.bitcast(data_ptr, val.type.as_pointer())
        self.builder.store(val, typed_ptr)

    def _codegen_Assignment(self, node):
        from .ast_nodes import MemberAccess, Identifier

        # 1. Specialized logic: Assigning to a specific union variant (e.g., f.i = 10, s.y.b = 3.14)
        if isinstance(node.target, MemberAccess):
            try:
                # Get the base (the union itself)
                base_ptr, type_name = self._codegen_lvalue(node.target.expr)
                resolved = self._resolve_type_name(type_name)

                if resolved in self.union_symtab:
                    union_info = self.union_symtab[resolved]
                    member = node.target.member

                    if member in union_info["variants"]:
                        vdata = union_info["variants"][member]
                        val = self._codegen(node.value)
                        val = self._emit_cast(val, vdata["llvm_type"])

                        # Store tag
                        tag_ptr = self.builder.gep(
                            base_ptr,
                            [
                                ir.Constant(ir.IntType(32), 0),
                                ir.Constant(ir.IntType(32), 0),
                            ],
                            inbounds=True,
                        )
                        self.builder.store(
                            ir.Constant(ir.IntType(32), vdata["index"]), tag_ptr
                        )

                        # Store data
                        data_ptr = self.builder.gep(
                            base_ptr,
                            [
                                ir.Constant(ir.IntType(32), 0),
                                ir.Constant(ir.IntType(32), 1),
                            ],
                            inbounds=True,
                        )
                        typed_ptr = self.builder.bitcast(
                            data_ptr, vdata["llvm_type"].as_pointer()
                        )
                        self.builder.store(val, typed_ptr)
                        return
                    elif member == "cur":
                        raise LeashError(
                            "Cannot assign to '.cur'. Assign to a specific union variant or directly to the union."
                        )
            except LeashError:
                pass  # Fallback to standard assignment

        # 2. General Assignment (handles Identifiers, IndexAccess, and Struct Members)
        ptr, target_type_name = self._codegen_lvalue(node.target)
        resolved_target_type = self._resolve_type_name(target_type_name)

        # 3. Auto-detect Union variant if target is a union (e.g., f = 10, s.y = 3.14)
        if resolved_target_type in self.union_symtab:
            val = self._codegen(node.value)
            self._union_auto_store(ptr, val, self.union_symtab[resolved_target_type])
            return

        # 4. Standard Typed Assignment
        old_target = self.current_target_type
        self.current_target_type = target_type_name
        val = self._codegen(node.value)
        self.current_target_type = old_target

        target_llvm = self._get_llvm_type(target_type_name)
        val = self._emit_cast(val, target_llvm)
        self.builder.store(val, ptr)

    def _codegen_lvalue(self, node):
        from .ast_nodes import (
            Identifier,
            MemberAccess,
            IndexAccess,
            ThisExpr,
            UnaryOp,
            PointerMemberAccess,
        )

        if isinstance(node, Identifier):
            if node.name not in self.var_symtab:
                if node.name in self.class_symtab:
                    return None, node.name  # Static access
                # Check if this is a generic class name that needs instantiation
                from .typechecker import TypeChecker

                # Find a concrete instantiation (prefer one without placeholder types)
                for inst_name in TypeChecker.instantiated_class_nodes:
                    if inst_name.startswith(node.name + "_") and "_T" not in inst_name:
                        return None, inst_name
                # Fallback to any instantiation
                for inst_name in TypeChecker.instantiated_class_nodes:
                    if inst_name.startswith(node.name + "_"):
                        return None, inst_name
                raise LeashError(f"Undefined variable: '{node.name}'")
            ptr, type_name = self.var_symtab[node.name]
            resolved = self._resolve_type_name(type_name)
            while resolved.startswith("&"):
                ptr = self.builder.load(ptr)
                resolved = resolved[1:]
            return ptr, resolved
        elif isinstance(node, MemberAccess):
            base_ptr, type_name = self._codegen_lvalue(node.expr)
            resolved = self._resolve_type_name(type_name)

            instance_ptr = base_ptr

            if resolved in self.struct_symtab:
                struct_info = self.struct_symtab[resolved]
                idx = struct_info["fields"].get(node.member)
                if idx is None:
                    raise LeashError(
                        f"Struct '{resolved}' has no member named '{node.member}'"
                    )
                field_type_name = struct_info["field_types"][node.member]
                return self.builder.gep(
                    instance_ptr,
                    [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), idx)],
                ), field_type_name
            elif resolved in self.class_symtab:
                # Classes are reference types (pointers). LOAD the pointer first.
                instance_ptr = self.builder.load(instance_ptr)
                cls_info = self.class_symtab[resolved]
                idx = cls_info["fields"].get(node.member)
                if idx is None:
                    raise LeashError(
                        f"Class '{resolved}' has no field named '{node.member}'"
                    )

                field_type_name = cls_info["field_types"][node.member]
                return self.builder.gep(
                    instance_ptr,
                    [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), idx)],
                ), field_type_name
            elif resolved in self.union_symtab:
                union_info = self.union_symtab[resolved]
                if node.member in union_info["variants"]:
                    vdata = union_info["variants"][node.member]
                    data_ptr = self.builder.gep(
                        instance_ptr,
                        [
                            ir.Constant(ir.IntType(32), 0),
                            ir.Constant(ir.IntType(32), 1),
                        ],
                        inbounds=True,
                    )
                    typed_ptr = self.builder.bitcast(
                        data_ptr, vdata["llvm_type"].as_pointer()
                    )
                    return typed_ptr, vdata["type_name"]
                elif node.member == "cur":
                    raise LeashError(
                        "Cannot use '.cur' as an l-value. Assign directly to the union or a specific variant member."
                    )
                else:
                    raise LeashError(
                        f"Union '{resolved}' has no variant named '{node.member}'"
                    )
            else:
                raise LeashError(
                    f"Cannot access member '{node.member}': '{type_name}' is not a struct or union"
                )
        elif isinstance(node, PointerMemberAccess):
            ptr = self._codegen(node.expr)
            type_name = self._get_leash_type_name(node.expr)
            resolved = self._resolve_type_name(type_name)
            underlying = self._resolve_type_name(resolved[1:])

            if underlying in self.struct_symtab:
                struct_info = self.struct_symtab[underlying]
                idx = struct_info["fields"][node.member]
                return self.builder.gep(
                    ptr,
                    [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), idx)],
                ), struct_info["field_types"][node.member]
            elif underlying in self.class_symtab:
                cls_info = self.class_symtab[underlying]
                idx = cls_info["fields"][node.member]
                return self.builder.gep(
                    ptr,
                    [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), idx)],
                ), cls_info["field_types"][node.member]
            raise LeashError(f"Type '{underlying}' is not a struct or class")
        elif isinstance(node, UnaryOp) and node.op == "*":
            ptr = self._codegen(node.expr)
            type_name = self._get_leash_type_name(node.expr)
            resolved = self._resolve_type_name(type_name)
            return ptr, resolved[1:]
        elif isinstance(node, IndexAccess):
            slice_ptr, slice_type_name = self._codegen_lvalue(node.expr)
            if slice_type_name == "string":
                str_ptr = self.builder.load(slice_ptr)
                idx_val = self._codegen(node.index)
                ptr = self.builder.gep(str_ptr, [idx_val], inbounds=True)
                return (ptr, "char")
            slice_val = self.builder.load(slice_ptr)
            data_ptr = self.builder.extract_value(slice_val, 1)
            idx_val = self._codegen(node.index)
            ptr = self.builder.gep(data_ptr, [idx_val], inbounds=True)
            elem_type_name = (
                slice_type_name.split("[")[0] if "[" in slice_type_name else "int"
            )
            return (ptr, elem_type_name)
        elif isinstance(node, ThisExpr):
            if "this" not in self.var_symtab:
                raise LeashError("'this' is not available in the current context")
            return self.var_symtab["this"]
        else:
            raise LeashError(f"Invalid l-value: {type(node).__name__}")

    def _codegen_PointerMemberAccess(self, node):
        ptr = self._codegen(node.expr)
        type_name = self._get_leash_type_name(node.expr)
        resolved = self._resolve_type_name(type_name)

        if resolved.startswith("*") or resolved.startswith("&"):
            underlying = self._resolve_type_name(resolved[1:])
        else:
            raise LeashError(
                "Cannot use '->' on non-pointer type",
                line=getattr(node, "line", None),
                col=getattr(node, "col", None),
            )

        if underlying in self.struct_symtab:
            struct_info = self.struct_symtab[underlying]
            idx = struct_info["fields"].get(node.member)
            if idx is None:
                raise LeashError(
                    f"Struct '{underlying}' has no member named '{node.member}'"
                )
            res_ptr = self.builder.gep(
                ptr, [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), idx)]
            )
            return self.builder.load(res_ptr)
        elif underlying in self.class_symtab:
            cls_info = self.class_symtab[underlying]
            idx = cls_info["fields"].get(node.member)
            if idx is None:
                raise LeashError(
                    f"Class '{underlying}' has no field named '{node.member}'"
                )
            res_ptr = self.builder.gep(
                ptr, [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), idx)]
            )
            return self.builder.load(res_ptr)

        raise LeashError(
            f"Type '{underlying}' is not a struct or class",
            line=getattr(node, "line", None),
            col=getattr(node, "col", None),
        )

    def _codegen_ExpressionStatement(self, node):
        self._codegen(node.expr)

    def _codegen_ShowStatement(self, node):
        from .ast_nodes import Identifier, MemberAccess

        # Check if any arg is a union variable or union .cur — those need special per-variant printing
        # First, collect which args are "union-cur" args
        union_arg_indices = set()
        for i, arg_node in enumerate(node.args):
            union_name = self._get_union_type_for_node(arg_node)
            if union_name:
                union_arg_indices.add(i)

        if not union_arg_indices:
            # No union args: standard show path
            self._show_standard(node.args)
            return

        # For simplicity, handle the common case: all non-union args come before or after union args
        # We'll print non-union prefix first, then branch for union args, then non-union suffix
        # Actually, the simplest correct approach: print all non-union args as prefix,
        # then for each union arg, emit a per-variant branching printf

        # Split into segments: print non-union prefix
        prefix_args = []
        for i, arg_node in enumerate(node.args):
            if i in union_arg_indices:
                break
            prefix_args.append(arg_node)

        if prefix_args:
            self._show_standard(prefix_args, newline=False)

        # Now handle each union arg
        for i, arg_node in enumerate(node.args):
            if i not in union_arg_indices:
                if i > max(union_arg_indices):
                    # suffix non-union args handled below
                    continue
                continue
            union_name = self._get_union_type_for_node(arg_node)
            union_info = self.union_symtab[union_name]

            # Get the union pointer
            if isinstance(arg_node, Identifier):
                ptr, _ = self.var_symtab[arg_node.name]
            elif (
                isinstance(arg_node, MemberAccess)
                and arg_node.member == "cur"
                and isinstance(arg_node.expr, Identifier)
            ):
                ptr, _ = self.var_symtab[arg_node.expr.name]
            else:
                # fallback
                self._show_standard([arg_node], newline=False)
                continue

            tag_ptr = self.builder.gep(
                ptr,
                [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 0)],
                inbounds=True,
            )
            tag_val = self.builder.load(tag_ptr)
            data_ptr = self.builder.gep(
                ptr,
                [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 1)],
                inbounds=True,
            )

            self._union_show_branched(tag_val, data_ptr, union_info)

        # suffix non-union args
        suffix_args = []
        if union_arg_indices:
            last_union = max(union_arg_indices)
            for i in range(last_union + 1, len(node.args)):
                suffix_args.append(node.args[i])

        if suffix_args:
            self._show_standard(suffix_args, newline=True)
        else:
            # Print newline
            self._print_raw("\n")

    def _get_union_type_for_node(self, arg_node):
        """Check if an AST node refers to a union variable or union.cur."""
        from .ast_nodes import Identifier, MemberAccess

        if isinstance(arg_node, Identifier):
            if arg_node.name in self.var_symtab:
                _, type_name = self.var_symtab[arg_node.name]
                resolved = self._resolve_type_name(type_name)
                if resolved in self.union_symtab:
                    return resolved
        elif isinstance(arg_node, MemberAccess) and arg_node.member == "cur":
            if (
                isinstance(arg_node.expr, Identifier)
                and arg_node.expr.name in self.var_symtab
            ):
                _, type_name = self.var_symtab[arg_node.expr.name]
                resolved = self._resolve_type_name(type_name)
                if resolved in self.union_symtab:
                    return resolved
        return None

    def _print_raw(self, text):
        """Print a raw string via printf."""
        s = bytearray(text.encode("utf8") + b"\0")
        c_str = ir.Constant(ir.ArrayType(ir.IntType(8), len(s)), s)
        g = ir.GlobalVariable(
            self.module, c_str.type, name=self.module.get_unique_name("raw")
        )
        g.linkage = "internal"
        g.global_constant = True
        g.initializer = c_str
        ptr = self.builder.bitcast(g, ir.IntType(8).as_pointer())
        self.builder.call(self.printf, [ptr])

    def _union_show_branched(self, tag_val, data_ptr, union_info):
        """Print the current union value by branching on the tag and calling printf per variant."""
        variants = list(union_info["variants"].items())
        merge_bb = self.builder.function.append_basic_block("union_show_merge")

        var_bbs = []
        for vname, vdata in variants:
            bb = self.builder.function.append_basic_block(f"union_show_{vname}")
            var_bbs.append(bb)

        # Build if-else chain
        for i, (vname, vdata) in enumerate(variants[:-1]):
            cmp = self.builder.icmp_signed(
                "==", tag_val, ir.Constant(ir.IntType(32), vdata["index"])
            )
            next_check = self.builder.function.append_basic_block(
                f"union_show_check_{i + 1}"
            )
            self.builder.cbranch(cmp, var_bbs[i], next_check)
            self.builder.position_at_end(next_check)
        self.builder.branch(var_bbs[-1])

        # In each variant BB, printf the loaded value with appropriate format
        for i, (vname, vdata) in enumerate(variants):
            self.builder.position_at_end(var_bbs[i])
            typed_ptr = self.builder.bitcast(data_ptr, vdata["llvm_type"].as_pointer())
            loaded = self.builder.load(typed_ptr)

            # Determine format
            fmt, val = self._format_value(loaded)
            fmt_bytes = bytearray(fmt.encode("utf8") + b"\0")
            c_fmt = ir.Constant(ir.ArrayType(ir.IntType(8), len(fmt_bytes)), fmt_bytes)
            g_fmt = ir.GlobalVariable(
                self.module, c_fmt.type, name=self.module.get_unique_name("ufmt")
            )
            g_fmt.linkage = "internal"
            g_fmt.global_constant = True
            g_fmt.initializer = c_fmt
            fmt_ptr = self.builder.bitcast(g_fmt, ir.IntType(8).as_pointer())
            self.builder.call(self.printf, [fmt_ptr, val])
            self.builder.branch(merge_bb)

        self.builder.position_at_end(merge_bb)

    def _format_value(self, val):
        """Return (format_str, possibly_cast_val) for a single value."""
        if isinstance(val.type, ir.IntType):
            width = val.type.width
            if width < 32:
                val = self.builder.zext(val, ir.IntType(32))
            if width == 64:
                return ("%lld", val)
            elif width == 8:
                return ("%c", val)
            else:
                return ("%d", val)
        elif isinstance(val.type, ir.FloatType):
            val = self.builder.fpext(val, ir.DoubleType())
            return ("%f", val)
        elif isinstance(val.type, ir.DoubleType):
            return ("%f", val)
        elif isinstance(val.type, ir.PointerType):
            return ("%s", val)
        return ("%s", val)

    def _show_standard(self, arg_nodes, newline=True):
        """Standard show() implementation for non-union args."""
        format_str = ""
        args = []
        for arg_node in arg_nodes:
            val = self._codegen(arg_node)

            is_char_slice = False
            if (
                isinstance(val.type, ir.LiteralStructType)
                and len(val.type.elements) == 2
            ):
                elt = val.type.elements[1]
                if (
                    hasattr(ir, "PointerType")
                    and getattr(ir, "PointerType") is not None
                    and isinstance(elt, ir.PointerType)
                    and getattr(elt, "pointee", None) == ir.IntType(8)
                ) or (
                    getattr(elt, "is_pointer", False)
                    and getattr(elt, "pointee", None) == ir.IntType(8)
                ):
                    is_char_slice = True

            if is_char_slice:
                format_str += "%.*s"
                length = self.builder.extract_value(val, 0)
                args.append(self.builder.trunc(length, ir.IntType(32)))
                args.append(self.builder.extract_value(val, 1))
                continue

            if isinstance(val.type, ir.IntType):
                width = val.type.width
                if width < 32:
                    val = self.builder.zext(val, ir.IntType(32))

                if width == 64:
                    format_str += "%lld"
                elif width == 8:
                    format_str += "%c"
                else:
                    format_str += "%d"
            elif isinstance(val.type, ir.FloatType):
                val = self.builder.fpext(val, ir.DoubleType())
                format_str += "%f"
            elif isinstance(val.type, ir.DoubleType):
                format_str += "%f"
            elif isinstance(val.type, ir.PointerType):
                if val.type.pointee == ir.IntType(8):
                    format_str += "%s"
                else:
                    format_str += "%p"
            else:
                format_str += "%s"  # fallback
            args.append(val)

        if newline:
            format_str += "\n"

        # Create global string for format
        fmt_bytes = bytearray(format_str.encode("utf8") + b"\0")
        c_fmt = ir.Constant(ir.ArrayType(ir.IntType(8), len(fmt_bytes)), fmt_bytes)
        global_fmt = ir.GlobalVariable(
            self.module, c_fmt.type, name=self.module.get_unique_name("fmt")
        )
        global_fmt.linkage = "internal"
        global_fmt.global_constant = True
        global_fmt.initializer = c_fmt

        fmt_ptr = self.builder.bitcast(global_fmt, ir.IntType(8).as_pointer())
        self.builder.call(self.printf, [fmt_ptr] + args)

    def _cast_bool(self, cond_val):
        if not isinstance(cond_val.type, ir.IntType) or cond_val.type.width != 1:
            # Handle pointer types (compare with null)
            if isinstance(cond_val.type, ir.PointerType):
                null_ptr = ir.Constant(cond_val.type, None)
                return self.builder.icmp_unsigned("!=", cond_val, null_ptr)
            zero = ir.Constant(cond_val.type, 0)
            if isinstance(cond_val.type, (ir.FloatType, ir.DoubleType)):
                return self.builder.fcmp_ordered("!=", cond_val, zero)
            else:
                return self.builder.icmp_signed("!=", cond_val, zero)
        return cond_val

    def _codegen_IfStatement(self, node):
        cond_val = self._cast_bool(self._codegen(node.condition))
        then_bb = self.builder.function.append_basic_block("then")

        also_bbs = []
        for _ in node.also_blocks:
            also_cond_bb = self.builder.function.append_basic_block("also_cond")
            also_body_bb = self.builder.function.append_basic_block("also_body")
            also_bbs.append((also_cond_bb, also_body_bb))

        else_bb = (
            self.builder.function.append_basic_block("else")
            if node.else_block
            else None
        )
        merge_bb = self.builder.function.append_basic_block("if_merge")

        next_bb = also_bbs[0][0] if also_bbs else (else_bb if else_bb else merge_bb)
        self.builder.cbranch(cond_val, then_bb, next_bb)

        self.builder.position_at_end(then_bb)
        for stmt in node.then_block:
            self._codegen(stmt)
            if self.builder.block.is_terminated:
                break
        if not self.builder.block.is_terminated:
            self.builder.branch(merge_bb)

        for i, (also_cond, also_body) in enumerate(node.also_blocks):
            cond_bb, body_bb = also_bbs[i]
            self.builder.position_at_end(cond_bb)
            a_cond_val = self._cast_bool(self._codegen(also_cond))
            next_also = (
                also_bbs[i + 1][0]
                if i + 1 < len(also_bbs)
                else (else_bb if else_bb else merge_bb)
            )
            self.builder.cbranch(a_cond_val, body_bb, next_also)

            self.builder.position_at_end(body_bb)
            for stmt in also_body:
                self._codegen(stmt)
                if self.builder.block.is_terminated:
                    break
            if not self.builder.block.is_terminated:
                self.builder.branch(merge_bb)

        if else_bb:
            self.builder.position_at_end(else_bb)
            for stmt in node.else_block:
                self._codegen(stmt)
                if self.builder.block.is_terminated:
                    break
            if not self.builder.block.is_terminated:
                self.builder.branch(merge_bb)

        self.builder.position_at_end(merge_bb)

    def _codegen_WhileStatement(self, node):
        cond_bb = self.builder.function.append_basic_block("while_cond")
        body_bb = self.builder.function.append_basic_block("while_body")
        merge_bb = self.builder.function.append_basic_block("while_merge")
        continue_bb = cond_bb  # continue jumps to condition check
        break_bb = merge_bb  # break jumps to merge

        # Push loop context for nested stop/continue
        self.loop_stack.append((break_bb, continue_bb))

        self.builder.branch(cond_bb)
        self.builder.position_at_end(cond_bb)

        cond_val = self._cast_bool(self._codegen(node.condition))
        self.builder.cbranch(cond_val, body_bb, merge_bb)

        self.builder.position_at_end(body_bb)
        for stmt in node.body:
            self._codegen(stmt)
            if self.builder.block.is_terminated:
                break
        if not self.builder.block.is_terminated:
            self.builder.branch(cond_bb)

        # Pop loop context
        self.loop_stack.pop()

        self.builder.position_at_end(merge_bb)

    def _codegen_ForStatement(self, node):
        self._codegen(node.init)
        cond_bb = self.builder.function.append_basic_block("for_cond")
        body_bb = self.builder.function.append_basic_block("for_body")
        merge_bb = self.builder.function.append_basic_block("for_merge")
        continue_bb = cond_bb  # continue jumps to condition check (after step)
        break_bb = merge_bb  # break jumps to merge

        # Push loop context for nested stop/continue
        self.loop_stack.append((break_bb, continue_bb))

        self.builder.branch(cond_bb)
        self.builder.position_at_end(cond_bb)
        cond_val = self._cast_bool(self._codegen(node.condition))
        self.builder.cbranch(cond_val, body_bb, merge_bb)

        self.builder.position_at_end(body_bb)
        for stmt in node.body:
            self._codegen(stmt)
            if self.builder.block.is_terminated:
                break
        if not self.builder.block.is_terminated:
            self._codegen(node.step)
            self.builder.branch(cond_bb)

        # Pop loop context
        self.loop_stack.pop()

        self.builder.position_at_end(merge_bb)

    def _codegen_DoWhileStatement(self, node):
        body_bb = self.builder.function.append_basic_block("do_body")
        cond_bb = self.builder.function.append_basic_block("do_cond")
        merge_bb = self.builder.function.append_basic_block("do_merge")
        continue_bb = cond_bb  # continue jumps to condition check
        break_bb = merge_bb  # break jumps to merge

        # Push loop context for nested stop/continue
        self.loop_stack.append((break_bb, continue_bb))

        self.builder.branch(body_bb)
        self.builder.position_at_end(body_bb)
        for stmt in node.body:
            self._codegen(stmt)
            if self.builder.block.is_terminated:
                break
        if not self.builder.block.is_terminated:
            self.builder.branch(cond_bb)

        # Pop loop context
        self.loop_stack.pop()

        self.builder.position_at_end(cond_bb)
        cond_val = self._cast_bool(self._codegen(node.condition))
        self.builder.cbranch(cond_val, body_bb, merge_bb)
        self.builder.position_at_end(merge_bb)

    def _codegen_ForeachStructStatement(self, node):
        struct_ptr, struct_type_name = self._codegen_lvalue(node.struct_expr)
        struct_ir_type = struct_ptr.type.pointee

        struct_meta = None
        for sname, sdata in self.struct_symtab.items():
            if sdata["type"] == struct_ir_type:
                struct_meta = sdata
                break

        if not struct_meta:
            raise LeashError(
                "Cannot use 'foreach' with 'in<struct>' on a non-struct type"
            )

        from .ast_nodes import StringLiteral

        for i, (field_name, field_idx) in enumerate(struct_meta["fields"].items()):
            name_val = self._codegen(StringLiteral(field_name))
            name_ptr = self.builder.alloca(name_val.type, name=node.name_var)
            self.builder.store(name_val, name_ptr)
            self.var_symtab[node.name_var] = (name_ptr, "string")

            field_ptr = self.builder.gep(
                struct_ptr,
                [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), i)],
            )
            field_val = self.builder.load(field_ptr)
            val_ptr = self.builder.alloca(field_val.type, name=node.value_var)
            self.builder.store(field_val, val_ptr)
            self.var_symtab[node.value_var] = (
                val_ptr,
                struct_meta["field_types"][field_name],
            )

            for stmt in node.body:
                self._codegen(stmt)
                if self.builder.block.is_terminated:
                    break

    def _codegen_ForeachArrayStatement(self, node):
        try:
            _, full_type_name = self._codegen_lvalue(node.array_expr)
            elem_type_name = (
                full_type_name.split("[")[0] if "[" in full_type_name else "int"
            )
        except:
            elem_type_name = "int"

        slice_val = self._codegen(node.array_expr)

        length_val = self.builder.extract_value(slice_val, 0)
        data_ptr = self.builder.extract_value(slice_val, 1)

        idx_ptr = self.builder.alloca(ir.IntType(64), name=node.index_var)
        self.builder.store(ir.Constant(ir.IntType(64), 0), idx_ptr)
        self.var_symtab[node.index_var] = (idx_ptr, "int")

        elem_type = data_ptr.type.pointee
        val_ptr = self.builder.alloca(elem_type, name=node.value_var)
        self.var_symtab[node.value_var] = (val_ptr, elem_type_name)

        cond_bb = self.builder.function.append_basic_block("foreach_cond")
        body_bb = self.builder.function.append_basic_block("foreach_body")
        merge_bb = self.builder.function.append_basic_block("foreach_merge")
        continue_bb = cond_bb  # continue goes back to condition
        break_bb = merge_bb  # break exits to merge

        # Push loop context for nested stop/continue
        self.loop_stack.append((break_bb, continue_bb))

        self.builder.branch(cond_bb)
        self.builder.position_at_end(cond_bb)

        curr_idx = self.builder.load(idx_ptr)
        cmp_res = self.builder.icmp_signed("<", curr_idx, length_val)
        self.builder.cbranch(cmp_res, body_bb, merge_bb)

        self.builder.position_at_end(body_bb)

        curr_elem_ptr = self.builder.gep(data_ptr, [curr_idx], inbounds=True)
        curr_elem_val = self.builder.load(curr_elem_ptr)
        self.builder.store(curr_elem_val, val_ptr)

        for stmt in node.body:
            self._codegen(stmt)
            if self.builder.block.is_terminated:
                break

        if not self.builder.block.is_terminated:
            next_idx = self.builder.add(curr_idx, ir.Constant(ir.IntType(64), 1))
            self.builder.store(next_idx, idx_ptr)
            self.builder.branch(cond_bb)

        # Pop loop context
        self.loop_stack.pop()

        self.builder.position_at_end(merge_bb)

    def _codegen_ForeachStringStatement(self, node):
        str_val = self._codegen(node.string_expr)
        length_val = self.builder.call(self.strlen, [str_val])

        idx_ptr = self.builder.alloca(ir.IntType(64), name=node.index_var)
        self.builder.store(ir.Constant(ir.IntType(64), 0), idx_ptr)
        self.var_symtab[node.index_var] = (
            idx_ptr,
            "int",
        )  # it's i64 now but symtab just needs a type name

        val_ptr = self.builder.alloca(ir.IntType(8), name=node.char_var)
        self.var_symtab[node.char_var] = (val_ptr, "char")

        cond_bb = self.builder.function.append_basic_block("foreach_str_cond")
        body_bb = self.builder.function.append_basic_block("foreach_str_body")
        merge_bb = self.builder.function.append_basic_block("foreach_str_merge")
        continue_bb = cond_bb  # continue goes back to condition
        break_bb = merge_bb  # break exits to merge

        # Push loop context for nested stop/continue
        self.loop_stack.append((break_bb, continue_bb))

        self.builder.branch(cond_bb)
        self.builder.position_at_end(cond_bb)

        curr_idx = self.builder.load(idx_ptr)
        cmp_res = self.builder.icmp_signed("<", curr_idx, length_val)
        self.builder.cbranch(cmp_res, body_bb, merge_bb)

        self.builder.position_at_end(body_bb)

        curr_char_ptr = self.builder.gep(str_val, [curr_idx], inbounds=True)
        curr_char_val = self.builder.load(curr_char_ptr)
        self.builder.store(curr_char_val, val_ptr)

        for stmt in node.body:
            self._codegen(stmt)
            if self.builder.block.is_terminated:
                break

        if not self.builder.block.is_terminated:
            next_idx = self.builder.add(curr_idx, ir.Constant(ir.IntType(64), 1))
            self.builder.store(next_idx, idx_ptr)
            self.builder.branch(cond_bb)

        # Pop loop context
        self.loop_stack.pop()

        self.builder.position_at_end(merge_bb)

    def _codegen_ForeachVectorStatement(self, node):
        vec_val = self._codegen(node.vector_expr)

        data_ptr = self.builder.extract_value(vec_val, 0)
        size_val = self.builder.extract_value(vec_val, 1)

        idx_ptr = self.builder.alloca(ir.IntType(64), name=node.index_var)
        self.builder.store(ir.Constant(ir.IntType(64), 0), idx_ptr)
        self.var_symtab[node.index_var] = (idx_ptr, "int<64>")

        elem_type = data_ptr.type.pointee
        val_ptr = self.builder.alloca(elem_type, name=node.value_var)

        # Get element type name for symtab
        try:
            _, full_type_name = self._codegen_lvalue(node.vector_expr)
            elem_type_name = (
                full_type_name[4:-1] if full_type_name.startswith("vec<") else "int"
            )
        except:
            elem_type_name = "int"

        self.var_symtab[node.value_var] = (val_ptr, elem_type_name)

        cond_bb = self.builder.function.append_basic_block("foreach_vec_cond")
        body_bb = self.builder.function.append_basic_block("foreach_vec_body")
        merge_bb = self.builder.function.append_basic_block("foreach_vec_merge")
        continue_bb = cond_bb  # continue goes back to condition
        break_bb = merge_bb  # break exits to merge

        # Push loop context for nested stop/continue
        self.loop_stack.append((break_bb, continue_bb))

        self.builder.branch(cond_bb)
        self.builder.position_at_end(cond_bb)

        curr_idx = self.builder.load(idx_ptr)
        cmp_res = self.builder.icmp_signed("<", curr_idx, size_val)
        self.builder.cbranch(cmp_res, body_bb, merge_bb)

        self.builder.position_at_end(body_bb)

        curr_elem_ptr = self.builder.gep(data_ptr, [curr_idx], inbounds=True)
        curr_elem_val = self.builder.load(curr_elem_ptr)
        self.builder.store(curr_elem_val, val_ptr)

        for stmt in node.body:
            self._codegen(stmt)
            if self.builder.block.is_terminated:
                break

        if not self.builder.block.is_terminated:
            next_idx = self.builder.add(curr_idx, ir.Constant(ir.IntType(64), 1))
            self.builder.store(next_idx, idx_ptr)
            self.builder.branch(cond_bb)

        # Pop loop context
        self.loop_stack.pop()

        self.builder.position_at_end(merge_bb)

    def _codegen_BinaryOp(self, node):
        left = self._codegen(node.left)

        # Logical operations (short-circuiting)
        if node.op == "&&":
            res_ptr = self.builder.alloca(ir.IntType(1))
            self.builder.store(ir.Constant(ir.IntType(1), 0), res_ptr)
            left_bool = self._cast_bool(left)
            with self.builder.if_then(left_bool):
                right_bool = self._cast_bool(self._codegen(node.right))
                self.builder.store(right_bool, res_ptr)
            return self.builder.load(res_ptr)

        if node.op == "||":
            res_ptr = self.builder.alloca(ir.IntType(1))
            self.builder.store(ir.Constant(ir.IntType(1), 1), res_ptr)
            left_bool = self._cast_bool(left)
            with self.builder.if_else(left_bool) as (then, otherwise):
                with then:
                    pass
                with otherwise:
                    right_bool = self._cast_bool(self._codegen(node.right))
                    self.builder.store(right_bool, res_ptr)
            return self.builder.load(res_ptr)

        # Standard binary ops
        right = self._codegen(node.right)

        def is_ptr(typ):
            if not (
                isinstance(typ, ir.PointerType) or getattr(typ, "is_pointer", False)
            ):
                return False
            pointee = getattr(typ, "pointee", None)
            # String is i8*, so we check for pointers NOT to i8
            return not (isinstance(pointee, ir.IntType) and pointee.width == 8)

        # Check if left operand is a char pointer (should use pointer arithmetic, not string concat)
        def is_char_ptr_leash(node):
            leash_type = self._get_leash_type_name(node)
            resolved = self._resolve_type_name(leash_type)
            return resolved == "*char"

        left_is_char_ptr = is_char_ptr_leash(node.left)
        right_is_char_ptr = (
            is_char_ptr_leash(node.right) if hasattr(node, "right") else False
        )

        if (
            node.op == "+"
            and (is_ptr(left.type) or left_is_char_ptr)
            and isinstance(right.type, ir.IntType)
        ):
            return self.builder.gep(
                left,
                [
                    self.builder.zext(right, ir.IntType(64))
                    if right.type.width < 64
                    else right
                ],
            )
        if (
            node.op == "+"
            and isinstance(left.type, ir.IntType)
            and (is_ptr(right.type) or right_is_char_ptr)
        ):
            return self.builder.gep(
                right,
                [
                    self.builder.zext(left, ir.IntType(64))
                    if left.type.width < 64
                    else left
                ],
            )
        if (
            node.op == "-"
            and (is_ptr(left.type) or left_is_char_ptr)
            and isinstance(right.type, ir.IntType)
        ):
            idx = (
                self.builder.zext(right, ir.IntType(64))
                if right.type.width < 64
                else right
            )
            neg_idx = self.builder.neg(idx)
            return self.builder.gep(left, [neg_idx])

        def is_i8_ptr(typ):
            if not (
                isinstance(typ, ir.PointerType) or getattr(typ, "is_pointer", False)
            ):
                return False
            pointee = getattr(typ, "pointee", None)
            return isinstance(pointee, ir.IntType) and pointee.width == 8

        is_string_l = is_i8_ptr(left.type)
        is_string_r = is_i8_ptr(right.type)

        is_slice_l = (
            isinstance(left.type, ir.LiteralStructType)
            and len(left.type.elements) == 2
            and is_i8_ptr(left.type.elements[1])
        )
        is_slice_r = (
            isinstance(right.type, ir.LiteralStructType)
            and len(right.type.elements) == 2
            and is_i8_ptr(right.type.elements[1])
        )

        is_char_l = isinstance(left.type, ir.IntType) and left.type.width == 8
        is_char_r = isinstance(right.type, ir.IntType) and right.type.width == 8

        # Mixed string concatenation
        is_numeric_l = isinstance(left.type, (ir.IntType, ir.FloatType, ir.DoubleType))
        is_numeric_r = isinstance(right.type, (ir.IntType, ir.FloatType, ir.DoubleType))

        if (
            node.op == "+"
            and (
                (is_string_l or is_slice_l)
                and (is_string_r or is_slice_r or is_numeric_r or is_char_r)
            )
            or ((is_string_r or is_slice_r) and (is_numeric_l or is_char_l))
        ):
            # Convert non-strings to strings
            if not (is_string_l or is_slice_l):
                left = self._emit_tostring(left, left.type)
                is_string_l = True
            if not (is_string_r or is_slice_r):
                right = self._emit_tostring(right, right.type)
                is_string_r = True

            len_l = None
            if is_string_l:
                # Handle null pointers safely - treat as empty string using explicit blocks
                null_left = self.builder.icmp_unsigned("==", left, ir.Constant(left.type, None))
                len_l_null_bb = self.builder.function.append_basic_block("len_l_null")
                len_l_str_bb = self.builder.function.append_basic_block("len_l_str")
                len_l_merge_bb = self.builder.function.append_basic_block("len_l_merge")
                self.builder.cbranch(null_left, len_l_null_bb, len_l_str_bb)
                
                self.builder.position_at_end(len_l_null_bb)
                len_l_null = ir.Constant(ir.IntType(64), 0)
                self.builder.branch(len_l_merge_bb)
                
                self.builder.position_at_end(len_l_str_bb)
                len_l_str = self.builder.call(self.strlen, [left])
                self.builder.branch(len_l_merge_bb)
                
                self.builder.position_at_end(len_l_merge_bb)
                phi_l = self.builder.phi(ir.IntType(64))
                phi_l.add_incoming(len_l_null, len_l_null_bb)
                phi_l.add_incoming(len_l_str, len_l_str_bb)
                len_l = phi_l
            elif is_slice_l:
                len_l = self.builder.zext(
                    self.builder.extract_value(left, 0), ir.IntType(64)
                )
            elif is_char_l:
                len_l = ir.Constant(ir.IntType(64), 1)

            len_r = None
            if is_string_r:
                # Handle null pointers safely - treat as empty string using explicit blocks
                null_right = self.builder.icmp_unsigned("==", right, ir.Constant(right.type, None))
                len_r_null_bb = self.builder.function.append_basic_block("len_r_null")
                len_r_str_bb = self.builder.function.append_basic_block("len_r_str")
                len_r_merge_bb = self.builder.function.append_basic_block("len_r_merge")
                self.builder.cbranch(null_right, len_r_null_bb, len_r_str_bb)
                
                self.builder.position_at_end(len_r_null_bb)
                len_r_null = ir.Constant(ir.IntType(64), 0)
                self.builder.branch(len_r_merge_bb)
                
                self.builder.position_at_end(len_r_str_bb)
                len_r_str = self.builder.call(self.strlen, [right])
                self.builder.branch(len_r_merge_bb)
                
                self.builder.position_at_end(len_r_merge_bb)
                phi_r = self.builder.phi(ir.IntType(64))
                phi_r.add_incoming(len_r_null, len_r_null_bb)
                phi_r.add_incoming(len_r_str, len_r_str_bb)
                len_r = phi_r
            elif is_slice_r:
                len_r = self.builder.zext(
                    self.builder.extract_value(right, 0), ir.IntType(64)
                )
            elif is_char_r:
                len_r = ir.Constant(ir.IntType(64), 1)

            total_len = self.builder.add(len_l, len_r)
            total_len_plus_1 = self.builder.add(
                total_len, ir.Constant(ir.IntType(64), 1)
            )
            new_str = self.builder.call(self.malloc, [total_len_plus_1])
            self._track_alloc(new_str)  # TRACK THIS ALLOCATION

            # Copy left
            if is_string_l:
                # Handle null pointers safely using explicit blocks
                null_left = self.builder.icmp_unsigned("==", left, ir.Constant(left.type, None))
                copy_left_null_bb = self.builder.function.append_basic_block("copy_left_null")
                copy_left_str_bb = self.builder.function.append_basic_block("copy_left_str")
                copy_left_merge_bb = self.builder.function.append_basic_block("copy_left_merge")
                self.builder.cbranch(null_left, copy_left_null_bb, copy_left_str_bb)
                
                self.builder.position_at_end(copy_left_null_bb)
                # Left is null, start with empty string
                self.builder.store(
                    ir.Constant(ir.IntType(8), 0),
                    self.builder.gep(new_str, [ir.Constant(ir.IntType(64), 0)], inbounds=True)
                )
                self.builder.branch(copy_left_merge_bb)
                
                self.builder.position_at_end(copy_left_str_bb)
                self.builder.call(self.strcpy, [new_str, left])
                self.builder.branch(copy_left_merge_bb)
                
                self.builder.position_at_end(copy_left_merge_bb)
            elif is_slice_l:
                ptr_l = self.builder.extract_value(left, 1)
                self.builder.call(self.strncpy, [new_str, ptr_l, len_l])
                null_pos = self.builder.gep(new_str, [len_l], inbounds=True)
                self.builder.store(ir.Constant(ir.IntType(8), 0), null_pos)
            elif is_char_l:
                self.builder.store(
                    left,
                    self.builder.gep(
                        new_str, [ir.Constant(ir.IntType(64), 0)], inbounds=True
                    ),
                )
                self.builder.store(
                    ir.Constant(ir.IntType(8), 0),
                    self.builder.gep(
                        new_str, [ir.Constant(ir.IntType(64), 1)], inbounds=True
                    ),
                )

            # Concatenate right
            if is_string_r:
                # Handle null pointers safely using explicit blocks
                null_right = self.builder.icmp_unsigned("==", right, ir.Constant(right.type, None))
                concat_right_null_bb = self.builder.function.append_basic_block("concat_right_null")
                concat_right_str_bb = self.builder.function.append_basic_block("concat_right_str")
                concat_right_merge_bb = self.builder.function.append_basic_block("concat_right_merge")
                self.builder.cbranch(null_right, concat_right_null_bb, concat_right_str_bb)
                
                self.builder.position_at_end(concat_right_null_bb)
                # Right is null, nothing to concatenate
                self.builder.branch(concat_right_merge_bb)
                
                self.builder.position_at_end(concat_right_str_bb)
                self.builder.call(self.strcat, [new_str, right])
                self.builder.branch(concat_right_merge_bb)
                
                self.builder.position_at_end(concat_right_merge_bb)
            elif is_slice_r:
                ptr_r = self.builder.extract_value(right, 1)
                dest = self.builder.gep(new_str, [len_l], inbounds=True)
                self.builder.call(self.strncpy, [dest, ptr_r, len_r])
                end_pos = self.builder.add(len_l, len_r)
                self.builder.store(
                    ir.Constant(ir.IntType(8), 0),
                    self.builder.gep(new_str, [end_pos], inbounds=True),
                )
            elif is_char_r:
                dest = self.builder.gep(new_str, [len_l], inbounds=True)
                self.builder.store(right, dest)
                end_pos = self.builder.add(len_l, ir.Constant(ir.IntType(64), 1))
                self.builder.store(
                    ir.Constant(ir.IntType(8), 0),
                    self.builder.gep(new_str, [end_pos], inbounds=True),
                )

            return new_str

        is_string = is_string_l and is_string_r
        if is_string:
            if node.op == "-":
                p = self.builder.call(self.strstr, [left, right])
                null_ptr = (
                    getattr(ir.IntType(8), "as_pointer")()(None)
                    if hasattr(ir.IntType(8), "as_pointer")
                    else ir.Constant(left.type, None)
                )
                null_ptr = ir.Constant(left.type, None) if not null_ptr else null_ptr
                is_not_null = self.builder.icmp_unsigned("!=", p, null_ptr)

                found_bb = self.builder.function.append_basic_block("strstr_found")
                not_found_bb = self.builder.function.append_basic_block(
                    "strstr_not_found"
                )
                merge_bb = self.builder.function.append_basic_block("strstr_merge")

                self.builder.cbranch(is_not_null, found_bb, not_found_bb)

                self.builder.position_at_end(found_bb)
                len_l = self.builder.call(self.strlen, [left])
                len_r = self.builder.call(self.strlen, [right])
                p_int = self.builder.ptrtoint(p, ir.IntType(64))
                l_int = self.builder.ptrtoint(left, ir.IntType(64))
                prefix_len = self.builder.sub(p_int, l_int)

                new_len = self.builder.sub(len_l, len_r)
                new_len_plus_1 = self.builder.add(
                    new_len, ir.Constant(ir.IntType(64), 1)
                )
                res_found = self.builder.call(self.malloc, [new_len_plus_1])
                self._track_alloc(res_found)  # TRACK

                self.builder.call(self.strncpy, [res_found, left, prefix_len])
                res_null_pos = self.builder.gep(res_found, [prefix_len], inbounds=True)
                self.builder.store(ir.Constant(ir.IntType(8), 0), res_null_pos)

                suffix_ptr = self.builder.gep(p, [len_r], inbounds=True)
                self.builder.call(self.strcat, [res_found, suffix_ptr])
                self.builder.branch(merge_bb)

                self.builder.position_at_end(not_found_bb)
                len_l2 = self.builder.call(self.strlen, [left])
                len_l2_plus_1 = self.builder.add(len_l2, ir.Constant(ir.IntType(64), 1))
                res_not_found = self.builder.call(self.malloc, [len_l2_plus_1])
                self._track_alloc(res_not_found)  # TRACK
                self.builder.call(self.strcpy, [res_not_found, left])
                self.builder.branch(merge_bb)

                self.builder.position_at_end(merge_bb)
                phi = self.builder.phi(left.type, name="str_sub_res")
                phi.add_incoming(res_found, found_bb)
                phi.add_incoming(res_not_found, not_found_bb)
                return phi
            elif node.op == "==":
                cmp = self.builder.call(self.strcmp, [left, right])
                return self.builder.icmp_signed(
                    "==", cmp, ir.Constant(ir.IntType(32), 0)
                )
            elif node.op == "!=":
                cmp = self.builder.call(self.strcmp, [left, right])
                return self.builder.icmp_signed(
                    "!=", cmp, ir.Constant(ir.IntType(32), 0)
                )
            else:
                raise Exception(f"Unknown string binary op {node.op}")

        # Type promotion
        if left.type != right.type:
            if isinstance(left.type, ir.IntType) and isinstance(right.type, ir.IntType):
                if left.type.width < right.type.width:
                    left = self._emit_cast(left, right.type)
                else:
                    right = self._emit_cast(right, left.type)
            elif isinstance(left.type, (ir.FloatType, ir.DoubleType)) and isinstance(
                right.type, (ir.FloatType, ir.DoubleType)
            ):
                target = ir.DoubleType()
                left = self._emit_cast(left, target)
                right = self._emit_cast(right, target)
            elif isinstance(left.type, (ir.FloatType, ir.DoubleType)) and isinstance(
                right.type, ir.IntType
            ):
                right = self._emit_cast(right, left.type)
            elif isinstance(left.type, ir.IntType) and isinstance(
                right.type, (ir.FloatType, ir.DoubleType)
            ):
                left = self._emit_cast(left, right.type)

        # Determine if float or int based on types (assume matching types for now)
        is_float = isinstance(left.type, (ir.FloatType, ir.DoubleType))

        if node.op == "+":
            return (
                self.builder.fadd(left, right)
                if is_float
                else self.builder.add(left, right)
            )
        elif node.op == "-":
            return (
                self.builder.fsub(left, right)
                if is_float
                else self.builder.sub(left, right)
            )
        elif node.op == "*":
            return (
                self.builder.fmul(left, right)
                if is_float
                else self.builder.mul(left, right)
            )
        elif node.op == "/":
            return (
                self.builder.fdiv(left, right)
                if is_float
                else self.builder.sdiv(left, right)
            )
        elif node.op == "%":
            return (
                self.builder.frem(left, right)
                if is_float
                else self.builder.srem(left, right)
            )
        elif node.op == "&":
            return self.builder.and_(left, right)
        elif node.op == "|":
            return self.builder.or_(left, right)
        elif node.op == "^":
            return self.builder.xor(left, right)
        elif node.op == "<<":
            return self.builder.shl(left, right)
        elif node.op == ">>":
            return self.builder.ashr(left, right)  # assume signed shift right
        elif node.op == "==":
            return (
                self.builder.fcmp_ordered("==", left, right)
                if is_float
                else self.builder.icmp_signed("==", left, right)
            )
        elif node.op == "!=":
            return (
                self.builder.fcmp_ordered("!=", left, right)
                if is_float
                else self.builder.icmp_signed("!=", left, right)
            )
        elif node.op == "<":
            return (
                self.builder.fcmp_ordered("<", left, right)
                if is_float
                else self.builder.icmp_signed("<", left, right)
            )
        elif node.op == "<=":
            return (
                self.builder.fcmp_ordered("<=", left, right)
                if is_float
                else self.builder.icmp_signed("<=", left, right)
            )
        elif node.op == ">":
            return (
                self.builder.fcmp_ordered(">", left, right)
                if is_float
                else self.builder.icmp_signed(">", left, right)
            )
        elif node.op == ">=":
            return (
                self.builder.fcmp_ordered(">=", left, right)
                if is_float
                else self.builder.icmp_signed(">=", left, right)
            )

        raise LeashError(f"Unknown binary operator: '{node.op}'")

    def _codegen_UnaryOp(self, node):
        if node.op == "*":  # Dereference
            val = self._codegen(node.expr)
            return self.builder.load(val)
        if node.op == "&":  # Address-of
            ptr, _ = self._codegen_lvalue(node.expr)
            return ptr

        val = self._codegen(node.expr)
        if node.op == "-":
            if isinstance(val.type, (ir.FloatType, ir.DoubleType)):
                return self.builder.fneg(val)
            else:
                return self.builder.neg(val)
        elif node.op == "!":
            bool_val = self._cast_bool(val)
            return self.builder.not_(bool_val)
        elif node.op == "~":
            return self.builder.not_(val)

        raise LeashError(f"Unknown unary operator: '{node.op}'")

    def _emit_get_input(self, node):
        """Implement the 'get' builtin for interactive input."""
        # 1. Print prompt if exists
        if node.args:
            prompt_val = self._codegen(node.args[0])
            # Use printf-like show_standard logic for consistency
            fmt = self._emit_const_str("%s")
            self.builder.call(self.printf, [fmt, prompt_val])

        # 2. Buffer for reading
        # capacity = 256; size = 0; buffer = malloc(capacity)
        capacity_ptr = self.builder.alloca(ir.IntType(64), name="input_cap")
        size_ptr = self.builder.alloca(ir.IntType(64), name="input_size")
        self.builder.store(ir.Constant(ir.IntType(64), 256), capacity_ptr)
        self.builder.store(ir.Constant(ir.IntType(64), 0), size_ptr)

        buffer_ptr_ptr = self.builder.alloca(
            ir.IntType(8).as_pointer(), name="input_buf_ptr"
        )
        initial_buf = self.builder.call(self.malloc, [ir.Constant(ir.IntType(64), 256)])
        self.builder.store(initial_buf, buffer_ptr_ptr)

        # 3. Read loop
        loop_bb = self.builder.function.append_basic_block("get_loop")
        loop_end_bb = self.builder.function.append_basic_block("get_loop_end")

        self.builder.branch(loop_bb)
        self.builder.position_at_end(loop_bb)

        c = self.builder.call(self.getchar, [])

        # Check for newline (10), carriage return (13), or EOF (-1)
        is_eof = self.builder.icmp_signed("==", c, ir.Constant(ir.IntType(32), -1))
        is_nl = self.builder.icmp_signed("==", c, ir.Constant(ir.IntType(32), 10))
        is_cr = self.builder.icmp_signed("==", c, ir.Constant(ir.IntType(32), 13))
        should_stop = self.builder.or_(self.builder.or_(is_eof, is_nl), is_cr)

        next_char_bb = self.builder.function.append_basic_block("get_next_char")
        self.builder.cbranch(should_stop, loop_end_bb, next_char_bb)

        # Body of next char
        self.builder.position_at_end(next_char_bb)

        # Append char: buffer[size] = (i8)c; size++
        curr_size = self.builder.load(size_ptr)
        curr_cap = self.builder.load(capacity_ptr)

        # Check realloc: if size + 2 >= capacity (reserve space for null)
        is_full = self.builder.icmp_signed(
            ">=", self.builder.add(curr_size, ir.Constant(ir.IntType(64), 2)), curr_cap
        )

        realloc_bb = self.builder.function.append_basic_block("get_realloc")
        store_char_bb = self.builder.function.append_basic_block("get_store_char")
        self.builder.cbranch(is_full, realloc_bb, store_char_bb)

        # Realloc
        self.builder.position_at_end(realloc_bb)
        new_cap = self.builder.mul(curr_cap, ir.Constant(ir.IntType(64), 2))
        self.builder.store(new_cap, capacity_ptr)
        old_buf = self.builder.load(buffer_ptr_ptr)
        new_buf = self.builder.call(self.realloc, [old_buf, new_cap])
        self.builder.store(new_buf, buffer_ptr_ptr)
        self.builder.branch(store_char_bb)

        # Store char
        self.builder.position_at_end(store_char_bb)
        buf = self.builder.load(buffer_ptr_ptr)
        idx_ptr = self.builder.gep(buf, [curr_size])
        self.builder.store(self.builder.trunc(c, ir.IntType(8)), idx_ptr)

        self.builder.store(
            self.builder.add(curr_size, ir.Constant(ir.IntType(64), 1)), size_ptr
        )
        self.builder.branch(loop_bb)

        self.builder.position_at_end(loop_end_bb)

        # 4. Null terminator
        final_size = self.builder.load(size_ptr)
        final_buf = self.builder.load(buffer_ptr_ptr)
        term_ptr = self.builder.gep(final_buf, [final_size])
        self.builder.store(ir.Constant(ir.IntType(8), 0), term_ptr)

        # 5. Track for SAMM and return
        return self._track_alloc(final_buf)

    def _codegen_MethodCall(self, node):
        base_ptr, type_name = self._codegen_lvalue(node.expr)
        resolved = self._resolve_type_name(type_name)

        if resolved.startswith("vec<"):
            return self._codegen_vector_method(
                base_ptr, resolved, node.method, node.args
            )

        if resolved == "string" and node.method == "size":
            val = self.builder.load(base_ptr)
            length = self.builder.call(self.strlen, [val])
            return self.builder.trunc(length, ir.IntType(32))
        if resolved.endswith("]") and node.method == "size":
            val = self.builder.load(base_ptr)
            length = self.builder.extract_value(val, 0)
            return self.builder.trunc(length, ir.IntType(32))

        if resolved in self.class_symtab:
            cls_info = self.class_symtab[resolved]
            func = cls_info["methods"].get(node.method)
            if not func:
                raise LeashError(
                    f"Class '{resolved}' has no method named '{node.method}'"
                )

            # Prepare arguments
            args = []
            for i, arg_node in enumerate(node.args):
                args.append(self._codegen(arg_node))

            is_m_static = cls_info["method_static"].get(node.method, False)

            if not is_m_static:
                if base_ptr is None:
                    raise LeashError(
                        f"Method '{node.method}' of class '{resolved}' is an instance method and requires an instance."
                    )
                # Get the 'this' pointer
                if not resolved.startswith("&"):
                    this_ptr = self.builder.load(base_ptr)
                else:
                    this_ptr = base_ptr

                # For dynamic dispatch, use vtable lookup
                # Find the method index in the vtable
                vtable_idx = cls_info.get("vtable_indices", {}).get(node.method)

                if vtable_idx is not None and cls_info.get("vtable_global"):
                    # Load vtable pointer from instance (index 0)
                    vtable_ptr_ptr = self.builder.gep(
                        this_ptr,
                        [
                            ir.Constant(ir.IntType(32), 0),
                            ir.Constant(ir.IntType(32), 0),
                        ],
                    )
                    vtable_ptr_i8 = self.builder.load(vtable_ptr_ptr)

                    # Cast i8* vtable pointer to the actual vtable struct pointer type
                    vtable_struct_ptr_type = cls_info["vtable_type"].as_pointer()
                    vtable_ptr = self.builder.bitcast(
                        vtable_ptr_i8, vtable_struct_ptr_type
                    )

                    # Load function pointer from vtable at the method index
                    func_ptr_ptr = self.builder.gep(
                        vtable_ptr,
                        [
                            ir.Constant(ir.IntType(32), 0),
                            ir.Constant(ir.IntType(32), vtable_idx),
                        ],
                    )
                    func_ptr = self.builder.load(func_ptr_ptr)

                    # Cast function pointer to the correct type
                    func_type = func.function_type
                    typed_func_ptr = self.builder.bitcast(
                        func_ptr, func_type.as_pointer()
                    )

                    # Call through vtable with this as first argument
                    args = [this_ptr] + args
                    casted_args = []
                    for arg_val, expected_type in zip(args, func.args):
                        casted_args.append(self._emit_cast(arg_val, expected_type.type))

                    return self.builder.call(typed_func_ptr, casted_args)
                else:
                    # No vtable (shouldn't happen for instance methods), direct call
                    args = [this_ptr] + args
                    casted_args = []
                    for arg_val, expected_type in zip(args, func.args):
                        casted_args.append(self._emit_cast(arg_val, expected_type.type))
                    return self.builder.call(func, casted_args)
            else:
                # Static method - direct call
                casted_args = []
                for arg_val, expected_type in zip(args, func.args):
                    casted_args.append(self._emit_cast(arg_val, expected_type.type))
                return self.builder.call(func, casted_args)

        raise LeashError(
            f"Method '{node.method}' is not implemented for type '{resolved}'",
            line=getattr(node, "line", None),
            col=getattr(node, "col", None),
        )

    def _update_vec_struct(self, vec_ptr, data, size, cap):
        """Update the fields of a vector struct through its pointer."""
        struct_val = self.builder.load(vec_ptr)
        struct_val = self.builder.insert_value(struct_val, data, 0)
        struct_val = self.builder.insert_value(struct_val, size, 1)
        struct_val = self.builder.insert_value(struct_val, cap, 2)
        self.builder.store(struct_val, vec_ptr)

    def _vector_check_capacity(self, vec_ptr, data, size, cap, elem_llvm):
        """Check if capacity is enough, otherwise allocate larger buffer. Safe for SAMM (uses malloc)."""
        needed = self.builder.icmp_unsigned(">=", size, cap)

        merge_bb = self.builder.function.append_basic_block("vec_cap_merge")
        needed_bb = self.builder.function.append_basic_block("vec_cap_needed")

        self.builder.cbranch(needed, needed_bb, merge_bb)

        self.builder.position_at_end(needed_bb)
        # new_cap = (cap == 0) ? 8 : cap * 2
        is_zero = self.builder.icmp_unsigned("==", cap, ir.Constant(ir.IntType(64), 0))
        new_cap = self.builder.select(
            is_zero,
            ir.Constant(ir.IntType(64), 8),
            self.builder.mul(cap, ir.Constant(ir.IntType(64), 2)),
        )

        # total_bytes = new_cap * sizeof(elem)
        # sizeof(elem) trick via gep
        dummy_ptr = ir.Constant(elem_llvm.as_pointer(), None)
        elem_size_ptr = self.builder.gep(
            dummy_ptr, [ir.Constant(ir.IntType(32), 1)], inbounds=True
        )
        elem_size = self.builder.ptrtoint(elem_size_ptr, ir.IntType(64))
        total_bytes = self.builder.mul(new_cap, elem_size)

        new_data_bytes = self.builder.call(self.malloc, [total_bytes])
        self._track_alloc(new_data_bytes)

        # Copy old data
        is_not_null = self.builder.icmp_unsigned(
            "!=",
            self.builder.ptrtoint(data, ir.IntType(64)),
            ir.Constant(ir.IntType(64), 0),
        )
        with self.builder.if_then(is_not_null):
            old_bytes = self.builder.bitcast(data, ir.IntType(8).as_pointer())
            copy_bytes = self.builder.mul(size, elem_size)
            self.builder.call(self.memmove, [new_data_bytes, old_bytes, copy_bytes])

        new_data = self.builder.bitcast(new_data_bytes, elem_llvm.as_pointer())
        self._update_vec_struct(vec_ptr, new_data, size, new_cap)
        self.builder.branch(merge_bb)

        self.builder.position_at_end(merge_bb)

        # Reload current state
        final_struct = self.builder.load(vec_ptr)
        final_data = self.builder.extract_value(final_struct, 0)
        final_cap = self.builder.extract_value(final_struct, 2)
        return final_data, final_cap

    def _codegen_vector_method(self, vec_ptr, vec_type_name, method, args):
        inner_type_name = vec_type_name[4:-1]
        inner_llvm = self._get_llvm_type(inner_type_name)

        struct_val = self.builder.load(vec_ptr)
        data = self.builder.extract_value(struct_val, 0)
        size = self.builder.extract_value(struct_val, 1)
        cap = self.builder.extract_value(struct_val, 2)

        if method == "pushb":
            val = self._codegen(args[0])
            val = self._emit_cast(val, inner_llvm)

            new_data, new_cap = self._vector_check_capacity(
                vec_ptr, data, size, cap, inner_llvm
            )

            # Store at data[size]
            store_ptr = self.builder.gep(new_data, [size], inbounds=True)
            self.builder.store(val, store_ptr)

            # Update size
            new_size = self.builder.add(size, ir.Constant(ir.IntType(64), 1))
            self._update_vec_struct(vec_ptr, new_data, new_size, new_cap)
            return None

        elif method == "popb":
            # return data[size-1], size--
            new_size = self.builder.sub(size, ir.Constant(ir.IntType(64), 1))
            res_ptr = self.builder.gep(data, [new_size], inbounds=True)
            res_val = self.builder.load(res_ptr)

            self._update_vec_struct(vec_ptr, data, new_size, cap)
            return res_val

        elif method == "size":
            return self.builder.trunc(size, ir.IntType(32))

        elif method == "get":
            idx = self._codegen(args[0])
            idx = self._emit_cast(idx, ir.IntType(32))
            ptr = self.builder.gep(data, [idx], inbounds=True)
            return self.builder.load(ptr)

        elif method == "set":
            idx = self._codegen(args[0])
            idx = self._emit_cast(idx, ir.IntType(32))
            val = self._codegen(args[1])
            val = self._emit_cast(val, inner_llvm)
            ptr = self.builder.gep(data, [idx], inbounds=True)
            self.builder.store(val, ptr)
            return None

        elif method == "clear":
            self._update_vec_struct(vec_ptr, data, ir.Constant(ir.IntType(32), 0), cap)
            return None

        elif method == "pushf":
            val = self._codegen(args[0])
            val = self._emit_cast(val, inner_llvm)

            new_data, new_cap = self._vector_check_capacity(
                vec_ptr, data, size, cap, inner_llvm
            )

            # memmove data[1..size] = data[0..size-1]
            old_data_bytes = self.builder.bitcast(new_data, ir.IntType(8).as_pointer())
            elem_size_val = self._type_byte_size(inner_llvm)
            new_dst_bytes = self.builder.gep(
                old_data_bytes,
                [ir.Constant(ir.IntType(64), elem_size_val)],
                inbounds=True,
            )

            dummy_ptr = ir.Constant(inner_llvm.as_pointer(), None)
            elem_size = self.builder.ptrtoint(
                self.builder.gep(dummy_ptr, [ir.Constant(ir.IntType(32), 1)]),
                ir.IntType(64),
            )
            copy_bytes = self.builder.mul(
                self.builder.zext(size, ir.IntType(64)), elem_size
            )

            self.builder.call(self.memmove, [new_dst_bytes, old_data_bytes, copy_bytes])

            # Store at data[0]
            self.builder.store(val, new_data)

            new_size = self.builder.add(size, ir.Constant(ir.IntType(64), 1))
            self._update_vec_struct(vec_ptr, new_data, new_size, new_cap)
            return None

        elif method == "popf":
            # res = data[0], memmove data[0..size-2] = data[1..size-1], size--
            res_val = self.builder.load(data)

            new_size = self.builder.sub(size, ir.Constant(ir.IntType(64), 1))

            dst_bytes = self.builder.bitcast(data, ir.IntType(8).as_pointer())
            elem_size_val = self._type_byte_size(inner_llvm)
            src_bytes = self.builder.gep(
                dst_bytes, [ir.Constant(ir.IntType(64), elem_size_val)], inbounds=True
            )

            dummy_ptr = ir.Constant(inner_llvm.as_pointer(), None)
            elem_size = self.builder.ptrtoint(
                self.builder.gep(dummy_ptr, [ir.Constant(ir.IntType(32), 1)]),
                ir.IntType(64),
            )
            copy_bytes = self.builder.mul(
                self.builder.zext(new_size, ir.IntType(64)), elem_size
            )

            self.builder.call(self.memmove, [dst_bytes, src_bytes, copy_bytes])

            self._update_vec_struct(vec_ptr, data, new_size, cap)
            return res_val

        elif method == "insert":
            idx = self._codegen(args[0])
            idx = self._emit_cast(idx, ir.IntType(32))
            val = self._codegen(args[1])
            val = self._emit_cast(val, inner_llvm)

            new_data, new_cap = self._vector_check_capacity(
                vec_ptr, data, size, cap, inner_llvm
            )

            # memmove data[idx+1..size] = data[idx..size-1]
            elem_size_val = self._type_byte_size(inner_llvm)

            data_bytes = self.builder.bitcast(new_data, ir.IntType(8).as_pointer())
            idx_64 = self.builder.zext(idx, ir.IntType(64))
            src_bytes = self.builder.gep(
                data_bytes,
                [self.builder.mul(idx_64, ir.Constant(ir.IntType(64), elem_size_val))],
                inbounds=True,
            )
            dst_bytes = self.builder.gep(
                src_bytes, [ir.Constant(ir.IntType(64), elem_size_val)], inbounds=True
            )

            copy_count = self.builder.sub(size, idx)
            copy_bytes = self.builder.mul(
                self.builder.zext(copy_count, ir.IntType(64)),
                ir.Constant(ir.IntType(64), elem_size_val),
            )

            self.builder.call(self.memmove, [dst_bytes, src_bytes, copy_bytes])

            # Store at data[idx]
            store_ptr = self.builder.gep(new_data, [idx], inbounds=True)
            self.builder.store(val, store_ptr)

            new_size = self.builder.add(size, ir.Constant(ir.IntType(64), 1))
            self._update_vec_struct(vec_ptr, new_data, new_size, new_cap)
            return None

        elif method == "remove":
            # vec.remove(index) - remove element at index
            idx = self._codegen(args[0])
            idx = self._emit_cast(idx, ir.IntType(32))
            idx_64 = self.builder.zext(idx, ir.IntType(64))

            # Compute element size at runtime (same as pushf/popf)
            dummy_ptr = ir.Constant(inner_llvm.as_pointer(), None)
            elem_size = self.builder.ptrtoint(
                self.builder.gep(dummy_ptr, [ir.Constant(ir.IntType(32), 1)]),
                ir.IntType(64),
            )

            # memmove data[idx..size-2] = data[idx+1..size-1]
            data_bytes = self.builder.bitcast(data, ir.IntType(8).as_pointer())
            dst_offset = self.builder.mul(idx_64, elem_size)
            dst_bytes = self.builder.gep(data_bytes, [dst_offset], inbounds=True)
            src_bytes = self.builder.gep(dst_bytes, [elem_size], inbounds=True)

            copy_count = self.builder.sub(size, idx_64)
            copy_count = self.builder.sub(copy_count, ir.Constant(ir.IntType(64), 1))
            copy_bytes = self.builder.mul(copy_count, elem_size)

            self.builder.call(self.memmove, [dst_bytes, src_bytes, copy_bytes])

            new_size = self.builder.sub(size, ir.Constant(ir.IntType(64), 1))
            self._update_vec_struct(vec_ptr, data, new_size, cap)
            return None

        raise LeashError(
            f"Vector method '{method}' not fully implemented yet", node=vec_ptr
        )

    def _codegen_GenericCall(self, node):
        """Handle generic function calls like add<int>(10, 20)."""
        # Generate the mangled function name
        type_args_str = "_".join(
            t.replace("<", "_").replace(">", "_").replace(",", "_").replace(" ", "")
            for t in node.type_args
        )
        mangled_name = f"{node.name}_{type_args_str}"

        # Check if the function exists in the symbol table
        func = self.func_symtab.get(mangled_name)
        if not func:
            raise LeashError(
                f"Call to undefined generic function: '{node.name}' with types {node.type_args}"
            )

        # Generate arguments
        args = []
        for i, arg_expr in enumerate(node.args):
            if i < len(func.args):
                target_llvm = func.args[i].type
                v = self._codegen(arg_expr)
                v = self._emit_cast(v, target_llvm)
                args.append(v)
            else:
                args.append(self._codegen(arg_expr))

        return self.builder.call(func, args)

    def _codegen_Call(self, node):
        if node.name == "get":
            return self._emit_get_input(node)

        if node.name == "cstr":
            arg_val = self._codegen(node.args[0])
            # arg_val is i8*
            length = self.builder.call(self.strlen, [arg_val])
            slice_type = ir.LiteralStructType(
                [ir.IntType(64), ir.IntType(8).as_pointer()]
            )
            slice_val = ir.Constant(slice_type, ir.Undefined)
            slice_val = self.builder.insert_value(slice_val, length, 0)
            slice_val = self.builder.insert_value(slice_val, arg_val, 1)
            return slice_val

        if node.name == "lstr":
            arg_val = self._codegen(node.args[0])
            # arg_val is {i32, i8*} slice
            length = self.builder.zext(
                self.builder.extract_value(arg_val, 0), ir.IntType(64)
            )
            ptr = self.builder.extract_value(arg_val, 1)
            length_plus_1 = self.builder.add(length, ir.Constant(ir.IntType(64), 1))
            new_str = self.builder.call(self.malloc, [length_plus_1])
            self._track_alloc(new_str)
            self.builder.call(self.strncpy, [new_str, ptr, length])
            null_ptr = self.builder.gep(new_str, [length], inbounds=True)
            self.builder.store(ir.Constant(ir.IntType(8), 0), null_ptr)
            return new_str

        if node.name == "tostring":
            arg_val = self._codegen(node.args[0])
            # format based on type
            # allocate 64 bytes for the result string
            buf = self.builder.call(self.malloc, [ir.Constant(ir.IntType(64), 64)])
            self._track_alloc(buf)

            if isinstance(arg_val.type, ir.IntType):
                fmt = self._emit_const_str("%lld")
                # cast if needed
                if arg_val.type.width < 64:
                    arg_val = self.builder.sext(arg_val, ir.IntType(64))
                self.builder.call(self.sprintf, [buf, fmt, arg_val])
            elif isinstance(arg_val.type, (ir.FloatType, ir.DoubleType, ir.HalfType)):
                fmt = self._emit_const_str("%f")
                if not isinstance(arg_val.type, ir.DoubleType):
                    arg_val = self.builder.fpext(arg_val, ir.DoubleType())
                self.builder.call(self.sprintf, [buf, fmt, arg_val])
            else:
                return arg_val
            return buf

        func = self.func_symtab.get(node.name)
        if not func:
            raise LeashError(f"Call to undefined function: '{node.name}'")

        args = []
        for i, arg_expr in enumerate(node.args):
            if i < len(func.args):
                target_llvm = func.args[i].type
                src_type = self._get_leash_type_name(arg_expr)
                resolved_src = self._resolve_type_name(src_type)

                # If target is pointer and source is not a pointer (safe pointer conversion)
                # BUT: skip this for i8* (string) pointers - strings are passed by value
                target_pointee = getattr(target_llvm, "pointee", None)
                is_string_ptr = (
                    isinstance(target_pointee, ir.IntType) and target_pointee.width == 8
                )
                if (
                    isinstance(target_llvm, ir.PointerType)
                    and not is_string_ptr
                    and not (
                        resolved_src.startswith("*") or resolved_src.startswith("&")
                    )
                ):
                    try:
                        v, _ = self._codegen_lvalue(arg_expr)
                    except:
                        v = self._codegen(arg_expr)
                else:
                    v = self._codegen(arg_expr)
                v = self._emit_cast(v, target_llvm)
            else:
                v = self._codegen(arg_expr)
            args.append(v)

        return self.builder.call(func, args)

    def _codegen_StructInit(self, node):
        is_class = False
        struct_info = self.struct_symtab.get(node.name)
        if not struct_info:
            struct_info = self.class_symtab.get(node.name)
            is_class = True
        if not struct_info:
            raise LeashError(f"Undefined struct or class: '{node.name}'")
        struct_type = struct_info["type"]

        if is_class:
            # Allocate class instances on the heap (GC_malloc)
            # To get the size: gep(null, 1) and ptrtoint
            ptr_type = struct_type.as_pointer()
            dummy_ptr = ir.Constant(ptr_type, None)
            size_ptr = self.builder.gep(
                dummy_ptr, [ir.Constant(ir.IntType(32), 1)], inbounds=True
            )
            size = self.builder.ptrtoint(size_ptr, ir.IntType(64))

            ptr_void = self.builder.call(self.malloc, [size])
            ptr = self.builder.bitcast(ptr_void, ptr_type)

            # Initialize vtable pointer at index 0
            vtable_global = struct_info.get("vtable_global")
            if vtable_global:
                vtable_ptr = self.builder.bitcast(
                    vtable_global, ir.IntType(8).as_pointer()
                )
                vtable_field_ptr = self.builder.gep(
                    ptr,
                    [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 0)],
                )
                self.builder.store(vtable_ptr, vtable_field_ptr)

            # Initialize other fields
            for key, expr in node.kwargs:
                idx = struct_info["fields"].get(key)
                if idx is None:
                    raise LeashError(f"Class '{node.name}' has no field named '{key}'")
                field_val = self._codegen(expr)
                # gep on the instance pointer
                field_ptr = self.builder.gep(
                    ptr,
                    [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), idx)],
                )
                self.builder.store(field_val, field_ptr)
            return ptr
        else:
            val = ir.Constant(struct_type, ir.Undefined)
            for key, expr in node.kwargs:
                idx = struct_info["fields"].get(key)
                if idx is None:
                    raise LeashError(
                        f"Struct '{node.name}' has no member named '{key}'"
                    )
                field_val = self._codegen(expr)
                val = self.builder.insert_value(val, field_val, idx)
            return val

    def _codegen_Identifier(self, node):
        ptr, type_name = self.var_symtab.get(node.name, (None, None))
        if not ptr:
            raise LeashError(f"Undefined variable: '{node.name}'")
        val = self.builder.load(ptr)
        resolved = self._resolve_type_name(type_name)
        while resolved.startswith("&"):
            val = self.builder.load(val)
            resolved = resolved[1:]
        return val

    def _codegen_MemberAccess(self, node):
        from .ast_nodes import Identifier

        # Get the leash type name of the base expression
        type_name = self._get_leash_type_name(node.expr)
        resolved = self._resolve_type_name(type_name)

        # 1. Handle Enum .name
        if resolved in self.enum_symtab and node.member == "name":
            enum_info = self.enum_symtab[resolved]
            enum_val = self._codegen(node.expr)
            idx = self._emit_cast(enum_val, ir.IntType(32))
            names_ptr = enum_info["names_arr"]
            member_ptr = self.builder.gep(
                names_ptr, [ir.Constant(ir.IntType(32), 0), idx], inbounds=True
            )
            return self.builder.load(member_ptr)

        # 2. Handle String .size
        val = self._codegen(node.expr)

        is_string = getattr(val.type, "pointee", None) == ir.IntType(8)
        if hasattr(ir, "PointerType") and getattr(ir, "PointerType") is not None:
            is_string = is_string and isinstance(val.type, ir.PointerType)
        elif getattr(val.type, "is_pointer", False):
            is_string = is_string and val.type.is_pointer

        if is_string and node.member == "size":
            length = self.builder.call(self.strlen, [val])
            return self.builder.trunc(length, ir.IntType(32))

        # Handle Array .size
        if (
            resolved
            and resolved.endswith("]")
            and "[" in resolved
            and node.member == "size"
        ):
            length = self.builder.extract_value(val, 0)
            return self.builder.trunc(length, ir.IntType(32))

        # 3. Handle Union variants and .cur
        if resolved in self.union_symtab:
            ptr, _ = self._codegen_lvalue(node.expr)

            union_info = self.union_symtab[resolved]
            data_ptr = self.builder.gep(
                ptr,
                [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 1)],
                inbounds=True,
            )

            if node.member == "cur":
                tag_ptr = self.builder.gep(
                    ptr,
                    [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 0)],
                    inbounds=True,
                )
                tag_val = self.builder.load(tag_ptr)
                return self._union_cur_load(tag_val, data_ptr, union_info)
            elif node.member in union_info["variants"]:
                vdata = union_info["variants"][node.member]
                tag_ptr = self.builder.gep(
                    ptr,
                    [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 0)],
                    inbounds=True,
                )
                tag_val = self.builder.load(tag_ptr)
                self._emit_union_tag_check(
                    tag_val, vdata["index"], node.member, resolved
                )
                typed_ptr = self.builder.bitcast(
                    data_ptr, vdata["llvm_type"].as_pointer()
                )
                return self.builder.load(typed_ptr)

        # 4. Standard Struct access or other l-values
        ptr, _ = self._codegen_lvalue(node)
        return self.builder.load(ptr)

    def _emit_union_tag_check(self, tag_val, expected_idx, member_name, union_name):
        """Emit runtime check: if tag != expected_idx, print error to stderr and exit(1)."""
        ok_bb = self.builder.function.append_basic_block("union_check_ok")
        fail_bb = self.builder.function.append_basic_block("union_check_fail")

        cmp = self.builder.icmp_signed(
            "==", tag_val, ir.Constant(ir.IntType(32), expected_idx)
        )
        self.builder.cbranch(cmp, ok_bb, fail_bb)

        # Fail path: print error and exit
        self.builder.position_at_end(fail_bb)

        # Build a descriptive error message
        # Find the name of the actual active variant based on the tag
        # We can't do that statically, so we'll print the tag index
        err_msg = f"Runtime error: Attempted to access '.{member_name}' on union '{union_name}', but the active variant is different.\n"
        err_bytes = bytearray(err_msg.encode("utf8") + b"\0")
        c_err = ir.Constant(ir.ArrayType(ir.IntType(8), len(err_bytes)), err_bytes)
        g_err = ir.GlobalVariable(
            self.module, c_err.type, name=self.module.get_unique_name("union_err")
        )
        g_err.linkage = "internal"
        g_err.global_constant = True
        g_err.initializer = c_err
        err_ptr = self.builder.bitcast(g_err, ir.IntType(8).as_pointer())

        # Load stderr and call fprintf
        stderr_val = self.builder.load(self.stderr_var)
        self.builder.call(self.fprintf, [stderr_val, err_ptr])
        self.builder.call(self.exit_fn, [ir.Constant(ir.IntType(32), 1)])
        self.builder.unreachable()

        # Continue on the ok path
        self.builder.position_at_end(ok_bb)

    def _union_cur_load(self, tag_val, data_ptr, union_info):
        """Load the current value from a union based on the runtime tag."""
        variants = list(union_info["variants"].items())

        if len(variants) == 1:
            vname, vdata = variants[0]
            typed_ptr = self.builder.bitcast(data_ptr, vdata["llvm_type"].as_pointer())
            return self.builder.load(typed_ptr)

        # Determine the common type to promote all variants into
        has_float = any(
            isinstance(vd["llvm_type"], (ir.FloatType, ir.DoubleType))
            for _, vd in variants
        )
        has_ptr = any(isinstance(vd["llvm_type"], ir.PointerType) for _, vd in variants)

        if has_ptr:
            common_type = ir.IntType(8).as_pointer()
        elif has_float:
            common_type = ir.DoubleType()
        else:
            common_type = ir.IntType(64)

        merge_bb = self.builder.function.append_basic_block("union_cur_merge")

        var_bbs = []
        for vname, vdata in variants:
            bb = self.builder.function.append_basic_block(f"union_cur_{vname}")
            var_bbs.append(bb)

        # Build the if-else chain from current position
        for i, (vname, vdata) in enumerate(variants[:-1]):
            cmp = self.builder.icmp_signed(
                "==", tag_val, ir.Constant(ir.IntType(32), vdata["index"])
            )
            next_check = self.builder.function.append_basic_block(
                f"union_cur_check_{i + 1}"
            )
            self.builder.cbranch(cmp, var_bbs[i], next_check)
            self.builder.position_at_end(next_check)

        # Default: last variant
        self.builder.branch(var_bbs[-1])

        # In each variant BB: load, convert to common type, branch to merge
        incoming = []
        for i, (vname, vdata) in enumerate(variants):
            self.builder.position_at_end(var_bbs[i])
            typed_ptr = self.builder.bitcast(data_ptr, vdata["llvm_type"].as_pointer())
            loaded = self.builder.load(typed_ptr)

            # Convert to common type
            converted = self._convert_to_common(loaded, common_type)
            incoming.append((converted, var_bbs[i]))
            self.builder.branch(merge_bb)

        self.builder.position_at_end(merge_bb)
        phi = self.builder.phi(common_type)
        for val, bb in incoming:
            phi.add_incoming(val, bb)
        return phi

    def _convert_to_common(self, val, common_type):
        """Convert a loaded union variant value to the common type for phi."""
        src = val.type
        dst = common_type
        if src == dst:
            return val

        src_is_int = isinstance(src, ir.IntType)
        src_is_float = isinstance(src, (ir.FloatType, ir.DoubleType))
        src_is_ptr = isinstance(src, ir.PointerType)
        dst_is_int = isinstance(dst, ir.IntType)
        dst_is_float = isinstance(dst, (ir.FloatType, ir.DoubleType))
        dst_is_ptr = isinstance(dst, ir.PointerType)

        if src_is_int and dst_is_int:
            if src.width < dst.width:
                return self.builder.sext(val, dst)
            elif src.width > dst.width:
                return self.builder.trunc(val, dst)
        elif src_is_int and dst_is_float:
            return self.builder.sitofp(val, dst)
        elif src_is_float and dst_is_float:
            if isinstance(src, ir.FloatType):
                return self.builder.fpext(val, dst)
        elif src_is_float and dst_is_int:
            return self.builder.fptosi(val, dst)
        elif src_is_ptr and dst_is_ptr:
            return self.builder.bitcast(val, dst)
        elif src_is_int and dst_is_ptr:
            return self.builder.inttoptr(val, dst)
        elif src_is_ptr and dst_is_int:
            return self.builder.ptrtoint(val, dst)

        return val

    def _codegen_NumberLiteral(self, node):
        if self.current_target_type:
            target_llvm = self._get_llvm_type(self.current_target_type)
            if isinstance(target_llvm, ir.IntType):
                return ir.Constant(target_llvm, node.value)
        return ir.Constant(ir.IntType(32), node.value)

    def _codegen_FloatLiteral(self, node):
        return ir.Constant(ir.DoubleType(), node.value)

    def _codegen_CharLiteral(self, node):
        return ir.Constant(ir.IntType(8), ord(node.value))

    def _codegen_BoolLiteral(self, node):
        return ir.Constant(ir.IntType(1), 1 if node.value else 0)

    def _codegen_NullLiteral(self, node):
        return ir.Constant(ir.IntType(8).as_pointer(), None)

    def _codegen_StringLiteral(self, node):
        s = bytearray(node.value.encode("utf-8") + b"\0")
        c_str = ir.Constant(ir.ArrayType(ir.IntType(8), len(s)), s)
        global_str = ir.GlobalVariable(
            self.module, c_str.type, name=self.module.get_unique_name("str")
        )
        global_str.linkage = "internal"
        global_str.global_constant = True
        global_str.initializer = c_str
        return self.builder.bitcast(global_str, ir.IntType(8).as_pointer())

    def _codegen_ArrayInit(self, node):
        target = self.current_target_type
        elem_type = None
        if target and "[" in target:
            elem_type_name = target.split("[")[0]
            elem_type = self._get_llvm_type(elem_type_name)

        old_target = self.current_target_type
        if target and "[" in target:
            self.current_target_type = target.split("[")[0]
        else:
            self.current_target_type = None

        vals = []
        for e in node.elements:
            v = self._codegen(e)
            if elem_type:
                v = self._emit_cast(v, elem_type)
            vals.append(v)

        self.current_target_type = old_target

        length = len(vals)
        if not elem_type:
            elem_type = ir.IntType(32) if not vals else vals[0].type

        arr_type = ir.ArrayType(elem_type, length)

        is_const = all(isinstance(v, ir.Constant) for v in vals)
        if is_const:
            c_arr = ir.Constant(arr_type, vals)
            global_arr = ir.GlobalVariable(
                self.module, arr_type, name=self.module.get_unique_name("const_arr")
            )
            global_arr.linkage = "internal"
            global_arr.global_constant = True
            global_arr.initializer = c_arr
            arr_ptr = global_arr
        else:
            arr_ptr = self.builder.alloca(arr_type)
            for i, v in enumerate(vals):
                ptr = self.builder.gep(
                    arr_ptr,
                    [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), i)],
                    inbounds=True,
                )
                self.builder.store(v, ptr)

        slice_type = self._get_llvm_type(
            f"{elem_type_name}[]" if elem_type_name else "int[]"
        )
        slice_val = ir.Constant(slice_type, ir.Undefined)

        slice_val = self.builder.insert_value(
            slice_val, ir.Constant(ir.IntType(64), length), 0
        )

        elem_ptr = self.builder.bitcast(arr_ptr, elem_type.as_pointer())
        slice_val = self.builder.insert_value(slice_val, elem_ptr, 1)

        return slice_val

    def _codegen_EnumMemberAccess(self, node):
        enum_info = self.enum_symtab.get(node.enum_name)
        if not enum_info:
            raise LeashError(f"Undefined enum: '{node.enum_name}'")

        try:
            idx = enum_info["members"].index(node.member_name)
        except ValueError:
            raise LeashError(
                f"Enum '{node.enum_name}' has no member named '{node.member_name}'"
            )

        return ir.Constant(ir.IntType(32), idx)

    def _codegen_IndexAccess(self, node):
        ptr, _ = self._codegen_lvalue(node)
        return self.builder.load(ptr)

    def _codegen_TypeConvExpr(self, node):
        val = self._codegen(node.expr)
        dst_type = self._get_llvm_type(node.target_type)

        # Check if src is string (i8*)
        src_is_str = (
            isinstance(val.type, ir.PointerType)
            and isinstance(val.type.pointee, ir.IntType)
            and val.type.pointee.width == 8
        )

        if node.name == "toint":
            if src_is_str:
                res64 = self.builder.call(self.atoll, [val])
                return self._emit_cast(res64, dst_type)
            else:
                return self._emit_cast(val, dst_type)
        elif node.name == "tofloat":
            if src_is_str:
                res64 = self.builder.call(self.atof, [val])
                return self._emit_cast(res64, dst_type)
            else:
                return self._emit_cast(val, dst_type)
        return val

    def _codegen_CastExpr(self, node):
        from .ast_nodes import ThisExpr

        val = self._codegen(node.expr)
        src_type_name = self._get_leash_type_name(node.expr)
        dst_type_name = self._resolve_type_name(node.target_type)

        # Handle class casting (inheritance)
        src_resolved = self._resolve_type_name(src_type_name)
        dst_resolved = self._resolve_type_name(dst_type_name)

        if src_resolved in self.class_symtab and dst_resolved in self.class_symtab:
            # Both are classes - use bitcast for inheritance
            target_type = self._get_llvm_type(node.target_type)
            return self.builder.bitcast(val, target_type)

        target_type = self._get_llvm_type(node.target_type)
        return self._emit_cast(val, target_type)

    def _emit_cast(self, val, target_type):
        """Cast a value to the target LLVM type."""
        src = val.type
        dst = target_type
        if src == dst:
            return val

        src_is_int = isinstance(src, ir.IntType)
        dst_is_int = isinstance(dst, ir.IntType)
        src_is_float = isinstance(src, (ir.FloatType, ir.DoubleType, ir.HalfType))
        dst_is_float = isinstance(dst, (ir.FloatType, ir.DoubleType, ir.HalfType))
        src_is_ptr = isinstance(src, ir.PointerType)
        dst_is_ptr = isinstance(dst, ir.PointerType)

        # int -> int (trunc / zext / sext)
        if src_is_int and dst_is_int:
            if src.width > dst.width:
                return self.builder.trunc(val, dst)
            elif src.width < dst.width:
                return self.builder.zext(val, dst)
            return val
        # float -> float (fpext / fptrunc)
        elif src_is_float and dst_is_float:
            src_size = {ir.HalfType: 16, ir.FloatType: 32, ir.DoubleType: 64}.get(
                type(src), 64
            )
            dst_size = {ir.HalfType: 16, ir.FloatType: 32, ir.DoubleType: 64}.get(
                type(dst), 64
            )
            if src_size < dst_size:
                return self.builder.fpext(val, dst)
            elif src_size > dst_size:
                return self.builder.fptrunc(val, dst)
            return val
        # int -> float
        elif src_is_int and dst_is_float:
            return self.builder.sitofp(val, dst)
        # float -> int
        elif src_is_float and dst_is_int:
            return self.builder.fptosi(val, dst)
        # ptr -> ptr (bitcast)
        elif src_is_ptr and dst_is_ptr:
            return self.builder.bitcast(val, dst)
        # ptr -> int (ptrtoint)
        elif src_is_ptr and dst_is_int:
            return self.builder.ptrtoint(val, dst)
        # int -> ptr (inttoptr)
        elif src_is_int and dst_is_ptr:
            return self.builder.inttoptr(val, dst)

        # Slice to pointer (e.g., char[] to *char)
        if (
            isinstance(src, ir.LiteralStructType)
            and len(src.elements) == 2
            and dst_is_ptr
        ):
            return self.builder.extract_value(val, 1)

        # Pointer to slice (e.g., *char to char[]) - treat as slice of length 1 or unknown
        if (
            src_is_ptr
            and isinstance(dst, ir.LiteralStructType)
            and len(dst.elements) == 2
        ):
            new_slice = ir.Constant(dst, ir.Undefined)
            new_slice = self.builder.insert_value(
                new_slice, ir.Constant(ir.IntType(32), 1), 0
            )
            new_slice = self.builder.insert_value(new_slice, val, 1)
            return new_slice

        # Struct-to-struct (slices)
        if isinstance(src, ir.LiteralStructType) and isinstance(
            dst, ir.LiteralStructType
        ):
            if len(src.elements) == 2 and len(dst.elements) == 2:
                # If it's a slice {i32, T*} to {i32, U*}
                if isinstance(src.elements[1], ir.PointerType) and isinstance(
                    dst.elements[1], ir.PointerType
                ):
                    length = self.builder.extract_value(val, 0)
                    ptr = self.builder.extract_value(val, 1)
                    # Bitcast the pointer part
                    new_ptr = self.builder.bitcast(ptr, dst.elements[1])
                    new_slice = ir.Constant(dst, ir.Undefined)
                    new_slice = self.builder.insert_value(new_slice, length, 0)
                    new_slice = self.builder.insert_value(new_slice, new_ptr, 1)
                    return new_slice

        # Fallback: bitcast if same size, otherwise error
        return self.builder.bitcast(val, dst)

    def get_ir(self):
        return str(self.module)
