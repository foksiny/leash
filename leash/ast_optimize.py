"""AST-level semantic optimizations for the Leash compiler.

Runs on the parsed AST *before* LLVM IR generation to reduce output
binary size.  All passes are semantics-preserving:

  - Constant folding (evaluate known expressions at compile time)
  - Dead branch elimination (if/while with constant conditions)
  - Unreachable code removal (after return / break / continue)
  - Dead code elimination (remove unreferenced top-level definitions)
"""

from .ast_nodes import (
    Program, Function, Block, VariableDecl, Assignment, IfStatement,
    WhileStatement, ForStatement, LoopStatement, DoWhileStatement,
    ReturnStatement, MultiReturnStatement, StopStatement, ContinueStatement,
    SpawnStatement,
    ExpressionStatement, EmptyStatement, IgnoreStatement, ShowStatement,
    ThrowStatement, DeferStatement, WorksOtherwiseStatement, SwitchStatement,
    StructDef, UnionDef, EnumDef, ClassDef, ClassField, ClassMethod,
    TypeAlias, ErrorDef, GlobalVarDecl, ImportStmt, ConditionalDef,
    MacroDef, OpDef, TemplateDef, NativeImport,
    BinaryOp, UnaryOp, Call, MethodCall, GenericCall, Identifier,
    MemberAccess, PointerMemberAccess, EnumMemberAccess, IndexAccess,
    StructInit, ArrayInit, HashInit,
    NumberLiteral, FloatLiteral, StringLiteral, CharLiteral, BoolLiteral,
    NullLiteral, FilePathLiteral, BuiltinVarLiteral, CastExpr, AsExpr,
    TypeConvExpr, ByteConvExpr, SizeofExpr, TernaryOp, IsExpr,
    ThisExpr, ThisWorkerExpr, SelfExpr, Lambda, CreateExpr, ThisOpTypeExpr,
    GenericTypeExpr, MultiVariableDecl, MultiAssign,
    ForeachArrayStatement, ForeachVectorStatement, ForeachStringStatement,
    ForeachStructStatement, DelStatement, ASTNode,
)

__all__ = ["optimize_ast"]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def optimize_ast(program):
    """Run all semantics-preserving AST-level optimizations on *program*.

    Always safe — folds constants, eliminates dead branches, removes
    unreachable code, and drops unused top-level definitions.
    """
    program = _deep_fold_program(program)
    program = _dead_branch_elim_program(program)
    program = _unreachable_code_elim_program(program)
    program = _dead_code_elimination(program)
    return program


# ---------------------------------------------------------------------------
# Phase 1 – Constant folding
# ---------------------------------------------------------------------------

