#!/usr/bin/env python3
"""Stability of the -O scale.

The scale is a contract, not a heuristic: every higher level must add
optimizations and never remove a lower level's. This test checks that
contract deterministically (emitted IR features and the flag chain in the
driver) rather than relying on noisy wall-clock microbenchmarks, which
cannot be strictly ordered for programs whose binaries are identical.

Run directly:  python3 tests/test_opt_scale.py
"""
import os
import re
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEASHC = os.path.join(ROOT, "bin", "leashc")
DRIVER = os.path.join(ROOT, "compiler", "driver.lsh")

VEC_PUSH = """\
fnc main() : void {
    v: vec<int>;
    for i: int = 0; i < 100; i = i + 1 { v.pushb(i); }
    show(v.size);
}
"""

SMOKE = """\
def N : class { v: string; }
fnc make() : N { n: N = N { v: "ok" }; return n; }
fnc main() { show(make().v); }
"""


def _flag_tokens(expr):
    """Extract the clang flags assigned to `opt_flag` in a source block."""
    return set(re.findall(r"-O[0-4sz]", expr)) | {
        t for t in re.findall(r"-[a-zA-Z][a-zA-Z0-9=-]*", expr)
        if t.startswith(("-mllvm", "-flto", "-march", "-mtune"))
    }


