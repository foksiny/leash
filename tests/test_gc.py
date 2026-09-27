#!/usr/bin/env python3
"""Build and run the standalone GC harness (tests/test_gc.c).

Run directly:  python3 tests/test_gc.py
Or via unittest discovery from the repo root:
    python3 -m unittest tests.test_gc
"""
import os
import shutil
import subprocess
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestGarbageCollector(unittest.TestCase):
    def _compiler(self):
        cc = os.environ.get("CC")
        if cc:
            return cc.split()
        for cand in ("cc", "gcc", "clang"):
            path = shutil.which(cand)
            if path:
                return [path]
        self.skipTest("no C compiler available")

    def test_gc_harness(self):
        """Allocator + mark/sweep + roots + scan regions + pool + futures."""
        cc = self._compiler()
        with tempfile.TemporaryDirectory() as td:
            exe = os.path.join(td, "test_gc")
            build = subprocess.run(
                cc + ["tests/test_gc.c", "leash/gc.c", "-O1", "-Wall",
                      "-o", exe, "-pthread"],
                cwd=REPO_ROOT, capture_output=True, text=True, timeout=120)
            self.assertEqual(build.returncode, 0, build.stderr)
            run = subprocess.run([exe], cwd=REPO_ROOT, capture_output=True,
                                 text=True, timeout=300)
            self.assertEqual(run.returncode, 0,
                             run.stdout + "\n" + run.stderr)
            self.assertIn("all tests passed", run.stdout)

    def test_gc_no_gc_mode_compiles(self):
        """-DNO_GC stub mode must stay link-complete (matrix ops, bigint,
        futures all live outside the NO_GC guard)."""
        cc = self._compiler()
        with tempfile.TemporaryDirectory() as td:
            exe = os.path.join(td, "test_gc_stub")
            build = subprocess.run(
                cc + ["tests/test_gc.c", "leash/gc.c", "-O1", "-Wall",
                      "-DNO_GC", "-o", exe, "-pthread"],
                cwd=REPO_ROOT, capture_output=True, text=True, timeout=120)
            self.assertEqual(build.returncode, 0, build.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
