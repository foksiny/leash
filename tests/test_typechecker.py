#!/usr/bin/env python3
"""Type-checker regression tests for the Phase-2 audit fixes.

Covers:
  - argument / field / method type mismatches are errors (were warnings)
  - mixed-type array & hash literals are errors
  - incompatible comparisons and `<>` operand mismatches are errors
  - shift amount >= bit-width is an error, with correct bit-width defaults
    (bare int/uint are 32-bit, not 64)
  - non-void functions must return on all paths (was a warning)
  - generic template placeholders (`_T`) are compatible with `T` inside the
    template body (method-lookup instantiations must not leak)
  - `check_file` reports internal (non-Leash) exceptions as errors instead of
    silently passing (L6)

Run: python3 -m unittest tests.test_typechecker
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from leash.cli import check_file  # noqa: E402


def _check(src, name="main.lsh"):
    d = tempfile.mkdtemp(prefix="leash_tc_test_")
    path = os.path.join(d, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(src)
    errors, warnings = check_file(path, verbose=False)
    return errors, warnings


def _msgs(errors):
    out = []
    for e in errors:
        out.append(e.msg if hasattr(e, "msg") else str(e))
    return out


def _assert_error(test, errors, fragment):
    msgs = _msgs(errors)
    for m in msgs:
        if fragment in m:
            return m
    test.fail("expected error containing %r, got: %s" % (fragment, msgs))


class TestArgTypeMismatchIsError(unittest.TestCase):
    def test_positional_arg_type_mismatch(self):
        errors, _ = _check(
            "fnc add(a int, b int) : int { return a + b; }\n"
            "fnc main() : void { show(add(\"x\", 1)); }\n"
        )
        _assert_error(self, errors, "Argument 1 of 'add' expects 'int'")
        _assert_error(self, errors, "but got 'string'")

    def test_kwarg_type_mismatch(self):
        errors, _ = _check(
            "fnc add(a int, b int) : int { return a + b; }\n"
            "fnc main() : void { show(add(a = 1, b = \"x\")); }\n"
        )
        _assert_error(self, errors, "Argument 'b' of 'add' expects 'int'")

    def test_struct_field_type_mismatch(self):
        errors, _ = _check(
            "def P : struct { x: int; };\n"
            "fnc main() : void { p := P { x: \"s\" }; show(p.x); }\n"
        )
        _assert_error(self, errors, "field 'x' expects 'int'")

    def test_method_arg_type_mismatch(self):
        errors, _ = _check(
            "def Counter : struct { n: int; };\n"
            "fnc inc(by int) : void -> Counter { this.n = this.n + by; }\n"
            "fnc main() : void { c: Counter = Counter { n: 0 }; c.inc(\"x\"); }\n"
        )
        _assert_error(self, errors, "of struct method 'inc' expects 'int'")

    def test_vector_push_arg_type_mismatch(self):
        errors, _ = _check(
            "fnc main() : void { v: vec<int>; v.pushb(\"x\"); }\n"
        )
        _assert_error(self, errors, "Vector method 'pushb' expects argument of type 'int'")

    def test_extra_positional_arg_errors(self):
        errors, _ = _check(
            "fnc one(a int) : int { return a; }\n"
            "fnc main() : void { show(one(1, 2)); }\n"
        )
        _assert_error(self, errors, "expects at most 1 argument(s)")

    def test_duplicate_positional_and_kwarg_errors(self):
        errors, _ = _check(
            "fnc one(a int) : int { return a; }\n"
            "fnc main() : void { show(one(1, a = 2)); }\n"
        )
        _assert_error(self, errors, "multiple values for argument(s)")


class TestLiteralAndComparisonErrors(unittest.TestCase):
    def test_mixed_array_literal(self):
        errors, _ = _check('fnc main() : void { v := {1, "a"}; show(v.size); }\n')
        _assert_error(self, errors, "Array contains mixed types")

    def test_incompatible_comparison(self):
        errors, _ = _check(
            'fnc main() : void { x := 1; if x == "a" { show(1); } }\n'
        )
        self.assertTrue(errors, "int == string must be an error")
        joined = " ".join(_msgs(errors))
        self.assertTrue(
            "Cannot use operator '==' between 'int' and 'string'" in joined
            or "Comparing values of different types" in joined,
            joined,
        )

    def test_contains_operator_operand_mismatch(self):
        errors, _ = _check(
            'fnc main() : void { arr: int[3] = {1, 2, 3}; if "x" <> arr { show(1); } }\n'
        )
        _assert_error(self, errors, "expects left operand of type 'int'")


class TestShiftBitWidth(unittest.TestCase):
    def test_shift_at_32_on_bare_int_errors(self):
        # Bare `int` is 32-bit: previously defaulted to 64 and never warned.
        errors, _ = _check("fnc main() : void { x := 1; y := x << 32; show(y); }\n")
        _assert_error(self, errors, "Shift amount 32")

    def test_shift_within_32_on_bare_int_ok(self):
        errors, _ = _check("fnc main() : void { x := 1; y := x << 31; show(y); }\n")
        self.assertEqual(errors, [], _msgs(errors))

    def test_shift_32_on_uint64_ok(self):
        errors, _ = _check(
            "fnc main() : void { z: uint<64> = 1; y: uint<64> = z << 32; show(y); }\n"
        )
        self.assertEqual(errors, [], _msgs(errors))


class TestReturnValueRequired(unittest.TestCase):
    def test_non_void_without_return_errors(self):
        errors, _ = _check("fnc f() : int { x := 1; }\n"
                           "fnc main() : void { show(f()); }\n")
        _assert_error(self, errors, "might not return a value on all paths")

    def test_non_void_with_return_ok(self):
        errors, _ = _check("fnc f() : int { x := 1; return x; }\n"
                           "fnc main() : void { show(f()); }\n")
        self.assertEqual(errors, [], _msgs(errors))


class TestGenericPlaceholders(unittest.TestCase):
    TEMPLATE = (
        "priv def T : template;\n"
        "pub def Buf : class<T> {\n"
        "    priv items: vec<T>;\n"
        "    pub fnc grab() : vec<T> {\n"
        "        out: vec<T>;\n"
        "        out.pushb(this.items.get(0));\n"
        "        return out;\n"
        "    }\n"
        "};\n"
        "fnc main() : void { ignore; }\n"
    )

    def test_placeholder_T_equals_T_inside_template(self):
        # `this.items.at(0)` infers `_T` via placeholder method-lookup
        # instantiation; inside the template body it must match `vec<T>`.
        errors, warnings = _check(self.TEMPLATE)
        self.assertEqual(errors, [], _msgs(errors))
        for w in warnings:
            if "expects" in w["msg"] and "_T" in w["msg"]:
                self.fail("placeholder mismatch leaked: %s" % w["msg"])


class TestCheckFileInternalErrors(unittest.TestCase):
    def test_internal_exception_becomes_error(self):
        # L6: a non-Leash exception used to return (errors=[], warnings=[])
        # which callers read as success (exit code 0).
        class BoomParser:
            def __init__(self, *a, **k):
                pass

            def parse(self):
                raise RuntimeError("kaboom")

        with mock.patch("leash.cli.Parser", BoomParser):
            errors, _ = _check("fnc main() : void { ignore; }\n")
        self.assertTrue(errors, "internal exception must produce an error")
        _assert_error(self, errors, "Internal compiler error")
        _assert_error(self, errors, "kaboom")

    def test_internal_exception_in_typechecker_becomes_error(self):
        class BoomChecker:
            def __init__(self, *a, **k):
                pass

            def check(self, ast):
                raise RuntimeError("tc exploded")

            errors = []

        with mock.patch("leash.cli.TypeChecker", BoomChecker):
            errors, _ = _check("fnc main() : void { ignore; }\n")
        self.assertTrue(errors, "internal typechecker exception must error")
        _assert_error(self, errors, "tc exploded")


class TestLiteralRangeFits(unittest.TestCase):
    """C2: integer literals must fit the declared type's sign/width."""

    def test_int8_overflow_errors(self):
        errors, _ = _check(
            "fnc main() : void { c: int<8> = 129; show(c); }\n"
        )
        _assert_error(self, errors, "out of range for type 'int<8>'")

    def test_int8_max_ok(self):
        errors, _ = _check(
            "fnc main() : void { c: int<8> = 127; show(c); }\n"
        )
        self.assertEqual(errors, [], _msgs(errors))

    def test_uint8_256_errors(self):
        errors, _ = _check(
            "fnc main() : void { u: uint<8> = 256; show(u); }\n"
        )
        _assert_error(self, errors, "out of range for type 'uint<8>'")


