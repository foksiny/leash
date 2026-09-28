#!/usr/bin/env python3
"""Regression tests for Phase 3 (optimizer + codegen) audit fixes.

Covers:
- dead-branch elimination must preserve non-literal `also` chains behind an
  always-false condition (previously silently dropped reachable code)
- exec(..., "wait"/"silent") must accumulate ALL output lines (previously the
  result buffer aliased the fgets line buffer, so output was corrupted)
- File.replace/replaceall with an empty old_str must be a no-op (previously an
  infinite loop in replaceall) and an empty new_str must not under-allocate
  the result buffer (previously a 1-byte heap overflow)
- sizeof uses ABI-padded struct sizes (via _type_byte_size -> _get_type_size)

Run directly:  python3 tests/test_codegen_fixes.py
Or via unittest discovery from the repo root:
    python3 -m unittest tests.test_codegen_fixes
"""
import os
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from leash.ast_nodes import (  # noqa: E402
    BinaryOp,
    Block,
    BoolLiteral,
    ExpressionStatement,
    Function,
    Identifier,
    IfStatement,
    NumberLiteral,
    Program,
)
from leash.ast_optimize import _walk_stmt_dead_branch, optimize_ast  # noqa: E402


def _mark(name):
    return ExpressionStatement(Identifier(name))


class TestDeadBranchAlsoChain(unittest.TestCase):
    """ast_optimize dead-branch pass must not drop reachable also-chains."""

    def test_nonliteral_also_chain_is_preserved(self):
        mid_cond = BinaryOp(Identifier("x"), ">", NumberLiteral(2))
        stmt = IfStatement(
            BoolLiteral(False),
            Block([_mark("then")]),
            [
                (mid_cond, Block([_mark("middle")]), False),
                (BoolLiteral(True), Block([_mark("late")]), False),
            ],
            Block([_mark("else")]),
        )
        out = _walk_stmt_dead_branch(stmt)
        self.assertIsInstance(out, IfStatement)
        self.assertIs(out.condition, mid_cond)
        self.assertEqual(
            [s.expr.name for s in out.then_block.statements], ["middle"]
        )
        self.assertEqual(len(out.also_blocks), 1)
        ac, ab, inv = out.also_blocks[0]
        self.assertIsInstance(ac, BoolLiteral)
        self.assertTrue(ac.value)
        self.assertFalse(inv)
        self.assertEqual([s.expr.name for s in ab.statements], ["late"])
        self.assertIsNotNone(out.else_block)
        self.assertEqual(
            [s.expr.name for s in out.else_block.statements], ["else"]
        )

    def test_nonliteral_also_without_else_promoted(self):
        cond = BinaryOp(Identifier("y"), ">", NumberLiteral(1))
        stmt = IfStatement(
            BoolLiteral(False),
            Block([_mark("then")]),
            [(cond, Block([_mark("body")]), False)],
            None,
        )
        out = _walk_stmt_dead_branch(stmt)
        self.assertIsInstance(out, IfStatement)
        self.assertIs(out.condition, cond)
        self.assertEqual([s.expr.name for s in out.then_block.statements], ["body"])
        self.assertEqual(out.also_blocks, [])
        self.assertIsNone(out.else_block)

    def test_literal_true_also_returns_that_block(self):
        stmt = IfStatement(
            BoolLiteral(False),
            Block([_mark("then")]),
            [(BoolLiteral(True), Block([_mark("late")]), False)],
            Block([_mark("else")]),
        )
        out = _walk_stmt_dead_branch(stmt)
        self.assertIsInstance(out, list)
        self.assertEqual([s.expr.name for s in out], ["late"])

    def test_literal_false_also_skipped_to_next(self):
        stmt = IfStatement(
            BoolLiteral(False),
            Block([_mark("then")]),
            [
                (BoolLiteral(False), Block([_mark("never")]), False),
                (BoolLiteral(True), Block([_mark("late")]), False),
            ],
            None,
        )
        out = _walk_stmt_dead_branch(stmt)
        self.assertIsInstance(out, list)
        self.assertEqual([s.expr.name for s in out], ["late"])

    def test_true_condition_keeps_then(self):
        stmt = IfStatement(
            BoolLiteral(True),
            Block([_mark("then")]),
            [(BinaryOp(Identifier("x"), ">", NumberLiteral(0)),
              Block([_mark("never")]), False)],
            Block([_mark("else")]),
        )
        out = _walk_stmt_dead_branch(stmt)
        self.assertIsInstance(out, list)
        self.assertEqual([s.expr.name for s in out], ["then"])

    def test_also_chain_survives_full_optimize_ast(self):
        mid_cond = BinaryOp(Identifier("x"), ">", NumberLiteral(2))
        stmt = IfStatement(
            BoolLiteral(False),
            Block([_mark("then")]),
            [(mid_cond, Block([_mark("middle")]), False)],
            Block([_mark("else")]),
        )
        fn = Function(
            "main", [], "void", Block([stmt]), visibility="pub"
        )
        prog = optimize_ast(Program([fn]))
        out = prog.items[0].body.statements[0]
        self.assertIsInstance(out, IfStatement)
        self.assertIs(out.condition, mid_cond)


