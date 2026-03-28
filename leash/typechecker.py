from .ast_nodes import *
from .errors import LeashError


# Type categories used for compatibility checks
INT_TYPES = {'int', 'uint'}
FLOAT_TYPES = {'float'}
NUMERIC_TYPES = INT_TYPES | FLOAT_TYPES


class TypeChecker:
    """Static type checker for Leash AST.
    
    Runs between parsing and codegen. Reports type errors as LeashError
    and collects warnings for non-fatal issues.
    """

    def __init__(self):
        self.var_types = {}       # name -> type string
        self.func_types = {}      # name -> (arg_types, return_type)
        self.struct_types = {}    # name -> {field: type}
        self.union_types = {}     # name -> {variant: type}
        self.type_aliases = {}    # name -> resolved type string
        self.warnings = []
        self.current_func = None  # name of current function being checked
        self.current_return_type = None

    def check(self, program):
        """Run type checking on a Program AST node. Returns list of warnings."""
        # First pass: register all top-level definitions (structs, unions, aliases, functions)
        for item in program.items:
            if isinstance(item, StructDef):
                self._register_struct(item)
            elif isinstance(item, UnionDef):
                self._register_union(item)
            elif isinstance(item, TypeAlias):
                self._register_alias(item)
            elif isinstance(item, Function):
                self._register_function_sig(item)

        # Second pass: check function bodies
        for item in program.items:
            if isinstance(item, Function):
                self._check_function(item)

        return self.warnings

    # ── Registration ────────────────────────────────────────────────

    def _register_struct(self, node):
        fields = {}
        for fname, ftype in node.fields:
            resolved = self._resolve(ftype)
            if not self._is_valid_type(resolved):
                raise LeashError(
                    f"Struct '{node.name}' field '{fname}' has unknown type '{ftype}'")
            fields[fname] = ftype
        self.struct_types[node.name] = fields

    def _register_union(self, node):
        variants = {}
        for vname, vtype in node.variants:
            resolved = self._resolve(vtype)
            if not self._is_valid_type(resolved):
                raise LeashError(
                    f"Union '{node.name}' variant '{vname}' has unknown type '{vtype}'")
            variants[vname] = vtype
        self.union_types[node.name] = variants

    def _register_alias(self, node):
        self.type_aliases[node.name] = node.target_type

    def _register_function_sig(self, node):
        arg_types = [t for _, t in node.args]
        self.func_types[node.name] = (arg_types, node.return_type)

    # ── Type resolution ─────────────────────────────────────────────

    def _resolve(self, type_name):
        """Resolve type aliases."""
        visited = set()
        while type_name in self.type_aliases and type_name not in visited:
            visited.add(type_name)
            type_name = self.type_aliases[type_name]
        return type_name

    def _base_type(self, type_name):
        """Get the base type category (strip bit-widths, array brackets)."""
        t = self._resolve(type_name)
        if t.endswith(']') and '[' in t:
            return 'array'
        if t.startswith('int<') or t == 'int':
            return 'int'
        if t.startswith('uint<') or t == 'uint':
            return 'uint'
        if t.startswith('float<') or t == 'float':
            return 'float'
        return t

    def _is_valid_type(self, type_name):
        """Check if a type name is a known type."""
        t = self._resolve(type_name)
        # Strip array suffix
        if t.endswith(']') and '[' in t:
            t = t.split('[')[0]
        base = self._base_type(t)
        if base in ('int', 'uint', 'float', 'string', 'char', 'bool', 'void'):
            return True
        if t in self.struct_types or t in self.union_types:
            return True
        return False

    def _is_numeric(self, type_name):
        b = self._base_type(type_name)
        return b in ('int', 'uint', 'float')

    def _is_int_family(self, type_name):
        b = self._base_type(type_name)
        return b in ('int', 'uint', 'char', 'bool')

    def _types_compatible(self, src, dst):
        """Check if src type can be assigned into dst type."""
        src_r = self._resolve(src)
        dst_r = self._resolve(dst)

        if src_r == dst_r:
            return True

        # Handle arrays
        if '[' in src_r and ']' in src_r and '[' in dst_r and ']' in dst_r:
            src_elem = src_r.split('[')[0]
            dst_elem = dst_r.split('[')[0]
            return self._types_compatible(src_elem, dst_elem)

        src_b = self._base_type(src_r)
        dst_b = self._base_type(dst_r)

        # Numeric types are broadly compatible with each other
        if src_b in ('int', 'uint', 'float', 'char', 'bool') and dst_b in ('int', 'uint', 'float', 'char', 'bool'):
            return True

        # Union accepts any of its variant types
        if dst_r in self.union_types:
            for _, vtype in self.union_types[dst_r].items():
                if self._types_compatible(src, vtype):
                    return True

        return False

    # ── Function checking ───────────────────────────────────────────

    def _check_function(self, node):
        # Save and restore var scope per function
        saved = self.var_types.copy()
        self.current_func = node.name
        self.current_return_type = node.return_type
        
        # Register args as local vars
        for arg_name, arg_type in node.args:
            self.var_types[arg_name] = arg_type

        for stmt in node.body:
            self._check_stmt(stmt)

        self.var_types = saved
        self.current_func = None
        self.current_return_type = None

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
                    if '[' in resolved and ']' in resolved:
                        raise LeashError(
                            f"Argument {i+1} of show() is an array ('{t}'), which is not supported.",
                            tip="To print an array, use a `foreach` loop to iterate over its elements: `foreach i, v in<array> my_arr { show(v); }`")
                    # Check if it's a struct (unions are handled via .cur/auto-detection in codegen, but raw struct printing is not supported)
                    if resolved in self.struct_types:
                         raise LeashError(
                            f"Argument {i+1} of show() is a struct ('{resolved}'), which is not supported.",
                            tip="To print a struct, use a `foreach` loop to iterate over its members: `foreach k, v in<struct> my_struct { show(k, \": \", v); }`")
        elif isinstance(stmt, ExpressionStatement):
            self._infer_type(stmt.expr)
        elif isinstance(stmt, IfStatement):
            self._check_if(stmt)
        elif isinstance(stmt, WhileStatement):
            self._infer_type(stmt.condition)
            for s in stmt.body:
                self._check_stmt(s)
        elif isinstance(stmt, ForStatement):
            self._check_stmt(stmt.init)
            self._infer_type(stmt.condition)
            self._check_stmt(stmt.step)
            for s in stmt.body:
                self._check_stmt(s)
        elif isinstance(stmt, DoWhileStatement):
            for s in stmt.body:
                self._check_stmt(s)
            self._infer_type(stmt.condition)
        elif isinstance(stmt, ForeachStructStatement):
            self._infer_type(stmt.struct_expr)
            self.var_types[stmt.name_var] = 'string'
            self.var_types[stmt.value_var] = 'int'  # Approximate
            for s in stmt.body:
                self._check_stmt(s)
        elif isinstance(stmt, ForeachArrayStatement):
            self._infer_type(stmt.array_expr)
            self.var_types[stmt.index_var] = 'int'
            self.var_types[stmt.value_var] = 'int'  # Approximate
            for s in stmt.body:
                self._check_stmt(s)

    def _check_var_decl(self, stmt):
        if stmt.name in self.var_types:
             raise LeashError(f"Redefinition of variable '{stmt.name}' in the same function",
                              tip=f"Variable '{stmt.name}' is already defined. Use a different name or just assign to it if you want to change its value.")
        decl_type = stmt.var_type
        resolved = self._resolve(decl_type)
        
        if not self._is_valid_type(resolved):
            raise LeashError(
                f"Variable '{stmt.name}' declared with unknown type '{decl_type}'",
                tip=f"Type '{decl_type}' has not been defined. Did you forget to add a `def {decl_type} : type ...;` or `def {decl_type} : struct {{ ... }};`?")
        
        # Add to current scope
        self.var_types[stmt.name] = decl_type
        val_type = self._infer_type(stmt.value)

        # Union types accept any compatible variant
        if resolved in self.union_types:
            self.var_types[stmt.name] = decl_type
            return

        if val_type and not self._types_compatible(val_type, decl_type):
            self.warnings.append(
                f"Warning: Variable '{stmt.name}' declared as '{decl_type}' "
                f"but assigned a value of type '{val_type}'.")

        self.var_types[stmt.name] = decl_type

    def _check_assignment(self, stmt):
        target_type = self._infer_type(stmt.target)
        val_type = self._infer_type(stmt.value)

        if target_type and val_type:
            target_resolved = self._resolve(target_type)
            # Union assignment: any variant type is valid
            if target_resolved in self.union_types:
                return
            if not self._types_compatible(val_type, target_type):
                self.warnings.append(
                    f"Warning: Assigning '{val_type}' to a variable of type '{target_type}'.")

    def _check_return(self, stmt):
        val_type = self._infer_type(stmt.value)
        if self.current_return_type and val_type:
            if not self._types_compatible(val_type, self.current_return_type):
                raise LeashError(
                    f"Function '{self.current_func}' should return '{self.current_return_type}' "
                    f"but returns '{val_type}'",
                    tip=f"Make sure your return value matches the declared return type.")

    def _check_if(self, stmt):
        self._infer_type(stmt.condition)
        for s in stmt.then_block:
            self._check_stmt(s)
        for cond, block in stmt.also_blocks:
            self._infer_type(cond)
            for s in block:
                self._check_stmt(s)
        if stmt.else_block:
            for s in stmt.else_block:
                self._check_stmt(s)

    # ── Expression type inference ───────────────────────────────────

    def _infer_type(self, expr):
        """Infer and return the type string for an expression, or None if unknown."""
        if isinstance(expr, NumberLiteral):
            return 'int'
        elif isinstance(expr, FloatLiteral):
            return 'float'
        elif isinstance(expr, StringLiteral):
            return 'string'
        elif isinstance(expr, CharLiteral):
            return 'char'
        elif isinstance(expr, BoolLiteral):
            return 'bool'
        elif isinstance(expr, NullLiteral):
            return 'void'
        elif isinstance(expr, Identifier):
            t = self.var_types.get(expr.name)
            if t is None:
                raise LeashError(f"Undefined variable: '{expr.name}'",
                                 tip="Make sure this variable is declared before use.")
            return t
        elif isinstance(expr, BinaryOp):
            return self._check_binary_op(expr)
        elif isinstance(expr, Call):
            return self._check_call(expr)
        elif isinstance(expr, MemberAccess):
            return self._check_member_access(expr)
        elif isinstance(expr, IndexAccess):
            return self._check_index_access(expr)
        elif isinstance(expr, CastExpr):
            return self._check_cast(expr)
        elif isinstance(expr, StructInit):
            return self._check_struct_init(expr)
        elif isinstance(expr, ArrayInit):
            return self._check_array_init(expr)
        return None

    def _check_binary_op(self, expr):
        left_t = self._infer_type(expr.left)
        right_t = self._infer_type(expr.right)

        if left_t and right_t:
            left_b = self._base_type(left_t)
            right_b = self._base_type(right_t)

            # String operations
            if left_b == 'string' and right_b == 'string':
                if expr.op in ('+', '-'):
                    return 'string'
                elif expr.op in ('==', '!='):
                    return 'bool'
                else:
                    raise LeashError(
                        f"Operator '{expr.op}' is not supported for strings",
                        tip="Strings support: + (concatenation), - (removal), == and != (comparison).")

            # Numeric operations
            if self._is_numeric(left_t) and self._is_numeric(right_t):
                if expr.op in ('==', '!=', '<', '<=', '>', '>='):
                    return 'bool'
                # Result type: float wins over int
                if left_b == 'float' or right_b == 'float':
                    return 'float'
                
                # Zero-division safety check (static)
                if expr.op == '/' and isinstance(expr.right, NumberLiteral) and expr.right.value == 0:
                     raise LeashError("Division by zero detected statically!", 
                                      tip="Make sure you aren't dividing by zero, as it will crash your program at runtime.")
                
                return 'int'

            # Mixed string + non-string
            if (left_b == 'string') != (right_b == 'string'):
                raise LeashError(
                    f"Cannot use operator '{expr.op}' between '{left_t}' and '{right_t}'",
                    tip="You cannot mix string and numeric types in binary operations. Use a cast if needed.")

        return left_t  # Best guess fallback

    def _check_call(self, expr):
        if expr.name == 'show':
            return 'void'

        sig = self.func_types.get(expr.name)
        if sig is None:
            raise LeashError(f"Call to undefined function: '{expr.name}'",
                             tip="Make sure the function is defined before calling it.")

        expected_args, return_type = sig

        if len(expr.args) != len(expected_args):
            raise LeashError(
                f"Function '{expr.name}' expects {len(expected_args)} argument(s), "
                f"but got {len(expr.args)}",
                tip="Check the function signature and pass the correct number of arguments.")

        for i, (arg_expr, expected_type) in enumerate(zip(expr.args, expected_args)):
            arg_type = self._infer_type(arg_expr)
            if arg_type and not self._types_compatible(arg_type, expected_type):
                self.warnings.append(
                    f"Warning: Argument {i+1} of '{expr.name}' expects '{expected_type}' "
                    f"but got '{arg_type}'.")

        return return_type

    def _check_member_access(self, expr):
        base_type = self._infer_type(expr.expr)
        if base_type is None:
            return None

        resolved = self._resolve(base_type)

        # String .size
        if self._base_type(resolved) == 'string' and expr.member == 'size':
            return 'int'

        # Struct member
        if resolved in self.struct_types:
            fields = self.struct_types[resolved]
            if expr.member not in fields:
                raise LeashError(
                    f"Struct '{resolved}' has no member named '{expr.member}'",
                    tip=f"Available members: {', '.join(fields.keys())}")
            return fields[expr.member]

        # Union member
        if resolved in self.union_types:
            variants = self.union_types[resolved]
            if expr.member == 'cur':
                return resolved  # .cur is a dynamic type, returns the union itself for show
            if expr.member not in variants:
                raise LeashError(
                    f"Union '{resolved}' has no variant named '{expr.member}'",
                    tip=f"Available variants: {', '.join(variants.keys())}")
            return variants[expr.member]

        return None

    def _check_index_access(self, expr):
        base_type = self._infer_type(expr.expr)
        idx_type = self._infer_type(expr.index)

        if idx_type and not self._is_int_family(idx_type):
            raise LeashError(
                f"Array/string index must be an integer, but got '{idx_type}'",
                tip="Use an integer expression as the index.")

        if base_type:
            resolved = self._resolve(base_type)
            # Array bounds safety check (static)
            if '[' in resolved and ']' in resolved:
                 try:
                     parts = resolved.split('[')
                     if len(parts) > 1 and parts[1].strip(']') != "":
                         size = int(parts[1].strip(']'))
                         if isinstance(expr.index, NumberLiteral):
                             idx = expr.index.value
                             if idx < 0 or idx >= size:
                                 raise LeashError(f"Array index {idx} is out of bounds for '{resolved}'",
                                                  tip=f"This array only has {size} elements. Remember that Leash uses 0-based indexing (0 to {size-1}).")
                 except (ValueError, IndexError):
                     pass

            if self._base_type(resolved) == 'string':
                return 'char'
            if resolved.endswith(']') and '[' in resolved:
                return resolved.split('[')[0]

        return None


    def _check_cast(self, expr):
        src_type = self._infer_type(expr.expr)
        dst_type = expr.target_type

        if not self._is_valid_type(self._resolve(dst_type)):
            raise LeashError(
                f"Cannot cast to unknown type '{dst_type}'",
                tip="Make sure the target type is defined before casting.")

        if src_type:
            src_b = self._base_type(src_type)
            dst_b = self._base_type(dst_type)

            # Allow numeric-to-numeric casts
            castable = {'int', 'uint', 'float', 'char', 'bool'}
            if src_b in castable and dst_b in castable:
                return dst_type

            # Disallow clearly incompatible casts
            if src_b == 'string' or dst_b == 'string':
                raise LeashError(
                    f"Cannot cast from '{src_type}' to '{dst_type}'",
                    tip="Casting between strings and other types is not supported.")

        return dst_type

    def _check_struct_init(self, expr):
        if expr.name not in self.struct_types:
            raise LeashError(f"Undefined struct: '{expr.name}'")

        fields = self.struct_types[expr.name]
        for key, val_expr in expr.kwargs:
            if key not in fields:
                raise LeashError(
                    f"Struct '{expr.name}' has no member named '{key}'",
                    tip=f"Available members: {', '.join(fields.keys())}")
            expected = fields[key]
            actual = self._infer_type(val_expr)
            if actual and not self._types_compatible(actual, expected):
                self.warnings.append(
                    f"Warning: Struct '{expr.name}' field '{key}' expects '{expected}' "
                    f"but got '{actual}'.")
        return expr.name

    def _check_array_init(self, expr):
        if not expr.elements:
            return 'int[]'
        first_type = self._infer_type(expr.elements[0])
        for elem in expr.elements[1:]:
            elem_type = self._infer_type(elem)
            if elem_type and first_type and not self._types_compatible(elem_type, first_type):
                self.warnings.append(
                    f"Warning: Array contains mixed types: '{first_type}' and '{elem_type}'.")
        base = first_type or 'int'
        return f"{base}[]"