def _deep_fold(node):
    if node is None or isinstance(node, (str, int, float, bool)):
        return node
    if isinstance(node, list):
        return [_deep_fold(i) for i in node]
    if not hasattr(node, "__dict__"):
        return node

    for a in vars(node):
        attr = getattr(node, a)
        if isinstance(attr, list):
            setattr(node, a, [_deep_fold(i) for i in attr])
        elif hasattr(attr, "__dict__") and not isinstance(attr, (str, int, float, bool)):
            setattr(node, a, _deep_fold(attr))

    if isinstance(node, BinaryOp):
        l = _deep_fold(node.left)
        r = _deep_fold(node.right)
        node.left, node.right = l, r

        if isinstance(l, NumberLiteral) and isinstance(r, NumberLiteral):
            lv, rv = l.value, r.value
            if node.op == "+":  return NumberLiteral(lv + rv)
            if node.op == "-":  return NumberLiteral(lv - rv)
            if node.op == "*":  return NumberLiteral(lv * rv)
            if node.op == "/" and rv != 0: return NumberLiteral(lv // rv)
            if node.op == "%":  return NumberLiteral(lv % rv)
            if node.op == "==": return BoolLiteral(lv == rv)
            if node.op == "!=": return BoolLiteral(lv != rv)
            if node.op == "<":  return BoolLiteral(lv < rv)
            if node.op == ">":  return BoolLiteral(lv > rv)
            if node.op == "<=": return BoolLiteral(lv <= rv)
            if node.op == ">=": return BoolLiteral(lv >= rv)

        if isinstance(l, FloatLiteral) and isinstance(r, FloatLiteral):
            lv, rv = l.value, r.value
            if node.op == "+":  return FloatLiteral(lv + rv)
            if node.op == "-":  return FloatLiteral(lv - rv)
            if node.op == "*":  return FloatLiteral(lv * rv)
            if node.op == "/" and rv != 0: return FloatLiteral(lv / rv)
            if node.op == "==": return BoolLiteral(lv == rv)
            if node.op == "!=": return BoolLiteral(lv != rv)
            if node.op == "<":  return BoolLiteral(lv < rv)
            if node.op == ">":  return BoolLiteral(lv > rv)
            if node.op == "<=": return BoolLiteral(lv <= rv)
            if node.op == ">=": return BoolLiteral(lv >= rv)

        if isinstance(l, StringLiteral) and isinstance(r, StringLiteral):
            if node.op == "+":  return StringLiteral(l.value + r.value)
            if node.op == "==": return BoolLiteral(l.value == r.value)
            if node.op == "!=": return BoolLiteral(l.value != r.value)

        if isinstance(l, BoolLiteral) and isinstance(r, BoolLiteral):
            lv, rv = l.value, r.value
            if node.op == "&&": return BoolLiteral(lv and rv)
            if node.op == "||": return BoolLiteral(lv or rv)
            if node.op == "==": return BoolLiteral(lv == rv)
            if node.op == "!=": return BoolLiteral(lv != rv)

        return node

    if isinstance(node, UnaryOp):
        e = _deep_fold(node.expr)
        node.expr = e
        if node.op == "!" and isinstance(e, BoolLiteral):
            return BoolLiteral(not e.value)
        if node.op == "-" and isinstance(e, NumberLiteral):
            return NumberLiteral(-e.value)
        if node.op == "-" and isinstance(e, FloatLiteral):
            return FloatLiteral(-e.value)
        return node

    return node


def _fold_body(block):
    if block is None:
        return block
    if isinstance(block, Block):
        block.statements = [_deep_fold(s) for s in block.statements]
    return block


def _deep_fold_program(program):
    new = []
    for item in program.items:
        if isinstance(item, Function):
            item.body = _fold_body(item.body)
            item.args = [(n, t, _deep_fold(d)) for n, t, d in item.args]
        elif isinstance(item, ClassDef):
            for m in item.methods:
                m.fnc.body = _fold_body(m.fnc.body)
            for f in item.fields:
                f.value = _deep_fold(f.value)
        elif isinstance(item, OpDef):
            item.body = _fold_body(item.body)
        elif isinstance(item, GlobalVarDecl):
            item.value = _deep_fold(item.value)
        new.append(item)
    return Program(new)


# ---------------------------------------------------------------------------
# Phase 2 – Dead branch elimination
# ---------------------------------------------------------------------------

def _walk_stmts_dead_branch(stmts):
    out = []
    for s in stmts:
        r = _walk_stmt_dead_branch(s)
        if r is None:
            continue
        if isinstance(r, list):
            out.extend(r)
        else:
            out.append(r)
    return out


def _walk_block_dead_branch(block):
    if block is None:
        return None
    if isinstance(block, Block):
        block.statements = _walk_stmts_dead_branch(block.statements)
    return block


def _walk_stmt_dead_branch(stmt):
    if stmt is None:
        return None

    if isinstance(stmt, IfStatement):
        cond = stmt.condition
        if isinstance(cond, BoolLiteral):
            if (cond.value and not stmt.invert) or (not cond.value and stmt.invert):
                return _walk_stmts_dead_branch(stmt.then_block.statements)
            for ac, ab, inv in stmt.also_blocks:
                if isinstance(ac, BoolLiteral) and ((ac.value and not inv) or (not ac.value and inv)):
                    return _walk_stmts_dead_branch(ab.statements)
            if stmt.else_block:
                return _walk_stmts_dead_branch(stmt.else_block.statements)
            return None
        stmt.then_block = _walk_block_dead_branch(stmt.then_block)
        stmt.also_blocks = [(c, _walk_block_dead_branch(b), inv) for c, b, inv in stmt.also_blocks]
        if stmt.else_block:
            stmt.else_block = _walk_block_dead_branch(stmt.else_block)
        return stmt

    if isinstance(stmt, WhileStatement):
        if isinstance(stmt.condition, BoolLiteral) and not stmt.condition.value:
            return None
        stmt.body = _walk_block_dead_branch(stmt.body)
        return stmt

    for a in vars(stmt):
        attr = getattr(stmt, a)
        if isinstance(attr, Block):
            setattr(stmt, a, _walk_block_dead_branch(attr))
        elif isinstance(attr, list):
            for i, v in enumerate(attr):
                if isinstance(v, Block):
                    attr[i] = _walk_block_dead_branch(v)

    return stmt


def _dead_branch_elim_program(program):
    new = []
    for item in program.items:
        if isinstance(item, Function):
            item.body = _walk_block_dead_branch(item.body)
        elif isinstance(item, ClassDef):
            for m in item.methods:
                m.fnc.body = _walk_block_dead_branch(m.fnc.body)
        elif isinstance(item, OpDef):
            item.body = _walk_block_dead_branch(item.body)
        new.append(item)
    return Program(new)


# ---------------------------------------------------------------------------
# Phase 3 – Unreachable code elimination
# ---------------------------------------------------------------------------

_TERMINAL_STMTS = (ReturnStatement, MultiReturnStatement, StopStatement,
                   ContinueStatement, IgnoreStatement, ThrowStatement)


def _walk_block_unreachable(block):
    if block is None:
        return None
    if isinstance(block, Block):
        out = []
        for s in block.statements:
            if isinstance(s, _TERMINAL_STMTS):
                out.append(s)
                break
            out.append(_walk_stmt_unreachable(s))
        block.statements = out
    return block


def _walk_stmt_unreachable(stmt):
    if stmt is None:
        return None

    for a in vars(stmt):
        attr = getattr(stmt, a)
        if isinstance(attr, Block):
            setattr(stmt, a, _walk_block_unreachable(attr))
        elif isinstance(attr, list):
            for i, v in enumerate(attr):
                if isinstance(v, Block):
                    attr[i] = _walk_block_unreachable(v)

    return stmt


def _unreachable_code_elim_program(program):
    new = []
    for item in program.items:
        if isinstance(item, Function):
            item.body = _walk_block_unreachable(item.body)
        elif isinstance(item, ClassDef):
            for m in item.methods:
                m.fnc.body = _walk_block_unreachable(m.fnc.body)
        elif isinstance(item, OpDef):
            item.body = _walk_block_unreachable(item.body)
        new.append(item)
    return Program(new)


# ---------------------------------------------------------------------------
# Phase 4 – Dead code elimination (top-level definitions)
# ---------------------------------------------------------------------------

def _collect_ref_names(node):
    """Return a set of all names used in *reference* positions in *node*.

    References include: called function names, identifiers, struct/class
    instantiation names, type annotations, cast targets, enum member
    accesses, thrown error names, parent class names, etc.
    """
    refs = set()

    def walk(n):
        if n is None or isinstance(n, (str, int, float, bool)):
            return
        if isinstance(n, (list, tuple)):
            for item in n:
                walk(item)
            return
        if not hasattr(n, "__dict__"):
            return

        if isinstance(n, Identifier):
            refs.add(n.name)
        elif isinstance(n, Call):
            refs.add(n.name)
        elif isinstance(n, GenericCall):
            refs.add(n.name)
        elif isinstance(n, StructInit):
            refs.add(n.name)
        elif isinstance(n, CreateExpr):
            refs.add(n.class_name)
        elif isinstance(n, EnumMemberAccess):
            refs.add(n.enum_name)
        elif isinstance(n, ThrowStatement):
            refs.add(n.error_name)

        str_attrs = set()

        if isinstance(n, (VariableDecl, ClassField)):
            if isinstance(n.var_type, str):
                str_attrs.add(n.var_type)
        if isinstance(n, MultiVariableDecl):
            for vt in n.var_types:
                if isinstance(vt, str):
                    str_attrs.add(vt)
        if isinstance(n, (CastExpr, AsExpr, TypeConvExpr)):
            if isinstance(n.target_type, str):
                str_attrs.add(n.target_type)
        if isinstance(n, SizeofExpr) and isinstance(n.target, str):
            str_attrs.add(n.target)
        if isinstance(n, IsExpr) and n.is_type_check and isinstance(n.right, str):
            str_attrs.add(n.right)
        if isinstance(n, TypeAlias) and isinstance(n.target_type, str):
            str_attrs.add(n.target_type)
        if isinstance(n, GenericTypeExpr):
            str_attrs.add(n.name)
            for ta in n.type_args:
                if isinstance(ta, str):
                    str_attrs.add(ta)
        if isinstance(n, ClassDef) and n.parent:
            str_attrs.add(n.parent)
        if isinstance(n, MemberAccess):
            str_attrs.add(n.member)

        if isinstance(n, (Function, Lambda)):
            if isinstance(n.return_type, str):
                str_attrs.add(n.return_type)
            for aname, atype, adefault in n.args:
                if isinstance(atype, str):
                    str_attrs.add(atype)
        if isinstance(n, OpDef):
            str_attrs.add(n.type_name)
            if isinstance(n.return_type, str):
                str_attrs.add(n.return_type)
            for aname, atype, adefault in n.args:
                if isinstance(atype, str):
                    str_attrs.add(atype)

        if isinstance(n, StructDef):
            for fn, ft, fd in n.fields:
                if isinstance(ft, str):
                    str_attrs.add(ft)
        if isinstance(n, UnionDef):
            for un, ut in n.variants:
                if isinstance(ut, str):
                    str_attrs.add(ut)
        if isinstance(n, EnumDef):
            for en, et, ev in n.members:
                if et is not None and isinstance(et, str):
                    str_attrs.add(et)
        if isinstance(n, ErrorDef):
            for en, et in n.args:
                if isinstance(et, str):
                    str_attrs.add(et)

        refs.update(str_attrs)

        if isinstance(n, ClassMethod):
            walk(n.fnc)
            return

        for attr_name in vars(n):
            if attr_name in ("name",):
                continue
            attr = getattr(n, attr_name)
            if isinstance(attr, (list, tuple)):
                for item in attr:
                    walk(item)
            elif hasattr(attr, "__dict__") and not isinstance(attr, (str, int, float, bool)):
                walk(attr)

    walk(node)
    return refs


def _dead_code_elimination(program):
    """Remove top-level definitions whose names never appear as references."""

    refs = _collect_ref_names(program)

    always_keep = {"main"}

    def keep(item):
        name = getattr(item, "name", None)
        if not name:
            return True
        if name in always_keep:
            return True
        if isinstance(item, (NativeImport, TemplateDef, ImportStmt, ConditionalDef)):
            return True
        if isinstance(item, MacroDef):
            return True
        if isinstance(item, OpDef):
            return True
        if isinstance(item, Function) and item.struct_type:
            return True
        return name in refs

    new_items = [item for item in program.items if keep(item)]
    return Program(new_items)