class _CompileRunBase(unittest.TestCase):
    """Compile a Leash source file with the CLI and run the binary."""

    def _compile_and_run(self, source, timeout=300):
        with tempfile.TemporaryDirectory() as td:
            src = os.path.join(td, "main.lsh")
            exe = os.path.join(td, "prog")
            with open(src, "w") as f:
                f.write(source)
            build = subprocess.run(
                [sys.executable, "-m", "leash.cli", "compile", src, "to", exe],
                cwd=REPO_ROOT, capture_output=True, text=True, timeout=timeout,
            )
            self.assertEqual(
                build.returncode, 0,
                "compile failed:\n" + build.stdout + build.stderr,
            )
            run = subprocess.run(
                [exe], cwd=REPO_ROOT, capture_output=True, text=True,
                timeout=timeout,
            )
            self.assertEqual(
                run.returncode, 0,
                "run failed:\n" + run.stdout + run.stderr,
            )
            return run.stdout


class TestExecOutput(_CompileRunBase):
    def test_exec_wait_returns_all_lines(self):
        out = self._compile_and_run(
            'fnc main() : void {\n'
            '    o: string = exec("printf \'aaa\\\\nbbb\\\\nccc\\\\n\'", "wait");\n'
            '    show("A[", o, "]");\n'
            '    s: string = exec("printf \'x\\\\ny\\\\n\'", "silent");\n'
            '    show("S[", s, "]");\n'
            '}\n'
        )
        self.assertIn("A[aaa\nbbb\nccc\n]", out)
        self.assertIn("S[x\ny\n]", out)

    def test_exec_wait_large_output_realloc(self):
        expected = subprocess.run(
            ["seq", "1", "500"], capture_output=True, text=True, check=True
        ).stdout
        out = self._compile_and_run(
            'fnc main() : void {\n'
            '    big: string = exec("seq 1 500", "wait");\n'
            '    show("B=", big.size);\n'
            '}\n'
        )
        self.assertIn("B=%d" % len(expected), out)