class TestWorksBlockStaticErrors(unittest.TestCase):
    """C3: static errors inside `works` are surfaced, not swallowed."""

    def test_type_error_in_works_is_reported(self):
        errors, _ = _check(
            "fnc main() : void {\n"
            "    works { x: int = \"s\"; show(x); } otherwise err { show(\"e\"); }\n"
            "}\n"
        )
        _assert_error(self, errors, "declared as 'int'")


class TestInferenceFailureStops(unittest.TestCase):
    """H1: `:=` with an uninferable initializer must not crash the checker."""

    def test_infer_failure_reports_and_continues(self):
        errors, _ = _check(
            "fnc main() : void { x := no_such_thing; show(\"done\"); }\n"
        )
        _assert_error(self, errors, "Undefined variable: 'no_such_thing'")
        joined = " ".join(_msgs(errors))
        self.assertNotIn("Internal compiler error", joined)


class TestBinaryOpFallbackErrors(unittest.TestCase):
    """H3: operators between unsupported types are hard errors."""

    def test_struct_plus_int_errors(self):
        errors, _ = _check(
            "def P : struct { x: int; };\n"
            "fnc main() : void { p: P = P{x: 1}; show(p + 1); }\n"
        )
        _assert_error(self, errors, "Cannot use operator '+' between 'P' and 'int'")

    def test_int_plus_int_ok(self):
        errors, _ = _check("fnc main() : void { show(1 + 2); }\n")
        self.assertEqual(errors, [], _msgs(errors))