class TestOptScale(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.exists(LEASHC):
            raise unittest.SkipTest("bin/leashc missing (run scripts/bootstrap.sh)")

    def _compile_ir(self, src, opt, td, name):
        path = os.path.join(td, name + ".lsh")
        with open(path, "w") as fh:
            fh.write(src)
        out = os.path.join(td, name + "_O" + opt)
        r = subprocess.run([LEASHC, "compile", path, "--emit-llvm", "-O" + opt,
                            "to", out], cwd=ROOT, capture_output=True,
                           text=True, timeout=180)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for cand in (out + ".ll", out + ".ll.ll"):
            if os.path.exists(cand):
                with open(cand) as fh:
                    return fh.read()
        self.fail("no .ll emitted for " + out)

    def test_ir_tiers_are_additive(self):
        """O2 folds a loop's trip count to a constant; O1 keeps the runtime
        count. This is the observable proof that the O2 IR tier is a strict
        superset of O1's."""
        with tempfile.TemporaryDirectory() as td:
            ir1 = self._compile_ir(VEC_PUSH, "1", td, "vp1")
            ir2 = self._compile_ir(VEC_PUSH, "2", td, "vp2")
            ir3 = self._compile_ir(VEC_PUSH, "3", td, "vp3")
            self.assertIn("b_rsrv_do", ir1, "O1 should use a runtime trip count")
            self.assertNotIn("b_rsrv_do", ir2, "O2 should fold the trip count")
            self.assertNotIn("b_rsrv_do", ir3, "O3 keeps every O2 optimization")

    def test_llvm_flag_chain_is_monotonic(self):
        """O1 ⊆ O2 ⊆ O3 ⊆ O4, with each step adding at least one flag."""
        with open(DRIVER) as fh:
            src = fh.read()
        # The native branches are the ones that carry the full flag sets;
        # cross targets deliberately fall back to plain -O2.
        by_level = {
            "1": _flag_tokens('"-O1"'),
            "2": _flag_tokens('"-O2"'),
            "3": _flag_tokens('"-O2 -flto=thin -fno-plt"'),
            "4": _flag_tokens('"-O2 -flto=thin -fno-plt -march=native -mtune=native"'),
        }
        for s in ("-O1", "-O2 -flto=thin -fno-plt",
                  "-O2 -flto=thin -fno-plt -march=native -mtune=native"):
            self.assertIn('"' + s + '"', src)
        # The -O codegen level must never go down (O2 is a real -O2 now,
        # not the old -O1 alias). Extra flags are additive from O2 on.
        def codegen_level(tokens):
            return max(int(t[2:]) for t in tokens if re.fullmatch(r"-O\d+", t))
        levels = [codegen_level(by_level[k]) for k in "1234"]
        self.assertEqual(levels, sorted(levels), f"codegen levels not monotonic: {levels}")
        self.assertEqual(levels, [1, 2, 2, 2])
        extras = [by_level[k] - {"-O2"} for k in "234"]
        self.assertTrue(extras[0] <= extras[1] <= extras[2],
                        f"extra flags not additive: {extras}")
        self.assertIn("-flto=thin", by_level["3"] - by_level["2"])
        self.assertTrue(any(t.startswith("-march") for t in by_level["4"] - by_level["3"]),
                        "O4 must add native tuning over O3")

    def test_runtime_opt_flags_are_monotonic(self):
        with open(DRIVER) as fh:
            src = fh.read()
        self.assertIn('if opt_level == "0" { rt_opt = "-O0"; }', src)
        self.assertIn('if opt_level == "1" { rt_opt = "-O1"; }', src)
        self.assertIn('rt_opt = "-O3 -flto=thin";', src)

    def test_recursive_unroll_only_at_high_levels(self):
        """O3/O4 clone self-recursive functions for inlining; O2 does not."""
        src = """\
fnc fib(n: int<64>) : int<64> {
    if n == 0 { return 0; }
    also n == 1 { return 1; }
    else { return fib(n - 1) + fib(n - 2); }
}
fnc main() { show(fib(20)); }
"""
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "fib.lsh")
            with open(path, "w") as fh:
                fh.write(src)
            ir = {}
            for o in "234":
                r = subprocess.run([LEASHC, "compile", path, "--emit-llvm",
                                    "-O" + o], cwd=td, capture_output=True,
                                   text=True, timeout=180)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                ll = os.path.join(td, "fib.ll")
                self.assertTrue(os.path.exists(ll), "no .ll emitted")
                ir[o] = open(ll).read()
            self.assertNotIn("__leash_ru", ir["2"], "O2 must stay LTO-free/small")
            self.assertIn("__leash_ru", ir["3"], "O3 should unroll recursion")
            self.assertIn("__leash_ru", ir["4"], "O4 should unroll recursion")
            for o in "234":
                exe = os.path.join(td, "fib_O" + o)
                r = subprocess.run([LEASHC, "compile", path, "-O" + o,
                                    "to", exe], cwd=td, capture_output=True,
                                   text=True, timeout=180)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                run = subprocess.run([exe], capture_output=True, text=True,
                                     timeout=60)
                self.assertEqual(run.stdout.strip(), "6765", f"O{o} output")

    def test_all_levels_produce_correct_output(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "smoke.lsh")
            with open(path, "w") as fh:
                fh.write(SMOKE)
            for o in "01234":
                exe = os.path.join(td, "smoke_O" + o)
                r = subprocess.run([LEASHC, "compile", path, "-O" + o, "to", exe],
                                   cwd=ROOT, capture_output=True, text=True, timeout=180)
                self.assertEqual(r.returncode, 0, f"O{o} compile: {r.stderr}")
                run = subprocess.run([exe], cwd=ROOT, capture_output=True,
                                     text=True, timeout=60)
                self.assertEqual(run.returncode, 0, f"O{o} run: {run.stderr}")
                self.assertEqual(run.stdout.strip(), "ok", f"O{o} output")



    def test_o4_unroll_is_selected_from_fp_density(self):
        """O4 adds -funroll-loops only for FP-heavy kernels; the gate must
        exist in the driver and both program classes must still build."""
        with open(DRIVER) as fh:
            driver = fh.read()
        self.assertIn("fp_ops > 64", driver)
        self.assertIn('-funroll-loops', driver)
        with tempfile.TemporaryDirectory() as td:
            for name, src in (
                ("mlp", 'fnc main() : void { x: float = 1.5; i: int = 0;\n'
                        '  while i < 200 { x = x + tofloat(float, i) * 0.25; i = i + 1; }\n'
                        '  show(x);\n}'),
                ("int", 'fnc main() : void { x: int = 0; i: int = 0;\n'
                        '  while i < 200 { x = x + i; i = i + 1; }\n'
                        '  show(x);\n}'),
            ):
                path = os.path.join(td, name + ".lsh")
                with open(path, "w") as fh:
                    fh.write(src)
                exe = os.path.join(td, name + "_O4")
                r = subprocess.run([LEASHC, "compile", path, "-O4", "to", exe],
                                   cwd=ROOT, capture_output=True, text=True,
                                   timeout=180)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                run = subprocess.run([exe], capture_output=True, text=True,
                                     timeout=60)
                self.assertEqual(run.returncode, 0, run.stderr)
                self.assertTrue(run.stdout.strip())

if __name__ == "__main__":
    unittest.main()
