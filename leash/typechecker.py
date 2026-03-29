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
        self.var_immutable = {}   # name -> bool (True if immutable)
        self.func_types = {}      # name -> (arg_types, return_type)
        self.struct_types = {}    # name -> {field: type}
        self.union_types = {}     # name -> {variant: type}
        self.enum_types = {}      # name -> list of member names
        self.type_aliases = {}    # name -> resolved type string
        self.class_types = {}     # name -> {fields: {name: (type, vis)}, methods: {name: (node, vis)}}
        self.current_class = None # name of current class being checked
        self.warnings = []
        self.current_func = None  # name of current function being checked
        self.current_func_node = None # Function node
        self.current_return_type = None
        self.used_vars = set()    # name
        self.used_params = set()  # name
        self.current_func_params = set()

    def check(self, program):
        """Run type checking on a Program AST node. Returns list of warnings."""
        # First pass: register all top-level definitions (structs, unions, aliases, functions)
        for item in program.items:
            if isinstance(item, StructDef):
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

        # Second pass: check function bodies
        for item in program.items:
            if isinstance(item, Function):
                self._check_function(item)
            elif isinstance(item, ClassDef):
                self._check_class(item)

        return self.warnings

    # ── Registration ────────────────────────────────────────────────

    def _register_struct(self, node):
        fields = {}
        for fname, ftype in node.fields:
            if fname in fields:
                 self._error(f"Duplicate field '{fname}' in struct '{node.name}'", node=node,
                             tip="Each field in a struct must have a unique name.")
            resolved = self._resolve(ftype)
            if not self._is_valid_type(resolved):
                raise LeashError(
                    f"Struct '{node.name}' field '{fname}' has unknown type '{ftype}'")
            fields[fname] = ftype
        if not fields:
             self._warn(f"Struct '{node.name}' is empty.", node=node, tip="An empty struct doesn't store any data.")
        self.struct_types[node.name] = fields

    def _register_union(self, node):
        variants = {}
        for vname, vtype in node.variants:
            if vname in variants:
                 self._error(f"Duplicate variant '{vname}' in union '{node.name}'", node=node,
                             tip="Each variant in a union must have a unique name.")
            resolved = self._resolve(vtype)
            if not self._is_valid_type(resolved):
                raise LeashError(
                    f"Union '{node.name}' variant '{vname}' has unknown type '{vtype}'")
            variants[vname] = vtype
        if not variants:
             self._warn(f"Union '{node.name}' is empty.", node=node)
        self.union_types[node.name] = variants

    def _register_enum(self, node):
        seen = set()
        for member in node.members:
            if member in seen:
                raise LeashError(f"Duplicate member '{member}' in enum '{node.name}'",
                                 tip=f"Enum members must have unique names within the same enum definition.")
            seen.add(member)
        if not node.members:
             self._warn(f"Enum '{node.name}' is empty.", node=node)
        self.enum_types[node.name] = node.members

    def _register_alias(self, node):
        self.type_aliases[node.name] = node.target_type

    def _register_class(self, node):
        if node.name in self.class_types or node.name in self.struct_types or node.name in self.union_types:
            self._error(f"Redefinition of type '{node.name}'", node=node)
        
        fields = {}
        for f in node.fields:
            if f.name in fields:
                self._error(f"Duplicate field '{f.name}' in class '{node.name}'", node=f)
            fields[f.name] = (f.var_type, f.visibility)
        
        methods = {}
        for m in node.methods:
            if m.fnc.name in methods:
                self._error(f"Duplicate method '{m.fnc.name}' in class '{node.name}'", node=m.fnc)
            methods[m.fnc.name] = (m.fnc, m.visibility, m.is_static)
        
        self.class_types[node.name] = {'fields': fields, 'methods': methods}

    def _register_function_sig(self, node):
        arg_types = [t for _, t in node.args]
        self.func_types[node.name] = (arg_types, node.return_type)

    # ── Type resolution ─────────────────────────────────────────────

    def _strip_imut(self, type_name):
        """Strip the 'imut ' prefix from a type name if present."""
        if isinstance(type_name, str) and type_name.startswith('imut '):
            return type_name[5:]
        return type_name

    def _is_imut(self, type_name):
        """Check if a type string has the imut qualifier."""
        return isinstance(type_name, str) and type_name.startswith('imut ')

    def _resolve(self, type_name):
        """Resolve type aliases (strips imut first, then re-applies if needed)."""
        stripped = self._strip_imut(type_name)
        visited = set()
        while stripped in self.type_aliases and stripped not in visited:
            visited.add(stripped)
            stripped = self.type_aliases[stripped]
        return stripped

    def _base_type(self, type_name):
        """Get the base type category (strip bit-widths, array brackets, imut)."""
        t = self._resolve(type_name)
        if t.endswith(']') and '[' in t:
            return 'array'
        if t.startswith('int<') or t == 'int':
            return 'int'
        if t.startswith('uint<') or t == 'uint':
            return 'uint'
        if t.startswith('float<') or t == 'float':
            return 'float'
        if t.startswith('vec<') and t.endswith('>'):
            return 'vec'
        if t in self.class_types:
            return 'class'
        return t

    def _is_valid_type(self, type_name):
        """Check if a type name is a known type."""
        t = self._resolve(type_name)
        # Strip array suffix
        if t.endswith(']') and '[' in t:
            t = t.split('[')[0]
        base = self._base_type(t)
        if base in ('int', 'uint', 'float', 'string', 'char', 'bool', 'void', 'vec'):
            return True
        if t in self.struct_types or t in self.union_types or t in self.enum_types or t in self.class_types:
            return True
        return False

    def _is_numeric(self, type_name):
        b = self._base_type(type_name)
        return b in ('int', 'uint', 'float')

    def _check_type_conv(self, expr):
        src_t = self._infer_type(expr.expr)
        dst_t = expr.target_type
        
        # We allow toint(int, ANY string/number)
        if expr.name == 'toint':
            if not self._is_int_family(dst_t):
                 raise LeashError(f"toint requires a target integer type, got '{dst_t}'", node=expr)
            return dst_t
        elif expr.name == 'tofloat':
            if not self._is_float_family(dst_t):
                 raise LeashError(f"tofloat requires a target float type, got '{dst_t}'", node=expr)
            return dst_t
        return dst_t

    def _is_int_family(self, type_name):
        b = self._base_type(type_name)
        return b in ('int', 'uint', 'char', 'bool')

    def _is_float_family(self, type_name):
        b = self._base_type(type_name)
        return b == 'float'

    def _error(self, msg, node=None, tip=None):
        """Create a LeashError with position info from an AST node."""
        line = getattr(node, 'line', None) if node else None
        col = getattr(node, 'col', None) if node else None
        raise LeashError(msg, line=line, col=col, tip=tip)

    def _warn(self, msg, node=None, tip=None):
        """Add a warning with position info."""
        line = getattr(node, 'line', None) if node else None
        col = getattr(node, 'col', None) if node else None
        pos = f" (line {line}, col {col})" if line else ""
        self.warnings.append(f"{msg}{pos}" + (f"\n   Tip: {tip}" if tip else ""))

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

        # Enums are only compatible with the same enum type or int
        if src_r in self.enum_types and dst_r in self.enum_types:
            if src_r != dst_r:
                raise LeashError(f"Cannot compare or assign different enum types: '{src_r}' and '{dst_r}'",
                                 tip="To compare different enums, cast one to int: `(int)Enum::Member == (int)OtherEnum::OtherMember`")
            return True
            
        if src_r in self.enum_types and dst_b == 'int':
            return True
        if dst_r in self.enum_types and src_b == 'int':
            return True
        
        # Class compatibility
        if src_r == dst_r and src_r in self.class_types:
            return True

        return False

    def _check_visibility(self, type_name, member_name, is_method, node):
        if type_name not in self.class_types:
            return
        
        cls = self.class_types[type_name]
        kind = 'methods' if is_method else 'fields'
        if member_name not in cls[kind]:
            return # Let the caller handle missing member
        
        info = cls[kind][member_name]
        vis = info[1]
        if vis == 'priv' and self.current_class != type_name:
            self._error(f"Cannot access private {'method' if is_method else 'field'} '{member_name}' of class '{type_name}'", node=node)

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
             self._warn(f"Function '{node.name}' has an empty body.", node=node,
                        tip="Did you forget to implement it? Or maybe you wanted a `void` function that does nothing?")

        if len(node.args) > 8:
            self._warn(f"Function '{node.name}' has {len(node.args)} parameters.", node=node,
                       tip="Functions with too many parameters are harder to read and maintain. Consider grouping them in a `struct`.")

        # Register args as local vars
        for arg_name, arg_type in node.args:
            is_imut = self._is_imut(arg_type)
            bare_type = self._strip_imut(arg_type)
            self.var_types[arg_name] = bare_type
            self.var_immutable[arg_name] = is_imut
            self.current_func_params.add(arg_name)

        last_was_return = self._check_statements(node.body)
        
        bare_ret = self._strip_imut(node.return_type) if node.return_type else 'void'
        if bare_ret != 'void' and not last_was_return:
             self._warn(f"Function '{node.name}' might not return a value on all paths.", node=node,
                        tip=f"This function is declared to return '{bare_ret}', but its body doesn't end with a `return` statement.")

        # Post-check: usage
        for var_name in self.var_types:
            if var_name not in saved_types: # Local variable
                if var_name not in self.used_vars and var_name not in self.current_func_params:
                     if var_name != "args": # Specialize for 'args' in main
                         self._warn(f"Unused local variable '{var_name}' in function '{node.name}'",
                                    tip=f"If you don't need this variable, consider removing it.")
        
        for param_name in self.current_func_params:
            if param_name not in self.used_params:
                if node.name == "main" and param_name == "args":
                     pass # Allow unused args in main
                else:
                    self._warn(f"Unused parameter '{param_name}' in function '{node.name}'",
                               tip=f"If the function logic doesn't require this parameter, consider removing it or renaming it to `_{param_name}`.")

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
                 self._error(f"Class '{node.name}' field '{f.name}' has unknown type '{f.var_type}'", node=f)

        for m in node.methods:
             # Add 'this' to scope for instance methods
             if not m.is_static:
                  self.var_types['this'] = node.name
                  self.var_immutable['this'] = True
             
             self._check_function(m.fnc)
             
             if not m.is_static:
                  del self.var_types['this']
                  del self.var_immutable['this']

        self.current_class = None

    def _check_statements(self, statements):
        """Check a list of statements, checking for unreachable code. Returns True if last stmt is return."""
        was_returned = False
        for stmt in statements:
            if was_returned:
                 self._warn("Unreachable code detected after return statement.", node=stmt,
                            tip="Any statements after a `return`, `break` or `continue` will never be executed.")
                 break # Only warn once per block
            
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
                    if '[' in resolved and ']' in resolved:
                        if resolved != 'char[]':
                            self._error(
                                f"Argument {i+1} of show() is an array ('{t}'), which is not supported.",
                                node=arg,
                                tip="To print an array, use a `foreach` loop to iterate over its elements: `foreach i, v in<array> my_arr { show(v); }`")
                    # Check if it's a struct
                    if resolved in self.struct_types:
                         self._error(
                            f"Argument {i+1} of show() is a struct ('{resolved}'), which is not supported.",
                            node=arg,
                            tip="To print a struct, use a `foreach` loop to iterate over its members: `foreach k, v in<struct> my_struct { show(k, \": \", v); }`")
        elif isinstance(stmt, ExpressionStatement):
            self._infer_type(stmt.expr)
        elif isinstance(stmt, IfStatement):
            self._check_if(stmt)
        elif isinstance(stmt, WhileStatement):
            self._infer_type(stmt.condition)
            if not stmt.body:
                 self._warn("Empty `while` loop body.", node=stmt, tip="This loop will spin and probably hang your program if it runs. Did you forget to add logic or a break condition?")
            self._check_statements(stmt.body)
        elif isinstance(stmt, ForStatement):
            self._check_stmt(stmt.init)
            self._infer_type(stmt.condition)
            self._check_stmt(stmt.step)
            if not stmt.body:
                 self._warn("Empty `for` loop body.", node=stmt, tip="This loop will execute its condition and step repeatedly but do nothing within the body.")
            self._check_statements(stmt.body)
        elif isinstance(stmt, DoWhileStatement):
            if not stmt.body:
                  self._warn("Empty `do-while` loop body.", node=stmt)
            self._check_statements(stmt.body)
            self._infer_type(stmt.condition)
        elif isinstance(stmt, ForeachStructStatement):
            self._infer_type(stmt.struct_expr)
            self.var_types[stmt.name_var] = 'string'
            self.var_types[stmt.value_var] = 'int'  # Approximate
            self._check_statements(stmt.body)
        elif isinstance(stmt, ForeachArrayStatement):
            arr_t = self._infer_type(stmt.array_expr)
            elem_t = 'int'
            if arr_t and '[' in arr_t:
                elem_t = arr_t.split('[')[0]
            self.var_types[stmt.index_var] = 'int'
            self.var_types[stmt.value_var] = elem_t
            self._check_statements(stmt.body)
        elif isinstance(stmt, ForeachStringStatement):
            self._infer_type(stmt.string_expr)
            self.var_types[stmt.index_var] = 'int'
            self.var_types[stmt.char_var] = 'char'
            self._check_statements(stmt.body)
        elif isinstance(stmt, ForeachVectorStatement):
            vec_t = self._infer_type(stmt.vector_expr)
            elem_t = 'any' # default if unknown
            if vec_t and vec_t.startswith('vec<'):
                elem_t = vec_t[4:-1]
            self.var_types[stmt.index_var] = 'int'
            self.var_types[stmt.value_var] = elem_t
            self._check_statements(stmt.body)

    def _check_var_decl(self, stmt):
        if stmt.name in self.var_types:
             self._error(f"Redefinition of variable '{stmt.name}' in the same function",
                         node=stmt,
                         tip=f"Variable '{stmt.name}' is already defined. Use a different name or just assign to it if you want to change its value.")
        decl_type = stmt.var_type
        is_imut = self._is_imut(decl_type)
        bare_decl_type = self._strip_imut(decl_type)
        resolved = self._resolve(bare_decl_type)
        
        if bare_decl_type == 'void':
             self._error("Cannot declare a variable with type 'void'.", node=stmt, 
                         tip="'void' is for functions that don't return a value, not for variables.")

        if not self._is_valid_type(resolved):
            self._error(
                f"Variable '{stmt.name}' declared with unknown type '{bare_decl_type}'",
                node=stmt,
                tip=f"Type '{bare_decl_type}' has not been defined. Did you forget to add a `def {bare_decl_type} : type ...;` or `def {bare_decl_type} : struct {{ ... }};`?")
        
        # Add to current scope
        self.var_types[stmt.name] = bare_decl_type
        self.var_immutable[stmt.name] = is_imut

        # Check for large array
        if '[' in resolved and ']' in resolved:
             try:
                 size_str = resolved.split('[')[1].strip(']')
                 if size_str:
                      total_size = int(size_str)
                      # Assume element size is at least 1 byte, usually 4 or 8.
                      if total_size > 10000:
                           self._warn(f"Variable '{stmt.name}' is a large array ({total_size} elements) declared on the stack.", node=stmt,
                                      tip="Large stack allocations can cause stack overflow. Consider using a `vec<T>` for dynamic or large data.")
             except: pass
        
        if stmt.value is None:
             return # Default initialization (codegen should handle this)

        val_type = self._infer_type(stmt.value)

        # If the assigned value comes from a function returning imut, the variable becomes immutable
        if not is_imut and val_type and self._is_imut(val_type):
            self.var_immutable[stmt.name] = True

        # Union types accept any compatible variant
        if resolved in self.union_types:
            return

        bare_val_type = self._strip_imut(val_type) if val_type else None
        if bare_val_type and not self._types_compatible(bare_val_type, bare_decl_type):
            self.warnings.append(
                f"Warning: Variable '{stmt.name}' declared as '{bare_decl_type}' "
                f"but assigned a value of type '{bare_val_type}'.")

    def _check_assignment(self, stmt):
        # Check immutability on the target
        from .ast_nodes import Identifier
        if isinstance(stmt.target, Identifier):
            if self.var_immutable.get(stmt.target.name, False):
                self._error(
                    f"Cannot assign to immutable variable '{stmt.target.name}'",
                    node=stmt,
                    tip=f"Variable '{stmt.target.name}' was declared as `imut` or received an immutable value from a function. It cannot be reassigned.")
            
            # Self assignment check
            if isinstance(stmt.value, Identifier) and stmt.target.name == stmt.value.name:
                self._warn(f"Self-assignment: '{stmt.target.name} = {stmt.value.name}' has no effect.", node=stmt)

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
                self.warnings.append(
                    f"Warning: Assigning '{bare_val}' to a variable of type '{bare_target}'.")

    def _check_return(self, stmt):
        val_type = self._infer_type(stmt.value)
        bare_ret = self._strip_imut(self.current_return_type) if self.current_return_type else 'void'
        
        if bare_ret == 'void' and stmt.value is not None:
             self._error(f"Function '{self.current_func}' is void but returns a value.", node=stmt,
                         tip="Remove the return value or change the function's return type.")
        
        if bare_ret != 'void' and stmt.value is None:
             self._error(f"Function '{self.current_func}' must return a value of type '{bare_ret}'.", node=stmt,
                         tip="Add a return value that matches the declared return type.")

        if self.current_return_type and val_type:
            bare_val = self._strip_imut(val_type)
            if not self._types_compatible(bare_val, bare_ret):
                self._error(
                    f"Function '{self.current_func}' should return '{bare_ret}' "
                    f"but returns '{bare_val}'",
                    node=stmt,
                    tip=f"Make sure your return value matches the declared return type.")

    def _check_if(self, stmt):
        self._infer_type(stmt.condition)
        if isinstance(stmt.condition, BoolLiteral):
             if stmt.condition.value == True:
                  self._warn("Always-true `if` condition. The `then` block will always execute.", node=stmt)
             else:
                  self._warn("Always-false `if` condition. The `then` block will never execute.", node=stmt)

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
                 self._warn(f"Number literal '{expr.value}' is very large and might overflow 64-bit integer.", node=expr)
            return 'int'
        elif isinstance(expr, FloatLiteral):
            return 'float'
        elif isinstance(expr, StringLiteral):
            if not expr.value:
                 self._warn("Empty string literal.", node=expr)
            return 'string'
        elif isinstance(expr, CharLiteral):
            return 'char'
        elif isinstance(expr, BoolLiteral):
            return 'bool'
        elif isinstance(expr, NullLiteral):
            return 'void'
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
            
            # If not a variable, check if it's a class name (for static calls)
            if expr.name in self.class_types:
                return expr.name

            self._error(f"Undefined variable: '{expr.name}'",
                        node=expr,
                        tip="Make sure this variable is declared before use.")
        elif isinstance(expr, BinaryOp):
            return self._check_binary_op(expr)
        elif isinstance(expr, UnaryOp):
            return self._check_unary_op(expr)
        elif isinstance(expr, Call):
            return self._check_call(expr)
        elif isinstance(expr, MethodCall):
            return self._check_method_call(expr)
        elif isinstance(expr, MemberAccess):
            return self._check_member_access(expr)
        elif isinstance(expr, IndexAccess):
            return self._check_index_access(expr)
        elif isinstance(expr, CastExpr):
            return self._check_cast(expr)
        elif isinstance(expr, StructInit):
            if expr.name in self.class_types:
                return self._check_class_init(expr)
            return self._check_struct_init(expr)
        elif isinstance(expr, ArrayInit):
            return self._check_array_init(expr)
        elif isinstance(expr, EnumMemberAccess):
            return self._check_enum_member_access(expr)
        elif isinstance(expr, TypeConvExpr):
            return self._check_type_conv(expr)
        return None

    def _check_binary_op(self, expr):
        left_t = self._infer_type(expr.left)
        right_t = self._infer_type(expr.right)

        if left_t and right_t:
            left_b = self._base_type(left_t)
            right_b = self._base_type(right_t)

            # Redundancy checks
            if isinstance(expr.right, NumberLiteral):
                if expr.op in ('+', '-') and expr.right.value == 0:
                    self._warn(f"Redundant operation: '{expr.op} 0' has no effect.", node=expr)
                if expr.op == '*' and expr.right.value == 1:
                    self._warn(f"Redundant operation: '* 1' has no effect.", node=expr)
                if expr.op == '/' and expr.right.value == 1:
                    self._warn(f"Redundant operation: '/ 1' has no effect.", node=expr)
                if expr.op == '*' and expr.right.value == 0:
                    self._warn(f"Operation '* 0' always results in 0.", node=expr)
                if expr.op == '%' and expr.right.value == 1:
                    self._warn(f"Operation '% 1' always results in 0.", node=expr)
                if expr.op == '&' and expr.right.value == 0:
                    self._warn(f"Operation '& 0' always results in 0.", node=expr)
                if expr.op in ('|', '^') and expr.right.value == 0:
                    self._warn(f"Redundant operation: '{expr.op} 0' has no effect.", node=expr)

            if isinstance(expr.left, Identifier) and isinstance(expr.right, Identifier) and expr.left.name == expr.right.name:
                if expr.op == '==':
                    self._warn(f"Comparison '{expr.left.name} == {expr.right.name}' is always true.", node=expr)
                if expr.op == '!=':
                    self._warn(f"Comparison '{expr.left.name} != {expr.right.name}' is always false.", node=expr)
                if expr.op == '^':
                    self._warn(f"Operation '{expr.left.name} ^ {expr.right.name}' always results in 0.", node=expr)

            # Mixed sign warning
            if left_b == 'int' and right_b == 'uint' or left_b == 'uint' and right_b == 'int':
                if expr.op not in ('<<', '>>'):
                    self._warn(f"Mixing signed and unsigned integers in '{expr.op}' operation.", node=expr,
                               tip="This can lead to unexpected results if the signed value is negative. Consider casting both to the same type.")

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
            # Mixed string concatenations
            elif expr.op == '+' and ((left_b == 'string' and (right_b in ('char', 'int', 'uint', 'float', 'bool') or (right_t and right_t.startswith('char[')))) or (right_b == 'string' and (left_b in ('char', 'int', 'uint', 'float', 'bool') or (left_t and left_t.startswith('char['))))):
                return 'string'

            # Bitwise and Modulo operations
            if expr.op in ('&', '|', '^', '<<', '>>', '%'):
                if self._is_int_family(left_t) and self._is_int_family(right_t):
                    # Shift limit check
                    if expr.op in ('<<', '>>') and isinstance(expr.right, NumberLiteral):
                        bits = 64 # Default
                        if '<' in left_t:
                             try: bits = int(left_t.split('<')[1].split('>')[0])
                             except: pass
                        if expr.right.value >= bits:
                             self._warn(f"Shift amount {expr.right.value} is greater than or equal to bit-width {bits}.", node=expr,
                                        tip="This is undefined behavior in many environments. Make sure the shift amount is within [0, bits-1].")
                    
                    return left_t
                else:
                    raise LeashError(
                        f"Operator '{expr.op}' is only supported for integer types",
                        tip=f"Operands are '{left_t}' and '{right_t}'.")

            # Logical operations
            if expr.op in ('&&', '||'):
                if isinstance(expr.left, BoolLiteral):
                    if expr.op == '&&' and expr.left.value == True:
                         self._warn("Redundant 'true && ...' operation.", node=expr)
                    if expr.op == '||' and expr.left.value == False:
                         self._warn("Redundant 'false || ...' operation.", node=expr)
                return 'bool'

            # Numeric operations
            if self._is_numeric(left_t) and self._is_numeric(right_t):
                if expr.op in ('==', '!=', '<', '<=', '>', '>='):
                    return 'bool'
                # Result type: float wins over int
                if left_b == 'float' or right_b == 'float':
                    return 'float'
                
                # Zero-division safety check (static)
                if expr.op in ('/', '%') and isinstance(expr.right, NumberLiteral) and expr.right.value == 0:
                     raise LeashError(f"{'Division' if expr.op == '/' else 'Modulo'} by zero detected statically!", 
                                      tip="Make sure you aren't dividing by zero, as it will crash your program at runtime.")
                
                return 'int'

            # Mixed string + non-string
            if (left_b == 'string') or (right_b == 'string'):
                if (left_b == 'string') and (right_b == 'string'):
                     # already handled above
                     pass
                elif expr.op == '+' and ((left_b == 'string' and (right_b == 'char' or (right_t and right_t.startswith('char[')))) or (right_b == 'string' and (left_b == 'char' or (left_t and left_t.startswith('char['))))):
                     pass
                else:
                    raise LeashError(
                        f"Cannot use operator '{expr.op}' between '{left_t}' and '{right_t}'",
                        tip="Leash supports '+' for concatenating strings with numbers, but other operators like '-', '*', '/' are not supported for mixed types.")

        return left_t  # Best guess fallback

    def _check_unary_op(self, expr):
        val_t = self._infer_type(expr.expr)
        if val_t:
            val_b = self._base_type(val_t)
            if expr.op == '!':
                if isinstance(expr.expr, UnaryOp) and expr.expr.op == '!':
                    self._warn("Double negation '!!' is redundant.", node=expr)
                if val_b not in ('bool', 'int', 'uint'):
                    raise LeashError(f"Operator '!' is not supported for type '{val_t}'", tip="Logical NOT is supported for bool and integer types.")
                return 'bool'
            if expr.op == '~':
                if val_b not in ('int', 'uint'):
                    raise LeashError(f"Operator '~' is not supported for type '{val_t}'", tip="Bitwise NOT is supported for integer types.")
                return val_t
            if expr.op == '-':
                if val_b not in ('int', 'uint', 'float'):
                    raise LeashError(f"Unary minus is not supported for type '{val_t}'", tip="Unary minus is supported for numeric types.")
                return val_t
        return val_t

    def _check_call(self, expr):
        if expr.name == 'show':
            return 'void'
        
        if expr.name == 'cstr':
            if len(expr.args) != 1:
                self._error(f"Function 'cstr' expects 1 argument", node=expr)
            arg_t = self._infer_type(expr.args[0])
            if arg_t and self._resolve(arg_t) == 'char[]':
                 self._warn("Calling 'cstr' on a 'char[]' is redundant.", node=expr)
            if arg_t and self._resolve(arg_t) != 'string' and self._resolve(arg_t) != 'char[]':
                self._error(f"Argument 1 of 'cstr' must be 'string', got '{arg_t}'", node=expr.args[0])
            return 'char[]'
            
        if expr.name == 'lstr':
            if len(expr.args) != 1:
                self._error(f"Function 'lstr' expects 1 argument", node=expr)
            arg_t = self._infer_type(expr.args[0])
            if arg_t and self._resolve(arg_t) == 'string':
                 self._warn("Calling 'lstr' on a 'string' is redundant.", node=expr)
            if arg_t and self._resolve(arg_t) != 'char[]' and self._resolve(arg_t) != 'string':
                self._error(f"Argument 1 of 'lstr' must be 'char[]', got '{arg_t}'", node=expr.args[0])
            return 'string'

        if expr.name == 'get':
            if len(expr.args) > 1:
                self._error(f"Function 'get' expects 0 or 1 argument(s), but got {len(expr.args)}", node=expr)
            if len(expr.args) == 0:
                 self._warn("Calling 'get()' without a prompt string.", node=expr, tip="You can pass a string to `get()` to show a prompt: `get(\"Enter name: \")`.")
            if len(expr.args) == 1:
               arg_t = self._infer_type(expr.args[0])
               if arg_t and self._resolve(arg_t) != 'string':
                   self._error(f"Argument 1 of 'get' must be 'string', got '{arg_t}'", node=expr.args[0])
            return 'string'
        
        if expr.name == 'tostring':
            if len(expr.args) != 1:
                self._error(f"Function 'tostring' expects 1 argument, but got {len(expr.args)}", node=expr)
            arg_t = self._infer_type(expr.args[0])
            if arg_t and self._resolve(arg_t) == 'string':
                 self._warn("Calling 'tostring' on a 'string' is redundant.", node=expr)
            return 'string'

        sig = self.func_types.get(expr.name)
        if sig is None:
            self._error(f"Call to undefined function: '{expr.name}'",
                        node=expr,
                        tip="Make sure the function is defined before calling it.")

        expected_args, return_type = sig

        if len(expr.args) != len(expected_args):
            self._error(
                f"Function '{expr.name}' expects {len(expected_args)} argument(s), "
                f"but got {len(expr.args)}",
                node=expr,
                tip="Check the function signature and pass the correct number of arguments.")

        for i, (arg_expr, expected_type) in enumerate(zip(expr.args, expected_args)):
            arg_type = self._infer_type(arg_expr)
            bare_arg = self._strip_imut(arg_type) if arg_type else None
            bare_expected = self._strip_imut(expected_type)
            if bare_arg and not self._types_compatible(bare_arg, bare_expected):
                self.warnings.append(
                    f"Warning: Argument {i+1} of '{expr.name}' expects '{bare_expected}' "
                    f"but got '{bare_arg}'.")

        return return_type

    def _check_member_access(self, expr):
        base_type = self._infer_type(expr.expr)
        if base_type is None:
            return None

        resolved = self._resolve(base_type)

        # String .size
        if self._base_type(resolved) == 'string' and expr.member == 'size':
            return 'int'

        # Array .size
        if resolved and resolved.endswith(']') and '[' in resolved and expr.member == 'size':
            return 'int'

        # Struct member
        if resolved in self.struct_types:
            fields = self.struct_types[resolved]
            if expr.member not in fields:
                raise LeashError(
                    f"Struct '{resolved}' has no member named '{expr.member}'",
                    tip=f"Available members: {', '.join(fields.keys())}")
            return fields[expr.member]

        # Enum .name property
        if resolved in self.enum_types:
            if expr.member == 'name':
                return 'string'
            else:
                 raise LeashError(
                    f"Enum '{resolved}' has no property named '{expr.member}'",
                    tip="Enums only have a `.name` property which returns the member name as a string.")

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

        # Class member
        if resolved in self.class_types:
            self._check_visibility(resolved, expr.member, False, expr)
            fields = self.class_types[resolved]['fields']
            if expr.member not in fields:
                raise LeashError(f"Class '{resolved}' has no field named '{expr.member}'",
                                 tip=f"Available fields: {', '.join(fields.keys())}")
            return fields[expr.member][0]

        return None

    def _check_index_access(self, expr):
        base_type = self._infer_type(expr.expr)
        idx_type = self._infer_type(expr.index)

        if idx_type and not self._is_int_family(idx_type):
            raise LeashError(
                f"Array/string index must be an integer, but got '{idx_type}'",
                tip="Use an integer expression as the index.")
        
        if isinstance(expr.index, NumberLiteral) and expr.index.value < 0:
             self._error(f"Negative array index {expr.index.value} is not allowed.", node=expr.index,
                         tip="Array indices must be non-negative (0 or greater).")

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

    def _check_class_init(self, expr):
        if expr.name not in self.class_types:
            raise LeashError(f"Undefined class: '{expr.name}'")
        
        cls = self.class_types[expr.name]
        fields = cls['fields']
        for key, val_expr in expr.kwargs:
            if key not in fields:
                raise LeashError(f"Class '{expr.name}' has no field named '{key}'",
                                 tip=f"Available fields: {', '.join(fields.keys())}")
            
            self._check_visibility(expr.name, key, False, val_expr)
            expected, _ = fields[key]
            actual = self._infer_type(val_expr)
            if actual and not self._types_compatible(actual, expected):
                self.warnings.append(f"Warning: Class '{expr.name}' field '{key}' expects '{expected}' but got '{actual}'.")
        
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

    def _check_enum_member_access(self, expr):
        if expr.enum_name not in self.enum_types:
            # Maybe it's not an enum, but someone used :: anyway
            raise LeashError(f"Undefined enum: '{expr.enum_name}'",
                             tip=f"Did you forget to define it? `def {expr.enum_name} : enum {{ ... }};` ")
        
        members = self.enum_types[expr.enum_name]
        if expr.member_name not in members:
            raise LeashError(f"Enum '{expr.enum_name}' has no member named '{expr.member_name}'",
                             tip=f"Available members: {', '.join(members)}")
        
        return expr.enum_name

    def _check_method_call(self, expr):
        base_t = self._infer_type(expr.expr)
        if not base_t:
            return None
        base_b = self._base_type(base_t)
        
        if base_b == 'vec':
            inner_t = base_t[4:-1]
            if expr.method in ('pushb', 'pushf', 'insert'):
                 # These should take one or two arguments and return void (or maybe something else, let's assume void)
                 return 'void'
            elif expr.method in ('popb', 'popf'):
                 return inner_t
            elif expr.method == 'size':
                 return 'int'
            elif expr.method == 'get':
                 return inner_t
            elif expr.method == 'set':
                 return 'void'
            elif expr.method == 'clear':
                 return 'void'
            else:
                 raise LeashError(f"Vector has no method named '{expr.method}'", node=expr)
        
        # String methods? Currently string only has .size which is member access
        # but maybe someone uses .size()
        if base_b == 'string' and expr.method == 'size':
             return 'int'
        
        # Array methods?
        if base_b == 'array' and expr.method == 'size':
             return 'int'

        # Class method / Static call
        # If expr.expr is an Identifier and it's a class name, it's a static call?
        from .ast_nodes import Identifier
        is_static = False
        target_cls = None
        if isinstance(expr.expr, Identifier) and expr.expr.name in self.class_types:
            is_static = True
            target_cls = expr.expr.name
        elif base_t in self.class_types:
            target_cls = base_t
        
        if target_cls:
            self._check_visibility(target_cls, expr.method, True, expr)
            methods = self.class_types[target_cls]['methods']
            if expr.method not in methods:
                 raise LeashError(f"Class '{target_cls}' has no method named '{expr.method}'",
                                  tip=f"Available methods: {', '.join(methods.keys())}")
            
            fnc_node, _, m_is_static = methods[expr.method]
            
            # Static/Instance check 
            if is_static and not m_is_static:
                 self._error(f"Cannot call instance method '{expr.method}' statically on class '{target_cls}'",
                             node=expr, tip=f"Instance methods require an object. Call it on an instance variable: `let p = {target_cls}.new(...); p.{expr.method}(...);` ")
            elif not is_static and m_is_static:
                 self._error(f"Cannot call static method '{expr.method}' on an instance of class '{target_cls}'",
                             node=expr, tip=f"Static methods should be called on the class name: `{target_cls}.{expr.method}(...)` ")
            
            expected_args = [t for _, t in fnc_node.args]
            
            if len(expr.args) != len(expected_args):
                 raise LeashError(f"Method '{expr.method}' of class '{target_cls}' expects {len(expected_args)} arguments, but got {len(expr.args)}")
            
            for i, (arg_expr, expected_type) in enumerate(zip(expr.args, expected_args)):
                arg_type = self._infer_type(arg_expr)
                if arg_type and not self._types_compatible(arg_type, expected_type):
                    self._warn(f"Argument {i+1} of method '{expr.method}' expects '{expected_type}' but got '{arg_type}'", node=arg_expr)
            
            return fnc_node.return_type

        raise LeashError(f"Type '{base_t}' has no method named '{expr.method}'", node=expr)
