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
        # The chain is written as a sequence of `opt_flag = "..."` inside
        # the O0/O1/O2/O3/O4 branches (the default declaration uses
        # `opt_flag: string` and is not matched here).
        start = src.index('opt_flag: string = "-O2";')
        end = src.index('lib_flags: string', start)
        block = src[start:end]
        assigns = re.findall(r'opt_flag\s*=\s*"([^"]+)"', block)
        # O0, O1, O2, O3, O4(native), O4(non-native), s, z.
        by_level = {
            "1": _flag_tokens(assigns[1]),
            "2": _flag_tokens(assigns[2]),
            "3": _flag_tokens(assigns[3]),
            "4": _flag_tokens(assigns[4]),
        }
        for lo, hi in (("1", "2"), ("2", "3"), ("3", "4")):
            self.assertTrue(by_level[lo] <= by_level[hi],
                            f"O{lo} flags {by_level[lo]} not subset of O{hi} {by_level[hi]}")
        self.assertIn("-flto", by_level["3"] - by_level["2"])
        self.assertTrue(any(t.startswith("-march") for t in by_level["4"] - by_level["3"]),
                        "O4 must add native tuning over O3")

    def test_runtime_opt_flags_are_monotonic(self):
        with open(DRIVER) as fh:
            src = fh.read()
        self.assertIn('if opt_level == "0" { rt_opt = "-O0"; }', src)
        self.assertIn('if opt_level == "1" { rt_opt = "-O1"; }', src)
        self.assertIn('if opt_level == "3" { rt_opt = "-O3"; }', src)
        self.assertIn('if opt_level == "4" { rt_opt = "-O3 -flto"; }', src)

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


if __name__ == "__main__":
    unittest.main()
