from .errors import LeashError
from .ast_nodes import (
    UnionDef, VariableDecl, Assignment, MemberAccess, Identifier,
    NumberLiteral, FloatLiteral, BoolLiteral, StringLiteral, NullLiteral,
    AsExpr, ByteConvExpr, Function, ClassMethod, Call,
)

class LowLevelChecker:
    def __init__(self):
        self.in_unsafe_func = False
        self.errors = []
        self.in_assign_target = False
        self.union_variants = {}  # union_type_name -> set of variant names
        self.var_union_info = {}  # var_name -> {union_type, active_variant} (per function scope)
        self.param_types = {}     # param_name -> type_name (per function scope)
        self.unsafe_func_names = set()  # names of functions marked `unsafe`

    def check(self, ast):
        # First pass: collect union definitions and unsafe function names
        self._collect_info(ast)
        self.visit(ast)
        return self.errors

    def _collect_info(self, node):
        if isinstance(node, UnionDef):
            self.union_variants[node.name] = {v[0] for v in node.variants}
        elif isinstance(node, Function) and getattr(node, "is_unsafe", False):
            self.unsafe_func_names.add(node.name)
        elif isinstance(node, ClassMethod):
            fnc = getattr(node, "fnc", None)
            if fnc and getattr(node, "is_unsafe", False):
                self.unsafe_func_names.add(fnc.name)
        elif isinstance(node, list):
            for item in node:
                self._collect_info(item)
        elif hasattr(node, '__dict__'):
            for key, value in vars(node).items():
                if not key.startswith('_'):
                    self._collect_info(value)

    def _error(self, msg, node=None, tip=None):
        line = getattr(node, "line", None)
        col = getattr(node, "col", None)
        self.errors.append(LeashError(msg, line=line, col=col, tip=tip, code="E_LOWLEVEL"))

    def _get_var_name(self, expr):
        """Extract variable name from an expression (handles chains like a.b.c -> a)."""
        if isinstance(expr, Identifier):
            return expr.name
        if isinstance(expr, MemberAccess):
            return self._get_var_name(expr.expr)
        return None

    def _infer_literal_type_name(self, expr):
        """Guess the type name of a literal expression for union variant matching."""
        if isinstance(expr, NumberLiteral):
            v = expr.value
            if isinstance(v, float) or (isinstance(v, str) and '.' in v):
                return "float"
            return "int"
        if isinstance(expr, FloatLiteral):
            return "float"
        if isinstance(expr, BoolLiteral):
            return "bool"
        if isinstance(expr, StringLiteral):
            return "string"
        if isinstance(expr, NullLiteral):
            return None
        return None

    def _find_matching_variant(self, union_name, expr):
        """Try to find which union variant matches an expression based on simple type heuristics."""
        type_name = self._infer_literal_type_name(expr)
        if type_name is None:
            # Check if it's an identifier whose type we know from function params
            if isinstance(expr, Identifier) and expr.name in self.param_types:
                type_name = self.param_types[expr.name]
        if type_name is None:
            return None
        # Normalize type name to match variant names
        type_name = type_name.lower().replace('<', '_').replace('>', '_').replace(' ', '_')
        # Try exact match first, then partial match
        variants = self.union_variants.get(union_name, set())
        # Check if type_name is in any variant name or vice versa
        best = None
        for v in variants:
            vn = v.lower()
            if type_name == vn:
                return v
            if type_name in vn or vn in type_name:
                best = v
        return best

    def visit(self, node):
        if node is None:
            return
        if isinstance(node, list):
            for item in node:
                self.visit(item)
            return
        method_name = f"visit_{node.__class__.__name__}"
        visitor = getattr(self, method_name, self.generic_visit)
        visitor(node)

    def generic_visit(self, node):
        if hasattr(node, '__dict__'):
            for key, value in vars(node).items():
                if not key.startswith('_'):
                    self.visit(value)

    def visit_Function(self, node):
        old_unsafe = self.in_unsafe_func
        old_var_info = self.var_union_info
        old_param_types = self.param_types
        self.in_unsafe_func = getattr(node, "is_unsafe", False)
        self.var_union_info = {}
        self.param_types = {}
        # Track parameter types for union variant matching
        for arg in node.args:
            if len(arg) >= 2:
                self.param_types[arg[0]] = arg[1]
        self.generic_visit(node)
        self.param_types = old_param_types
        self.var_union_info = old_var_info
        self.in_unsafe_func = old_unsafe

    def visit_ClassMethod(self, node):
        old_unsafe = self.in_unsafe_func
        old_var_info = self.var_union_info
        old_param_types = self.param_types
        self.in_unsafe_func = getattr(node, "is_unsafe", False) or getattr(getattr(node, "fnc", None), "is_unsafe", False)
        self.var_union_info = {}
        self.param_types = {}
        fnc = getattr(node, "fnc", None)
        if fnc:
            for arg in fnc.args:
                if len(arg) >= 2:
                    self.param_types[arg[0]] = arg[1]
        self.generic_visit(node)
        self.param_types = old_param_types
        self.var_union_info = old_var_info
        self.in_unsafe_func = old_unsafe

    def visit_VariableDecl(self, node):
        # Track union variable declarations
        var_type = node.var_type
        if var_type in self.union_variants:
            info = {"union_type": var_type, "active_variant": None}
            # Try to determine active variant from initializer
            if node.value is not None:
                variant = self._find_matching_variant(var_type, node.value)
                if variant is not None:
                    info["active_variant"] = variant
            # Also check if var_type has stripped pointer types
            self.var_union_info[node.name] = info
        self.generic_visit(node)

    def visit_MemberAccess(self, node):
        """Detect reads from union variants where the active variant differs."""
        var_name = self._get_var_name(node)
        if var_name and var_name in self.var_union_info and not self.in_assign_target:
            info = self.var_union_info[var_name]
            variant = node.member
            if variant in self.union_variants.get(info["union_type"], set()):
                if info["active_variant"] is not None and info["active_variant"] != variant:
                    if not self.in_unsafe_func:
                        self._error(
                            f"Reading union '{info['union_type']}' variant '{variant}' when "
                            f"'{info['active_variant']}' is active is type-punning and unsafe "
                            f"outside an `unsafe` function",
                            node,
                            tip="Mark the containing function as `unsafe`: `unsafe fnc ...`. "
                                 "Or avoid accessing different union variants as different types."
                        )
        self.generic_visit(node)

    def visit_Assignment(self, node):
        target = node.target
        new_variant = None
        # Determine the target variant (if any) before visiting the value
        if isinstance(target, MemberAccess):
            var_name = self._get_var_name(target.expr)
            if var_name and var_name in self.var_union_info:
                info = self.var_union_info[var_name]
                variant = target.member
                if variant in self.union_variants.get(info["union_type"], set()):
                    new_variant = (var_name, info, variant)
        # Visit the value first (reads) before updating the active variant for the write
        self.visit(node.value)
        # Then handle the write to the union variant
        if new_variant is not None:
            var_name, info, variant = new_variant
            if info["active_variant"] is not None and info["active_variant"] != variant:
                if not self.in_unsafe_func:
                    self._error(
                        f"Changing union '{info['union_type']}' active variant from "
                        f"'{info['active_variant']}' to '{variant}' is type-punning "
                        f"and unsafe outside an `unsafe` function",
                        node,
                        tip="Mark the containing function as `unsafe`: `unsafe fnc ...`. "
                             "Or avoid accessing different union variants as different types."
                    )
            info["active_variant"] = variant
        self.in_assign_target = True
        self.visit(target)
        self.in_assign_target = False

    def visit_PointerMemberAccess(self, node):
        if not self.in_unsafe_func:
            self._error(
                "Dereferencing raw pointer `->` for member access is unsafe outside an `unsafe` function — this can corrupt memory or GC tracking",
                node,
                tip="Mark the containing function as `unsafe`: `unsafe fnc ...`. "
                     "Or use a safe pointer `&T` instead of `*T` and access with `.` — "
                     "safe pointers don't need `unsafe`."
            )
        self.generic_visit(node)

    def visit_UnaryOp(self, node):
        if getattr(node, "op", "") == "*" and not self.in_unsafe_func:
            self._error(
                "Dereferencing raw pointer `*` is unsafe outside an `unsafe` function — this can corrupt memory",
                node,
                tip="Mark the containing function as `unsafe`: `unsafe fnc ...`. "
                     "Or use a safe pointer `&T` instead of `*T` — "
                     "safe pointers auto-dereference and don't need `unsafe`."
            )
        self.generic_visit(node)

    def visit_CastExpr(self, node):
        if not self.in_unsafe_func:
            dst_type = getattr(node.target_type, "name", str(node.target_type))
            if dst_type in ("int", "uint", "long", "ulong", "int64", "uint64", "int32", "uint32"):
                self._error(
                    "Casting a pointer to integer type is unsafe outside an `unsafe` function — this can hide pointers from the Garbage Collector",
                    node,
                    tip="Mark the containing function as `unsafe`: `unsafe fnc ...`. "
                         "Or avoid the cast entirely by keeping the value as a pointer "
                         "(`*T` or `&T`) instead of converting to an integer."
                )
        self.generic_visit(node)

    def visit_AsExpr(self, node):
        """Flag `as` casts: pointer↔integer conversions outside unsafe functions."""
        if not self.in_unsafe_func:
            target = node.target_type
            target_name = getattr(target, "name", str(target)) if not isinstance(target, str) else target
            if target_name in ("int", "uint", "long", "ulong", "int64", "uint64", "int32", "uint32",
                               "int8", "int16", "int64", "uint8", "uint16", "uint32", "uint64"):
                self._error(
                    "Casting a pointer to integer with `as` is unsafe outside an `unsafe` function — this can hide pointers from the Garbage Collector",
                    node,
                    tip="Mark the containing function as `unsafe`: `unsafe fnc ...`. "
                         "Or use safe type conversion functions (`toint`, `tofloat`) instead."
                )
            if target_name and target_name.startswith(("*", "&")):
                self._error(
                    "Casting to a pointer type with `as` is unsafe outside an `unsafe` function — this can create dangling or misaligned pointers",
                    node,
                    tip="Mark the containing function as `unsafe`: `unsafe fnc ...`. "
                         "Avoid casting arbitrary values to pointer types."
                )
        self.generic_visit(node)

    def visit_ByteConvExpr(self, node):
        """Flag raw byte reinterpretation builtins outside unsafe functions."""
        if not self.in_unsafe_func:
            self._error(
                f"Raw byte reinterpretation `{node.name}` is unsafe outside an `unsafe` function — this bypasses type safety",
                node,
                tip="Mark the containing function as `unsafe`: `unsafe fnc ...`. "
                     "These functions reinterpret memory directly and should only be used when absolutely necessary."
            )
        self.generic_visit(node)

    def visit_Call(self, node):
        """Flag calls to `unsafe` functions from non-unsafe context."""
        if not self.in_unsafe_func:
            fn_name = node.name if isinstance(node.name, str) else getattr(node.name, "name", str(node.name))
            if fn_name in self.unsafe_func_names:
                self._error(
                    f"Calling `unsafe` function `{fn_name}` is unsafe outside an `unsafe` function",
                    node,
                    tip="Mark the containing function as `unsafe`: `unsafe fnc ...`. "
                         "Or wrap the call in an `unsafe` context."
                )
        self.generic_visit(node)