class TestAllPathsReturn(unittest.TestCase):
    """H4/L2: if/else where both arms return satisfies the return check."""

    def test_both_branches_return_ok(self):
        errors, _ = _check(
            "fnc f(n int) : int { if n > 0 { return 1; } else { return 2; } }\n"
            "fnc main() : void { show(f(0)); }\n"
        )
        self.assertEqual(errors, [], _msgs(errors))

    def test_single_arm_return_errors(self):
        errors, _ = _check(
            "fnc f(n int) : int { if n > 0 { return 1; } }\n"
            "fnc main() : void { show(f(0)); }\n"
        )
        _assert_error(self, errors, "might not return a value on all paths")


class TestPointerCastUnsafe(unittest.TestCase):
    """H5: pointer->int casts are flagged by the low-level checker."""

    def test_ptr_as_int_flagged(self):
        errors, _ = _check(
            "fnc main() : void { a: int<64> = 5; p: *int<64> = &a; show(p as int); }\n"
        )
        _assert_error(self, errors, "Casting a pointer to integer")

    def test_value_cast_ok(self):
        errors, _ = _check(
            "fnc main() : void { d: int<64> = 3; c: char = '0'; show((char)(c + d)); }\n"
        )
        self.assertEqual(errors, [], _msgs(errors))