class TestFileReplace(_CompileRunBase):
    def test_empty_old_is_noop_and_empty_new_deletes(self):
        with tempfile.TemporaryDirectory() as td:
            target = os.path.join(td, "rt.txt")
            out = self._compile_and_run(
                "fnc main() : void {\n"
                "    f: File = File.open(\"%s\", \"w\");\n"
                "    f.write(\"hello world hello\");\n"
                "    f.close();\n"
                "    g: File = File.open(\"%s\", \"r+\");\n"
                "    show(\"r1=\", g.replace(\"hello\", \"HI\"));\n"
                "    show(\"r2=\", g.replaceall(\"\", \"X\"));\n"
                "    show(\"r3=\", g.replaceall(\"l\", \"\"));\n"
                "    g.rewind();\n"
                "    show(\"content=[\", g.read(), \"]\");\n"
                "    show(\"r4=\", g.replace(\"missing\", \"z\"));\n"
                "    show(\"r5=\", g.replace(\"\", \"XX\"));\n"
                "    g.rewind();\n"
                "    show(\"final=[\", g.read(), \"]\");\n"
                "    g.close();\n"
                "}\n" % (target, target)
            )
            self.assertIn("r1=1", out)
            self.assertIn("r2=0", out)   # empty old_str: no-op, no infinite loop
            self.assertIn("r3=3", out)   # empty new_str: deletions
            self.assertIn("content=[HI word heo]", out)
            self.assertIn("r4=0", out)
            self.assertIn("r5=0", out)   # replace() with empty old_str: no-op
            self.assertIn("final=[HI word heo]", out)
            with open(target) as f:
                self.assertEqual(f.read(), "HI word heo")

    def test_replaceall_growth_fits_buffer(self):
        with tempfile.TemporaryDirectory() as td:
            target = os.path.join(td, "gt.txt")
            out = self._compile_and_run(
                "fnc main() : void {\n"
                "    f: File = File.open(\"%s\", \"w\");\n"
                "    f.write(\"abcabc\");\n"
                "    f.close();\n"
                "    g: File = File.open(\"%s\", \"r+\");\n"
                "    show(\"grow=\", g.replaceall(\"abc\", \"LONGER\"));\n"
                "    g.rewind();\n"
                "    show(\"content=[\", g.read(), \"]\");\n"
                "    g.close();\n"
                "}\n" % (target, target)
            )
            self.assertIn("grow=2", out)
            self.assertIn("content=[LONGERLONGER]", out)
            with open(target) as f:
                self.assertEqual(f.read(), "LONGERLONGER")


class TestSizeofAbiPadding(_CompileRunBase):
    def test_struct_sizes_use_abi_layout(self):
        out = self._compile_and_run(
            "def P : struct {\n"
            "    a: char;\n"
            "    b: int;\n"
            "    c: char;\n"
            "};\n"
            "def Q : struct {\n"
            "    x: int<64>;\n"
            "    y: char;\n"
            "};\n"
            "fnc main() : void {\n"
            "    show(\"P=\", sizeof(P));\n"
            "    show(\"Q=\", sizeof(Q));\n"
            "    show(\"I=\", sizeof(int));\n"
            "}\n"
        )
        # ABI padding: P = 0 + pad3 + 4 + 1 + pad3 = 12; Q = 8 + 1 + pad7 = 16
        self.assertIn("P=12", out)
        self.assertIn("Q=16", out)
        self.assertIn("I=4", out)


