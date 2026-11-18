#!/usr/bin/env python3
"""Runtime numeric-safety and array-default regressions."""
import os
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEASH = os.path.join(ROOT, "bin", "leashc")


SRC_RANGE = """\
fnc main() : void { show(toint(int<8>, "300")); }
"""
SRC_FLOAT_RANGE = """\
fnc main() : void { show(toint(int<8>, 1e30)); }
"""
SRC_DIV_ZERO = """\
fnc div(a: int, b: int) : int { return a / b; }
fnc main() : void {
    z: int = toint(int, "0");
    show(div(10, z));
}
"""
SRC_DIV_OVERFLOW = """\
fnc div(a: int<8>, b: int<8>) : int<8> { return a / b; }
fnc main() : void {
    a: int<8> = toint(int<8>, "-128");
    b: int<8> = -1;
    show(div(a, b));
}
"""
SRC_VALID = """\
fnc main() : void {
    show(toint(int<8>, "127"), " ", toint(int<8>, "-128"), " ",
         toint(uint<8>, "255"), " ", toint(int, 42.9));
}
"""
SRC_ARRAYS = """\
fnc main() : void {
    a: int[1000];
    show(a.size, " ", a[0], " ", a[999]);
    s: string[4];
    show(s.size, " [", s[0], "]");
    b: int<64>[8];
    show(b.size, " ", b[7]);
}
"""


class TestSafetyConversions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.exists(LEASH):
            raise unittest.SkipTest("bin/leashc missing")

    def _run(self, src, td, name):
        path = os.path.join(td, name + ".lsh")
        exe = os.path.join(td, name)
        with open(path, "w") as fh:
            fh.write(src)
        build = subprocess.run([LEASH, "compile", path, "to", exe],
                               cwd=ROOT, capture_output=True, text=True,
                               timeout=180)
        self.assertEqual(build.returncode, 0, build.stderr)
        run = subprocess.run([exe], cwd=ROOT, capture_output=True, text=True,
                             timeout=30)
        return run

    def test_narrow_string_overflow_is_diagnosed(self):
        with tempfile.TemporaryDirectory() as td:
            r = self._run(SRC_RANGE, td, "range")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("out of range", r.stdout + r.stderr)

    def test_float_to_narrow_int_overflow_is_diagnosed(self):
        with tempfile.TemporaryDirectory() as td:
            r = self._run(SRC_FLOAT_RANGE, td, "floatrange")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("out of range", r.stdout + r.stderr)

    def test_dynamic_division_by_zero_is_diagnosed(self):
        with tempfile.TemporaryDirectory() as td:
            r = self._run(SRC_DIV_ZERO, td, "divzero")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("division by zero", r.stdout + r.stderr)

    def test_dynamic_div_overflow_is_diagnosed(self):
        with tempfile.TemporaryDirectory() as td:
            r = self._run(SRC_DIV_OVERFLOW, td, "divovf")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("division by zero or integer overflow", r.stdout + r.stderr)

    def test_valid_conversions_still_work(self):
        with tempfile.TemporaryDirectory() as td:
            r = self._run(SRC_VALID, td, "valid")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.strip(), "127 -128 255 42")

    def test_fixed_arrays_get_default_values(self):
        with tempfile.TemporaryDirectory() as td:
            r = self._run(SRC_ARRAYS, td, "arrays")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("1000 0 0", r.stdout)
            self.assertIn("4 []", r.stdout)
            self.assertIn("8 0", r.stdout)


if __name__ == "__main__":
    unittest.main()