class TestSpawnRequiresWorker(unittest.TestCase):
    """H7: `spawn` only accepts `worker fnc` functions."""

    def test_spawn_plain_function_errors(self):
        errors, _ = _check(
            "fnc hello() : void { show(\"hi\"); }\n"
            "fnc main() : void { spawn hello(); }\n"
        )
        _assert_error(self, errors, "Cannot spawn non-worker function 'hello'")

    def test_spawn_worker_ok(self):
        errors, _ = _check(
            "worker fnc w() : void { show(\"hi\"); }\n"
            "fnc main() : void { spawn w(); }\n"
        )
        self.assertEqual(errors, [], _msgs(errors))


class TestBuiltinArgCountNoCrash(unittest.TestCase):
    """M1: builtin arg-count errors return instead of crashing."""

    def test_cstr_no_args(self):
        errors, _ = _check("fnc main() : void { show(cstr()); }\n")
        _assert_error(self, errors, "Function 'cstr' expects 1 argument")
        joined = " ".join(_msgs(errors))
        self.assertNotIn("list index out of range", joined)


class TestStructInitMissingField(unittest.TestCase):
    """M2: struct initializers must cover fields without defaults."""

    def test_missing_field_errors(self):
        errors, _ = _check(
            "def P : struct { x: int; y: int; };\n"
            "fnc main() : void { p: P = P{x: 1}; show(p.y); }\n"
        )
        _assert_error(self, errors, "initializer is missing field(s): y")

    def test_default_field_omitted_ok(self):
        errors, _ = _check(
            "def Q : struct { a: int; b: int = 2; };\n"
            "fnc main() : void { q: Q = Q{a: 1}; show(q.b); }\n"
        )
        self.assertEqual(errors, [], _msgs(errors))


class TestDefaultParamType(unittest.TestCase):
    """M3: default parameter values must match the declared type."""

    def test_bad_default_type(self):
        errors, _ = _check(
            'fnc f(a int = "x") : int { return a; }\n'
            "fnc main() : void { show(f()); }\n"
        )
        _assert_error(self, errors, "Default value for parameter 'a'")


class TestUnknownSignatureTypes(unittest.TestCase):
    """M4: functions with unknown param/return types error at registration."""

    def test_unknown_param_type(self):
        errors, _ = _check(
            "fnc f(a Bogus) : void { }\n"
            "fnc main() : void { f(1); }\n"
        )
        _assert_error(self, errors, "parameter 'a' has unknown type 'Bogus'")

    def test_unknown_return_type(self):
        errors, _ = _check(
            "fnc g() : Nope { }\n"
            "fnc main() : void { g(); }\n"
        )
        _assert_error(self, errors, "return type 'Nope' is unknown")


class TestArraySizeValidation(unittest.TestCase):
    """M5: array sizes must be positive, bounded, or initialized."""

    def test_zero_size_errors(self):
        errors, _ = _check("fnc main() : void { a: int[0]; show(a); }\n")
        _assert_error(self, errors, "must be a positive integer")

    def test_huge_size_errors(self):
        errors, _ = _check(
            "fnc main() : void { a: int[4294967296]; show(a); }\n"
        )
        _assert_error(self, errors, "too large (maximum 2147483647)")

    def test_nonconstant_size_without_init_errors(self):
        errors, _ = _check(
            "fnc main() : void { n: int = 3; a: int[n]; show(a); }\n"
        )
        _assert_error(self, errors, "non-constant size")

    def test_nonconstant_size_with_init_ok(self):
        errors, _ = _check(
            "fnc main() : void { n: int = 3; a: int[n] = {1,2,3}; show(a[0]); }\n"
        )
        self.assertEqual(errors, [], _msgs(errors))


class TestIndexOnNonIndexable(unittest.TestCase):
    """M6: indexing a scalar is an error, not a silent None."""

    def test_int_index_errors(self):
        errors, _ = _check(
            "fnc main() : void { a: int<64> = 5; show(a[0]); }\n"
        )
        _assert_error(self, errors, "Cannot index a value of type 'int<64>'")


