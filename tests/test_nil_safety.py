#!/usr/bin/env python3
"""Compile-time nil-flow audit and runtime nil-show guard tests."""
import os
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEASH = os.path.join(ROOT, "bin", "leashc")

BAD = """\
fnc get(p: string) : string { return "Jhon Doe"; }
fnc main() : void {
    name: string = get("name? ");
    if name == "Jhon Doe" { name = nil; }
    show("Welcome, {name}!");
}
"""
OK_REASSIGN = """\
fnc get(p: string) : string { return "Jane"; }
fnc main() : void {
    name: string = nil;
    name = get("name? ");
    show("Welcome, {name}!");
}
"""
OK_BRANCH = """\
fnc get(p: string) : string { return "Jane"; }
fnc main() : void {
    name: string = get("name? ");
    if name == "Jhon Doe" { name = nil; }
    else { show("Welcome, {name}!"); }
}
"""
DIRECT_NIL = """\
fnc main() : void {
    s: string = nil;
    show(s);
}
"""
RUNTIME_NIL = """\
fnc main() : void {
    a: vec<string>;
    a.pushb(nil);
    show(a.get(0));
}
"""


class TestNilSafety(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.exists(LEASH):
            raise unittest.SkipTest("bin/leashc missing")

    def _compile(self, src, td, name, extra=None):
        path = os.path.join(td, name + ".lsh")
        exe = os.path.join(td, name)
        with open(path, "w") as fh:
            fh.write(src)
        cmd = [LEASH, "compile", path, "to", exe] + (extra or [])
        return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                              timeout=180), exe

    def test_nil_on_path_to_show_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            r, _ = self._compile(BAD, td, "bad")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("E_NIL_SHOW", r.stdout + r.stderr)

    def test_direct_nil_show_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            r, _ = self._compile(DIRECT_NIL, td, "direct")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("E_NIL_SHOW", r.stdout + r.stderr)

    def test_reassigned_and_branch_nil_compile(self):
        with tempfile.TemporaryDirectory() as td:
            for name, src in (("ok1", OK_REASSIGN), ("ok2", OK_BRANCH)):
                r, _ = self._compile(src, td, name)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_runtime_guard_catches_untracked_nil(self):
        with tempfile.TemporaryDirectory() as td:
            r, exe = self._compile(RUNTIME_NIL, td, "rt")
            self.assertEqual(r.returncode, 0, r.stderr)
            run = subprocess.run([exe], cwd=ROOT, capture_output=True,
                                 text=True, timeout=30)
            self.assertNotEqual(run.returncode, 0)
            self.assertIn("cannot show a nil value", run.stdout + run.stderr)


if __name__ == "__main__":
    unittest.main()
