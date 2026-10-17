#!/usr/bin/env python3
"""Security regression tests for the leash compiler front end.

Covers the security scan of the compiled CLI:
  - @from native library paths must stay inside the module directory
    (absolute paths, '..' segments and symlink escapes are fatal)
  - transitive (imported) native library linking must at least be warned about
  - output writes must refuse symlinked targets
  - config.lshc `main` must stay inside the project

Run: python3 -m unittest tests.test_security
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN_LEASHC = os.path.join(REPO_ROOT, "bin", "leashc")


def _write(d, name, content):
    p = os.path.join(d, name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(content)
    return p


def _leashc(*args, cwd=None):
    return subprocess.run(
        [BIN_LEASHC, *args],
        capture_output=True, text=True, cwd=cwd, timeout=600,
    )


class TestSecurityScan(unittest.TestCase):
    def setUp(self):
        if not os.path.exists(BIN_LEASHC):
            raise unittest.SkipTest("bin/leashc missing (run `leash self-host`)")
        self.dir = tempfile.mkdtemp(prefix="leash_sec_test_")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _check(self, main_name="main.lsh"):
        r = _leashc("compile", main_name, "--check", cwd=self.dir)
        return r.stdout + r.stderr

    def test_absolute_native_lib_path_is_fatal(self):
        _write(self.dir, "main.lsh",
               '@from("/tmp/evil/libevil.so") { fnc pwn() : void; };\n'
               'fnc main() : void { ignore; }\n')
        out = self._check()
        self.assertIn("E_SECURITY", out)

    def test_dotdot_native_lib_path_is_fatal(self):
        _write(self.dir, "main.lsh",
               '@from("../../evil/libevil.so") { fnc pwn() : void; };\n'
               'fnc main() : void { ignore; }\n')
        out = self._check()
        self.assertIn("E_SECURITY", out)

    def test_home_tilde_native_lib_path_is_fatal(self):
        _write(self.dir, "main.lsh",
               '@from("~/.ssh/evil.so") { fnc pwn() : void; };\n'
               'fnc main() : void { ignore; }\n')
        out = self._check()
        self.assertIn("E_SECURITY", out)

    def test_relative_native_lib_path_in_main_is_clean(self):
        _write(self.dir, "main.lsh",
               '@from("libmine.a") { fnc pwn() : void; };\n'
               'fnc main() : void { ignore; }\n')
        out = self._check()
        self.assertNotIn("E_SECURITY", out)
        self.assertNotIn("W_SECURITY", out)

    def test_transitive_native_import_warns(self):
        _write(self.dir, "dep.lsh",
               '@from("libevil.a") { fnc pwn() : void; };\n'
               'fnc helper() : void { ignore; }\n')
        _write(self.dir, "main.lsh",
               'use dep::*;\n'
               'fnc main() : void { ignore; }\n')
        out = self._check()
        self.assertNotIn("E_SECURITY", out)
        self.assertIn("W_SECURITY", out)
        self.assertIn("libevil.a", out)

    def test_transitive_absolute_native_import_is_fatal(self):
        _write(self.dir, "dep.lsh",
               '@from("/opt/evil/libevil.so") { fnc pwn() : void; };\n'
               'fnc helper() : void { ignore; }\n')
        _write(self.dir, "main.lsh",
               'use dep::*;\n'
               'fnc main() : void { ignore; }\n')
        out = self._check()
        self.assertIn("E_SECURITY", out)

    def test_security_errors_short_circuit(self):
        # A security error must be reported even when the rest of the file
        # would also fail to type-check (security errors take precedence).
        _write(self.dir, "main.lsh",
               '@from("../escape.a") { fnc pwn() : void; };\n'
               'fnc main() : void { show(undefined_variable_xyz); }\n')
        out = self._check()
        self.assertIn("E_SECURITY", out)


class TestSymlinkAndContainmentRegressions(unittest.TestCase):
    """Phase 5: symlink escapes and config containment (M4/M5/M6)."""

    def setUp(self):
        if not os.path.exists(BIN_LEASHC):
            raise unittest.SkipTest("bin/leashc missing (run `leash self-host`)")

    def test_from_symlink_escape_is_error(self):
        # M6: `@from("helper.so")` where helper.so is a symlink outside
        # the module directory passed every text-level check and was
        # handed straight to the linker.
        d = tempfile.mkdtemp(prefix="leash_sec_sym_")
        elsewhere = tempfile.mkdtemp(prefix="leash_sec_sym_out_")
        try:
            # the link target must live OUTSIDE the module directory
            with open(os.path.join(elsewhere, "evil.so"), "wb") as f:
                f.write(b"\x7fELF-fake")
            os.symlink(
                os.path.join(elsewhere, "evil.so"),
                os.path.join(d, "helper.so"),
            )
            _write(d, "main.lsh",
                   '@from("helper.so") { fnc stub() : void; };\n'
                   'fnc main() : void { }\n')
            r = _leashc("compile", "main.lsh", "--check", cwd=d)
            out = r.stdout + r.stderr
            self.assertNotEqual(r.returncode, 0, out)
            self.assertIn("E_SECURITY", out)
            self.assertIn("symlink", out)
        finally:
            shutil.rmtree(d, ignore_errors=True)
            shutil.rmtree(elsewhere, ignore_errors=True)

    def test_output_write_refuses_symlink(self):
        # M4: writing the build artifact through a planted symlink
        # truncates whatever the link points at.
        d = tempfile.mkdtemp(prefix="leash_sec_write_")
        try:
            victim = os.path.join(d, "victim.txt")
            with open(victim, "w") as f:
                f.write("IMPORTANT")
            link = os.path.join(d, "out_bin")
            os.symlink(victim, link)
            _write(d, "main.lsh", 'fnc main() : void { }\n')
            r = _leashc("compile", "main.lsh", "to", "out_bin", cwd=d)
            out = r.stdout + r.stderr
            self.assertNotEqual(r.returncode, 0, out)
            self.assertIn("refusing to write", out)
            with open(victim) as f:
                self.assertEqual(f.read(), "IMPORTANT")  # untouched
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_project_main_containment(self):
        # M5: `main: "../outside.lsh"` used to be accepted, letting a cloned
        # repo's config point the build at files outside the project.
        d = tempfile.mkdtemp(prefix="leash_sec_cfg_")
        try:
            proj = os.path.join(d, "proj")
            os.makedirs(proj)
            with open(os.path.join(d, "outside.lsh"), "w") as f:
                f.write("fnc main() : void { }\n")
            with open(os.path.join(proj, "config.lshc"), "w") as f:
                f.write('main: "../outside.lsh"\n')
            r = _leashc("build", cwd=proj)
            out = r.stdout + r.stderr
            self.assertNotEqual(r.returncode, 0, out)
            self.assertIn("inside the project", out)
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