class TestBlockScoping(unittest.TestCase):
    """M7: declarations inside if/loop bodies don't leak to the outer scope."""

    def test_if_block_no_leak(self):
        errors, _ = _check(
            "fnc main() : void { if true { z: int<64> = 1; } show(z); }\n"
        )
        _assert_error(self, errors, "Undefined variable: 'z'")

    def test_while_block_no_leak(self):
        errors, _ = _check(
            "fnc main() : void { while false { w: int<64> = 1; } show(w); }\n"
        )
        _assert_error(self, errors, "Undefined variable: 'w'")

    def test_outer_still_visible_inside(self):
        errors, _ = _check(
            "fnc main() : void { v: int<64> = 7; if true { show(v); } show(v); }\n"
        )
        self.assertEqual(errors, [], _msgs(errors))


class TestOpdefStructMethods(unittest.TestCase):
    """M8: opdef extension methods on user structs work, with implicit `this`."""

    OPDEF = (
        "def Vec2 : struct { x: int<64>; y: int<64>; };\n"
        "opdef Vec2.add(o: Vec2) : Vec2 { return Vec2{x: this.x + o.x, y: this.y + o.y}; }\n"
        "fnc main() : void {\n"
        "    a := Vec2{x: 1, y: 2};\n"
        "    b := Vec2{x: 3, y: 4};\n"
        "    c := a.add(b);\n"
        "    show(c.x); show(c.y);\n"
        "}\n"
    )

    def test_opdef_struct_method_ok(self):
        errors, _ = _check(self.OPDEF)
        self.assertEqual(errors, [], _msgs(errors))

    def test_opdef_struct_method_badarg(self):
        errors, _ = _check(
            "def Vec2 : struct { x: int<64>; y: int<64>; };\n"
            "opdef Vec2.add(o: Vec2) : Vec2 { return Vec2{x: this.x + o.x, y: this.y + o.y}; }\n"
            "fnc main() : void { a := Vec2{x: 1, y: 2}; c := a.add(42); show(c.x); }\n"
        )
        _assert_error(self, errors, "of struct method 'add' expects 'Vec2' but got 'int'")


class TestShiftChecks(unittest.TestCase):
    """M9: shift amounts are checked against the real (resolved) width."""

    def test_char_shift_over_8_errors(self):
        errors, _ = _check(
            "fnc main() : void { c: char = 'a'; show(c << 9); }\n"
        )
        _assert_error(self, errors, "Shift amount 9")

    def test_negative_shift_errors(self):
        errors, _ = _check(
            "fnc main() : void { a: int<64> = 1; show(a << -1); }\n"
        )
        _assert_error(self, errors, "is negative.")

    def test_alias_width_resolved(self):
        errors, _ = _check(
            "def u8x : type uint<8>;\n"
            "fnc main() : void { x: u8x = 1; show(x << 8); }\n"
        )
        _assert_error(self, errors, "Shift amount 8")


class TestModuloByZero(unittest.TestCase):
    """M10: `% 0` with a literal zero is caught statically."""

    def test_mod_zero_errors(self):
        errors, _ = _check(
            "fnc main() : void { a: int<64> = 10; show(a % 0); }\n"
        )
        _assert_error(self, errors, "Modulo by zero")

    def test_mod_two_ok(self):
        errors, _ = _check(
            "fnc main() : void { a: int<64> = 10; show(a % 2); }\n"
        )
        self.assertEqual(errors, [], _msgs(errors))


