#!/usr/bin/env python3
"""Adversarial stress tests for the arena memory layout.

Builds tests/test_arena_layout.c against the runtime and checks that every
malformed-header case either aborts cleanly or falls back safely. The
sanitized run turns any residual out-of-bounds read/write into a failure.

Run directly:  python3 tests/test_arena_layout.py
Or via unittest discovery from the repo root:
    python3 -m unittest tests.test_arena_layout
"""
import os
import shutil
import subprocess
import tempfile
import unittest


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _sanitizer_ok(cc):
    import tempfile as tf
    try:
        with tf.TemporaryDirectory() as td:
            src = os.path.join(td, "probe.c")
            exe = os.path.join(td, "probe")
            with open(src, "w") as fh:
                fh.write("int main(void){return 0;}\n")
            r = subprocess.run(cc + ["-fsanitize=address,undefined", src, "-o", exe],
                               capture_output=True, text=True, timeout=60)
            return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


class TestArenaLayout(unittest.TestCase):
    def _compiler(self):
        cc = os.environ.get("CC")
        if cc:
            return cc.split()
        for cand in ("cc", "gcc", "clang"):
            path = shutil.which(cand)
            if path:
                return [path]
        self.skipTest("no C compiler available")

    def _build_run(self, cc, td, sanitize):
        exe = os.path.join(td, "test_arena_layout")
        flags = ["-D_GNU_SOURCE", "-DLEASH_ARENA", "-O1", "-g", "-pthread"]
        if sanitize:
            flags += ["-fsanitize=address,undefined", "-fno-omit-frame-pointer"]
        build = subprocess.run(
            cc + ["tests/test_arena_layout.c", "leash/gc.c"] + flags + ["-o", exe],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=300)
        self.assertEqual(build.returncode, 0, build.stderr)
        env = dict(os.environ)
        if sanitize:
            env["ASAN_OPTIONS"] = "detect_leaks=0"
        run = subprocess.run([exe], cwd=REPO_ROOT, capture_output=True,
                             text=True, timeout=300, env=env)
        out = run.stdout + "\n" + run.stderr
        self.assertEqual(run.returncode, 0, out)
        self.assertIn("all tests passed", out)
        self.assertNotIn("AddressSanitizer", out)
        self.assertNotIn("runtime error:", out)
        for msg in ("forged string realloc", "interior pointer realloc",
                    "oversized realloc", "slice size overflow"):
            self.assertIn(msg + " aborted", out)

    def test_layout_plain(self):
        with tempfile.TemporaryDirectory() as td:
            self._build_run(self._compiler(), td, False)

    def test_layout_sanitized(self):
        cc = self._compiler()
        if not _sanitizer_ok(cc):
            self.skipTest("compiler does not support ASan/UBSan")
        with tempfile.TemporaryDirectory() as td:
            self._build_run(cc, td, True)


if __name__ == "__main__":
    unittest.main()
