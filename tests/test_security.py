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


if __name__ == "__main__":
    unittest.main(verbosity=2)