class TestUnionPunningRoots(unittest.TestCase):
    """M11: union type-punning through globals, struct fields, aliases."""

    UNION = "def U : union { i: int<64>; f: float; };\n"

    def test_global_union_pun(self):
        errors, _ = _check(
            self.UNION
            + "gu: U = 5;\n"
            + "fnc main() : void { show(gu.f); }\n"
        )
        _assert_error(self, errors, "type-punning")

    def test_struct_field_union_pun(self):
        errors, _ = _check(
            self.UNION
            + "def W : struct { u: U; };\n"
            + "fnc main() : void { w: W = W{u: 5}; show(w.u.f); }\n"
        )
        _assert_error(self, errors, "type-punning")

    def test_alias_union_pun(self):
        errors, _ = _check(
            self.UNION
            + "def MyU : type U;\n"
            + "fnc main() : void { u: MyU = 5; show(u.f); }\n"
        )
        _assert_error(self, errors, "type-punning")

    def test_same_variant_read_ok(self):
        errors, _ = _check(
            self.UNION
            + "fnc main() : void { u: U = 5; show(u.i); }\n"
        )
        self.assertEqual(errors, [], _msgs(errors))


class TestDelAliasAndBorrows(unittest.TestCase):
    """M12: `del` poisons aliases; read-only borrows can't feed `&` params."""

    def test_del_via_alias_errors(self):
        errors, _ = _check(
            "def Obj : class { v: int<64>; new(v: int<64>) { this.v = v; } };\n"
            "fnc main() : void { o: Obj = create Obj(1); p := o; del o; show(p.v); }\n"
        )
        _assert_error(self, errors, "Use of deleted variable 'p'")

    def test_double_delete_via_alias_errors(self):
        errors, _ = _check(
            "def Obj : class { v: int<64>; new(v: int<64>) { this.v = v; } };\n"
            "fnc main() : void { o: Obj = create Obj(1); p := o; del o; del p; }\n"
        )
        _assert_error(self, errors, "Double delete: 'p'")

    def test_readonly_borrow_to_mutable_param_errors(self):
        errors, _ = _check(
            "fnc fillr(a: &int[]) : void { a[0] = 99; }\n"
            "fnc caller(p: int[]) : void { fillr(p); }\n"
            "fnc main() : void { arr: int[3] = {1,2,3}; caller(arr); show(arr[0]); }\n"
        )
        _assert_error(self, errors, "requires a mutable borrow ('&')")

    def test_own_array_to_mutable_param_ok(self):
        errors, _ = _check(
            "fnc fillr(a: &int[]) : void { a[0] = 99; }\n"
            "fnc main() : void { arr: int[3] = {1,2,3}; fillr(arr); show(arr[0]); }\n"
        )
        self.assertEqual(errors, [], _msgs(errors))


class TestImutContainerWrites(unittest.TestCase):
    """M13: element/field writes through `imut` variables are rejected."""

    def test_imut_field_write_errors(self):
        errors, _ = _check(
            "def P : struct { x: int<64>; };\n"
            "fnc main() : void { p: imut P = P{x: 1}; p.x = 5; show(p.x); }\n"
        )
        _assert_error(self, errors, "it was declared `imut`")

    def test_imut_array_write_errors(self):
        errors, _ = _check(
            "fnc main() : void { a: imut int<64>[3] = {1,2,3}; a[0] = 9; show(a[0]); }\n"
        )
        _assert_error(self, errors, "it was declared `imut`")

    def test_imut_read_ok(self):
        errors, _ = _check(
            "fnc main() : void { a: imut int<64>[3] = {1,2,3}; show(a[0]); }\n"
        )
        self.assertEqual(errors, [], _msgs(errors))


class TestNegativeIndexBounds(unittest.TestCase):
    """M14: literal indices beyond the wrap range are caught statically."""

    def test_beyond_wrap_negative_errors(self):
        errors, _ = _check(
            "fnc main() : void { a: int<64>[4] = {1,2,3,4}; show(a[-5]); }\n"
        )
        _assert_error(self, errors, "Array index -5 is out of bounds")

    def test_wrap_last_element_ok(self):
        # -1 wraps to the last element by design (runtime-checked).
        errors, _ = _check(
            "fnc main() : void { a: int<64>[4] = {1,2,3,4}; show(a[-1]); }\n"
        )
        self.assertEqual(errors, [], _msgs(errors))

    def test_positive_oob_errors(self):
        errors, _ = _check(
            "fnc main() : void { a: int<64>[4] = {1,2,3,4}; show(a[10]); }\n"
        )
        _assert_error(self, errors, "Array index 10 is out of bounds")


