#!/usr/bin/env python3
"""Security regression tests for the leash compiler front end.

Covers the security_scan pass added to leash.cli:
  - @from native library paths must stay inside the module directory
  - transitive (imported) native library linking must at least be warned about

Run: python3 -m unittest tests.test_security
"""
import os
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from leash.cli import check_file  # noqa: E402


def _write(d, name, content):
    p = os.path.join(d, name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(content)
    return p


class TestSecurityScan(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="leash_sec_test_")

    def _check(self, main_name="main.lsh"):
        main = os.path.join(self.dir, main_name)
        return check_file(main, verbose=False)

    def test_absolute_native_lib_path_is_fatal(self):
        _write(self.dir, "main.lsh",
               '@from("/tmp/evil/libevil.so") { fnc pwn() : void; };\n'
               'fnc main() : void { ignore; }\n')
        errors, _ = self._check()
        self.assertTrue(errors, "absolute @from path must produce an error")
        self.assertTrue(any(e.code == "E_SECURITY" for e in errors), errors)

    def test_dotdot_native_lib_path_is_fatal(self):
        _write(self.dir, "main.lsh",
               '@from("../../evil/libevil.so") { fnc pwn() : void; };\n'
               'fnc main() : void { ignore; }\n')
        errors, _ = self._check()
        self.assertTrue(any(e.code == "E_SECURITY" for e in errors), errors)

    def test_home_tilde_native_lib_path_is_fatal(self):
        _write(self.dir, "main.lsh",
               '@from("~/.ssh/evil.so") { fnc pwn() : void; };\n'
               'fnc main() : void { ignore; }\n')
        errors, _ = self._check()
        self.assertTrue(any(e.code == "E_SECURITY" for e in errors), errors)

    def test_relative_native_lib_path_in_main_is_clean(self):
        _write(self.dir, "main.lsh",
               '@from("libmine.a") { fnc pwn() : void; };\n'
               'fnc main() : void { ignore; }\n')
        errors, warnings = self._check()
        self.assertEqual([e for e in errors if e.code == "E_SECURITY"], [])
        self.assertEqual([w for w in warnings if w["code"] == "W_SECURITY"], [])

    def test_transitive_native_import_warns(self):
        _write(self.dir, "dep.lsh",
               '@from("libevil.a") { fnc pwn() : void; };\n'
               'fnc helper() : void { ignore; }\n')
        _write(self.dir, "main.lsh",
               'use dep::*;\n'
               'fnc main() : void { ignore; }\n')
        errors, warnings = self._check()
        self.assertEqual([e for e in errors if e.code == "E_SECURITY"], [])
        sec = [w for w in warnings if w["code"] == "W_SECURITY"]
        self.assertEqual(len(sec), 1, warnings)
        self.assertIn("libevil.a", sec[0]["msg"])

    def test_transitive_absolute_native_import_is_fatal(self):
        _write(self.dir, "dep.lsh",
               '@from("/opt/evil/libevil.so") { fnc pwn() : void; };\n'
               'fnc helper() : void { ignore; }\n')
        _write(self.dir, "main.lsh",
               'use dep::*;\n'
               'fnc main() : void { ignore; }\n')
        errors, _ = self._check()
        self.assertTrue(any(e.code == "E_SECURITY" for e in errors), errors)

    def test_security_errors_short_circuit(self):
        # A security error must be reported even when the rest of the file
        # would also fail to type-check (security errors take precedence).
        _write(self.dir, "main.lsh",
               '@from("../escape.a") { fnc pwn() : void; };\n'
               'fnc main() : void { show(undefined_variable_xyz); }\n')
        errors, _ = self._check()
        self.assertTrue(any(e.code == "E_SECURITY" for e in errors), errors)


class TestSymlinkAndContainmentRegressions(unittest.TestCase):
    """Phase 5: symlink escapes and config containment (M4/M5/M6)."""

    def test_from_symlink_escape_is_error(self):
        # M6: `@from("helper.so")` where helper.so is a symlink outside
        # the module directory passed every text-level check and was
        # handed straight to the linker.
        import tempfile, shutil
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
            src_path = os.path.join(d, "main.lsh")
            with open(src_path, "w") as f:
                f.write('@from("helper.so") { fnc stub() : void; };\nfnc main() : void { }\n')
            from leash.cli import security_scan
            from leash.lexer import Lexer
            from leash.parser_l import Parser
            ast = Parser(Lexer(open(src_path).read()).tokenize(), src_path).parse()
            errors, _warnings = security_scan(ast, src_path)
            self.assertTrue(
                any(e.code == "E_SECURITY" and "symlink" in str(e) for e in errors),
                [str(e) for e in errors],
            )
        finally:
            shutil.rmtree(d, ignore_errors=True)
            shutil.rmtree(elsewhere, ignore_errors=True)

    def test_safe_write_bytes_refuses_symlink(self):
        # M4: open(path, "wb") truncates whatever a symlink points at.
        import tempfile, shutil, subprocess
        from leash.cli import _safe_write_bytes
        d = tempfile.mkdtemp(prefix="leash_sec_write_")
        try:
            victim = os.path.join(d, "victim.txt")
            with open(victim, "w") as f:
                f.write("IMPORTANT")
            link = os.path.join(d, "out.o")
            os.symlink(victim, link)
            proc = subprocess.run(
                [sys.executable, "-c",
                 f"import sys; sys.path.insert(0, {REPO_ROOT!r}); "
                 "from leash.cli import _safe_write_bytes; "
                 f"_safe_write_bytes({link!r}, b'x')"],
                capture_output=True, text=True,
            )
            self.assertNotEqual(proc.returncode, 0)
            with open(victim) as f:
                self.assertEqual(f.read(), "IMPORTANT")  # untouched
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_project_main_containment(self):
        # M5: `main: "../outside.lsh"` used to be accepted, letting a cloned
        # repo's config point the build at files outside the project.
        import tempfile, shutil, subprocess
        d = tempfile.mkdtemp(prefix="leash_sec_cfg_")
        try:
            proj = os.path.join(d, "proj")
            os.makedirs(proj)
            outside = os.path.join(d, "outside.lsh")
            with open(outside, "w") as f:
                f.write("fnc main() : void { }\n")
            with open(os.path.join(proj, "config.lshc"), "w") as f:
                f.write('main: "../outside.lsh"\n')
            proc = subprocess.run(
                [sys.executable, "-c",
                 f"import sys; sys.path.insert(0, {REPO_ROOT!r}); "
                 f"import os; os.chdir({proj!r}); "
                 "from leash.cli import read_project_config; read_project_config(os.getcwd())"],
                capture_output=True, text=True,
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("inside the project", proc.stderr)
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
