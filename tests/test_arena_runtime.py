#!/usr/bin/env python3
"""Build and run the arena runtime harness (tests/test_arena_runtime.c).

The harness pins the runtime invariants the generated code depends on:
the arena string identity tag survives an in-place growth, and a vec or
hash owned outside the current frame keeps a valid buffer/nodes after
the frame that grew or filled it is popped.

Run directly:  python3 tests/test_arena_runtime.py
Or via unittest discovery from the repo root:
    python3 -m unittest tests.test_arena_runtime
"""
import os
import shutil
import subprocess
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestArenaRuntime(unittest.TestCase):
    def _compiler(self):
        cc = os.environ.get("CC")
        if cc:
            return cc.split()
        for cand in ("cc", "gcc", "clang"):
            path = shutil.which(cand)
            if path:
                return [path]
        self.skipTest("no C compiler available")

    def _build(self, cc, td):
        exe = os.path.join(td, "test_arena_runtime")
        build = subprocess.run(
            cc + ["tests/test_arena_runtime.c", "leash/gc.c",
                  "-DLEASH_ARENA", "-O1", "-o", exe, "-pthread"],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=300)
        self.assertEqual(build.returncode, 0, build.stderr)
        return exe

    def test_runtime_harness(self):
        """String identity tag, rooted vec/hash lifetime, tip-growth safety."""
        cc = self._compiler()
        with tempfile.TemporaryDirectory() as td:
            exe = self._build(cc, td)
            run = subprocess.run([exe], cwd=REPO_ROOT, capture_output=True,
                                 text=True, timeout=300)
            self.assertEqual(run.returncode, 0,
                             run.stdout + "\n" + run.stderr)
            self.assertIn("all tests passed", run.stdout)


if __name__ == "__main__":
    unittest.main()
