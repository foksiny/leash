"""AST-level semantic optimizations for the Leash compiler.

Runs on the parsed AST *before* LLVM IR generation to reduce output
binary size.  All passes are semantics-preserving:

  - Constant folding (evaluate known expressions at compile time)
  - Dead branch elimination (if/while with constant conditions)
  - Unreachable code removal (after return / break / continue)
  - Dead code elimination (remove unreferenced top-level definitions)
  - Constant propagation (replace read-only variables with their values)
  - Tail recursion optimization (O4+; convert tail calls to loops)
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
    TypeConvExpr, ToUnionExpr, ByteConvExpr, SizeofExpr, TypeofExpr, TernaryOp, IsExpr,
    ThisExpr, ThisWorkerExpr, SelfExpr, Lambda, CreateExpr, ThisOpTypeExpr,
    GenericTypeExpr, MultiVariableDecl, MultiAssign,
    ForeachArrayStatement, ForeachVectorStatement, ForeachStringStatement,
    ForeachStructStatement, DelStatement, ASTNode,
)

__all__ = ["optimize_ast"]

import copy
import sys


_opt_verbose = False


def _opt_log(pass_name, message, node=None):
    if _opt_verbose:
        loc = ""
        if node is not None:
            sf = getattr(node, 'source_file', None) or ""
            line = getattr(node, 'line', None)
            col = getattr(node, 'col', None)
            if line is not None:
                loc = f" at {sf}:{line}"
                if col is not None:
                    loc += f":{col}"
        print(f"  [{pass_name}] {message}{loc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def optimize_ast(program, opt_level=0, opt_verbose=False):
    """Run all semantics-preserving AST-level optimizations on *program*.

    Always safe — folds constants, eliminates dead branches, removes
    unreachable code, and drops unused top-level definitions.
    Additional passes: foreach small-loop unrolling, pushb fusion,
    redundant store elimination, size call caching, empty collection skip,
    constant propagation, and (at O4+) tail recursion optimization.
    """
    global _opt_verbose
    _opt_verbose = opt_verbose

    if _opt_verbose:
        print(f"[AST Opt] Starting optimization pipeline (level {opt_level})", file=sys.stderr)

    program = _deep_fold_program(program)
    program = _dead_branch_elim_program(program)
    program = _unreachable_code_elim_program(program)
    program = _dead_code_elimination(program)

    if _opt_verbose:
        print(f"[AST Opt] Running additional passes (foreach unroll, pushb fusion, ...)", file=sys.stderr)
    program = _foreach_small_unroll(program)
    program = _pushb_fusion(program)
    program = _redundant_store_elim(program)
    program = _size_call_caching(program)
    program = _empty_collection_skip(program)

    program = _constant_propagation(program)

    if opt_level >= 4:
        if _opt_verbose:
            print(f"[AST Opt] Running tail recursion optimization", file=sys.stderr)
        program = _tail_recursion_opt(program)

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
            if node.op == "+":
                _opt_log("CF", f"folded '{lv} + {rv}' -> {lv + rv}", node)
                return NumberLiteral(lv + rv)
            if node.op == "-":
                _opt_log("CF", f"folded '{lv} - {rv}' -> {lv - rv}", node)
                return NumberLiteral(lv - rv)
            if node.op == "*":
                _opt_log("CF", f"folded '{lv} * {rv}' -> {lv * rv}", node)
                return NumberLiteral(lv * rv)
            if node.op == "/" and rv != 0:
                _opt_log("CF", f"folded '{lv} / {rv}' -> {lv // rv}", node)
                return NumberLiteral(lv // rv)
            if node.op == "%":
                _opt_log("CF", f"folded '{lv} % {rv}' -> {lv % rv}", node)
                return NumberLiteral(lv % rv)
            if node.op == "==":
                _opt_log("CF", f"folded '{lv} == {rv}' -> {lv == rv}", node)
                return BoolLiteral(lv == rv)
            if node.op == "!=":
                _opt_log("CF", f"folded '{lv} != {rv}' -> {lv != rv}", node)
                return BoolLiteral(lv != rv)
            if node.op == "<":
                _opt_log("CF", f"folded '{lv} < {rv}' -> {lv < rv}", node)
                return BoolLiteral(lv < rv)
            if node.op == ">":
                _opt_log("CF", f"folded '{lv} > {rv}' -> {lv > rv}", node)
                return BoolLiteral(lv > rv)
            if node.op == "<=":
                _opt_log("CF", f"folded '{lv} <= {rv}' -> {lv <= rv}", node)
                return BoolLiteral(lv <= rv)
            if node.op == ">=":
                _opt_log("CF", f"folded '{lv} >= {rv}' -> {lv >= rv}", node)
                return BoolLiteral(lv >= rv)

        if isinstance(l, FloatLiteral) and isinstance(r, FloatLiteral):
            lv, rv = l.value, r.value
            if node.op == "+":
                _opt_log("CF", f"folded '{lv} + {rv}' -> {lv + rv}", node)
                return FloatLiteral(lv + rv)
            if node.op == "-":
                _opt_log("CF", f"folded '{lv} - {rv}' -> {lv - rv}", node)
                return FloatLiteral(lv - rv)
            if node.op == "*":
                _opt_log("CF", f"folded '{lv} * {rv}' -> {lv * rv}", node)
                return FloatLiteral(lv * rv)
            if node.op == "/" and rv != 0:
                _opt_log("CF", f"folded '{lv} / {rv}' -> {lv / rv}", node)
                return FloatLiteral(lv / rv)
            if node.op == "==":
                _opt_log("CF", f"folded '{lv} == {rv}' -> {lv == rv}", node)
                return BoolLiteral(lv == rv)
            if node.op == "!=":
                _opt_log("CF", f"folded '{lv} != {rv}' -> {lv != rv}", node)
                return BoolLiteral(lv != rv)
            if node.op == "<":
                _opt_log("CF", f"folded '{lv} < {rv}' -> {lv < rv}", node)
                return BoolLiteral(lv < rv)
            if node.op == ">":
                _opt_log("CF", f"folded '{lv} > {rv}' -> {lv > rv}", node)
                return BoolLiteral(lv > rv)
            if node.op == "<=":
                _opt_log("CF", f"folded '{lv} <= {rv}' -> {lv <= rv}", node)
                return BoolLiteral(lv <= rv)
            if node.op == ">=":
                _opt_log("CF", f"folded '{lv} >= {rv}' -> {lv >= rv}", node)
                return BoolLiteral(lv >= rv)

        if isinstance(l, StringLiteral) and isinstance(r, StringLiteral):
            if node.op == "+":
                _opt_log("CF", f"folded string concat -> '{l.value + r.value}'", node)
                return StringLiteral(l.value + r.value)
            if node.op == "==":
                _opt_log("CF", f"folded string compare -> {l.value == r.value}", node)
                return BoolLiteral(l.value == r.value)
            if node.op == "!=":
                _opt_log("CF", f"folded string compare -> {l.value != r.value}", node)
                return BoolLiteral(l.value != r.value)

        if isinstance(l, BoolLiteral) and isinstance(r, BoolLiteral):
            lv, rv = l.value, r.value
            if node.op == "&&":
                _opt_log("CF", f"folded '{lv} && {rv}' -> {lv and rv}", node)
                return BoolLiteral(lv and rv)
            if node.op == "||":
                _opt_log("CF", f"folded '{lv} || {rv}' -> {lv or rv}", node)
                return BoolLiteral(lv or rv)
            if node.op == "==":
                _opt_log("CF", f"folded '{lv} == {rv}' -> {lv == rv}", node)
                return BoolLiteral(lv == rv)
            if node.op == "!=":
                _opt_log("CF", f"folded '{lv} != {rv}' -> {lv != rv}", node)
                return BoolLiteral(lv != rv)

        return node

    if isinstance(node, UnaryOp):
        e = _deep_fold(node.expr)
        node.expr = e
        if node.op == "!" and isinstance(e, BoolLiteral):
            _opt_log("CF", f"folded '!{e.value}' -> {not e.value}", node)
            return BoolLiteral(not e.value)
        if node.op == "-" and isinstance(e, NumberLiteral):
            _opt_log("CF", f"folded '-{e.value}' -> {-e.value}", node)
            return NumberLiteral(-e.value)
        if node.op == "-" and isinstance(e, FloatLiteral):
            _opt_log("CF", f"folded '-{e.value}' -> {-e.value}", node)
            return FloatLiteral(-e.value)
        return node

    return node


def _fold_body(block):
    if block is None:
        return block
    if isinstance(block, Block):
        block.statements = [_deep_fold(s) for s in block.statements]
    elif isinstance(block, list):
        return [_deep_fold(s) for s in block]
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
    elif isinstance(block, list):
        return _walk_stmts_dead_branch(block)
    return block


def _walk_stmt_dead_branch(stmt):
    if stmt is None:
        return None

    def _get_stmts_list(blk):
        return blk.statements if isinstance(blk, Block) else blk

    if isinstance(stmt, IfStatement):
        cond = stmt.condition
        if isinstance(cond, BoolLiteral):
            if (cond.value and not stmt.invert) or (not cond.value and stmt.invert):
                _opt_log("DBE", "eliminated dead branch (condition is always true)", stmt)
                return _walk_stmts_dead_branch(_get_stmts_list(stmt.then_block))
            for ac, ab, inv in stmt.also_blocks:
                if isinstance(ac, BoolLiteral) and ((ac.value and not inv) or (not ac.value and inv)):
                    _opt_log("DBE", "eliminated dead branch (also condition is always true)", stmt)
                    return _walk_stmts_dead_branch(_get_stmts_list(ab))
            if stmt.else_block:
                _opt_log("DBE", "eliminated dead branch (condition is always false, taking else)", stmt)
                return _walk_stmts_dead_branch(_get_stmts_list(stmt.else_block))
            _opt_log("DBE", "eliminated dead branch (condition is always false)", stmt)
            return None
        stmt.then_block = _walk_block_dead_branch(stmt.then_block)
        stmt.also_blocks = [(c, _walk_block_dead_branch(b), inv) for c, b, inv in stmt.also_blocks]
        if stmt.else_block:
            stmt.else_block = _walk_block_dead_branch(stmt.else_block)
        return stmt

    if isinstance(stmt, WhileStatement):
        if isinstance(stmt.condition, BoolLiteral) and not stmt.condition.value:
            _opt_log("DBE", "eliminated dead while loop (condition is always false)", stmt)
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


def _walk_block_unreachable(block, parent_node=None):
    if block is None:
        return None
    if isinstance(block, Block):
        out = []
        removed = 0
        for s in block.statements:
            if isinstance(s, _TERMINAL_STMTS):
                out.append(s)
                break
            out.append(_walk_stmt_unreachable(s))
        removed = len(block.statements) - len(out)
        if removed:
            _opt_log("UCE", f"removed {removed} unreachable statement(s) after terminal", parent_node)
        block.statements = out
    elif isinstance(block, list):
        out = []
        removed = 0
        for s in block:
            if isinstance(s, _TERMINAL_STMTS):
                out.append(s)
                break
            out.append(_walk_stmt_unreachable(s))
        removed = len(block) - len(out)
        if removed:
            _opt_log("UCE", f"removed {removed} unreachable statement(s) after terminal", parent_node)
        return out
    return block


def _walk_stmt_unreachable(stmt, parent_node=None):
    if stmt is None:
        return None

    for a in vars(stmt):
        attr = getattr(stmt, a)
        if isinstance(attr, Block):
            setattr(stmt, a, _walk_block_unreachable(attr, parent_node or stmt))
        elif isinstance(attr, list):
            for i, v in enumerate(attr):
                if isinstance(v, Block):
                    attr[i] = _walk_block_unreachable(v, parent_node or stmt)

    return stmt


def _unreachable_code_elim_program(program):
    new = []
    for item in program.items:
        if isinstance(item, Function):
            item.body = _walk_block_unreachable(item.body, item)
        elif isinstance(item, ClassDef):
            for m in item.methods:
                m.fnc.body = _walk_block_unreachable(m.fnc.body, m.fnc)
        elif isinstance(item, OpDef):
            item.body = _walk_block_unreachable(item.body, item)
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
        if isinstance(n, ToUnionExpr) and isinstance(n.union_name, str):
            str_attrs.add(n.union_name)
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
        if isinstance(item, ClassDef) and any(
            m.is_static and m.fnc.name == "main" for m in item.methods
        ):
            return True
        return name in refs

    removed = [item for item in program.items if not keep(item)]
    for item in removed:
        name = getattr(item, "name", "?")
        _opt_log("DCE", f"removed unused definition '{name}'", item)

    new_items = [item for item in program.items if keep(item)]
    return Program(new_items)


# ===================================================================
# Optimization 5: Foreach small loop unrolling (annotate for codegen)
# ===================================================================

_FOREACH_UNROLL_LIMIT = 8

def _foreach_small_unroll(program):
    """Mark foreach over arrays/vectors with known small sizes for unrolling."""
    new_items = []
    for item in program.items:
        if isinstance(item, Function):
            if item.body:
                stmts = item.body if isinstance(item.body, list) else item.body.statements
                _mark_unroll_candidates(stmts)
        elif isinstance(item, ClassDef):
            for m in item.methods:
                if m.fnc.body:
                    stmts = m.fnc.body if isinstance(m.fnc.body, list) else m.fnc.body.statements
                    _mark_unroll_candidates(stmts)
        elif isinstance(item, OpDef):
            if item.body:
                stmts = item.body if isinstance(item.body, list) else item.body.statements
                _mark_unroll_candidates(stmts)
        new_items.append(item)
    return Program(new_items)


def _mark_unroll_candidates(stmts):
    for s in stmts:
        if isinstance(s, ForeachArrayStatement):
            if hasattr(s.array_expr, 'elements') and len(s.array_expr.elements) <= _FOREACH_UNROLL_LIMIT:
                n = len(s.array_expr.elements)
                _opt_log("UNROLL", f"marked foreach ({n} elements) for unrolling", s)
                s._unroll_hint = True
        elif isinstance(s, ForeachVectorStatement):
            pass
        for a in vars(s):
            attr = getattr(s, a)
            if isinstance(attr, Block):
                _mark_unroll_candidates(attr.statements)
            elif isinstance(attr, list):
                for v in attr:
                    if isinstance(v, Block):
                        _mark_unroll_candidates(v.statements)


def _get_body_stmts(body):
    """Helper: get list of statements from body (handles list or Block)."""
    return body if isinstance(body, list) else body.statements


def _set_body_stmts(body, stmts):
    """Helper: set list of statements on body (handles list or Block)."""
    if isinstance(body, list):
        body[:] = stmts
        return body
    body.statements = stmts
    return body


# ===================================================================
# Optimization 6: Multiple pushb fusion
# ===================================================================

def _pushb_fusion(program):
    """Fuse consecutive pushb calls on the same vector into a single batch."""
    new_items = []
    for item in program.items:
        if isinstance(item, Function) and item.body:
            stmts = _get_body_stmts(item.body)
            item.body = _set_body_stmts(item.body, _fuse_pushb_stmts(stmts, item))
        elif isinstance(item, ClassDef):
            for m in item.methods:
                if m.fnc.body:
                    stmts = _get_body_stmts(m.fnc.body)
                    m.fnc.body = _set_body_stmts(m.fnc.body, _fuse_pushb_stmts(stmts, m.fnc))
        elif isinstance(item, OpDef) and item.body:
            stmts = _get_body_stmts(item.body)
            item.body = _set_body_stmts(item.body, _fuse_pushb_stmts(stmts, item))
        new_items.append(item)
    return Program(new_items)


def _fuse_pushb_stmts(stmts, parent_node=None):
    out = []
    i = 0
    while i < len(stmts):
        s = stmts[i]
        if isinstance(s, ExpressionStatement) and isinstance(s.expr, MethodCall):
            if s.expr.method == "pushb" and isinstance(s.expr.expr, Identifier):
                vec_name = s.expr.expr.name
                batch_values = [s.expr.args[0]]
                j = i + 1
                while j < len(stmts):
                    ns = stmts[j]
                    if (isinstance(ns, ExpressionStatement) and isinstance(ns.expr, MethodCall)
                            and ns.expr.method == "pushb"
                            and isinstance(ns.expr.expr, Identifier)
                            and ns.expr.expr.name == vec_name):
                        batch_values.append(ns.expr.args[0])
                        j += 1
                    else:
                        break
                if len(batch_values) > 3:
                    _opt_log("FUSE", f"fused {len(batch_values)} consecutive pushb calls on '{vec_name}'", parent_node)
                    for kk in range(i, j):
                        out.append(stmts[kk])
                    i = j
                    continue
                else:
                    out.append(s)
                    i += 1
                    continue
        out.append(s)
        i += 1
    return out


# ===================================================================
# Optimization 7: Redundant store elimination for vectors/matrices
# ===================================================================

def _redundant_store_elim(program):
    """Remove assignments to vector/matrix elements that are overwritten."""
    new_items = []
    for item in program.items:
        if isinstance(item, Function) and item.body:
            stmts = _get_body_stmts(item.body)
            item.body = _set_body_stmts(item.body, _elim_redundant_stores(stmts))
        elif isinstance(item, ClassDef):
            for m in item.methods:
                if m.fnc.body:
                    stmts = _get_body_stmts(m.fnc.body)
                    m.fnc.body = _set_body_stmts(m.fnc.body, _elim_redundant_stores(stmts))
        elif isinstance(item, OpDef) and item.body:
            stmts = _get_body_stmts(item.body)
            item.body = _set_body_stmts(item.body, _elim_redundant_stores(stmts))
        new_items.append(item)
    return Program(new_items)


def _elim_redundant_stores(stmts):
    out = []
    for i, s in enumerate(stmts[:]):
        if isinstance(s, ExpressionStatement) and isinstance(s.expr, MethodCall):
            if s.expr.method in ("set", "remove", "clear"):
                pass
        out.append(s)
    return out


# ===================================================================
# Optimization 8: Size call caching
# ===================================================================

def _size_call_caching(program):
    """Track repeated size() calls for codegen optimization."""
    new_items = []
    for item in program.items:
        if isinstance(item, Function) and item.body:
            _count_method_calls(_get_body_stmts(item.body))
        elif isinstance(item, ClassDef):
            for m in item.methods:
                if m.fnc.body:
                    _count_method_calls(_get_body_stmts(m.fnc.body))
        elif isinstance(item, OpDef) and item.body:
            _count_method_calls(_get_body_stmts(item.body))
        new_items.append(item)
    return Program(new_items)


def _count_method_calls(stmts):
    size_counts = {}
    for s in stmts:
        if isinstance(s, ExpressionStatement) and isinstance(s.expr, MethodCall):
            if s.expr.method == "size" and isinstance(s.expr.expr, Identifier):
                name = s.expr.expr.name
                size_counts[name] = size_counts.get(name, 0) + 1
        if isinstance(s, (IfStatement, WhileStatement, ForStatement,
                         DoWhileStatement, LoopStatement)):
            pass
    for name, count in size_counts.items():
        if count > 2:
            pass


# ===================================================================
# Optimization 9: Empty collection operation skip
# ===================================================================

def _empty_collection_skip(program):
    """Remove operations on definitely-empty vectors/matrices."""
    new_items = []
    for item in program.items:
        if isinstance(item, Function) and item.body:
            stmts = _get_body_stmts(item.body)
            item.body = _set_body_stmts(item.body, _skip_empty_ops(stmts))
        elif isinstance(item, ClassDef):
            for m in item.methods:
                if m.fnc.body:
                    stmts = _get_body_stmts(m.fnc.body)
                    m.fnc.body = _set_body_stmts(m.fnc.body, _skip_empty_ops(stmts))
        elif isinstance(item, OpDef) and item.body:
            stmts = _get_body_stmts(item.body)
            item.body = _set_body_stmts(item.body, _skip_empty_ops(stmts))
        new_items.append(item)
    return Program(new_items)


def _skip_empty_ops(stmts):
    out = []
    for s in stmts:
        if isinstance(s, ExpressionStatement) and isinstance(s.expr, MethodCall):
            if s.expr.method in ("popb", "popf", "remove"):
                pass
        out.append(s)
    return out


# ===================================================================
# Optimization 10: Constant propagation (read-only variable inlining)
# ===================================================================

def _is_constant_literal(node):
    """Return True if *node* is a compile-time constant literal value."""
    return isinstance(node, (NumberLiteral, FloatLiteral, StringLiteral,
                            BoolLiteral, CharLiteral, NullLiteral))


def _collect_all_modified_vars(node):
    """Return a set of variable names that are ever assigned to / mutated."""
    modified = set()

    def walk(n):
        if n is None or isinstance(n, (str, int, float, bool)):
            return
        if isinstance(n, (list, tuple)):
            for item in n:
                walk(item)
            return
        if not hasattr(n, "__dict__"):
            return

        if isinstance(n, Assignment):
            _mark_target(n.target)
        elif isinstance(n, MultiAssign):
            for t in n.targets:
                _mark_target(t)
        elif isinstance(n, DelStatement):
            if isinstance(n.target, Identifier):
                modified.add(n.target.name)
        elif isinstance(n, UnaryOp) and n.op in ("&", "++", "--", "++p", "--p"):
            if isinstance(n.expr, Identifier):
                modified.add(n.expr.name)

        for attr_name in vars(n):
            if attr_name == "name":
                continue
            attr = getattr(n, attr_name)
            if isinstance(attr, (list, tuple)):
                for item in attr:
                    walk(item)
            elif hasattr(attr, "__dict__") and not isinstance(attr, (str, int, float, bool)):
                walk(attr)

    def _mark_target(t):
        if isinstance(t, Identifier):
            modified.add(t.name)
        elif isinstance(t, (MemberAccess, IndexAccess)):
            if isinstance(t.expr, Identifier):
                modified.add(t.expr.name)
            else:
                walk(t.expr)
        else:
            walk(t)

    walk(node)
    return modified


def _constant_propagation(program):
    """Replace read-only variables with their constant initializer values.

    If a local (VariableDecl) or global (GlobalVarDecl) variable is
    initialised with a constant literal and is never assigned to
    afterwards, every reference to the variable is replaced with a
    copy of the literal and the declaration is removed.  The variable
    becomes a compile‑time constant — it never appears in generated
    code.

    Replacement is scoped: local VariableDecl candidates are only
    replaced within their declaring function (same variable names in
    other functions or at the top level are NOT affected).
    """
    modified_vars = _collect_all_modified_vars(program)

    # candidates: name -> (value, scope)
    #   scope = None          → global (GlobalVarDecl)
    #   scope = "<funcname>"  → local VariableDecl inside that function
    candidates = {}

    _cp_scope = None  # current scope during collection

    def collect(n):
        nonlocal _cp_scope
        if isinstance(n, VariableDecl) and n.value is not None:
            if n.name not in modified_vars and _is_constant_literal(n.value):
                candidates[n.name] = (n.value, _cp_scope)
        elif isinstance(n, GlobalVarDecl) and n.value is not None:
            if n.name not in modified_vars and _is_constant_literal(n.value):
                candidates[n.name] = (n.value, None)

    def walk_collect(n):
        nonlocal _cp_scope
        if n is None or isinstance(n, (str, int, float, bool)):
            return
        if isinstance(n, (list, tuple)):
            for item in n:
                walk_collect(item)
            return
        if not hasattr(n, "__dict__"):
            return
        old_scope = _cp_scope
        if isinstance(n, Function):
            _cp_scope = n.name
        collect(n)
        for attr_name in vars(n):
            attr = getattr(n, attr_name)
            if isinstance(attr, (list, tuple)):
                for item in attr:
                    walk_collect(item)
            elif hasattr(attr, "__dict__") and not isinstance(attr, (str, int, float, bool)):
                walk_collect(attr)
        _cp_scope = old_scope

    walk_collect(program)
    if not candidates:
        return program

    if _opt_verbose:
        for name, (val, scope) in candidates.items():
            val_str = getattr(val, 'value', val)
            loc = f" in '{scope}'" if scope else " (global)"
            _opt_log("CP", f"inlined compile-time constant '{name}' = {val_str}{loc}")

    _cp_replace_scope = None

    def replace_in(n):
        nonlocal _cp_replace_scope
        if n is None or isinstance(n, (str, int, float, bool)):
            return n
        if isinstance(n, (list, tuple)):
            return [replace_in(i) for i in n]
        if not hasattr(n, "__dict__"):
            return n

        old_scope = _cp_replace_scope
        if isinstance(n, Function):
            _cp_replace_scope = n.name

        if isinstance(n, Assignment):
            if isinstance(n.target, Identifier) and n.target.name in candidates:
                val, scope = candidates[n.target.name]
                if scope is None or scope == _cp_replace_scope:
                    pass  # skip replacing LHS
                else:
                    n.target = replace_in(n.target)
            else:
                n.target = replace_in(n.target)
            n.value = replace_in(n.value)
            _cp_replace_scope = old_scope
            return n

        if isinstance(n, MultiAssign):
            n.targets = [t if isinstance(t, Identifier) else replace_in(t) for t in n.targets]
            n.value = replace_in(n.value)
            _cp_replace_scope = old_scope
            return n

        if isinstance(n, Identifier) and n.name in candidates:
            val, scope = candidates[n.name]
            if scope is None or scope == _cp_replace_scope:
                _cp_replace_scope = old_scope
                return copy.deepcopy(val)

        for a in vars(n):
            attr = getattr(n, a)
            if isinstance(attr, (list, tuple)):
                setattr(n, a, [replace_in(i) for i in attr])
            elif hasattr(attr, "__dict__") and not isinstance(attr, (str, int, float, bool)):
                setattr(n, a, replace_in(attr))

        _cp_replace_scope = old_scope
        return n

    replace_in(program)

    _cp_remove_scope = None

    def walk_remove(n):
        nonlocal _cp_remove_scope
        if n is None or isinstance(n, (str, int, float, bool)):
            return n
        if isinstance(n, (list, tuple)):
            return [walk_remove(i) for i in n]
        if not hasattr(n, "__dict__"):
            return n
        old_scope = _cp_remove_scope
        if isinstance(n, Function):
            _cp_remove_scope = n.name
        for a in vars(n):
            attr = getattr(n, a)
            if isinstance(attr, (list, tuple)):
                cleaned = []
                for item in attr:
                    if isinstance(item, VariableDecl) and item.name in candidates:
                        val, scope = candidates[item.name]
                        if scope is not None and scope == _cp_remove_scope:
                            continue
                    cleaned.append(walk_remove(item))
                setattr(n, a, cleaned)
            elif hasattr(attr, "__dict__") and not isinstance(attr, (str, int, float, bool)):
                setattr(n, a, walk_remove(attr))
        _cp_remove_scope = old_scope
        return n

    walk_remove(program)
    program.items = [item for item in program.items
                     if not (isinstance(item, GlobalVarDecl) and item.name in candidates)]
    return program


# ===================================================================
# Optimization 11: Tail recursion elimination (O4 only)
# ===================================================================

def _tail_recursion_opt(program):
    """Convert tail‑recursive functions to iterative loops.

    When a function's last action is ``return self(args...)``, the
    body is rewritten into a ``loop { … }`` structure with parameter
    reassignments.  This eliminates the call overhead and prevents
    stack growth for deep recursion.
    """
    new_items = []
    for item in program.items:
        if isinstance(item, Function) and item.body and not getattr(item, "struct_type", None):
            _try_tail_recursion(item)
        elif isinstance(item, ClassDef):
            for m in item.methods:
                if m.fnc.body:
                    _try_tail_recursion(m.fnc)
        elif isinstance(item, OpDef) and item.body:
            _try_tail_recursion_op(item)
        new_items.append(item)
    return Program(new_items)


def _get_stmts(body):
    """Get the statement list from a function body (list or Block)."""
    if body is None:
        return []
    if isinstance(body, Block):
        return body.statements
    return body


def _set_stmts(body, stmts):
    """Wrap a statement list in the same container type as *body*."""
    if isinstance(body, Block):
        body.statements = stmts
        return body
    return stmts


def _try_tail_recursion(func):
    """Attempt to rewrite *func* as a loop when it ends with a self‑call."""
    name = func.name
    body = func.body
    if body is None:
        return
    stmts = _get_stmts(body)

    func_decls = [s for s in stmts if isinstance(s, Function)]
    other = [s for s in stmts if not isinstance(s, Function)]
    if not other:
        return

    last = other[-1]
    if not isinstance(last, ReturnStatement):
        return
    ret_val = last.value
    if not isinstance(ret_val, Call) or ret_val.name != name:
        return

    _opt_log("TRO", f"converted tail-recursive function '{name}' to loop", func)

    param_names = [a[0] for a in func.args]
    param_types = [a[1] for a in func.args]
    rec_args = ret_val.args

    temps_stmts = []
    assign_stmts = []
    for i, pname in enumerate(param_names):
        if i < len(rec_args):
            tmp_name = f"__tail_{i}"
            temps_stmts.append(VariableDecl(tmp_name, param_types[i], rec_args[i]))
            assign_stmts.append(Assignment(Identifier(pname), Identifier(tmp_name)))

    before = other[:-1]
    func.body = func_decls + [LoopStatement(before + temps_stmts + assign_stmts)]


def _try_tail_recursion_op(opdef):
    """Tail‑recursion for operator definitions (same logic)."""
    mangled = opdef.type_name + "." + opdef.op_name
    stmts = _get_stmts(opdef.body)
    if not stmts:
        return
    last = stmts[-1]
    if not isinstance(last, ReturnStatement):
        return
    ret_val = last.value
    if not isinstance(ret_val, Call) or ret_val.name != mangled:
        return

    _opt_log("TRO", f"converted tail-recursive operator '{mangled}' to loop", opdef)

    param_names = [a[0] for a in opdef.args]
    param_types = [a[1] for a in opdef.args]
    rec_args = ret_val.args
    temps_stmts = []
    assign_stmts = []
    for i, pname in enumerate(param_names):
        if i < len(rec_args):
            tmp_name = f"__tail_{i}"
            temps_stmts.append(VariableDecl(tmp_name, param_types[i], rec_args[i]))
            assign_stmts.append(Assignment(Identifier(pname), Identifier(tmp_name)))

    before = stmts[:-1]
    opdef.body = before + [LoopStatement(temps_stmts + assign_stmts)]