class TestPhase3bRegressions(_CompileRunBase):
    """Runtime regressions for the Phase 3b codegen audit fixes."""

    def _compile_failure(self, source, timeout=300):
        """Compile expecting failure; return the combined output text."""
        with tempfile.TemporaryDirectory() as td:
            src_path = os.path.join(td, "main.lsh")
            exe = os.path.join(td, "prog")
            with open(src_path, "w") as f:
                f.write(source)
            build = subprocess.run(
                [sys.executable, "-m", "leash.cli", "compile", src_path, "to", exe],
                cwd=REPO_ROOT, capture_output=True, text=True, timeout=timeout,
            )
            self.assertNotEqual(
                build.returncode, 0,
                "compile unexpectedly succeeded:\n" + build.stdout,
            )
            return build.stdout + build.stderr

    def _run_failure(self, source, timeout=300):
        """Compile OK but the program must abort with a runtime error."""
        with tempfile.TemporaryDirectory() as td:
            src_path = os.path.join(td, "main.lsh")
            exe = os.path.join(td, "prog")
            with open(src_path, "w") as f:
                f.write(source)
            build = subprocess.run(
                [sys.executable, "-m", "leash.cli", "compile", src_path, "to", exe],
                cwd=REPO_ROOT, capture_output=True, text=True, timeout=timeout,
            )
            self.assertEqual(
                build.returncode, 0,
                "compile failed:\n" + build.stdout + build.stderr,
            )
            run = subprocess.run(
                [exe], cwd=REPO_ROOT, capture_output=True, text=True,
                timeout=timeout,
            )
            self.assertNotEqual(run.returncode, 0, "expected a runtime abort")
            return run.stdout + run.stderr

    def test_uint_widening_preserves_value(self):
        # H2: uint<32> -> int<64> used to sign-extend to -1294967296.
        out = self._compile_and_run(
            "fnc main() : void { u: uint<32> = 3000000000; big: int<64> = u; show(big); }\n"
        )
        self.assertIn("3000000000", out)

    def test_uint_to_float_conversion(self):
        # H3: uint + float used sitofp on the signed reinterpretation.
        out = self._compile_and_run(
            "fnc main() : void { x: uint = 4000000000; show(x + 0.5); }\n"
        )
        self.assertIn("4000000000.500000", out)

    def test_float_to_uint_conversion(self):
        # H3: float -> uint used fptosi (poison for values over INT32_MAX).
        out = self._compile_and_run(
            "fnc main() : void { f: float = 4000000000.0; w: uint = f; show(w); }\n"
        )
        self.assertIn("4000000000", out)

    def test_char_is_unsigned_in_binary_ops(self):
        # M1: char(200) used to take the signed path for >> and comparisons.
        out = self._compile_and_run(
            "fnc main() : void { c: char = 200; show(c > 'A'); show(c >> 1); }\n"
        )
        self.assertIn("true", out)
        self.assertIn("100", out)

    def test_tostring_bool_and_uint(self):
        # M2: tostring(true) used to produce "-1"; uint printed negative.
        out = self._compile_and_run(
            "fnc main() : void { show(tostring(true)); u: uint = 4000000000; show(tostring(u)); }\n"
        )
        self.assertIn("true", out)
        self.assertIn("4000000000", out)

    def test_string_concat_huge_double(self):
        # C2: "x = " + 1.5e300 overflowed the 64-byte tostring buffer.
        out = self._compile_and_run(
            'fnc main() : void { x: float = 1.5e300; show("x = " + x); }\n'
        )
        self.assertIn("x = 1500", out)
        self.assertGreater(len(out.strip()), 200)

    def test_foreach_index_restores_outer(self):
        # H4: the loop variable used to leak over the outer binding.
        out = self._compile_and_run(
            "fnc main() : void {\n"
            "    i := 42;\n"
            "    a: int[3] = {1, 2, 3};\n"
            "    foreach i, v in<array> a { }\n"
            "    show(i);\n"
            "    b: int[2] = {1, 2};\n"
            "    c: int[5] = {10, 20, 30, 40, 50};\n"
            "    foreach i, v in<array> b {\n"
            "        foreach i, w in<array> c { }\n"
            "        show(i);\n"
            "    }\n"
            "}\n"
        )
        self.assertIn("42\n0\n1\n", out.replace("\r", ""))

    def test_huge_i64_index_bounds_checked(self):
        # H5: an index of 2^32+3 used to truncate to 3 and read in bounds.
        err = self._run_failure(
            "fnc main() : void {\n"
            "    s: char[8] = {'a','b','c','d','e','f','g','h'};\n"
            "    i: int<64> = 4294967299;\n"
            "    show(s[i]);\n"
            "}\n"
        )
        self.assertIn("out of bounds", err)

    def test_pointer_arith_negative_offset(self):
        # H6: `p + (-1)` zext'd the offset to 4294967295 elements.
        out = self._compile_and_run(
            "unsafe fnc main() : void {\n"
            "    buf: int[10] = {0,1,2,3,4,5,6,7,8,9};\n"
            "    p: *int = &buf[3];\n"
            "    off: int = -1;\n"
            "    show(*(p + off));\n"
            "}\n"
        )
        self.assertIn("2", out)

    def test_runtime_shift_range_checked(self):
        # M3: shifting by a runtime value >= width is poison (was unchecked).
        err = self._run_failure(
            "fnc getr() : int<32> { return toint(int, 66406) - 66405; }\n"
            "fnc main() : void {\n"
            "    r: int<32> = getr() * 33;\n"
            "    show(1 << r);\n"
            "}\n"
        )
        self.assertIn("Shift amount out of range", err)

    def test_static_int_min_div_minus_one(self):
        # M4 (+ constant propagation): INT_MIN / -1 is caught statically.
        outp = self._compile_failure(
            "fnc main() : void { a: int<32> = -2147483647 - 1; show(a / -1); }\n"
        )
        self.assertIn("Signed division overflow", outp)

    def test_vec_isin_struct_and_string(self):
        # M9: isin compared addresses — struct elements never matched and
        # equal strings compared unequal.
        out = self._compile_and_run(
            "def Node : struct { typ: string; val: int<64>; };\n"
            "fnc main() : void {\n"
            "    v: vec<Node> = {Node{typ: \"person\", val: 24}, Node{typ: \"x\", val: 1}};\n"
            "    show(v.isin(Node{typ: \"person\", val: 24}));\n"
            "    show(v.isin(Node{typ: \"nope\", val: 9}));\n"
            "    s: vec<string> = {\"alpha\", \"beta\"};\n"
            "    show(s.isin(\"beta\"));\n"
            "    show(s.isin(\"gamma\"));\n"
            "}\n"
        )
        self.assertIn("true\nfalse\ntrue\nfalse", out.replace("\r", ""))

    def test_vec_insert_remove_negative_index(self):
        # L8: insert/remove used to zext the index (no negative normalize).
        out = self._compile_and_run(
            "fnc main() : void {\n"
            "    v: vec<int> = {1, 2, 3};\n"
            "    v.insert(-1, 99);\n"
            "    show(v.get(0), v.get(1), v.get(2), v.get(3));\n"
            "    v.remove(-1);\n"
            "    show(v.get(0), v.get(1), v.get(2));\n"
            "}\n"
        )
        self.assertIn("12993", out)
        self.assertIn("1299", out)

    def test_wide_union_variant_aligned(self):
        # M8: int<128> union variants used to be stored misaligned at
        # offset 8; the struct-field store of a boxed tounion crashed.
        out = self._compile_and_run(
            "def Big : union { wide: int<128>; d: float; };\n"
            "def W : struct { u: Big; n: int<64>; };\n"
            "fnc main() : void {\n"
            "    w: W = W{u: tounion(Big, 340282366920938463463374607431768211455), n: 7};\n"
            "    show(w.u.wide);\n"
            "    show(w.n);\n"
            "}\n"
        )
        self.assertIn("-1", out)
        self.assertIn("7", out)

    def test_rand_bad_range_aborts(self):
        # L6: rand(min, max) with max < min used to srem by a negative range.
        err = self._run_failure(
            "fnc main() : void { show(rand(5, 2)); }\n"
        )
        self.assertIn("max >= min", err)