class TestBoolConditions(unittest.TestCase):
    """M15: if/while/for conditions and && / || operands must be bool."""

    def test_if_int_condition_errors(self):
        errors, _ = _check('fnc main() : void { if 5 { show("y"); } }\n')
        _assert_error(self, errors, "'if' condition must be 'bool', but got 'int'")

    def test_if_string_condition_errors(self):
        errors, _ = _check('fnc main() : void { if "abc" { show("y"); } }\n')
        _assert_error(self, errors, "'if' condition must be 'bool', but got 'string'")

    def test_while_string_condition_errors(self):
        errors, _ = _check(
            'fnc main() : void { i: int<64> = 0; while "s" { i = i + 1; if i > 3 { stop; } } show(i); }\n'
        )
        _assert_error(self, errors, "'while' condition must be 'bool', but got 'string'")

    def test_logic_on_ints_errors(self):
        errors, _ = _check(
            "fnc main() : void { a: int<64> = 5; b: int<64> = 6; if a && b { show(1); } }\n"
        )
        _assert_error(self, errors, "Operator '&&' requires 'bool' operands")

    def test_bool_condition_ok(self):
        errors, _ = _check(
            'fnc main() : void { x: int<64> = 1; if x == 1 { show("y"); } }\n'
        )
        self.assertEqual(errors, [], _msgs(errors))


class TestUnknownMemberOnPrimitives(unittest.TestCase):
    """M16: member access on primitives/arrays/strings is an error."""

    def test_member_on_int_errors(self):
        errors, _ = _check(
            "fnc main() : void { a: int<64> = 5; show(a.foo); }\n"
        )
        _assert_error(self, errors, "has no member named 'foo'")

    def test_member_on_array_errors(self):
        errors, _ = _check(
            "fnc main() : void { a: int<64>[3] = {1,2,3}; show(a.foo); }\n"
        )
        _assert_error(self, errors, "has no member named 'foo'")

    def test_member_on_string_errors(self):
        errors, _ = _check(
            'fnc main() : void { s: string = "hi"; show(s.bogus); }\n'
        )
        _assert_error(self, errors, "has no member named 'bogus'")

    def test_size_members_still_ok(self):
        errors, _ = _check(
            'fnc main() : void { s: string = "hi"; a: int[3] = {1,2,3}; show(s.size, a.size); }\n'
        )
        self.assertEqual(errors, [], _msgs(errors))


class TestSharedOneWriterRule(unittest.TestCase):
    """Phase 6: `shared` globals allow exactly one writing function."""

    def test_shared_two_writers_error(self):
        errors, _ = _check(
            "shared result: int<64> = 0;\n"
            "worker fnc w1() : void { result = 1; }\n"
            "worker fnc w2() : void { result = 2; }\n"
            "fnc main() : void { spawn w1(); spawn w2(); }\n"
        )
        _assert_error(self, errors, "shared variable 'result' is written by both")

    def test_shared_one_writer_ok(self):
        errors, _ = _check(
            "shared result: int<64> = 0;\n"
            "worker fnc w1() : void { result = 1; }\n"
            "worker fnc r() : void { show(result); }\n"
            "fnc main() : void { spawn w1(); spawn r(); show(result); }\n"
        )
        self.assertEqual(errors, [], _msgs(errors))

    def test_shared_shadowed_by_local_ok(self):
        # A local that shadows the shared global is NOT a write to it.
        errors, _ = _check(
            "shared result: int<64> = 0;\n"
            "worker fnc w1() : void { result = 1; }\n"
            "fnc main() : void { result: int<64> = 5; result = 6; show(result); }\n"
        )
        self.assertEqual(errors, [], _msgs(errors))


if __name__ == "__main__":
    unittest.main()
