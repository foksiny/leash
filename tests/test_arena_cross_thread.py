"""Sanitized cross-thread root visibility tests for the arena runtime."""
import os
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestArenaCrossThread(unittest.TestCase):
    def _build_and_run(self, sanitize):
        cc = os.environ.get("CC", "cc")
        with tempfile.TemporaryDirectory(prefix="leash_xthread_") as td:
            exe = os.path.join(td, "cross")
            cmd = [cc, os.path.join(ROOT, "tests", "test_arena_cross_thread.c"),
                   os.path.join(ROOT, "leash", "gc.c"),
                   "-D_GNU_SOURCE", "-DLEASH_ARENA", "-O1", "-g", "-pthread",
                   "-I" + ROOT, "-I" + os.path.join(ROOT, "leash")]
            if sanitize:
                cmd += ["-fsanitize=address,undefined", "-fno-omit-frame-pointer"]
            cmd += ["-o", exe]
            build = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(build.returncode, 0, build.stderr)
            env = dict(os.environ)
            if sanitize:
                env["ASAN_OPTIONS"] = "detect_leaks=0"
            run = subprocess.run([exe], cwd=ROOT, capture_output=True,
                                 text=True, errors="replace", timeout=120, env=env)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertIn("cross-thread root ok", run.stdout)
            if sanitize:
                self.assertNotIn("AddressSanitizer", run.stderr)
                self.assertNotIn("runtime error:", run.stderr)

    def test_cross_thread_plain(self):
        self._build_and_run(False)

    def test_cross_thread_sanitized(self):
        self._build_and_run(True)


if __name__ == "__main__":
    unittest.main()
