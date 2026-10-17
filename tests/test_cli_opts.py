#!/usr/bin/env python3
"""CLI option parity: `-o` is not supported, unknown flags are reported.

Both compilers set the output file with `to <path>` only. A stray `-o`
(or any other unknown option) must produce an `info:` line instead of
being silently swallowed, and must not change the build result.
"""
import os
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN_LEASHC = os.path.join(ROOT, "bin", "leashc")

PROG = 'fnc main() {\n    show("hi");\n}\n'

O_INFO = "info: the '-o' option is not supported; set the output file with 'to <path>'"


def _run(cmd, cwd):
    return subprocess.run(cmd, capture_output=True, text=True,
                          cwd=cwd, timeout=600)


class TestCliOpts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.exists(BIN_LEASHC):
            raise unittest.SkipTest("bin/leashc missing (run `leash self-host`)")
        cls.tmp = tempfile.mkdtemp(prefix="leash_cli_opts_")
        with open(os.path.join(cls.tmp, "prog.lsh"), "w") as fh:
            fh.write(PROG)

    def _compile(self, compiler, extra):
        cmd = compiler + ["compile", os.path.join(self.tmp, "prog.lsh")] + extra
        return _run(cmd, ROOT)

    def test_dash_o_selfhosted(self):
        o_out = os.path.join(self.tmp, "o_out")
        r = self._compile([BIN_LEASHC], ["-o", o_out])
        self.assertEqual(r.returncode, 0)
        self.assertIn(O_INFO, r.stdout + r.stderr)
        # `-o` sets nothing: its path must not appear ...
        self.assertFalse(os.path.exists(o_out))
        self.assertFalse(os.path.exists(o_out + ".exe"))

    def test_unknown_flag_selfhosted(self):
        out = os.path.join(self.tmp, "bogus_bin")
        r = self._compile([BIN_LEASHC], ["--bogus", "to", out])
        self.assertEqual(r.returncode, 0)
        self.assertIn("info: ignoring unknown option '--bogus'",
                      r.stdout + r.stderr)
        run = _run([out], self.tmp)
        self.assertEqual(run.stdout.splitlines(), ["hi"])

    def test_info_lines_exact(self):
        # `-o` and unknown-option INFO lines are byte-exact.
        o1 = os.path.join(self.tmp, "par_s1")
        r1 = self._compile([BIN_LEASHC], ["-o", o1, "--bogus"])
        info1 = sorted(l for l in (r1.stdout + r1.stderr).splitlines()
                       if l.startswith("info:"))
        self.assertEqual(len(info1), 2)
        self.assertIn(O_INFO, info1)
        self.assertIn("info: ignoring unknown option '--bogus'", info1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
