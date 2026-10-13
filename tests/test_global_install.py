#!/usr/bin/env python3
"""Global binary installs: config.lshc `global_install` + `leash rm`.

`leash install` (no path args, inside a project) compiles every
`global_install` entry ({name: src, ...}, single line) to a host binary
in ~/.leash/bin; `leash rm <name>...` removes them after confirming
(empty answer means yes).

HOME is redirected per-test so the real ~/.leash is never touched.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN_LEASHC = os.path.join(ROOT, "bin", "leashc")


def _run(cmd, cwd, env, stdin=None):
    return subprocess.run(cmd, capture_output=True, text=True, input=stdin,
                          cwd=cwd, env=env, timeout=600)


class TestGlobalInstall(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.exists(BIN_LEASHC):
            raise unittest.SkipTest("bin/leashc missing (run `leash self-host`)")

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="leash_gi_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)
        self.proj = os.path.join(self.tmp, "proj")
        src = os.path.join(self.proj, "src")
        os.makedirs(src)
        with open(os.path.join(self.proj, "config.lshc"), "w") as fh:
            fh.write('main: "src/main.lsh"\n'
                     'global_install: {tester: src/main_test.lsh, tester_unsafe: src/main_test_unsafe.lsh}\n')
        with open(os.path.join(src, "main.lsh"), "w") as fh:
            fh.write('fnc main() {\n    show("lib");\n}\n')
        with open(os.path.join(src, "main_test.lsh"), "w") as fh:
            fh.write('fnc main() {\n    show("tester-ok");\n}\n')
        with open(os.path.join(src, "main_test_unsafe.lsh"), "w") as fh:
            fh.write('fnc main() {\n    show("unsafe-ok");\n}\n')
        self.env = dict(os.environ, HOME=self.home,
                        LEASH_RUNTIME=os.path.join(ROOT, "leash"))
        self.bindir = os.path.join(self.home, ".leash", "bin")

    def _install(self):
        return _run([BIN_LEASHC, "install"], self.proj, self.env)

    def _rm(self, names, stdin=None):
        return _run([BIN_LEASHC, "rm"] + names, self.proj, self.env,
                    stdin=stdin)

    def _bin(self, name):
        return os.path.join(self.bindir, name)

    def test_install_builds_and_runs(self):
        r = self._install()
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        for name, want in (("tester", "tester-ok"),
                           ("tester_unsafe", "unsafe-ok")):
            self.assertTrue(os.path.exists(self._bin(name)), msg=name)
            run = _run([self._bin(name)], self.proj, self.env)
            self.assertEqual(run.stdout.splitlines(), [want])

    def test_install_without_field_errors(self):
        with open(os.path.join(self.proj, "config.lshc"), "w") as fh:
            fh.write('main: "src/main.lsh"\n')
        r = self._install()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("global_install", r.stdout + r.stderr)

    def test_install_missing_source_errors(self):
        with open(os.path.join(self.proj, "config.lshc"), "w") as fh:
            fh.write('main: "src/main.lsh"\n'
                     'global_install: {ghost: src/missing.lsh}\n')
        r = self._install()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("missing.lsh", r.stdout + r.stderr)

    def test_rm_default_yes(self):
        self.assertEqual(self._install().returncode, 0)
        r = self._rm(["tester", "tester_unsafe"], stdin="\n")
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        self.assertIn("Removed", r.stdout + r.stderr)
        self.assertFalse(os.path.exists(self._bin("tester")))
        self.assertFalse(os.path.exists(self._bin("tester_unsafe")))

    def test_rm_decline_keeps(self):
        self.assertEqual(self._install().returncode, 0)
        r = self._rm(["tester"], stdin="n\n")
        self.assertEqual(r.returncode, 0)
        self.assertIn("Cancelled", r.stdout + r.stderr)
        self.assertTrue(os.path.exists(self._bin("tester")))

    def test_rm_missing_and_traversal(self):
        self.assertEqual(self._install().returncode, 0)
        r = self._rm(["nosuch"])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Not installed", r.stdout + r.stderr)
        # everything is still there (nothing was prompted for)
        self.assertTrue(os.path.exists(self._bin("tester")))
        for bad in ("../evil", "a/b"):
            r = self._rm([bad])
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("invalid binary name", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
