#!/usr/bin/env python3
"""Regression tests for int/uint widths 128, 256 and 512."""
import os
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEASHC = os.path.join(ROOT, "bin", "leashc")


def _run(src, name, td, opt="-O4"):
    path = os.path.join(td, name + ".lsh")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(src)
    exe = os.path.join(td, name + "_bin")
    r = subprocess.run([LEASHC, "compile", path, opt, "to", exe], cwd=ROOT,
                       capture_output=True, text=True, errors="replace",
                       timeout=180)
    if r.returncode != 0:
        raise AssertionError(r.stdout + r.stderr)
    run = subprocess.run([exe], cwd=td, capture_output=True, text=True,
                         errors="replace", timeout=120)
    return run.stdout.strip(), r.stdout + r.stderr


class WideIntegers(unittest.TestCase):
    def test_aliases_arithmetic(self):
        with tempfile.TemporaryDirectory(prefix="leash_wide_") as td:
            for typ in ("int128", "int256", "int512",
                        "uint128", "uint256", "uint512"):
                src = ("use types::*;\nfnc main() {\n"
                       "  a: %s = 3;\n  b: %s = 4;\n"
                       "  show((a + b) * 7);\n}\n" % (typ, typ))
                out, _ = _run(src, typ, td)
                self.assertEqual(out, "49", typ)

    def test_values_beyond_64_bits(self):
        with tempfile.TemporaryDirectory(prefix="leash_wide_") as td:
            out, _ = _run("""use types::*;
fnc main() {
  x: int128 = 1;
  x = (x << 64) + 1;
  show(x);
  y: uint256 = 1;
  y = (y << 200) + 5;
  show(y);
}
""", "big", td)
            self.assertEqual(out.splitlines()[0], "18446744073709551617")
            self.assertEqual(out.splitlines()[1],
                             "1606938044258990275541962092341162602522202993782792835301381")

    def test_recursive_ackermann_128(self):
        with tempfile.TemporaryDirectory(prefix="leash_wide_") as td:
            src = """use types::*;
fnc ackermann(m int128, n int128) : int128 {
  if m == 0 { return n + 1; }
  also m > 0 && n == 0 { return ackermann(m-1,1); }
  else { return ackermann(m-1,ackermann(m,n-1)); }
}
fnc main() { show(ackermann(4,1)); }
"""
            out, diag = _run(src, "ack128", td)
            self.assertEqual(out, "65533")
            self.assertNotIn("grep:", diag)


if __name__ == "__main__":
    unittest.main()
