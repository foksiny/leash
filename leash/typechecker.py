from .ast_nodes import *
from .errors import LeashError


# Type categories used for compatibility checks
INT_TYPES = {"int", "uint"}
FLOAT_TYPES = {"float"}
NUMERIC_TYPES = INT_TYPES | FLOAT_TYPES


class TypeChecker:
    """Static type checker for Leash AST.

    Runs between parsing and codegen. Reports type errors as LeashError
    and collects warnings for non-fatal issues.
    """

    # Class-level storage for instantiated generics (shared with code generator)
    instantiated_func_nodes = {}  # mangled_name -> Function node
    instantiated_class_nodes = {}  # mangled_name -> ClassDef node

    def __init__(self):
        self.var_types = {}  # name -> type string (local variables)
        self.var_immutable = {}  # name -> bool (True if immutable)
        self.func_types = {}  # name -> (arg_types, return_type)
        self.struct_types = {}  # name -> {field: type}
        self.union_types = {}  # name -> {variant: type}
        self.enum_types = {}  # name -> list of member names
        self.type_aliases = {}  # name -> resolved type string
        self.class_types = {}  # name -> {fields: {name: (type, vis)}, methods: {name: (node, vis)}}
        self.template_types = {}  # name -> True (tracks template parameters like T1, T2)
        self.generic_funcs = {}  # name -> Function node (for generic function templates)
        self.generic_classes = {}  # name -> ClassDef node (for generic class templates)
        self.instantiated_funcs = {}  # (name, type_args_tuple) -> mangled_name
        self.instantiated_classes = {}  # (name, type_args_tuple) -> mangled_name
        self.current_class = None  # name of current class being checked
        self.warnings = []
        self.current_func = None  # name of current function being checked
        self.current_func_node = None  # Function node
        self.current_return_type = None
        self.used_vars = set()  # name
        self.used_params = set()  # name
        self.current_func_params = set()
        self.loop_depth = 0  # Track nesting depth of loops for stop/continue
        self.global_vars = {}  # name -> (type, visibility) for module-level variables
        self.in_works_block = (
            False  # Track if we're inside a works block for error catching
        )
        self.works_error_occured = False  # Flag for errors caught in works block
        self.error_collecting = False  # Whether to collect errors instead of raising
        self.collected_errors = []  # Errors collected in works block

        # Register built-in classes
        self._register_builtin_classes()

    def check(self, program):
        """Run type checking on a Program AST node. Returns list of warnings."""
        # First pass: register all top-level definitions (structs, unions, aliases, functions, templates, global vars)
        for item in program.items:
            if isinstance(item, TemplateDef):
                self._register_template(item)
            elif isinstance(item, StructDef):
                self._register_struct(item)
            elif isinstance(item, UnionDef):
                self._register_union(item)
            elif isinstance(item, EnumDef):
                self._register_enum(item)
            elif isinstance(item, TypeAlias):
                self._register_alias(item)
            elif isinstance(item, ClassDef):
                self._register_class(item)
            elif isinstance(item, Function):
                self._register_function_sig(item)
            elif isinstance(item, GlobalVarDecl):
                self._register_global_var(item)
            elif isinstance(item, NativeImport):
                self._register_native_import(item)

        # Second pass: check function bodies and global var initializers
        for item in program.items:
            if isinstance(item, Function):
                self._check_function(item)
            elif isinstance(item, ClassDef):
                self._check_class(item)
            elif isinstance(item, GlobalVarDecl):
                self._check_global_var(item)

        return self.warnings

    def _register_native_import(self, node):
        """Register function, variable, struct, union, enum, and typedef signatures from a native library import."""
        for name, args, return_type in node.func_declarations:
            arg_types = tuple(arg_type for _, arg_type in args)
            self.func_types[name] = (arg_types, return_type)
        for name, var_type in node.var_declarations:
            self.global_vars[name] = (var_type, "pub")
        for _, name, fields in node.struct_declarations:
            self.struct_types[name] = {fname: ftype for fname, ftype in fields}
        for _, name, variants in node.union_declarations:
            self.union_types[name] = {vname: vtype for vname, vtype in variants}
        for _, name, members in node.enum_declarations:
            self.enum_types[name] = members
        for _, name, target_type in node.typedef_declarations:
            self.type_aliases[name] = target_type

    def _register_builtin_classes(self):
        """Register built-in classes like File."""
        # File class
        # The File class has a single field: the FILE* pointer (represented as int for simplicity)
        # In codegen, this will be handled specially
        self.class_types["File"] = {
            "fields": {},  # No user-visible fields, internal state only
            "methods": {
                # Static methods - we use placeholder Function nodes for type checking
                "open": (
                    Function(
                        "File_open",
                        [("filename", "string"), ("mode", "string")],
                        "File",
                        None,
                    ),
                    "pub",
                    True,
                    False,
                ),
                "rename": (
                    Function(
                        "File_rename",
                        [("oldname", "string"), ("newname", "string")],
                        "int",
                        None,
                    ),
                    "pub",
                    True,
                    False,
                ),
                "delete": (
                    Function("File_delete", [("filename", "string")], "int", None),
                    "pub",
                    True,
                    False,
                ),
                # Instance methods
                "read": (
                    Function("File_read", [("this", "File")], "string", None),
                    "pub",
                    False,
                    False,
                ),
                "write": (
                    Function(
                        "File_write",
                        [("this", "File"), ("text", "string")],
                        "int",
                        None,
                    ),
                    "pub",
                    False,
                    False,
                ),
                "close": (
                    Function("File_close", [("this", "File")], "int", None),
                    "pub",
                    False,
                    False,
                ),
                "writeb": (
                    Function(
                        "File_writeb",
                        [("this", "File"), ("btext", "char[]")],
                        "int",
                        None,
                    ),
                    "pub",
                    False,
                    False,
                ),
                "readb": (
                    Function("File_readb", [("this", "File")], "char[]", None),
                    "pub",
                    False,
                    False,
                ),
                "readln": (
                    Function("File_readln", [("this", "File")], "string", None),
                    "pub",
                    False,
                    False,
                ),
                "readlnb": (
                    Function("File_readlnb", [("this", "File")], "char[]", None),
                    "pub",
                    False,
                    False,
                ),
                "replace": (
                    Function(
                        "File_replace",
                        [("this", "File"), ("oldstr", "string"), ("newstr", "string")],
                        "int",
                        None,
                    ),
                    "pub",
                    False,
                    False,
                ),
                "replaceall": (
                    Function(
                        "File_replaceall",
                        [("this", "File"), ("oldstr", "string"), ("newstr", "string")],
                        "int",
                        None,
                    ),
                    "pub",
                    False,
                    False,
                ),
                "rewind": (
                    Function("File_rewind", [("this", "File")], "void", None),
                    "pub",
                    False,
                    False,
                ),
            },
            "parent": None,
        }

    # ── Registration ────────────────────────────────────────────────

    def _register_template(self, node):
        """Register a template parameter like 'def T1 : template;'"""
        self.template_types[node.name] = True

    def _register_struct(self, node):
        fields = {}
        for fname, ftype in node.fields:
            if fname in fields:
                self._error(
                    f"Duplicate field '{fname}' in struct '{node.name}'",
                    node=node,
                    tip="Each field in a struct must have a unique name.",
                )
            resolved = self._resolve(ftype)
            if not self._is_valid_type(resolved):
                raise LeashError(
                    f"Struct '{node.name}' field '{fname}' has unknown type '{ftype}'"
                )
            fields[fname] = ftype
        if not fields:
            self._warn(
                f"Struct '{node.name}' is empty.",
                node=node,
                tip="An empty struct doesn't store any data.",
            )
        self.struct_types[node.name] = fields

    def _register_union(self, node):
        variants = {}
        for vname, vtype in node.variants:
            if vname in variants:
                self._error(
                    f"Duplicate variant '{vname}' in union '{node.name}'",
                    node=node,
                    tip="Each variant in a union must have a unique name.",
                )
            resolved = self._resolve(vtype)
            if not self._is_valid_type(resolved):
                raise LeashError(
                    f"Union '{node.name}' variant '{vname}' has unknown type '{vtype}'"
                )
            variants[vname] = vtype
        if not variants:
            self._warn(f"Union '{node.name}' is empty.", node=node)
        self.union_types[node.name] = variants

    def _register_enum(self, node):
        seen = set()
        for member in node.members:
            if member in seen:
                raise LeashError(
                    f"Duplicate member '{member}' in enum '{node.name}'",
                    tip=f"Enum members must have unique names within the same enum definition.",
                )
            seen.add(member)
        if not node.members:
            self._warn(f"Enum '{node.name}' is empty.", node=node)
        self.enum_types[node.name] = node.members

    def _register_alias(self, node):
        self.type_aliases[node.name] = node.target_type

    def _register_class(self, node):
        if (
            node.name in self.class_types
            or node.name in self.struct_types
            or node.name in self.union_types
        ):
            self._error(f"Redefinition of type '{node.name}'", node=node)

        # Check if this class uses any template parameters
        uses_templates = bool(node.type_params)

        if not uses_templates:
            # Check if any field uses template types
            for f in node.fields:
                if self._uses_template_type(f.var_type):
                    uses_templates = True
                    break

        if uses_templates:
            # Store the template params if not explicitly declared
            if not node.type_params:
                template_set = set()
                for f in node.fields:
                    self._collect_template_types(f.var_type, template_set)
                node.type_params = list(template_set)
            self.generic_classes[node.name] = node
            # Also register the generic class in class_types so that member access
            # on 'this' inside its methods works correctly. This allows argument
            # usage tracking for parameters passed to vector methods.
            fields = {}
            # Inherit parent fields if any
            if node.parent:
                if node.parent in self.class_types:
                    parent_info = self.class_types[node.parent]
                    for fname, (ftype, fvis) in parent_info["fields"].items():
                        fields[fname] = (ftype, fvis)
                else:
                    self._error(
                        f"Parent class '{node.parent}' is not defined",
                        node=node,
                        tip=f"Make sure '{node.parent}' is defined before '{node.name}'.",
                    )
            for f in node.fields:
                if f.name in fields:
                    self._error(
                        f"Duplicate field '{f.name}' in class '{node.name}'", node=f
                    )
                fields[f.name] = (f.var_type, f.visibility)
            methods = {}
            if node.parent and node.parent in self.class_types:
                parent_info = self.class_types[node.parent]
                for mname, (fnc, vis, is_static, m_is_imut) in parent_info[
                    "methods"
                ].items():
                    methods[mname] = (fnc, vis, is_static, m_is_imut)
            for m in node.methods:
                # Check if trying to override an imut method
                if m.fnc.name in methods:
                    parent_method = methods[m.fnc.name]
                    parent_is_imut = parent_method[3]
                    if parent_is_imut:
                        self._error(
                            f"Cannot override imut method '{m.fnc.name}' from parent class '{node.parent}'",
                            node=m.fnc,
                            tip="Remove 'imut' from the parent method to allow overriding.",
                        )
                methods[m.fnc.name] = (m.fnc, m.visibility, m.is_static, m.is_imut)
            self.class_types[node.name] = {
                "fields": fields,
                "methods": methods,
                "parent": node.parent,
            }
            return

        # Validate parent class exists
        if node.parent and node.parent not in self.class_types:
            self._error(
                f"Parent class '{node.parent}' is not defined",
                node=node,
                tip=f"Make sure '{node.parent}' is defined before '{node.name}'.",
            )

        fields = {}
        # Inherit parent fields
        if node.parent:
            parent_info = self.class_types[node.parent]
            for fname, (ftype, fvis) in parent_info["fields"].items():
                fields[fname] = (ftype, fvis)

        for f in node.fields:
            if f.name in fields:
                self._error(
                    f"Duplicate field '{f.name}' in class '{node.name}'", node=f
                )
            fields[f.name] = (f.var_type, f.visibility)

        methods = {}
        # Inherit parent methods
        if node.parent:
            parent_info = self.class_types[node.parent]
            for mname, (fnc, vis, is_static, m_is_imut) in parent_info[
                "methods"
            ].items():
                methods[mname] = (fnc, vis, is_static, m_is_imut)

        for m in node.methods:
            # Check if trying to override an imut method
            if m.fnc.name in methods:
                parent_method = methods[m.fnc.name]
                parent_is_imut = parent_method[3]  # is_imut is 4th element
                if parent_is_imut:
                    self._error(
                        f"Cannot override imut method '{m.fnc.name}' from parent class '{node.parent}'",
                        node=m.fnc,
                        tip="Remove 'imut' from the parent method to allow overriding.",
                    )
            # Add or replace method
            methods[m.fnc.name] = (m.fnc, m.visibility, m.is_static, m.is_imut)

        self.class_types[node.name] = {
            "fields": fields,
            "methods": methods,
            "parent": node.parent,
        }

    def _register_global_var(self, node):
        """Register a global variable."""
        # Resolve and validate the type
        resolved_type = self._resolve(node.var_type)
        if not self._is_valid_type(resolved_type):
            self._error(
                f"Global variable '{node.name}' has unknown type '{node.var_type}'",
                node=node,
            )
        if node.name in self.global_vars:
            self._error(f"Global variable '{node.name}' already defined", node=node)
        self.global_vars[node.name] = (node.var_type, node.visibility)

    def _check_global_var(self, node):
        """Check a global variable's initializer if present."""
        if node.value is not None:
            # Check the initializer expression
            init_type = self._infer_type(node.value)
            if not self._types_compatible(init_type, node.var_type):
                self._error(
                    f"Cannot initialize global variable '{node.name}' of type '{node.var_type}' with value of type '{init_type}'",
                    node=node,
                )

    def _register_function_sig(self, node):
        # Check if this function uses any template parameters
        # Template params can be explicitly declared or inferred from usage
        uses_templates = bool(node.type_params)

        # Also check if the function uses any registered template types
        if not uses_templates:
            for _, arg_type in node.args:
                if self._uses_template_type(arg_type):
                    uses_templates = True
                    break
            if not uses_templates and self._uses_template_type(node.return_type):
                uses_templates = True

        # Also check for multi-type syntax: [int, float, ...]
        if not uses_templates:
            for _, arg_type in node.args:
                if self._is_multi_type(arg_type):
                    uses_templates = True
                    break
            if not uses_templates and self._is_multi_type(node.return_type):
                uses_templates = True

        if uses_templates:
            # Store the template params if not explicitly declared
            if not node.type_params:
                # Find all template types used
                template_set = set()
                type_param_map = {}  # Map from multi-type string to generated param name
                for _, arg_type in node.args:
                    self._collect_template_types(arg_type, template_set)
                    self._extract_multi_type_params(
                        arg_type, template_set, type_param_map
                    )
                self._collect_template_types(node.return_type, template_set)
                self._extract_multi_type_params(
                    node.return_type, template_set, type_param_map
                )

                # Convert multi-types to single type params
                new_args = []
                for arg_name, arg_type in node.args:
                    if arg_type in type_param_map:
                        new_args.append((arg_name, type_param_map[arg_type]))
                    else:
                        new_args.append((arg_name, arg_type))
                node.args = tuple(new_args)

                if node.return_type in type_param_map:
                    node.return_type = type_param_map[node.return_type]

                node.type_params = list(template_set)
            self.generic_funcs[node.name] = node
            return
        arg_types = [t for _, t in node.args]
        self.func_types[node.name] = (arg_types, node.return_type)

    def _is_multi_type(self, type_name):
        """Check if a type name has multi-type syntax like [int, float]."""
        if not type_name:
            return False
        type_name = self._strip_imut(type_name)
        return type_name.startswith("[") and "]" in type_name

    def _extract_multi_type_params(self, type_name, template_set, type_param_map):
        """Extract type parameters from multi-type syntax like [int, float]."""
        if not type_name:
            return
        type_name = self._strip_imut(type_name)
        if not self._is_multi_type(type_name):
            return
        # Extract types from [int, float, ...]
        inner = type_name[1:-1]  # Remove [ and ]
        # Handle nested brackets in types like vec<int>
        types = []
        depth = 0
        current = ""
        for c in inner:
            if c == "<":
                depth += 1
                current += c
            elif c == ">":
                depth -= 1
                current += c
            elif c == "," and depth == 0:
                types.append(current.strip())
                current = ""
            else:
                current += c
        if current.strip():
            types.append(current.strip())

        # Generate a unique type parameter name for this multi-type
        type_param_name = f"_T_{len(template_set)}"

        # Store the mapping from original type to generated param
        type_param_map[type_name] = type_param_name

        # Add the generated type parameter
        if type_param_name not in template_set:
            template_set.add(type_param_name)

    def _uses_template_type(self, type_name):
        """Check if a type name contains any template parameters."""
        if not type_name:
            return False
        # Strip qualifiers
        if type_name.startswith("imut "):
            type_name = type_name[5:]
        if type_name.startswith("*") or type_name.startswith("&"):
            type_name = type_name[1:]
        # Check vec<T>
        if type_name.startswith("vec<") and type_name.endswith(">"):
            inner = type_name[4:-1]
            return self._uses_template_type(inner) or inner in self.template_types
        # Check for template type
        if type_name in self.template_types:
            return True
        return False

    def _collect_template_types(self, type_name, template_set):
        """Collect all template types used in a type name."""
        if not type_name:
            return
        # Strip qualifiers
        if type_name.startswith("imut "):
            type_name = type_name[5:]
        if type_name.startswith("*") or type_name.startswith("&"):
            type_name = type_name[1:]
        # Check vec<T>
        if type_name.startswith("vec<") and type_name.endswith(">"):
            inner = type_name[4:-1]
            self._collect_template_types(inner, template_set)
            return
        # Check for template type
        if type_name in self.template_types:
            template_set.add(type_name)

    # ── Type resolution ─────────────────────────────────────────────

    def _strip_imut(self, type_name):
        """Strip the 'imut ' prefix from a type name if present."""
        if isinstance(type_name, str) and type_name.startswith("imut "):
            return type_name[5:]
        return type_name

    def _is_imut(self, type_name):
        """Check if a type string has the imut qualifier."""
        return isinstance(type_name, str) and type_name.startswith("imut ")

    def _resolve(self, type_name):
        """Resolve type aliases (strips imut first, then re-applies if needed)."""
        stripped = self._strip_imut(type_name)
        visited = set()
        while stripped in self.type_aliases and stripped not in visited:
            visited.add(stripped)
            stripped = self.type_aliases[stripped]

        # Handle generic class instantiation
        # Format: ClassName<Type1, Type2, ...>
        if "<" in stripped and stripped.endswith(">"):
            base_class = stripped.split("<")[0]
            type_args_str = stripped[len(base_class) + 1 : -1]
            type_args = [a.strip() for a in type_args_str.split(",")]
            if base_class in self.generic_classes:
                key = (base_class, tuple(type_args))
                if key in self.instantiated_classes:
                    return self.instantiated_classes[key]

        return stripped

    def _base_type(self, type_name):
        """Get the base type category (strip bit-widths, array brackets, imut)."""
        t = self._resolve(type_name)
        if t.startswith("*"):
            return "ptr"
        if t.startswith("&"):
            return "sptr"
        if t.endswith("]") and "[" in t:
            return "array"
        if t.startswith("int<") or t == "int":
            return "int"
        if t.startswith("uint<") or t == "uint":
            return "uint"
        if t.startswith("float<") or t == "float":
            return "float"
        if t.startswith("vec<") and t.endswith(">"):
            return "vec"
        if t in self.class_types:
            return "class"
        # Check for instantiated generic class
        if "<" in t and t.endswith(">"):
            base_class = t.split("<")[0]
            if base_class in self.generic_classes:
                return "class"
        return t

    def _is_valid_type(self, type_name):
        """Check if a type name is a known type."""
        t = self._resolve(type_name)
        if t.startswith("*") or t.startswith("&"):
            return self._is_valid_type(t[1:])
        # Handle function pointer types: fnc(...) : ...
        if self._is_function_pointer_type(t):
            return True
        # Strip array suffix
        if t.endswith("]") and "[" in t:
            t = t.split("[")[0]
        # Check if it's a template parameter
        if t in self.template_types:
            return True
        base = self._base_type(t)
        if base in ("int", "uint", "float", "string", "char", "bool", "void", "vec"):
            return True
        if (
            t in self.struct_types
            or t in self.union_types
            or t in self.enum_types
            or t in self.class_types
        ):
            return True
        # Check for instantiated generic classes
        if t in [name for (name, _), _ in self.instantiated_classes.items()]:
            return True
        # Check if this is a generic class instantiation that hasn't been instantiated yet
        # Format: ClassName<Type1, Type2, ...>
        if "<" in t and t.endswith(">"):
            base_class = t.split("<")[0]
            type_args_str = t[len(base_class) + 1 : -1]
            type_args = [a.strip() for a in type_args_str.split(",")]
            if base_class in self.generic_classes:
                # Instantiate the generic class
                self._instantiate_generic_class(base_class, type_args, None)
                return True
        return False

    def _is_numeric(self, type_name):
        b = self._base_type(type_name)
        return b in ("int", "uint", "float")

    def _is_function_pointer_type(self, type_name):
        """Check if a type string represents a function pointer type."""
        if not isinstance(type_name, str):
            return False
        return type_name.startswith("fnc(") and " : " in type_name

    def _get_function_pointer_signature(self, type_name):
        """Extract (param_types, return_type) from a function pointer type string."""
        if not self._is_function_pointer_type(type_name):
            return None, None
        # Format: fnc(param1, param2) : return_type
        inner = type_name[4 : type_name.index(")")]
        return_type = type_name.split(" : ")[-1]
        param_types = [p.strip() for p in inner.split(",")] if inner else []
        return param_types, return_type

    def _check_type_conv(self, expr):
        src_t = self._infer_type(expr.expr)
        dst_t = expr.target_type

        # We allow toint(int, ANY string/number)
        if expr.name == "toint":
            if not self._is_int_family(dst_t):
                raise LeashError(
                    f"toint requires a target integer type, got '{dst_t}'", node=expr
                )
            return dst_t
        elif expr.name == "tofloat":
            if not self._is_float_family(dst_t):
                raise LeashError(
                    f"tofloat requires a target float type, got '{dst_t}'", node=expr
                )
            return dst_t
        return dst_t

    def _is_int_family(self, type_name):
        b = self._base_type(type_name)
        return b in ("int", "uint", "char", "bool")

    def _is_float_family(self, type_name):
        b = self._base_type(type_name)
        return b == "float"

    def _error(self, msg, node=None, tip=None):
        """Create a LeashError with position info from an AST node."""
        line = getattr(node, "line", None) if node else None
        col = getattr(node, "col", None) if node else None
        if self.in_works_block and self.error_collecting:
            self.collected_errors.append(
                {"msg": msg, "line": line, "col": col, "tip": tip}
            )
            self.works_error_occured = True
        else:
            raise LeashError(msg, line=line, col=col, tip=tip)

    def _warn(self, msg, node=None, tip=None):
        """Add a warning with position info."""
        line = getattr(node, "line", None) if node else None
        col = getattr(node, "col", None) if node else None
        # Store as a structured dict for better formatting in CLI
        self.warnings.append({"msg": msg, "line": line, "col": col, "tip": tip})

    def _types_compatible(self, src, dst):
        """Check if src type can be assigned into dst type."""
        src_r = self._resolve(src)
        dst_r = self._resolve(dst)

        if src_r == dst_r:
            return True

        # Handle function pointer types
        if self._is_function_pointer_type(src_r) and self._is_function_pointer_type(
            dst_r
        ):
            return src_r == dst_r

        # Allow generic class template to be assigned to any of its instantiations
        # e.g., Hash (template) can be used where Hash<string, int> is expected
        if src in self.generic_classes:
            # Destination might be a generic instantiation using <> syntax (e.g., Hash<string, int>)
            # or a mangled name (e.g., Hash_string_int)
            if dst.startswith(src + "<") or dst.startswith(src + "_"):
                return True

        # nil is compatible with any type
        if src_r == "nil":
            return True

        # Handle pointers (including safe pointer compatibility with unsafe)
        if src_r.startswith("&") and dst_r.startswith("*"):
            return self._types_compatible(src_r[1:], dst_r[1:])
        if src_r.startswith("*") and dst_r.startswith("*"):
            return self._types_compatible(src_r[1:], dst_r[1:])
        if src_r.startswith("&") and dst_r.startswith("&"):
            return self._types_compatible(src_r[1:], dst_r[1:])

        # Handle slices (arrays) to pointers compatibility
        if "[" in src_r and "]" in src_r and dst_r.startswith("*"):
            src_elem = src_r.split("[")[0]
            dst_elem = dst_r[1:]
            if self._types_compatible(src_elem, dst_elem):
                return True

        # Handle arrays
        if "[" in src_r and "]" in src_r and "[" in dst_r and "]" in dst_r:
            src_elem = src_r.split("[")[0]
            dst_elem = dst_r.split("[")[0]
            return self._types_compatible(src_elem, dst_elem)

        src_b = self._base_type(src_r)
        dst_b = self._base_type(dst_r)

        # Allow passing T to a function expecting &T (smart pointer)
        if dst_r.startswith("&"):
            if self._types_compatible(src_r, dst_r[1:]):
                return True

        # Numeric types are broadly compatible with each other
        if src_b in ("int", "uint", "float", "char", "bool") and dst_b in (
            "int",
            "uint",
            "float",
            "char",
            "bool",
        ):
            return True

        # Union accepts any of its variant types
        if dst_r in self.union_types:
            for _, vtype in self.union_types[dst_r].items():
                if self._types_compatible(src, vtype):
                    return True

        # Enums are only compatible with the same enum type or int
        if src_r in self.enum_types and dst_r in self.enum_types:
            if src_r != dst_r:
                raise LeashError(
                    f"Cannot compare or assign different enum types: '{src_r}' and '{dst_r}'",
                    tip="To compare different enums, cast one to int: `(int)Enum::Member == (int)OtherEnum::OtherMember`",
                )
            return True

        if src_r in self.enum_types and dst_b == "int":
            return True
        if dst_r in self.enum_types and src_b == "int":
            return True

        # Class compatibility
        if src_r in self.class_types and dst_r in self.class_types:
            # Exact match
            if src_r == dst_r:
                return True
            # Check if src is a subclass of dst (upcasting)
            if self._is_subclass_of(src_r, dst_r):
                return True

        # Handle placeholder-generic class compatibility
        # If src is a placeholder-instantiated generic (e.g., Hash__K__V)
        # and dst is the same generic with concrete types (e.g., Hash_string_int)
        # they are compatible
        src_base = self._get_generic_base_name(src_r)
        dst_base = self._get_generic_base_name(dst_r)
        if src_base and dst_base and src_base == dst_base:
            return True

        return False

    def _check_visibility(self, type_name, member_name, is_method, node):
        if type_name not in self.class_types:
            return

        cls = self.class_types[type_name]
        kind = "methods" if is_method else "fields"
        if member_name not in cls[kind]:
            return  # Let the caller handle missing member

        info = cls[kind][member_name]
        vis = info[1]
        if vis == "priv" and self.current_class != type_name:
            self._error(
                f"Cannot access private {'method' if is_method else 'field'} '{member_name}' of class '{type_name}'",
                node=node,
            )

    def _is_subclass_of(self, child, parent):
        """Check if child is a subclass of parent (directly or transitively)."""
        if child == parent:
            return True
        if child not in self.class_types:
            return False
        current = child
        visited = set()
        while current:
            if current in visited:
                break
            visited.add(current)
            info = self.class_types.get(current)
            if not info:
                break
            current = info.get("parent")
            if current == parent:
                return True
        return False

    def _is_placeholder_generic(self, type_name):
        """Check if a type name is a placeholder-instantiated generic (e.g., Hash__K__V)."""
        if not type_name:
            return False
        # Placeholder generic names have format: BaseName__Param1__Param2...
        # where params start with underscore (e.g., _K, _V)
        if "__" not in type_name:
            return False
        parts = type_name.split("__")
        if len(parts) < 2:
            return False
        # Check if all parts after the base name start with underscore
        for part in parts[1:]:
            if not part.startswith("_"):
                return False
        return True

    def _is_concrete_generic(self, type_name):
        """Check if a type name is a concrete generic (e.g., Hash_string_int)."""
        if not type_name:
            return False
        # Concrete generic names have format: BaseName_type1_type2...
        # where type parts are actual types (not starting with underscore)
        if "_" not in type_name:
            return False
        # Check if it's in the class_types (which means it was instantiated)
        return type_name in self.class_types

    def _get_generic_base_name(self, type_name):
        """Extract the base class name from a mangled generic type name."""
        if not type_name:
            return None
        # For placeholder generic: Hash__K__V -> Hash
        if "__" in type_name and type_name.startswith("Hash"):
            return type_name.split("__")[0]
        # For concrete generic: Hash_string_int -> Hash
        # Look for pattern: BaseName_type1_type2...
        # where BaseName is a known generic class
        for base_name in self.generic_classes:
            prefix = base_name + "_"
            if type_name.startswith(prefix):
                # Verify the rest are valid type names
                rest = type_name[len(prefix) :]
                # Check if it looks like mangled type args (contains only alphanumeric and _)
                if all(c.isalnum() or c == "_" for c in rest):
                    return base_name
        return None

    # ── Function checking ───────────────────────────────────────────

    def _check_function(self, node):
        # Reset usage tracking for new function
        saved_types = self.var_types.copy()
        saved_imut = self.var_immutable.copy()
        saved_used_vars = self.used_vars.copy()
        saved_used_params = self.used_params.copy()
        saved_params = self.current_func_params.copy()

        self.current_func = node.name
        self.current_func_node = node
        self.current_return_type = node.return_type
        self.used_vars = set()
        self.used_params = set()
        self.current_func_params = set()

        if not node.body:
            self._warn(
                f"Function '{node.name}' has an empty body.",
                node=node,
                tip="Did you forget to implement it? Or maybe you wanted a `void` function that does nothing?",
            )

        if len(node.args) > 8:
            self._warn(
                f"Function '{node.name}' has {len(node.args)} parameters.",
                node=node,
                tip="Functions with too many parameters are harder to read and maintain. Consider grouping them in a `struct`.",
            )

        # Register args as local vars
        for arg_name, arg_type in node.args:
            is_imut = self._is_imut(arg_type)
            bare_type = self._strip_imut(arg_type)
            self.var_types[arg_name] = bare_type
            self.var_immutable[arg_name] = is_imut
            self.current_func_params.add(arg_name)

        last_was_return = self._check_statements(node.body)

        bare_ret = self._strip_imut(node.return_type) if node.return_type else "void"
        if bare_ret != "void" and not last_was_return:
            self._warn(
                f"Function '{node.name}' might not return a value on all paths.",
                node=node,
                tip=f"This function is declared to return '{bare_ret}', but its body doesn't end with a `return` statement.",
            )

        # Post-check: usage
        for var_name in self.var_types:
            if var_name not in saved_types:  # Local variable
                if (
                    var_name not in self.used_vars
                    and var_name not in self.current_func_params
                ):
                    if var_name != "args":  # Specialize for 'args' in main
                        self._warn(
                            f"Unused local variable '{var_name}' in function '{node.name}'",
                            tip=f"If you don't need this variable, consider removing it.",
                        )

        for param_name in self.current_func_params:
            if param_name not in self.used_params:
                if node.name == "main" and param_name == "args":
                    pass  # Allow unused args in main
                else:
                    self._warn(
                        f"Unused parameter '{param_name}' in function '{node.name}'",
                        tip=f"If the function logic doesn't require this parameter, consider removing it or renaming it to `_{param_name}`.",
                    )

        # Restore
        self.var_types = saved_types
        self.var_immutable = saved_imut
        self.used_vars = saved_used_vars
        self.used_params = saved_used_params
        self.current_func_params = saved_params
        self.current_func = None
        self.current_func_node = None
        self.current_return_type = None

    def _check_class(self, node):
        self.current_class = node.name
        # Register fields and methods in scope? Maybe not fields directly, but methods should be checked.
        # Actually fields are accessed via 'this' or '.'
        for f in node.fields:
            resolved = self._resolve(f.var_type)
            if not self._is_valid_type(resolved):
                self._error(
                    f"Class '{node.name}' field '{f.name}' has unknown type '{f.var_type}'",
                    node=f,
                )

        for m in node.methods:
            # Add 'this' to scope for instance methods
            if not m.is_static:
                self.var_types["this"] = node.name
                self.var_immutable["this"] = True

            self._check_function(m.fnc)

            if not m.is_static:
                del self.var_types["this"]
                del self.var_immutable["this"]

        self.current_class = None

    def _check_statements(self, statements):
        """Check a list of statements, checking for unreachable code. Returns True if last stmt is return."""
        was_returned = False
        for stmt in statements:
            if was_returned:
                self._warn(
                    "Unreachable code detected after return statement.",
                    node=stmt,
                    tip="Any statements after a `return`, `break` or `continue` will never be executed.",
                )
                break  # Only warn once per block

            self._check_stmt(stmt)

            if isinstance(stmt, ReturnStatement):
                was_returned = True
        return was_returned

    # ── Statement checking ──────────────────────────────────────────

    def _check_stmt(self, stmt):
        if isinstance(stmt, VariableDecl):
            self._check_var_decl(stmt)
        elif isinstance(stmt, Assignment):
            self._check_assignment(stmt)
        elif isinstance(stmt, ReturnStatement):
            self._check_return(stmt)
        elif isinstance(stmt, ShowStatement):
            for i, arg in enumerate(stmt.args):
                t = self._infer_type(arg)
                if t:
                    resolved = self._resolve(t)
                    # Check if it's an array
                    if "[" in resolved and "]" in resolved:
                        if resolved != "char[]":
                            self._error(
                                f"Argument {i + 1} of show() is an array ('{t}'), which is not supported.",
                                node=arg,
                                tip="To print an array, use a `foreach` loop to iterate over its elements: `foreach i, v in<array> my_arr { show(v); }`",
                            )
                    # Check if it's a struct
                    if resolved in self.struct_types:
                        self._error(
                            f"Argument {i + 1} of show() is a struct ('{resolved}'), which is not supported.",
                            node=arg,
                            tip='To print a struct, use a `foreach` loop to iterate over its members: `foreach k, v in<struct> my_struct { show(k, ": ", v); }`',
                        )
        elif isinstance(stmt, ExpressionStatement):
            self._infer_type(stmt.expr)
        elif isinstance(stmt, IfStatement):
            self._check_if(stmt)
        elif isinstance(stmt, WhileStatement):
            self.loop_depth += 1
            self._infer_type(stmt.condition)
            if not stmt.body:
                self._warn(
                    "Empty `while` loop body.",
                    node=stmt,
                    tip="This loop will spin and probably hang your program if it runs. Did you forget to add logic or a break condition?",
                )
            self._check_statements(stmt.body)
            self.loop_depth -= 1
        elif isinstance(stmt, ForStatement):
            self.loop_depth += 1
            self._check_stmt(stmt.init)
            self._infer_type(stmt.condition)
            self._check_stmt(stmt.step)
            if not stmt.body:
                self._warn(
                    "Empty `for` loop body.",
                    node=stmt,
                    tip="This loop will execute its condition and step repeatedly but do nothing within the body.",
                )
            self._check_statements(stmt.body)
            self.loop_depth -= 1
        elif isinstance(stmt, DoWhileStatement):
            self.loop_depth += 1
            if not stmt.body:
                self._warn("Empty `do-while` loop body.", node=stmt)
            self._check_statements(stmt.body)
            self._infer_type(stmt.condition)
            self.loop_depth -= 1
        elif isinstance(stmt, ForeachStructStatement):
            # Note: foreach over structs is unrolled and does not support stop/continue at runtime
            self._infer_type(stmt.struct_expr)
            self.var_types[stmt.name_var] = "string"
            self.var_types[stmt.value_var] = "int"  # Approximate
            self._check_statements(stmt.body)
        elif isinstance(stmt, ForeachArrayStatement):
            self.loop_depth += 1
            arr_t = self._infer_type(stmt.array_expr)
            elem_t = "int"
            if arr_t and "[" in arr_t:
                elem_t = arr_t.split("[")[0]
            self.var_types[stmt.index_var] = "int"
            self.var_types[stmt.value_var] = elem_t
            self._check_statements(stmt.body)
            self.loop_depth -= 1
        elif isinstance(stmt, ForeachStringStatement):
            self.loop_depth += 1
            self._infer_type(stmt.string_expr)
            self.var_types[stmt.index_var] = "int"
            self.var_types[stmt.char_var] = "char"
            self._check_statements(stmt.body)
            self.loop_depth -= 1
        elif isinstance(stmt, ForeachVectorStatement):
            self.loop_depth += 1
            vec_t = self._infer_type(stmt.vector_expr)
            elem_t = "any"  # default if unknown
            if vec_t and vec_t.startswith("vec<"):
                elem_t = vec_t[4:-1]
            self.var_types[stmt.index_var] = "int"
            self.var_types[stmt.value_var] = elem_t
            self._check_statements(stmt.body)
            self.loop_depth -= 1
        elif isinstance(stmt, StopStatement):
            if self.loop_depth == 0:
                self._error(
                    "`stop` can only be used inside a loop",
                    node=stmt,
                    tip="`stop` (break) is used to exit a loop early. It can only be used within `while`, `for`, `do-while`, or `foreach` loops.",
                )
        elif isinstance(stmt, ContinueStatement):
            if self.loop_depth == 0:
                self._error(
                    "`continue` can only be used inside a loop",
                    node=stmt,
                    tip="`continue` skips to the next iteration of a loop. It can only be used within `while`, `for`, `do-while`, or `foreach` loops.",
                )
        elif isinstance(stmt, WorksOtherwiseStatement):
            self.in_works_block = True
            self.works_error_occured = False
            self.error_collecting = True
            self.collected_errors = []
            try:
                self._check_statements(stmt.body)
            except:
                pass
            finally:
                self.error_collecting = False
                self.in_works_block = False

            err_msg = "Runtime error in works block"
            if self.works_error_occured and self.collected_errors:
                err = self.collected_errors[0]
                err_msg = err.get("msg", "Runtime error in works block")

            self.var_types[stmt.err_var] = "string"
            self.var_immutable[stmt.err_var] = False

            if hasattr(stmt, "err_msg"):
                stmt.err_msg = err_msg
            else:
                stmt.err_msg = err_msg

            self._check_statements(stmt.otherwise_block)
            del self.var_types[stmt.err_var]
            del self.var_immutable[stmt.err_var]

    def _check_var_decl(self, stmt):
        if stmt.name in self.var_types:
            self._error(
                f"Redefinition of variable '{stmt.name}' in the same function",
                node=stmt,
                tip=f"Variable '{stmt.name}' is already defined. Use a different name or just assign to it if you want to change its value.",
            )
        decl_type = stmt.var_type
        is_imut = self._is_imut(decl_type)
        bare_decl_type = self._strip_imut(decl_type)
        resolved = self._resolve(bare_decl_type)

        if bare_decl_type == "void":
            self._error(
                "Cannot declare a variable with type 'void'.",
                node=stmt,
                tip="'void' is for functions that don't return a value, not for variables.",
            )

        if not self._is_valid_type(resolved):
            self._error(
                f"Variable '{stmt.name}' declared with unknown type '{bare_decl_type}'",
                node=stmt,
                tip=f"Type '{bare_decl_type}' has not been defined. Did you forget to add a `def {bare_decl_type} : type ...;` or `def {bare_decl_type} : struct {{ ... }};`?",
            )

        # Add to current scope
        self.var_types[stmt.name] = bare_decl_type
        self.var_immutable[stmt.name] = is_imut

        # Check for large array
        if "[" in resolved and "]" in resolved:
            try:
                size_str = resolved.split("[")[1].strip("]")
                if size_str:
                    total_size = int(size_str)
                    # Assume element size is at least 1 byte, usually 4 or 8.
                    if total_size > 10000:
                        self._warn(
                            f"Variable '{stmt.name}' is a large array ({total_size} elements) declared on the stack.",
                            node=stmt,
                            tip="Large stack allocations can cause stack overflow. Consider using a `vec<T>` for dynamic or large data.",
                        )
            except:
                pass

        if stmt.value is None:
            return  # Default initialization (codegen should handle this)

        val_type = self._infer_type(stmt.value)

        # If the assigned value comes from a function returning imut, the variable becomes immutable
        if not is_imut and val_type and self._is_imut(val_type):
            self.var_immutable[stmt.name] = True

        # Union types accept any compatible variant
        if resolved in self.union_types:
            return

        bare_val_type = self._strip_imut(val_type) if val_type else None
        if bare_val_type and not self._types_compatible(bare_val_type, bare_decl_type):
            self._warn(
                f"Variable '{stmt.name}' declared as '{bare_decl_type}' "
                f"but assigned a value of type '{bare_val_type}'.",
                node=stmt,
            )

    def _check_assignment(self, stmt):
        # Check immutability on the target
        from .ast_nodes import Identifier

        if isinstance(stmt.target, Identifier):
            if self.var_immutable.get(stmt.target.name, False):
                self._error(
                    f"Cannot assign to immutable variable '{stmt.target.name}'",
                    node=stmt,
                    tip=f"Variable '{stmt.target.name}' was declared as `imut` or received an immutable value from a function. It cannot be reassigned.",
                )

            # Self assignment check
            if (
                isinstance(stmt.value, Identifier)
                and stmt.target.name == stmt.value.name
            ):
                self._warn(
                    f"Self-assignment: '{stmt.target.name} = {stmt.value.name}' has no effect.",
                    node=stmt,
                )

        target_type = self._infer_type(stmt.target)
        val_type = self._infer_type(stmt.value)

        # If the assigned value is imut, make the target variable immutable going forward
        if isinstance(stmt.target, Identifier) and val_type and self._is_imut(val_type):
            self.var_immutable[stmt.target.name] = True

        bare_target = self._strip_imut(target_type) if target_type else None
        bare_val = self._strip_imut(val_type) if val_type else None

        if bare_target and bare_val:
            target_resolved = self._resolve(bare_target)
            # Union assignment: any variant type is valid
            if target_resolved in self.union_types:
                return
            if not self._types_compatible(bare_val, bare_target):
                self._warn(
                    f"Assigning '{bare_val}' to a variable of type '{bare_target}'.",
                    node=stmt,
                )

    def _check_return(self, stmt):
        val_type = self._infer_type(stmt.value)
        bare_ret = (
            self._strip_imut(self.current_return_type)
            if self.current_return_type
            else "void"
        )

        if bare_ret == "void" and stmt.value is not None:
            self._error(
                f"Function '{self.current_func}' is void but returns a value.",
                node=stmt,
                tip="Remove the return value or change the function's return type.",
            )

        if bare_ret != "void" and stmt.value is None:
            self._error(
                f"Function '{self.current_func}' must return a value of type '{bare_ret}'.",
                node=stmt,
                tip="Add a return value that matches the declared return type.",
            )

        if self.current_return_type and val_type:
            bare_val = self._strip_imut(val_type)
            if not self._types_compatible(bare_val, bare_ret):
                self._error(
                    f"Function '{self.current_func}' should return '{bare_ret}' "
                    f"but returns '{bare_val}'",
                    node=stmt,
                    tip=f"Make sure your return value matches the declared return type.",
                )

    def _check_if(self, stmt):
        self._infer_type(stmt.condition)
        if isinstance(stmt.condition, BoolLiteral):
            if stmt.condition.value == True:
                self._warn(
                    "Always-true `if` condition. The `then` block will always execute.",
                    node=stmt,
                )
            else:
                self._warn(
                    "Always-false `if` condition. The `then` block will never execute.",
                    node=stmt,
                )

        if not stmt.then_block:
            self._warn("Empty `if` then-block.", node=stmt)
        self._check_statements(stmt.then_block)
        for cond, block in stmt.also_blocks:
            self._infer_type(cond)
            self._check_statements(block)
        if stmt.else_block:
            if not stmt.else_block:
                self._warn("Empty `else` block.", node=stmt)
            self._check_statements(stmt.else_block)

    # ── Expression type inference ───────────────────────────────────

    def _infer_type(self, expr):
        """Infer and return the type string for an expression, or None if unknown."""
        if isinstance(expr, NumberLiteral):
            if expr.value > 9223372036854775807:
                self._warn(
                    f"Number literal '{expr.value}' is very large and might overflow 64-bit integer.",
                    node=expr,
                )
            return "int"
        elif isinstance(expr, FloatLiteral):
            return "float"
        elif isinstance(expr, StringLiteral):
            return "string"
        elif isinstance(expr, CharLiteral):
            return "char"
        elif isinstance(expr, BoolLiteral):
            return "bool"
        elif isinstance(expr, NullLiteral):
            # nil is a special polymorphic type that's compatible with any type
            return "nil"
        elif isinstance(expr, ThisExpr):
            if not self.current_class:
                self._error("'this' can only be used inside a class method", node=expr)
            return self.current_class
        elif isinstance(expr, Identifier):
            if expr.name in self.var_types:
                if expr.name in self.current_func_params:
                    self.used_params.add(expr.name)
                else:
                    self.used_vars.add(expr.name)
                return self.var_types[expr.name]

            # Check global variables
            if expr.name in self.global_vars:
                return self.global_vars[expr.name][0]  # return type

            # If not a variable, check if it's a class name (for static calls)
            if expr.name in self.class_types:
                return expr.name

            # Check if it's a generic class template
            if expr.name in self.generic_classes:
                return expr.name

            self._error(
                f"Undefined variable: '{expr.name}'",
                node=expr,
                tip="Make sure this variable is declared before use.",
            )
        elif isinstance(expr, BinaryOp):
            return self._check_binary_op(expr)
        elif isinstance(expr, UnaryOp):
            return self._check_unary_op(expr)
        elif isinstance(expr, Call):
            return self._check_call(expr)
        elif isinstance(expr, GenericCall):
            return self._check_generic_call(expr)
        elif isinstance(expr, MethodCall):
            return self._check_method_call(expr)
        elif isinstance(expr, MemberAccess):
            return self._check_member_access(expr)
        elif isinstance(expr, PointerMemberAccess):
            return self._check_pointer_member_access(expr)
        elif isinstance(expr, IndexAccess):
            return self._check_index_access(expr)
        elif isinstance(expr, CastExpr):
            return self._check_cast(expr)
        elif isinstance(expr, StructInit):
            # Check if it's a class (including generic classes)
            resolved_name = self._resolve(expr.name)
            if resolved_name in self.class_types:
                return self._check_class_init(expr)
            # Check if it's a generic class template
            if expr.name in self.generic_classes:
                return self._check_class_init(expr)
            return self._check_struct_init(expr)
        elif isinstance(expr, ArrayInit):
            return self._check_array_init(expr)
        elif isinstance(expr, EnumMemberAccess):
            return self._check_enum_member_access(expr)
        elif isinstance(expr, TypeConvExpr):
            return self._check_type_conv(expr)
        elif isinstance(expr, TernaryOp):
            return self._check_ternary_op(expr)
        return None

    def _check_binary_op(self, expr):
        left_t = self._infer_type(expr.left)
        right_t = self._infer_type(expr.right)

        if left_t and right_t:
            left_b = self._base_type(left_t)
            right_b = self._base_type(right_t)

            # Pointer arithmetic
            if (left_b == "ptr" or left_b == "sptr") and right_b == "int":
                return left_t
            if left_b == "int" and (right_b == "ptr" or right_b == "sptr"):
                return right_t

            # Redundancy checks
            if isinstance(expr.right, NumberLiteral):
                if expr.op in ("+", "-") and expr.right.value == 0:
                    self._warn(
                        f"Redundant operation: '{expr.op} 0' has no effect.", node=expr
                    )
                if expr.op == "*" and expr.right.value == 1:
                    self._warn(f"Redundant operation: '* 1' has no effect.", node=expr)
                if expr.op == "/" and expr.right.value == 1:
                    self._warn(f"Redundant operation: '/ 1' has no effect.", node=expr)
                if expr.op == "*" and expr.right.value == 0:
                    self._warn(f"Operation '* 0' always results in 0.", node=expr)
                if expr.op == "%" and expr.right.value == 1:
                    self._warn(f"Operation '% 1' always results in 0.", node=expr)
                if expr.op == "&" and expr.right.value == 0:
                    self._warn(f"Operation '& 0' always results in 0.", node=expr)
                if expr.op in ("|", "^") and expr.right.value == 0:
                    self._warn(
                        f"Redundant operation: '{expr.op} 0' has no effect.", node=expr
                    )

            if (
                isinstance(expr.left, Identifier)
                and isinstance(expr.right, Identifier)
                and expr.left.name == expr.right.name
            ):
                if expr.op == "==":
                    self._warn(
                        f"Comparison '{expr.left.name} == {expr.right.name}' is always true.",
                        node=expr,
                    )
                if expr.op == "!=":
                    self._warn(
                        f"Comparison '{expr.left.name} != {expr.right.name}' is always false.",
                        node=expr,
                    )
                if expr.op == "^":
                    self._warn(
                        f"Operation '{expr.left.name} ^ {expr.right.name}' always results in 0.",
                        node=expr,
                    )

            # Mixed sign warning
            if (
                left_b == "int"
                and right_b == "uint"
                or left_b == "uint"
                and right_b == "int"
            ):
                if expr.op not in ("<<", ">>"):
                    self._warn(
                        f"Mixing signed and unsigned integers in '{expr.op}' operation.",
                        node=expr,
                        tip="This can lead to unexpected results if the signed value is negative. Consider casting both to the same type.",
                    )

            # String operations
            if left_b == "string" and right_b == "string":
                if expr.op in ("+", "-"):
                    return "string"
                elif expr.op in ("==", "!="):
                    return "bool"
                else:
                    raise LeashError(
                        f"Operator '{expr.op}' is not supported for strings",
                        tip="Strings support: + (concatenation), - (removal), == and != (comparison).",
                    )
            # Mixed string concatenations
            elif expr.op == "+" and (
                (
                    left_b == "string"
                    and (
                        right_b in ("char", "int", "uint", "float", "bool")
                        or (right_t and right_t.startswith("char["))
                    )
                )
                or (
                    right_b == "string"
                    and (
                        left_b in ("char", "int", "uint", "float", "bool")
                        or (left_t and left_t.startswith("char["))
                    )
                )
            ):
                return "string"

            # Bitwise and Modulo operations
            if expr.op in ("&", "|", "^", "<<", ">>", "%"):
                if self._is_int_family(left_t) and self._is_int_family(right_t):
                    # Shift limit check
                    if expr.op in ("<<", ">>") and isinstance(
                        expr.right, NumberLiteral
                    ):
                        bits = 64  # Default
                        if "<" in left_t:
                            try:
                                bits = int(left_t.split("<")[1].split(">")[0])
                            except:
                                pass
                        if expr.right.value >= bits:
                            self._warn(
                                f"Shift amount {expr.right.value} is greater than or equal to bit-width {bits}.",
                                node=expr,
                                tip="This is undefined behavior in many environments. Make sure the shift amount is within [0, bits-1].",
                            )

                    return left_t
                else:
                    raise LeashError(
                        f"Operator '{expr.op}' is only supported for integer types",
                        tip=f"Operands are '{left_t}' and '{right_t}'.",
                    )

            # Logical operations
            if expr.op in ("&&", "||"):
                if isinstance(expr.left, BoolLiteral):
                    if expr.op == "&&" and expr.left.value == True:
                        self._warn("Redundant 'true && ...' operation.", node=expr)
                    if expr.op == "||" and expr.left.value == False:
                        self._warn("Redundant 'false || ...' operation.", node=expr)
                return "bool"

            # Numeric operations
            if self._is_numeric(left_t) and self._is_numeric(right_t):
                if expr.op in ("==", "!=", "<", "<=", ">", ">="):
                    return "bool"
                # Result type: float wins over int
                if left_b == "float" or right_b == "float":
                    return "float"

                # Zero-division safety check (static)
                if (
                    expr.op in ("/", "%")
                    and isinstance(expr.right, NumberLiteral)
                    and expr.right.value == 0
                ):
                    raise LeashError(
                        f"{'Division' if expr.op == '/' else 'Modulo'} by zero detected statically!",
                        tip="Make sure you aren't dividing by zero, as it will crash your program at runtime.",
                    )

                return "int"

            # Mixed string + non-string
            if (left_b == "string") or (right_b == "string"):
                if (left_b == "string") and (right_b == "string"):
                    # already handled above
                    pass
                elif expr.op == "+" and (
                    (
                        left_b == "string"
                        and (
                            right_b == "char"
                            or (right_t and right_t.startswith("char["))
                        )
                    )
                    or (
                        right_b == "string"
                        and (
                            left_b == "char" or (left_t and left_t.startswith("char["))
                        )
                    )
                ):
                    pass
                else:
                    raise LeashError(
                        f"Cannot use operator '{expr.op}' between '{left_t}' and '{right_t}'",
                        tip="Leash supports '+' for concatenating strings with numbers, but other operators like '-', '*', '/' are not supported for mixed types.",
                    )

        return left_t  # Best guess fallback

    def _check_unary_op(self, expr):
        from .ast_nodes import Identifier

        if expr.op == "&":  # Address-of
            # Check if we're taking address of a function (identifier referring to a function)
            if isinstance(expr.expr, Identifier):
                if expr.expr.name in self.func_types:
                    # It's a function name - return function pointer type
                    arg_types, return_type = self.func_types[expr.expr.name]
                    return f"fnc({', '.join(arg_types)}) : {return_type}"

        val_t = self._infer_type(expr.expr)
        if val_t:
            val_b = self._base_type(val_t)
            if expr.op == "*":  # Dereference
                if val_b not in ("ptr", "sptr"):
                    raise LeashError(
                        f"Cannot dereference non-pointer type '{val_t}'", node=expr
                    )
                return val_t[1:]
            if expr.op == "&":  # Address-of (for variables)
                # For variables, return pointer to type
                return f"*{val_t}"
            if expr.op == "!":
                if isinstance(expr.expr, UnaryOp) and expr.expr.op == "!":
                    self._warn("Double negation '!!' is redundant.", node=expr)
                if val_b not in ("bool", "int", "uint"):
                    raise LeashError(
                        f"Operator '!' is not supported for type '{val_t}'",
                        tip="Logical NOT is supported for bool and integer types.",
                    )
                return "bool"
            if expr.op == "~":
                if val_b not in ("int", "uint"):
                    raise LeashError(
                        f"Operator '~' is not supported for type '{val_t}'",
                        tip="Bitwise NOT is supported for integer types.",
                    )
                return val_t
            if expr.op == "-":
                if val_b not in ("int", "uint", "float"):
                    raise LeashError(
                        f"Unary minus is not supported for type '{val_t}'",
                        tip="Unary minus is supported for numeric types.",
                    )
                return val_t
        return val_t

    def _check_ternary_op(self, expr):
        cond_t = self._infer_type(expr.condition)
        true_t = self._infer_type(expr.true_expr)
        false_t = self._infer_type(expr.false_expr)

        if cond_t:
            cond_b = self._base_type(cond_t)
            if cond_b not in ("bool", "int", "uint"):
                raise LeashError(
                    f"Ternary condition must be a boolean type, got '{cond_t}'",
                    node=expr,
                )

        if true_t and false_t:
            if self._types_compatible(false_t, true_t):
                return true_t
            elif self._types_compatible(true_t, false_t):
                return false_t
            else:
                raise LeashError(
                    f"Ternary branches have incompatible types: '{true_t}' and '{false_t}'",
                    node=expr,
                    tip="Both branches of a ternary expression must have compatible types.",
                )

        return true_t

    def _check_call(self, expr):
        if expr.name == "show":
            return "void"

        if expr.name == "cstr":
            if len(expr.args) != 1:
                self._error(f"Function 'cstr' expects 1 argument", node=expr)
            arg_t = self._infer_type(expr.args[0])
            if arg_t and self._resolve(arg_t) == "char[]":
                self._warn("Calling 'cstr' on a 'char[]' is redundant.", node=expr)
            if (
                arg_t
                and self._resolve(arg_t) != "string"
                and self._resolve(arg_t) != "char[]"
            ):
                self._error(
                    f"Argument 1 of 'cstr' must be 'string', got '{arg_t}'",
                    node=expr.args[0],
                )
            return "char[]"

        if expr.name == "lstr":
            if len(expr.args) != 1:
                self._error(f"Function 'lstr' expects 1 argument", node=expr)
            arg_t = self._infer_type(expr.args[0])
            if arg_t and self._resolve(arg_t) == "string":
                self._warn("Calling 'lstr' on a 'string' is redundant.", node=expr)
            if (
                arg_t
                and self._resolve(arg_t) != "char[]"
                and self._resolve(arg_t) != "string"
            ):
                self._error(
                    f"Argument 1 of 'lstr' must be 'char[]', got '{arg_t}'",
                    node=expr.args[0],
                )
            return "string"

        if expr.name == "get":
            if len(expr.args) > 1:
                self._error(
                    f"Function 'get' expects 0 or 1 argument(s), but got {len(expr.args)}",
                    node=expr,
                )
            if len(expr.args) == 0:
                self._warn(
                    "Calling 'get()' without a prompt string.",
                    node=expr,
                    tip='You can pass a string to `get()` to show a prompt: `get("Enter name: ")`.',
                )
            if len(expr.args) == 1:
                arg_t = self._infer_type(expr.args[0])
                if arg_t and self._resolve(arg_t) != "string":
                    self._error(
                        f"Argument 1 of 'get' must be 'string', got '{arg_t}'",
                        node=expr.args[0],
                    )
            return "string"

        if expr.name == "tostring":
            if len(expr.args) != 1:
                self._error(
                    f"Function 'tostring' expects 1 argument, but got {len(expr.args)}",
                    node=expr,
                )
            arg_t = self._infer_type(expr.args[0])
            if arg_t and self._resolve(arg_t) == "string":
                self._warn("Calling 'tostring' on a 'string' is redundant.", node=expr)
            return "string"

        if expr.name == "rand":
            if len(expr.args) != 2:
                self._error(
                    f"Function 'rand' expects 2 arguments (min, max), but got {len(expr.args)}",
                    node=expr,
                )
            arg_t1 = self._infer_type(expr.args[0])
            arg_t2 = self._infer_type(expr.args[1])
            if arg_t1 and not self._is_int_family(self._resolve(arg_t1)):
                self._error(
                    f"Argument 1 of 'rand' must be an integer type, got '{arg_t1}'",
                    node=expr.args[0],
                )
            if arg_t2 and not self._is_int_family(self._resolve(arg_t2)):
                self._error(
                    f"Argument 2 of 'rand' must be an integer type, got '{arg_t2}'",
                    node=expr.args[1],
                )
            return "int"

        if expr.name == "randf":
            if len(expr.args) != 2:
                self._error(
                    f"Function 'randf' expects 2 arguments (min, max), but got {len(expr.args)}",
                    node=expr,
                )
            arg_t1 = self._infer_type(expr.args[0])
            arg_t2 = self._infer_type(expr.args[1])
            if arg_t1 and not self._is_numeric(self._resolve(arg_t1)):
                self._error(
                    f"Argument 1 of 'randf' must be a numeric type, got '{arg_t1}'",
                    node=expr.args[0],
                )
            if arg_t2 and not self._is_numeric(self._resolve(arg_t2)):
                self._error(
                    f"Argument 2 of 'randf' must be a numeric type, got '{arg_t2}'",
                    node=expr.args[1],
                )
            return "float"

        if expr.name == "seed":
            if len(expr.args) != 1:
                self._error(
                    f"Function 'seed' expects 1 argument, but got {len(expr.args)}",
                    node=expr,
                )
            arg_t = self._infer_type(expr.args[0])
            if arg_t and not self._is_int_family(self._resolve(arg_t)):
                self._error(
                    f"Argument 1 of 'seed' must be an integer type, got '{arg_t}'",
                    node=expr.args[0],
                )
            return "void"

        if expr.name == "choose":
            if len(expr.args) < 2:
                self._error(
                    f"Function 'choose' expects at least 2 arguments, but got {len(expr.args)}",
                    node=expr,
                )
            # All arguments must be strings
            for i, arg in enumerate(expr.args):
                arg_t = self._infer_type(arg)
                if arg_t and self._resolve(arg_t) != "string":
                    self._error(
                        f"Argument {i + 1} of 'choose' must be 'string', got '{arg_t}'",
                        node=arg,
                    )
            return "string"

        if expr.name == "wait":
            if len(expr.args) != 1:
                self._error(
                    f"Function 'wait' expects 1 argument (seconds), but got {len(expr.args)}",
                    node=expr,
                )
            arg_t = self._infer_type(expr.args[0])
            if arg_t and not self._is_numeric(self._resolve(arg_t)):
                self._error(
                    f"Argument 1 of 'wait' must be a numeric type, got '{arg_t}'",
                    node=expr.args[0],
                )
            return "void"

        if expr.name == "timepass":
            if len(expr.args) != 0:
                self._error(
                    f"Function 'timepass' expects 0 arguments, but got {len(expr.args)}",
                    node=expr,
                )
            return "float"

        if expr.name == "exit":
            if len(expr.args) != 1:
                self._error(
                    f"Function 'exit' expects 1 argument (exit code), but got {len(expr.args)}",
                    node=expr,
                )
            arg_t = self._infer_type(expr.args[0])
            if arg_t and not self._is_int_family(self._resolve(arg_t)):
                self._error(
                    f"Argument 1 of 'exit' must be an integer type, got '{arg_t}'",
                    node=expr.args[0],
                )
            return "void"

        sig = self.func_types.get(expr.name)
        if sig is None:
            # Check if it's a generic function (including multi-type functions)
            if expr.name in self.generic_funcs:
                return self._handle_multi_type_call(expr)

            # Check if it's a function pointer variable being called
            if expr.name in self.var_types:
                var_type = self.var_types[expr.name]
                resolved_type = self._resolve(var_type)
                if self._is_function_pointer_type(resolved_type):
                    # Mark the variable as used
                    if expr.name in self.current_func_params:
                        self.used_params.add(expr.name)
                    else:
                        self.used_vars.add(expr.name)
                    param_types, return_type = self._get_function_pointer_signature(
                        resolved_type
                    )
                    if len(expr.args) != len(param_types):
                        self._error(
                            f"Function pointer expects {len(param_types)} argument(s), "
                            f"but got {len(expr.args)}",
                            node=expr,
                        )
                    for i, (arg_expr, expected_type) in enumerate(
                        zip(expr.args, param_types)
                    ):
                        arg_type = self._infer_type(arg_expr)
                        bare_arg = self._strip_imut(arg_type) if arg_type else None
                        bare_expected = self._strip_imut(expected_type)
                        if bare_arg and not self._types_compatible(
                            bare_arg, bare_expected
                        ):
                            self._warn(
                                f"Argument {i + 1} of function pointer expects '{bare_expected}' "
                                f"but got '{bare_arg}'.",
                                node=expr,
                            )
                    return return_type

            self._error(
                f"Call to undefined function: '{expr.name}'",
                node=expr,
                tip="Make sure the function is defined before calling it.",
            )

        expected_args, return_type = sig

        if len(expr.args) != len(expected_args):
            self._error(
                f"Function '{expr.name}' expects {len(expected_args)} argument(s), "
                f"but got {len(expr.args)}",
                node=expr,
                tip="Check the function signature and pass the correct number of arguments.",
            )

        for i, (arg_expr, expected_type) in enumerate(zip(expr.args, expected_args)):
            arg_type = self._infer_type(arg_expr)
            bare_arg = self._strip_imut(arg_type) if arg_type else None
            bare_expected = self._strip_imut(expected_type)
            if bare_arg and not self._types_compatible(bare_arg, bare_expected):
                self._warn(
                    f"Argument {i + 1} of '{expr.name}' expects '{bare_expected}' "
                    f"but got '{bare_arg}'.",
                    node=expr,
                )

        return return_type

    def _check_generic_call(self, expr):
        """Check a generic function call like add<int>(10, 20)."""
        # Instantiate the generic function with the provided type arguments
        mangled_name = self._instantiate_generic_func(expr.name, expr.type_args, expr)

        # Now check it like a regular call
        sig = self.func_types.get(mangled_name)
        if sig is None:
            self._error(
                f"Failed to instantiate generic function: '{expr.name}'",
                node=expr,
            )

        expected_args, return_type = sig

        if len(expr.args) != len(expected_args):
            self._error(
                f"Function '{expr.name}' expects {len(expected_args)} argument(s), "
                f"but got {len(expr.args)}",
                node=expr,
            )

        for i, (arg_expr, expected_type) in enumerate(zip(expr.args, expected_args)):
            arg_type = self._infer_type(arg_expr)
            bare_arg = self._strip_imut(arg_type) if arg_type else None
            bare_expected = self._strip_imut(expected_type)
            if bare_arg and not self._types_compatible(bare_arg, bare_expected):
                self._warn(
                    f"Argument {i + 1} of '{expr.name}' expects '{bare_expected}' "
                    f"but got '{bare_arg}'.",
                    node=expr,
                )

        return return_type

    def _check_member_access(self, expr):
        base_type = self._infer_type(expr.expr)
        if base_type is None:
            return None

        resolved = self._resolve(base_type)

        # Safe pointer allows '.' access as if it were the type itself
        if resolved.startswith("&"):
            resolved = self._resolve(resolved[1:])

        # String .size
        if self._base_type(resolved) == "string" and expr.member == "size":
            return "int"

        # Array .size
        if (
            resolved
            and resolved.endswith("]")
            and "[" in resolved
            and expr.member == "size"
        ):
            return "int"

        # Vector .size property
        if (
            resolved
            and resolved.startswith("vec<")
            and resolved.endswith(">")
            and expr.member == "size"
        ):
            return "int"

        # Struct member
        if resolved in self.struct_types:
            fields = self.struct_types[resolved]
            if expr.member not in fields:
                raise LeashError(
                    f"Struct '{resolved}' has no member named '{expr.member}'",
                    tip=f"Available members: {', '.join(fields.keys())}",
                )
            return fields[expr.member]

        # Enum .name property
        if resolved in self.enum_types:
            if expr.member == "name":
                return "string"
            else:
                raise LeashError(
                    f"Enum '{resolved}' has no property named '{expr.member}'",
                    tip="Enums only have a `.name` property which returns the member name as a string.",
                )

        # Union member
        if resolved in self.union_types:
            variants = self.union_types[resolved]
            if expr.member == "cur":
                return resolved  # .cur is a dynamic type, returns the union itself for show
            if expr.member not in variants:
                raise LeashError(
                    f"Union '{resolved}' has no variant named '{expr.member}'",
                    tip=f"Available variants: {', '.join(variants.keys())}",
                )
            return variants[expr.member]

        # Class member
        if resolved in self.class_types:
            self._check_visibility(resolved, expr.member, False, expr)
            fields = self.class_types[resolved]["fields"]
            if expr.member not in fields:
                raise LeashError(
                    f"Class '{resolved}' has no field named '{expr.member}'",
                    tip=f"Available fields: {', '.join(fields.keys())}",
                )
            return fields[expr.member][0]

        return None

    def _check_pointer_member_access(self, expr):
        base_type = self._infer_type(expr.expr)
        if base_type is None:
            return None

        resolved = self._resolve(base_type)
        if not resolved.startswith("*") and not resolved.startswith("&"):
            self._error(f"Cannot use '->' on non-pointer type '{base_type}'", node=expr)

        # Access the member of the underlying type
        underlying = self._resolve(resolved[1:])

        # Struct member
        if underlying in self.struct_types:
            fields = self.struct_types[underlying]
            if expr.member not in fields:
                raise LeashError(
                    f"Struct '{underlying}' has no member named '{expr.member}'",
                    tip=f"Available members: {', '.join(fields.keys())}",
                )
            return fields[expr.member]

        # Class member
        if underlying in self.class_types:
            self._check_visibility(underlying, expr.member, False, expr)
            fields = self.class_types[underlying]["fields"]
            if expr.member not in fields:
                raise LeashError(
                    f"Class '{underlying}' has no field named '{expr.member}'",
                    tip=f"Available fields: {', '.join(fields.keys())}",
                )
            return fields[expr.member][0]

        self._error(f"Type '{underlying}' is not a struct or class", node=expr)

    def _check_index_access(self, expr):
        base_type = self._infer_type(expr.expr)
        idx_type = self._infer_type(expr.index)

        if idx_type and not self._is_int_family(idx_type):
            raise LeashError(
                f"Array/string index must be an integer, but got '{idx_type}'",
                tip="Use an integer expression as the index.",
            )

        if isinstance(expr.index, NumberLiteral) and expr.index.value < 0:
            self._error(
                f"Negative array index {expr.index.value} is not allowed.",
                node=expr.index,
                tip="Array indices must be non-negative (0 or greater).",
            )

        if base_type:
            resolved = self._resolve(base_type)
            # Array bounds safety check (static)
            if "[" in resolved and "]" in resolved:
                try:
                    parts = resolved.split("[")
                    if len(parts) > 1 and parts[1].strip("]") != "":
                        size = int(parts[1].strip("]"))
                        if isinstance(expr.index, NumberLiteral):
                            idx = expr.index.value
                            if idx < 0 or idx >= size:
                                raise LeashError(
                                    f"Array index {idx} is out of bounds for '{resolved}'",
                                    tip=f"This array only has {size} elements. Remember that Leash uses 0-based indexing (0 to {size - 1}).",
                                )
                except (ValueError, IndexError):
                    pass

            if self._base_type(resolved) == "string":
                return "char"
            if resolved.endswith("]") and "[" in resolved:
                return resolved.split("[")[0]

        return None

    def _check_cast(self, expr):
        src_type = self._infer_type(expr.expr)
        dst_type = expr.target_type

        if not self._is_valid_type(self._resolve(dst_type)):
            raise LeashError(
                f"Cannot cast to unknown type '{dst_type}'",
                tip="Make sure the target type is defined before casting.",
            )

        if src_type:
            src_b = self._base_type(src_type)
            dst_b = self._base_type(dst_type)

            # Allow numeric-to-numeric casts
            castable = {"int", "uint", "float", "char", "bool"}
            if src_b in castable and dst_b in castable:
                return dst_type

            # Class casting (upcasting and downcasting)
            src_resolved = self._resolve(src_type)
            dst_resolved = self._resolve(dst_type)
            if src_resolved in self.class_types and dst_resolved in self.class_types:
                # Upcasting (child to parent) is implicit, but also allow explicit
                # Downcasting (parent to child) requires explicit cast
                if self._is_subclass_of(
                    src_resolved, dst_resolved
                ) or self._is_subclass_of(dst_resolved, src_resolved):
                    return dst_type

            # Disallow clearly incompatible casts
            if src_b == "string" or dst_b == "string":
                raise LeashError(
                    f"Cannot cast from '{src_type}' to '{dst_type}'",
                    tip="Casting between strings and other types is not supported.",
                )

        return dst_type

    def _check_struct_init(self, expr):
        if expr.name not in self.struct_types:
            raise LeashError(f"Undefined struct: '{expr.name}'")

        fields = self.struct_types[expr.name]
        for key, val_expr in expr.kwargs:
            if key not in fields:
                raise LeashError(
                    f"Struct '{expr.name}' has no member named '{key}'",
                    tip=f"Available members: {', '.join(fields.keys())}",
                )
            expected = fields[key]
            actual = self._infer_type(val_expr)
            if actual and not self._types_compatible(actual, expected):
                self._warn(
                    f"Struct '{expr.name}' field '{key}' expects '{expected}' "
                    f"but got '{actual}'.",
                    node=expr,
                )
        return expr.name

    def _check_class_init(self, expr):
        resolved_name = self._resolve(expr.name)
        if resolved_name not in self.class_types:
            # Check if it's a generic class template
            if expr.name in self.generic_classes:
                # Instantiate with placeholder types for now
                # The actual types should come from the context
                type_params = self.generic_classes[expr.name].type_params
                placeholder_types = [f"_{p}" for p in type_params]
                resolved_name = self._instantiate_generic_class(
                    expr.name, placeholder_types, expr
                )
            else:
                raise LeashError(f"Undefined class: '{expr.name}'")

        cls = self.class_types[resolved_name]
        fields = cls["fields"]
        for key, val_expr in expr.kwargs:
            if key not in fields:
                raise LeashError(
                    f"Class '{expr.name}' has no field named '{key}'",
                    tip=f"Available fields: {', '.join(fields.keys())}",
                )

            self._check_visibility(expr.name, key, False, val_expr)
            expected, _ = fields[key]
            actual = self._infer_type(val_expr)
            if actual and not self._types_compatible(actual, expected):
                self._warn(
                    f"Class '{expr.name}' field '{key}' expects '{expected}' but got '{actual}'.",
                    node=expr,
                )

        return expr.name

    def _check_array_init(self, expr):
        if not expr.elements:
            return "int[]"
        first_type = self._infer_type(expr.elements[0])
        for elem in expr.elements[1:]:
            elem_type = self._infer_type(elem)
            if (
                elem_type
                and first_type
                and not self._types_compatible(elem_type, first_type)
            ):
                self._warn(
                    f"Array contains mixed types: '{first_type}' and '{elem_type}'.",
                    node=expr,
                )
        base = first_type or "int"
        return f"{base}[]"

    def _check_enum_member_access(self, expr):
        if expr.enum_name not in self.enum_types:
            # Maybe it's not an enum, but someone used :: anyway
            raise LeashError(
                f"Undefined enum: '{expr.enum_name}'",
                tip=f"Did you forget to define it? `def {expr.enum_name} : enum {{ ... }};` ",
            )

        members = self.enum_types[expr.enum_name]
        if expr.member_name not in members:
            raise LeashError(
                f"Enum '{expr.enum_name}' has no member named '{expr.member_name}'",
                tip=f"Available members: {', '.join(members)}",
            )

        return expr.enum_name

    def _check_method_call(self, expr):
        base_t = self._infer_type(expr.expr)
        if not base_t:
            return None
        base_b = self._base_type(base_t)

        if base_b == "vec":
            inner_t = base_t[4:-1]
            if expr.method in ("pushb", "pushf"):
                # Expect 1 argument: value of inner_t
                if len(expr.args) != 1:
                    self._error(
                        f"Vector method '{expr.method}' expects 1 argument, got {len(expr.args)}",
                        node=expr,
                    )
                else:
                    # Mark argument as used and check type
                    arg_type = self._infer_type(expr.args[0])
                    if arg_type and not self._types_compatible(arg_type, inner_t):
                        self._warn(
                            f"Vector method '{expr.method}' expects argument of type '{inner_t}' but got '{arg_type}'",
                            node=expr.args[0],
                        )
                return "void"
            elif expr.method == "insert":
                # Expect 2 arguments: index (int) and value (inner_t)
                if len(expr.args) != 2:
                    self._error(
                        f"Vector method '{expr.method}' expects 2 arguments, got {len(expr.args)}",
                        node=expr,
                    )
                else:
                    # Check index
                    idx_type = self._infer_type(expr.args[0])
                    if idx_type and not self._types_compatible(idx_type, "int"):
                        self._warn(
                            f"Vector method '{expr.method}' expects first argument of type 'int' (index) but got '{idx_type}'",
                            node=expr.args[0],
                        )
                    # Check value
                    val_type = self._infer_type(expr.args[1])
                    if val_type and not self._types_compatible(val_type, inner_t):
                        self._warn(
                            f"Vector method '{expr.method}' expects second argument of type '{inner_t}' but got '{val_type}'",
                            node=expr.args[1],
                        )
                return "void"
            elif expr.method in ("popb", "popf"):
                # Expect 0 arguments
                if len(expr.args) != 0:
                    self._error(
                        f"Vector method '{expr.method}' expects 0 arguments, got {len(expr.args)}",
                        node=expr,
                    )
                return inner_t
            elif expr.method == "size":
                # Expect 0 arguments
                if len(expr.args) != 0:
                    self._error(
                        f"Vector method '{expr.method}' expects 0 arguments, got {len(expr.args)}",
                        node=expr,
                    )
                return "int"
            elif expr.method == "get":
                # Expect 1 argument: index (int)
                if len(expr.args) != 1:
                    self._error(
                        f"Vector method '{expr.method}' expects 1 argument, got {len(expr.args)}",
                        node=expr,
                    )
                else:
                    idx_type = self._infer_type(expr.args[0])
                    if idx_type and not self._types_compatible(idx_type, "int"):
                        self._warn(
                            f"Vector method '{expr.method}' expects argument of type 'int' (index) but got '{idx_type}'",
                            node=expr.args[0],
                        )
                return inner_t
            elif expr.method == "set":
                # Expect 2 arguments: index (int) and value (inner_t)
                if len(expr.args) != 2:
                    self._error(
                        f"Vector method '{expr.method}' expects 2 arguments, got {len(expr.args)}",
                        node=expr,
                    )
                else:
                    idx_type = self._infer_type(expr.args[0])
                    if idx_type and not self._types_compatible(idx_type, "int"):
                        self._warn(
                            f"Vector method '{expr.method}' expects first argument of type 'int' (index) but got '{idx_type}'",
                            node=expr.args[0],
                        )
                    val_type = self._infer_type(expr.args[1])
                    if val_type and not self._types_compatible(val_type, inner_t):
                        self._warn(
                            f"Vector method '{expr.method}' expects second argument of type '{inner_t}' but got '{val_type}'",
                            node=expr.args[1],
                        )
                return "void"
            elif expr.method == "clear":
                # Expect 0 arguments
                if len(expr.args) != 0:
                    self._error(
                        f"Vector method '{expr.method}' expects 0 arguments, got {len(expr.args)}",
                        node=expr,
                    )
                return "void"
            elif expr.method == "remove":
                # Expect 1 argument: index (int)
                if len(expr.args) != 1:
                    self._error(
                        f"Vector method '{expr.method}' expects 1 argument, got {len(expr.args)}",
                        node=expr,
                    )
                else:
                    idx_type = self._infer_type(expr.args[0])
                    if idx_type and not self._types_compatible(idx_type, "int"):
                        self._warn(
                            f"Vector method '{expr.method}' expects argument of type 'int' (index) but got '{idx_type}'",
                            node=expr.args[0],
                        )
                return "void"
            else:
                raise LeashError(
                    f"Vector has no method named '{expr.method}'", node=expr
                )

        # String methods? Currently string only has .size which is member access
        # but maybe someone uses .size()
        if base_b == "string" and expr.method == "size":
            return "int"

        # Array methods?
        if base_b == "array" and expr.method == "size":
            return "int"

        # Class method / Static call
        # If expr.expr is an Identifier and it's a class name, it's a static call?
        from .ast_nodes import Identifier

        is_static = False
        target_cls = None

        # First check if this is a static call (class name as identifier)
        if isinstance(expr.expr, Identifier):
            # Check if it's a regular class
            if expr.expr.name in self.class_types:
                is_static = True
                target_cls = expr.expr.name
            # Check if it's a generic class template
            elif expr.expr.name in self.generic_classes:
                is_static = True
                target_cls = expr.expr.name

        # If not a static call, check if base_t is or resolves to a class
        if target_cls is None:
            # Check direct match in class_types
            if base_t in self.class_types:
                target_cls = base_t
            else:
                # Try to resolve the type (handles generic types like Hash<string, int>)
                resolved_base = self._resolve(base_t)
                if resolved_base in self.class_types:
                    target_cls = resolved_base
                elif base_t in self.generic_classes:
                    target_cls = base_t

        if target_cls:
            # Get methods from either regular class or generic class template
            if target_cls in self.class_types:
                methods = self.class_types[target_cls]["methods"]
            elif target_cls in self.generic_classes:
                # For generic classes, we need to look at the template's methods
                # For now, instantiate with placeholder types to get the methods
                generic_cls = self.generic_classes[target_cls]
                placeholder_types = [f"_{p}" for p in generic_cls.type_params]
                instantiated_name = self._instantiate_generic_class(
                    target_cls, placeholder_types, expr
                )
                if instantiated_name in self.class_types:
                    methods = self.class_types[instantiated_name]["methods"]
                else:
                    raise LeashError(
                        f"Failed to instantiate generic class '{target_cls}'"
                    )
            else:
                raise LeashError(f"Class '{target_cls}' not found")

            self._check_visibility(target_cls, expr.method, True, expr)
            if expr.method not in methods:
                raise LeashError(
                    f"Class '{target_cls}' has no method named '{expr.method}'",
                    tip=f"Available methods: {', '.join(methods.keys())}",
                )

            fnc_node, _, m_is_static, _ = methods[expr.method]

            # Static/Instance check
            if is_static and not m_is_static:
                self._error(
                    f"Cannot call instance method '{expr.method}' statically on class '{target_cls}'",
                    node=expr,
                    tip=f"Instance methods require an object. Call it on an instance variable: `let p = {target_cls}.new(...); p.{expr.method}(...);` ",
                )
            elif not is_static and m_is_static:
                self._error(
                    f"Cannot call static method '{expr.method}' on an instance of class '{target_cls}'",
                    node=expr,
                    tip=f"Static methods should be called on the class name: `{target_cls}.{expr.method}(...)` ",
                )

            # For instance methods, skip the first argument if it's 'this'
            # (built-in types like File include 'this' in args, user-defined classes don't)
            # For static methods, use all arguments
            if is_static:
                expected_args = [t for _, t in fnc_node.args]
            else:
                if fnc_node.args and fnc_node.args[0][0] == "this":
                    expected_args = [t for _, t in fnc_node.args[1:]]
                else:
                    expected_args = [t for _, t in fnc_node.args]

            if len(expr.args) != len(expected_args):
                raise LeashError(
                    f"Method '{expr.method}' of class '{target_cls}' expects {len(expected_args)} arguments, but got {len(expr.args)}"
                )

            for i, (arg_expr, expected_type) in enumerate(
                zip(expr.args, expected_args)
            ):
                arg_type = self._infer_type(arg_expr)
                if arg_type and not self._types_compatible(arg_type, expected_type):
                    self._warn(
                        f"Argument {i + 1} of method '{expr.method}' expects '{expected_type}' but got '{arg_type}'",
                        node=arg_expr,
                    )

            return fnc_node.return_type

        self._error(f"Type '{base_t}' has no method named '{expr.method}'", node=expr)

    # ── Generic type instantiation (monomorphization) ───────────────

    def _mangle_generic_name(self, base_name, type_args):
        """Create a unique mangled name for a generic instantiation."""
        args_str = "_".join(
            t.replace("<", "_").replace(">", "_").replace(",", "_").replace(" ", "")
            for t in type_args
        )
        return f"{base_name}_{args_str}"

    def _instantiate_generic_func(self, name, type_args, call_node):
        """Instantiate a generic function with concrete type arguments."""
        key = (name, tuple(type_args))
        if key in self.instantiated_funcs:
            return self.instantiated_funcs[key]

        # Get the generic function template
        template = self.generic_funcs.get(name)
        if not template:
            self._error(f"Call to undefined function: '{name}'", node=call_node)

        # Create type parameter mapping
        type_param_map = {}
        for i, param_name in enumerate(template.type_params):
            if i < len(type_args):
                type_param_map[param_name] = type_args[i]

        # Mangle the name
        mangled_name = self._mangle_generic_name(name, type_args)

        # Substitute type parameters in the function signature
        new_args = []
        for arg_name, arg_type in template.args:
            new_type = self._substitute_type_params(arg_type, type_param_map)
            new_args.append((arg_name, new_type))

        new_return_type = self._substitute_type_params(
            template.return_type, type_param_map
        )

        # Create a new Function node with substituted types
        new_body = self._substitute_body_types(template.body, type_param_map)
        new_func = Function(
            mangled_name, tuple(new_args), new_return_type, new_body, []
        )

        # Register the instantiated function
        arg_types = [t for _, t in new_args]
        self.func_types[mangled_name] = (arg_types, new_return_type)
        self.instantiated_funcs[key] = mangled_name

        # Store the instantiated function node for code generation
        TypeChecker.instantiated_func_nodes[mangled_name] = new_func

        return mangled_name

    def _handle_multi_type_call(self, expr):
        """Handle function calls to functions with multi-type arguments like [int, float]."""
        template = self.generic_funcs.get(expr.name)
        if not template:
            self._error(f"Call to undefined function: '{expr.name}'", node=expr)

        # Determine concrete types from arguments
        type_param_map = {}

        # Build a map from type parameter to position in multi-type
        multi_type_params = {}  # param_name -> (arg_index, position_in_multi_type)
        for arg_idx, (arg_name, arg_type) in enumerate(template.args):
            multi_types = self._parse_multi_type(arg_type)
            if multi_types:
                for pos, mt in enumerate(multi_types):
                    if mt in template.type_params:
                        multi_type_params[mt] = (arg_idx, pos)

        # Now map each concrete argument type to its corresponding type parameter
        for arg_idx, (arg_expr, (arg_name, arg_type)) in enumerate(
            zip(expr.args, template.args)
        ):
            arg_type_inferred = self._infer_type(arg_expr)
            if not arg_type_inferred:
                continue
            arg_type_inferred = self._strip_imut(arg_type_inferred)

            # Check if template arg type is multi-type
            template_multi_types = self._parse_multi_type(arg_type)
            if template_multi_types:
                # Find which type parameter this concrete type matches
                for param in template.type_params:
                    if param in template_multi_types:
                        type_param_map[param] = arg_type_inferred
                        break
            else:
                # Non-multi-type argument
                if arg_type in template.type_params:
                    type_param_map[arg_type] = arg_type_inferred

        # Create the mangled name with concrete types
        type_args = [type_param_map.get(p, p) for p in template.type_params]
        mangled_name = self._mangle_generic_name(expr.name, type_args)

        # Check if already instantiated
        key = (expr.name, tuple(type_args))
        if key in self.instantiated_funcs:
            # Get return type from registered function
            sig = self.func_types.get(mangled_name)
            if sig:
                return sig[1]
            # Otherwise, instantiate it
            return self._instantiate_generic_func(expr.name, type_args, expr)

        # Create and register the new instantiation with concrete types
        new_args = []
        for arg_name, arg_type in template.args:
            # If arg_type is a multi-type with a single parameter, substitute it
            new_type = self._substitute_type_params(arg_type, type_param_map)
            new_args.append((arg_name, new_type))

        new_return_type = self._substitute_type_params(
            template.return_type, type_param_map
        )
        new_body = self._substitute_body_types(template.body, type_param_map)
        new_func = Function(
            mangled_name, tuple(new_args), new_return_type, new_body, []
        )

        # Register the instantiated function
        arg_types = [t for _, t in new_args]
        self.func_types[mangled_name] = (arg_types, new_return_type)
        self.instantiated_funcs[key] = mangled_name
        TypeChecker.instantiated_func_nodes[mangled_name] = new_func

        # Modify the Call node to use the mangled name so codegen finds correct function
        expr.name = mangled_name

        return new_return_type

    def _resolve_concrete_type(self, type_name):
        """Resolve a type to its concrete type for mapping."""
        if not type_name:
            return None
        type_name = self._strip_imut(type_name)
        # Check for known types
        if type_name in ("int", "uint", "float", "string", "char", "bool", "void"):
            return type_name
        if type_name in self.struct_types:
            return type_name
        if type_name in self.class_types:
            return type_name
        return type_name

    def _is_builtin_type(self, type_name):
        """Check if a type is a builtin type."""
        return type_name in ("int", "uint", "float", "string", "char", "bool", "void")

    def _parse_multi_type(self, type_name):
        """Parse a multi-type like [int, float] into a list of types."""
        if not type_name:
            return []
        type_name = self._strip_imut(type_name)
        if not (type_name.startswith("[") and "]" in type_name):
            return []
        inner = type_name[1:-1]
        types = []
        depth = 0
        current = ""
        for c in inner:
            if c == "<":
                depth += 1
                current += c
            elif c == ">":
                depth -= 1
                current += c
            elif c == "," and depth == 0:
                types.append(current.strip())
                current = ""
            else:
                current += c
        if current.strip():
            types.append(current.strip())
        return types

    def _instantiate_generic_class(self, name, type_args, call_node):
        """Instantiate a generic class with concrete type arguments."""
        key = (name, tuple(type_args))
        if key in self.instantiated_classes:
            return self.instantiated_classes[key]

        # Get the generic class template
        template = self.generic_classes.get(name)
        if not template:
            self._error(f"Undefined class: '{name}'", node=call_node)

        # Create type parameter mapping
        type_param_map = {}
        for i, param_name in enumerate(template.type_params):
            if i < len(type_args):
                type_param_map[param_name] = type_args[i]

        # Mangle the name
        mangled_name = self._mangle_generic_name(name, type_args)

        # Class name map: original class name -> mangled name
        class_name_map = {name: mangled_name}

        # Substitute type parameters in fields
        new_fields = []
        for f in template.fields:
            new_type = self._substitute_type_params(
                f.var_type, type_param_map, class_name_map
            )
            new_fields.append(ClassField(f.name, new_type, f.visibility))

        # Substitute type parameters in methods
        new_methods = []
        for m in template.methods:
            new_args = []
            for arg_name, arg_type in m.fnc.args:
                new_type = self._substitute_type_params(arg_type, type_param_map)
                new_args.append((arg_name, new_type))
            new_return_type = self._substitute_type_params(
                m.fnc.return_type, type_param_map, class_name_map
            )
            # Pass the class name mapping for StructInit substitution
            class_name_map = {name: mangled_name}
            new_body = self._substitute_body_types(
                m.fnc.body, type_param_map, class_name_map
            )
            # Use the original method name (not mangled) so codegen can find it
            new_fnc = Function(
                m.fnc.name,  # Keep original name like 'new', 'add', etc.
                tuple(new_args),
                new_return_type,
                new_body,
                [],
            )
            # Store with the original method name for lookup
            new_methods.append(
                (m.fnc.name, ClassMethod(new_fnc, m.visibility, m.is_static, m.is_imut))
            )

        # Create a new ClassDef node
        # Convert new_methods from list of tuples to list of ClassMethod
        method_nodes = [method for _, method in new_methods]
        new_class = ClassDef(
            mangled_name, new_fields, method_nodes, template.parent, []
        )

        # Register the instantiated class
        self._register_class(new_class)

        # Override the method names in class_types to use original names
        if mangled_name in self.class_types:
            methods_dict = {}
            for orig_name, class_method in new_methods:
                methods_dict[orig_name] = (
                    class_method.fnc,
                    class_method.visibility,
                    class_method.is_static,
                    class_method.is_imut,
                )
            self.class_types[mangled_name]["methods"] = methods_dict

        self.instantiated_classes[key] = mangled_name

        # Store the instantiated class node for code generation
        TypeChecker.instantiated_class_nodes[mangled_name] = new_class

        return mangled_name

    def _substitute_type_params(self, type_name, type_param_map, class_name_map=None):
        """Substitute type parameters in a type name."""
        if not type_name:
            return type_name

        # Handle imut prefix
        if type_name.startswith("imut "):
            return "imut " + self._substitute_type_params(
                type_name[5:], type_param_map, class_name_map
            )

        # Handle multi-type syntax: [int, float] - just return as-is for now
        # (the actual type selection happens during call handling)
        if type_name.startswith("[") and "]" in type_name:
            # Check if all types in the multi-type can be resolved
            inner = type_name[1:-1]
            types = []
            depth = 0
            current = ""
            for c in inner:
                if c == "<":
                    depth += 1
                    current += c
                elif c == ">":
                    depth -= 1
                    current += c
                elif c == "," and depth == 0:
                    types.append(current.strip())
                    current = ""
                else:
                    current += c
            if current.strip():
                types.append(current.strip())

            # Substitute any type parameters in each type
            new_types = []
            for t in types:
                new_t = self._substitute_type_params(t, type_param_map, class_name_map)
                new_types.append(new_t)

            return f"[{', '.join(new_types)}]"

        # Handle pointer/reference prefix
        if type_name.startswith("*") or type_name.startswith("&"):
            return type_name[0] + self._substitute_type_params(
                type_name[1:], type_param_map, class_name_map
            )

        # Handle vec<T>
        if type_name.startswith("vec<") and type_name.endswith(">"):
            inner = type_name[4:-1]
            new_inner = self._substitute_type_params(
                inner, type_param_map, class_name_map
            )
            return f"vec<{new_inner}>"

        # Handle array T[]
        if type_name.endswith("]") and "[" in type_name:
            base = type_name.split("[")[0]
            bracket_part = type_name[len(base) :]
            new_base = self._substitute_type_params(
                base, type_param_map, class_name_map
            )
            return new_base + bracket_part

        # Check class name map (for class names like Box -> Box_int)
        if class_name_map and type_name in class_name_map:
            return class_name_map[type_name]

        # Direct type parameter substitution
        if type_name in type_param_map:
            return type_param_map[type_name]

        return type_name

    def _substitute_body_types(self, statements, type_param_map, class_name_map=None):
        """Create a deep copy of statements with type parameters substituted."""
        import copy

        def substitute_node(node):
            if node is None:
                return None

            # Create a shallow copy of the node
            new_node = copy.copy(node)

            # Handle specific node types
            if isinstance(node, VariableDecl):
                new_node.var_type = self._substitute_type_params(
                    node.var_type, type_param_map
                )
                new_node.value = substitute_node(node.value)
            elif isinstance(node, Function):
                new_node.args = tuple(
                    (name, self._substitute_type_params(t, type_param_map))
                    for name, t in node.args
                )
                new_node.return_type = self._substitute_type_params(
                    node.return_type, type_param_map
                )
                new_node.body = [substitute_node(s) for s in node.body]
            elif isinstance(node, ReturnStatement):
                new_node.value = substitute_node(node.value)
            elif isinstance(node, Assignment):
                new_node.target = substitute_node(node.target)
                new_node.value = substitute_node(node.value)
            elif isinstance(node, BinaryOp):
                new_node.left = substitute_node(node.left)
                new_node.right = substitute_node(node.right)
            elif isinstance(node, UnaryOp):
                new_node.expr = substitute_node(node.expr)
            elif isinstance(node, MethodCall):
                new_node.expr = substitute_node(node.expr)
                new_node.args = [substitute_node(a) for a in node.args]
            elif isinstance(node, Call):
                new_node.args = [substitute_node(a) for a in node.args]
            elif isinstance(node, IfStatement):
                new_node.condition = substitute_node(node.condition)
                new_node.then_block = [substitute_node(s) for s in node.then_block]
                new_node.also_blocks = [
                    (substitute_node(c), [substitute_node(s) for s in b])
                    for c, b in node.also_blocks
                ]
                if new_node.else_block:
                    new_node.else_block = [substitute_node(s) for s in node.else_block]
            elif isinstance(node, WhileStatement):
                new_node.condition = substitute_node(node.condition)
                new_node.body = [substitute_node(s) for s in node.body]
            elif isinstance(node, ForeachVectorStatement):
                new_node.vector_expr = substitute_node(node.vector_expr)
                new_node.body = [substitute_node(s) for s in node.body]
            elif isinstance(node, MemberAccess):
                new_node.expr = substitute_node(node.expr)
            elif isinstance(node, IndexAccess):
                new_node.expr = substitute_node(node.expr)
                new_node.index = substitute_node(node.index)
            elif isinstance(node, StructInit):
                # Substitute the class name if it's a generic class
                if class_name_map and node.name in class_name_map:
                    new_node.name = class_name_map[node.name]
                elif node.name in type_param_map:
                    new_node.name = type_param_map[node.name]
                elif "<" in node.name and node.name.endswith(">"):
                    # Substitute type params in the generic name
                    base = node.name.split("<")[0]
                    args_str = node.name[len(base) + 1 : -1]
                    new_args = [
                        self._substitute_type_params(a.strip(), type_param_map)
                        for a in args_str.split(",")
                    ]
                    new_node.name = f"{base}<{', '.join(new_args)}>"
                new_node.kwargs = [(k, substitute_node(v)) for k, v in node.kwargs]
            elif isinstance(node, ExpressionStatement):
                new_node.expr = substitute_node(node.expr)
            elif isinstance(node, ShowStatement):
                new_node.args = [substitute_node(a) for a in node.args]
            elif isinstance(node, Block):
                new_node.statements = [substitute_node(s) for s in node.statements]

            # Copy line/col info
            if hasattr(node, "line"):
                new_node.line = node.line
            if hasattr(node, "col"):
                new_node.col = node.col

            return new_node

        return [substitute_node(s) for s in statements]

    # ── Null/nil handling ──────────────────────────────────────────

    def _is_nil_literal(self, expr):
        """Check if an expression is a nil/null literal."""
        return isinstance(expr, NullLiteral)
