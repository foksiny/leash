from .errors import LeashError
from .ast_nodes import (
    UnionDef, VariableDecl, Assignment, MemberAccess, Identifier,
    NumberLiteral, FloatLiteral, BoolLiteral, StringLiteral, NullLiteral,
    AsExpr, ByteConvExpr, Function, ClassMethod, Call, UnaryOp, CastExpr,
    StructDef, TypeAlias, GlobalVarDecl, StructInit,
)
import re

class LowLevelChecker:
    def __init__(self):
        self.in_unsafe_func = False
        self.in_nogc_func = False
        self.errors = []
        self.in_assign_target = False
        self.union_variants = {}  # union_type_name -> set of variant names
        self.var_union_info = {}  # union location key -> {union_type, active_variant} (per function scope)
        self.param_types = {}     # param_name -> type_name (per function scope)
        self.local_types = {}     # local var_name -> type_name (per function scope)
        self.struct_fields = {}   # struct_name -> {field_name: type}
        self.type_aliases = {}    # alias name -> target type name
        self.global_types = {}    # global var name -> declared type
        self.global_union_info = {}  # global union var -> {union_type, active_variant}
        self.unsafe_func_names = set()  # names of functions marked `unsafe`
        self.nogc_func_names = set()  # names of functions marked `nogc`

    def check(self, ast):
        # First pass: collect union definitions and unsafe function names
        self._collect_info(ast)
        self.var_union_info = dict(self.global_union_info)
        self.visit(ast)
        return self.errors

    def _collect_info(self, node):
        if isinstance(node, UnionDef):
            self.union_variants[node.name] = {v[0] for v in node.variants}
        elif isinstance(node, StructDef):
            self.struct_fields[node.name] = {
                f[0]: f[1] for f in node.fields if len(f) >= 2
            }
        elif isinstance(node, TypeAlias):
            self.type_aliases[node.name] = node.target_type
        elif isinstance(node, GlobalVarDecl):
            if node.var_type:
                self.global_types[node.name] = node.var_type
                resolved = self._resolve_alias(node.var_type)
                if resolved in self.union_variants:
                    variant = None
                    if node.value is not None:
                        variant = self._find_matching_variant(resolved, node.value)
                    self.global_union_info[node.name] = {
                        "union_type": resolved,
                        "active_variant": variant,
                    }
        elif isinstance(node, Function):
            if getattr(node, "is_unsafe", False):
                self.unsafe_func_names.add(node.name)
            if getattr(node, "is_nogc", False):
                self.nogc_func_names.add(node.name)
        elif isinstance(node, ClassMethod):
            fnc = getattr(node, "fnc", None)
            if fnc:
                if getattr(node, "is_unsafe", False):
                    self.unsafe_func_names.add(fnc.name)
                if getattr(node.fnc, "is_nogc", False):
                    self.nogc_func_names.add(fnc.name)
        elif isinstance(node, list):
            for item in node:
                self._collect_info(item)
        elif hasattr(node, '__dict__'):
            for key, value in vars(node).items():
                if not key.startswith('_'):
                    self._collect_info(value)

    def _error(self, msg, node=None, tip=None):
        self.errors.append(LeashError(msg, node=node, tip=tip, code="E_LOWLEVEL"))

    def _resolve_alias(self, type_name):
        """Resolve `def X : type Y` aliases (also strips `imut ` and `*`/`&`)."""
        if not type_name:
            return type_name
        t = str(type_name).strip()
        while t and (t.startswith("imut ") or t[0] in "*&"):
            t = t[5:].strip() if t.startswith("imut ") else t[1:].strip()
        seen = set()
        while t in self.type_aliases and t not in seen:
            seen.add(t)
            t = str(self.type_aliases[t]).strip()
            while t and (t.startswith("imut ") or t[0] in "*&"):
                t = t[5:].strip() if t.startswith("imut ") else t[1:].strip()
        return t

    def _member_path(self, node):
        """Full path of a MemberAccess chain: `w.u.f` -> ('w', 'u', 'f')."""
        parts = []
        cur = node
        while isinstance(cur, MemberAccess):
            parts.append(cur.member)
            cur = cur.expr
        if isinstance(cur, Identifier):
            parts.append(cur.name)
            return tuple(reversed(parts))
        return None

    def _union_reads_in_path(self, path):
        """Find union variant reads along a member path.

        Yields (union_key, union_type, variant) where union_key is the
        location key of the union ('u' for a local/global, 'w.u' for a
        union stored in a struct field) and variant is the member read.
        """
        t = (
            self.local_types.get(path[0])
            or self.param_types.get(path[0])
            or self.global_types.get(path[0])
        )
        if t is None:
            return
        for i in range(1, len(path)):
            member = path[i]
            rt = self._resolve_alias(t)
            if rt in self.union_variants:
                if member in self.union_variants[rt]:
                    yield (".".join(path[:i]), rt, member)
                return
            fields = self.struct_fields.get(rt)
            if fields is None:
                return
            t = fields.get(member)
            if t is None:
                return

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
        old_nogc = self.in_nogc_func
        old_var_info = self.var_union_info
        old_param_types = self.param_types
        old_local_types = self.local_types
        self.in_unsafe_func = getattr(node, "is_unsafe", False)
        self.in_nogc_func = getattr(node, "is_nogc", False)
        # Globals stay visible inside functions; local declarations shadow.
        self.var_union_info = dict(self.global_union_info)
        self.param_types = {}
        self.local_types = {}
        # Track parameter types for union variant matching
        for arg in node.args:
            if len(arg) >= 2:
                self.param_types[arg[0]] = arg[1]
        self.generic_visit(node)
        self.param_types = old_param_types
        self.local_types = old_local_types
        self.var_union_info = old_var_info
        self.in_nogc_func = old_nogc
        self.in_unsafe_func = old_unsafe

    def visit_ClassMethod(self, node):
        old_unsafe = self.in_unsafe_func
        old_nogc = self.in_nogc_func
        old_var_info = self.var_union_info
        old_param_types = self.param_types
        old_local_types = self.local_types
        self.in_unsafe_func = getattr(node, "is_unsafe", False) or getattr(getattr(node, "fnc", None), "is_unsafe", False)
        self.in_nogc_func = getattr(getattr(node, "fnc", None), "is_nogc", False)
        # Globals stay visible inside functions; local declarations shadow.
        self.var_union_info = dict(self.global_union_info)
        self.param_types = {}
        self.local_types = {}
        fnc = getattr(node, "fnc", None)
        if fnc:
            for arg in fnc.args:
                if len(arg) >= 2:
                    self.param_types[arg[0]] = arg[1]
        self.generic_visit(node)
        self.param_types = old_param_types
        self.local_types = old_local_types
        self.var_union_info = old_var_info
        self.in_nogc_func = old_nogc
        self.in_unsafe_func = old_unsafe

    def visit_VariableDecl(self, node):
        # Track local declarations so pointer↔int cast checks can tell a
        # `*T`/`&T` source (unsafe to convert) from a plain value cast.
        if getattr(node, "name", None) and getattr(node, "var_type", None):
            self.local_types[node.name] = node.var_type
        # Track union variable declarations (resolving `def MyU : type U`
        # aliases so aliased unions are covered too)
        var_type = node.var_type
        if var_type:
            resolved = self._resolve_alias(var_type)
            if resolved in self.union_variants:
                info = {"union_type": resolved, "active_variant": None}
                # Try to determine active variant from initializer
                if node.value is not None:
                    variant = self._find_matching_variant(resolved, node.value)
                    if variant is not None:
                        info["active_variant"] = variant
                self.var_union_info[node.name] = info
            # `w: W = W{u: 5}` — a union stored in a struct field gets its
            # active variant from the field's literal initializer, tracked
            # under the path key `<var>.<field>`.
            value = node.value
            if isinstance(value, StructInit) and node.name:
                sname = self._resolve_alias(value.name)
                s_fields = self.struct_fields.get(sname, {})
                for fname, fexpr in (value.kwargs or []):
                    ftype = self._resolve_alias(s_fields.get(fname))
                    if ftype in self.union_variants and fexpr is not None:
                        variant = self._find_matching_variant(ftype, fexpr)
                        if variant is not None:
                            self.var_union_info[f"{node.name}.{fname}"] = {
                                "union_type": ftype,
                                "active_variant": variant,
                            }
        self.generic_visit(node)

    def visit_MemberAccess(self, node):
        """Detect reads from union variants where the active variant differs.

        Covers locals, globals, aliased unions, and unions stored in struct
        fields (`w.u.f`): the chain is resolved from the root variable's
        declared type through struct fields down to the union.
        """
        if not self.in_assign_target:
            path = self._member_path(node)
            if path:
                for union_key, union_type, variant in self._union_reads_in_path(path):
                    info = self.var_union_info.get(union_key)
                    if (
                        info
                        and info.get("union_type") == union_type
                        and info.get("active_variant") is not None
                        and info["active_variant"] != variant
                        and not self.in_unsafe_func
                    ):
                        self._error(
                            f"Reading union '{union_type}' variant '{variant}' when "
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
        # Determine the target variant (if any) before visiting the value.
        # Handles direct locals (`u.f = x`), globals, and struct fields
        # (`w.u.f = x`) through path resolution.
        if isinstance(target, MemberAccess):
            path = self._member_path(target)
            if path:
                for union_key, union_type, variant in self._union_reads_in_path(path):
                    info = self.var_union_info.get(union_key)
                    if info and info.get("union_type") == union_type:
                        new_variant = (union_key, info, variant)
                    break
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

    @staticmethod
    def _is_ptr_int_cast_target(target_name):
        """True if the cast target is an integer-family type spelling.

        The typechecker canonicalizes integers as `int<N>`/`uint<N>` (bare
        `int` = int<32>); the old hardcoded C-ish names (`int64`, `uint32`,
        ...) never matched those spellings, so `p as int<64>`, `(int<64>)p`,
        `p as char`, and `p as bool` all slipped past the unsafe-pointer-cast
        check.
        """
        if not target_name:
            return False
        t = str(target_name).strip()
        if t in (
            "int", "uint", "char", "bool",
            "long", "ulong", "int64", "uint64", "int32", "uint32",
            "int8", "int16", "uint8", "uint16",
        ):
            return True
        return re.fullmatch(r"(u?)int<\d+>", t) is not None

    def _expr_is_ptr_like(self, expr):
        """True if expr is statically known to produce a pointer/reference.

        The cast checks only guard pointer→int conversions; value casts like
        `(char)('0' + d)` are legal outside `unsafe`, so the source must be
        identified before flagging. Unknown expressions are not flagged.
        """
        if isinstance(expr, UnaryOp):
            if expr.op == "&":
                return True
            if expr.op == "*":
                # Dereference yields a value (the pointed-to type).
                return False
        if isinstance(expr, Identifier):
            t = self.local_types.get(expr.name) or self.param_types.get(expr.name)
            if t:
                return re.match(r"^(imut\s+)?[*&]", str(t).strip()) is not None
            return False
        # CastExpr/AsExpr chains of non-pointer types produce values.
        if isinstance(expr, (CastExpr, AsExpr)):
            return False
        return False

    def visit_CastExpr(self, node):
        if not self.in_unsafe_func:
            dst_type = getattr(node.target_type, "name", str(node.target_type))
            if self._is_ptr_int_cast_target(dst_type) and self._expr_is_ptr_like(
                getattr(node, "expr", None)
            ):
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
            if self._is_ptr_int_cast_target(target_name) and self._expr_is_ptr_like(
                getattr(node, "expr", None)
            ):
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
        """Flag calls to `unsafe`/`nogc` functions from non-unsafe/non-nogc context."""
        fn_name = node.name if isinstance(node.name, str) else getattr(node.name, "name", str(node.name))
        if not self.in_unsafe_func:
            if fn_name in self.unsafe_func_names:
                self._error(
                    f"Calling `unsafe` function `{fn_name}` is unsafe outside an `unsafe` function",
                    node,
                    tip="Mark the containing function as `unsafe`: `unsafe fnc ...`. "
                         "Or wrap the call in an `unsafe` context."
                )
        if not self.in_nogc_func:
            if fn_name in self.nogc_func_names:
                self._error(
                    f"Calling `nogc` function `{fn_name}` from a GC-managed function may cause memory issues",
                    node,
                    tip="Mark the containing function as `nogc`: `nogc fnc ...`. "
                         "nogc functions use manual memory management and should only be called from nogc functions."
                )
        self.generic_visit(node)
