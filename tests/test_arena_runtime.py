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


def _sanitizer_ok(cc):
    """Whether `cc` can build a trivial ASan/UBSan executable."""
    import tempfile as _tf
    try:
        with _tf.TemporaryDirectory() as td:
            src = os.path.join(td, "probe.c")
            exe = os.path.join(td, "probe")
            with open(src, "w") as fh:
                fh.write("int main(void){return 0;}\n")
            r = subprocess.run(cc + ["-fsanitize=address,undefined", src, "-o", exe],
                               capture_output=True, text=True, timeout=60)
            return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _compiler(test):
    cc = os.environ.get("CC")
    if cc:
        return cc.split()
    for cand in ("cc", "gcc", "clang"):
        path = shutil.which(cand)
        if path:
            return [path]
    test.skipTest("no C compiler available")


class TestArenaRuntime(unittest.TestCase):
    def _compiler(self):
        return _compiler(self)

    def _build(self, cc, td, sanitize=False):
        exe = os.path.join(td, "test_arena_runtime")
        flags = ["-D_GNU_SOURCE", "-DLEASH_ARENA", "-O1", "-g", "-pthread"]
        if sanitize:
            flags += ["-fsanitize=address,undefined", "-fno-omit-frame-pointer"]
        build = subprocess.run(
            cc + ["tests/test_arena_runtime.c", "leash/gc.c"] + flags +
            ["-o", exe],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=300)
        self.assertEqual(build.returncode, 0, build.stderr)
        return exe

    def _run(self, exe):
        run = subprocess.run([exe], cwd=REPO_ROOT, capture_output=True,
                             text=True, timeout=300)
        self.assertEqual(run.returncode, 0, run.stdout + "\n" + run.stderr)
        self.assertIn("all tests passed", run.stdout)

    def test_runtime_harness(self):
        """String identity tag, rooted vec/hash lifetime, tip-growth safety."""
        cc = self._compiler()
        with tempfile.TemporaryDirectory() as td:
            self._run(self._build(cc, td))

    def test_runtime_harness_sanitized(self):
        """Same invariants under ASan/UBSan: catches the shared-string
        realloc use-after-free that a plain build cannot observe."""
        cc = self._compiler()
        if not _sanitizer_ok(cc):
            self.skipTest("compiler does not support ASan/UBSan")
        with tempfile.TemporaryDirectory() as td:
            exe = self._build(cc, td, sanitize=True)
            run = subprocess.run([exe], cwd=REPO_ROOT, capture_output=True,
                                 text=True, timeout=300,
                                 env=dict(os.environ, ASAN_OPTIONS="detect_leaks=0"))
            self.assertEqual(run.returncode, 0, run.stdout + "\n" + run.stderr)
            self.assertIn("all tests passed", run.stdout)


class TestArenaCheckedGuard(unittest.TestCase):
    def test_checked_guard(self):
        """Live and NULL pointers pass; foreign and interior pointers abort."""
        cc = _compiler(self)
        with tempfile.TemporaryDirectory() as td:
            exe = os.path.join(td, "test_arena_checked")
            build = subprocess.run(
                cc + ["tests/test_arena_checked.c", "leash/gc.c",
                      "-D_GNU_SOURCE", "-DLEASH_ARENA", "-O1", "-g", "-pthread",
                      "-o", exe],
                cwd=REPO_ROOT, capture_output=True, text=True, timeout=300)
            self.assertEqual(build.returncode, 0, build.stderr)
            run = subprocess.run([exe], cwd=REPO_ROOT, capture_output=True,
                                 text=True, timeout=300)
            self.assertEqual(run.returncode, 0, run.stdout + "\n" + run.stderr)
            self.assertIn("checked guard ok", run.stdout)


if __name__ == "__main__":
    unittest.main()