class TestPhase4RuntimeRegressions(_CompileRunBase):
    """Runtime/GC regressions for the Phase 4 audit fixes."""

    def test_union_global_survives_gc_churn(self):
        # rt-H1: a union-typed global holding a string must be registered
        # as a GC scan region; before the fix the global was invisible to
        # the collector and the string could be freed while reachable.
        out = self._compile_and_run(
            "def Value : union { s: string; n: int<64>; };\n"
            "gu: Value = tounion(Value, \"hello-union\");\n"
            "fnc main() : void {\n"
            "    for i: int<64> = 0; i < 300; i = i + 1 {\n"
            "        churn: vec<int<64>>;\n"
            "        for k: int<64> = 0; k < 400; k = k + 1 {\n"
            "            churn.pushb(k * i);\n"
            "        }\n"
            "    }\n"
            "    show(gu.s);\n"
            "}\n",
            timeout=300,
        )
        self.assertIn("hello-union", out)

    def test_union_vec_survives_gc_churn(self):
        # rt-H1: vec<union> buffers must not be marked ATOMIC (an atomic
        # payload is never scanned, so a string variant would be freed).
        out = self._compile_and_run(
            "def Value : union { s: string; n: int<64>; };\n"
            "fnc main() : void {\n"
            "    v: vec<Value>;\n"
            "    for i: int<64> = 0; i < 100; i = i + 1 {\n"
            "        v.pushb(tounion(Value, \"str-\" + tostring(i)));\n"
            "    }\n"
            "    churn: vec<int<64>>;\n"
            "    for i: int<64> = 0; i < 300; i = i + 1 {\n"
            "        for k: int<64> = 0; k < 400; k = k + 1 {\n"
            "            churn.pushb(k * i);\n"
            "        }\n"
            "    }\n"
            "    show(v.get(7).s);\n"
            "}\n",
            timeout=300,
        )
        self.assertIn("str-7", out)

    def test_file_readb_empty_file(self):
        # rt-M2/M3: readb on a zero-byte file must not produce a NULL
        # buffer, and read on a zero-byte file must return "".
        with tempfile.TemporaryDirectory() as td:
            empty = os.path.join(td, "empty.bin")
            open(empty, "w").close()
            out = self._compile_and_run(
                f'''fnc main() : void {{
                g: File = File.open("{empty}", "r");
                b: char[] = g.readb();
                show("readb size:", b.size);
                g.close();
                h: File = File.open("{empty}", "r");
                s: string = h.read();
                show("read len:", s.size);
                h.close();
            }}\n'''
            )
        self.assertIn("readb size:0", out)
        self.assertIn("read len:0", out)

    def test_showb_worker_output_not_lost(self):
        # rt-H2/M1 (+ epilogue order): worker showb appends must be
        # serialized by the runtime lock and flushed after wait_workers.
        out = self._compile_and_run(
            '''worker fnc w() : void { showb("from-worker\\n"); }
            fnc main() : void {
                showb("from-main\\n");
                spawn w();
            }\n''',
            timeout=300,
        )
        self.assertIn("from-main", out)
        self.assertIn("from-worker", out)

    def test_exec_failed_popen_returns_empty(self):
        # rt-M5: a failed popen used to fgets/pclose a NULL FILE*.
        out = self._compile_and_run(
            '''fnc main() : void {
                r: string = exec("definitely-not-a-command-xyz-12345", "wait");
                show("len:", r.size);
            }\n''',
            timeout=300,
        )
        self.assertIn("len:0", out)

    def test_exec_code_mode_exit_code(self):
        out = self._compile_and_run(
            '''fnc main() : void { show(exec("exit 7", "code")); }\n'''
        )
        self.assertIn("7", out)


