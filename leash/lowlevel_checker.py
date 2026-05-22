from .errors import LeashError

class LowLevelChecker:
    def __init__(self):
        self.in_unsafe_func = False
        self.errors = []

    def check(self, ast):
        self.visit(ast)
        return self.errors

    def _error(self, msg, node=None, tip=None):
        line = getattr(node, "line", None)
        col = getattr(node, "col", None)
        self.errors.append(LeashError(msg, line=line, col=col, tip=tip, code="E_LOWLEVEL"))

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
        self.in_unsafe_func = getattr(node, "is_unsafe", False)
        self.generic_visit(node)
        self.in_unsafe_func = old_unsafe

    def visit_ClassMethod(self, node):
        old_unsafe = self.in_unsafe_func
        self.in_unsafe_func = getattr(node, "is_unsafe", False) or getattr(getattr(node, "fnc", None), "is_unsafe", False)
        self.generic_visit(node)
        self.in_unsafe_func = old_unsafe

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
