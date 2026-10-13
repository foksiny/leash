#!/usr/bin/env python3
"""Dispatch guard: a native binary older than the compiler sources must
fall back to Python loudly instead of silently behaving like an older
release (e.g. `leash -v` reporting a stale version after reinstall).
"""
import os
import shutil
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from leash import native as N  # noqa: E402


class TestNativeStaleness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="leash_native_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.src = os.path.join(self.tmp, "compiler")
        os.makedirs(self.src)
        self.bin = os.path.join(self.tmp, "leashc")
        with open(os.path.join(self.src, "a.lsh"), "w") as fh:
            fh.write("// a\n")
        with open(self.bin, "w") as fh:
            fh.write("binary")
        # Pin mtimes explicitly (no sleep-based flakiness).
        self.t0 = 1_700_000_000
        os.utime(os.path.join(self.src, "a.lsh"), (self.t0, self.t0))
        os.utime(self.bin, (self.t0 + 100, self.t0 + 100))

    def test_fresh_binary_not_stale(self):
        self.assertFalse(N._native_is_stale(self.bin, self.src))

    def test_newer_source_is_stale(self):
        os.utime(os.path.join(self.src, "a.lsh"), (self.t0 + 200, self.t0 + 200))
        self.assertTrue(N._native_is_stale(self.bin, self.src))

    def test_missing_sources_not_stale(self):
        # Wheels don't ship compiler/*.lsh: fresh by construction.
        self.assertFalse(N._native_is_stale(
            self.bin, os.path.join(self.tmp, "nosuchdir")))
        empt = os.path.join(self.tmp, "empty")
        os.makedirs(empt)
        self.assertFalse(N._native_is_stale(self.bin, empt))

    def test_missing_binary_not_stale(self):
        # Handled by have_native(); the predicate fails open.
        self.assertFalse(N._native_is_stale(
            os.path.join(self.tmp, "nosuchbin"), self.src))

    def test_non_lsh_files_ignored(self):
        with open(os.path.join(self.src, "notes.txt"), "w") as fh:
            fh.write("x\n")
        os.utime(os.path.join(self.src, "notes.txt"), (self.t0 + 200, self.t0 + 200))
        self.assertFalse(N._native_is_stale(self.bin, self.src))


if __name__ == "__main__":
    unittest.main(verbosity=2)