class TestFusionAtomics(_CompileRunBase):
    """Phase 6: `fusion` globals must lower to atomic accesses."""

    def test_fusion_counter_uses_atomics(self):
        with tempfile.TemporaryDirectory() as td:
            src_path = os.path.join(td, "main.lsh")
            ll_path = os.path.join(td, "out.ll")
            with open(src_path, "w") as f:
                f.write(
                    "fusion counter: int<64> = 0;\n"
                    "worker fnc bump() : void { counter = counter + 1; }\n"
                    "fnc main() : void { spawn bump(); counter = counter + 5; show(counter); }\n"
                )
            build = subprocess.run(
                [sys.executable, "-m", "leash.cli", "dump", src_path, "to", ll_path],
                cwd=REPO_ROOT, capture_output=True, text=True, timeout=300,
            )
            if build.returncode != 0:
                # older CLI syntax: dump writes <input>.ll next to the source
                build = subprocess.run(
                    [sys.executable, "-m", "leash.cli", "dump", src_path],
                    cwd=REPO_ROOT, capture_output=True, text=True, timeout=300,
                )
                self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
                ll_path = os.path.join(td, "main.ll")
            ir_text = open(ll_path).read()
            self.assertIn("atomicrmw add", ir_text)
            self.assertIn("load atomic", ir_text)
            self.assertIn("seq_cst", ir_text)

    def test_fusion_counter_runs(self):
        out = self._compile_and_run(
            "fusion counter: int<64> = 0;\n"
            "worker fnc bump() : void { counter = counter + 1; }\n"
            "fnc main() : void { spawn bump(); counter = counter + 5; show(counter >= 5); }\n",
            timeout=300,
        )
        self.assertIn("true", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
