"""Global-install (`leash install`) regression tests.

Covers the reported install breakage:
  1. `leash install` ships the runtime (~/.leash/leash, including the JVM
     shims) alongside the binaries, so an installed toolchain compiles
     and runs from any directory — not just from a repository checkout.
  2. A bare argv[0] resolves even when the PATH entry uses a literal `~`
     (`export PATH="~/.leash/bin:$PATH"`): bash finds the binary, so the
     compiler must find itself without delegating to `command -v`.
  3. `leash install <dir>` works with any argv[0] and streams the inner
     install's output.

HOME is redirected per-test so the real ~/.leash is never touched.
"""
import os
import shutil
import subprocess
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN_LEASHC = os.path.join(REPO_ROOT, "bin", "leashc")

TOOL_MAIN = 'fnc main() : int {\n    show("tool-ok");\n    return 0;\n}\n'
HELLO_MAIN = 'fnc main() : int {\n    show("run-ok");\n    return 0;\n}\n'

CONFIG = (
    'main: "src/main.lsh"\n'
    "global_install: {mytool: src/main.lsh}\n"
)


def _write(path, text):
    with open(path, "w") as f:
        f.write(text)


def _run(cmd, cwd, env, timeout=600):
    return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True,
                          text=True, errors="replace", timeout=timeout)


@unittest.skipUnless(os.path.exists(BIN_LEASHC), "bin/leashc not built")
class GlobalInstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = os.path.join(self.tmp.name, "home")
        os.makedirs(self.home)
        # Sandbox HOME; drop LEASH_RUNTIME so discovery is hermetic.
        self.env = dict(os.environ, HOME=self.home)
        self.env.pop("LEASH_RUNTIME", None)
        # A tiny project with one global_install entry.
        self.proj = os.path.join(self.tmp.name, "proj")
        os.makedirs(os.path.join(self.proj, "src"))
        _write(os.path.join(self.proj, "src", "main.lsh"), TOOL_MAIN)
        _write(os.path.join(self.proj, "config.lshc"), CONFIG)

    def tearDown(self):
        self.tmp.cleanup()

    def _install(self, cwd=None):
        return _run([BIN_LEASHC, "install"], cwd or self.proj, self.env)

    def _scratch(self, text=HELLO_MAIN):
        scratch = os.path.join(self.tmp.name, "scratch")
        os.makedirs(scratch, exist_ok=True)
        _write(os.path.join(scratch, "hello.lsh"), text)
        return scratch

    def test_install_ships_runtime_and_binaries(self):
        r = self._install()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(
            os.path.isfile(os.path.join(self.home, ".leash/leash/gc.c")),
            "runtime not installed to ~/.leash/leash")
        self.assertTrue(
            os.path.isfile(os.path.join(self.home,
                                        ".leash/leash/jvm/LeashHttp.class")),
            "JVM shims not installed to ~/.leash/leash/jvm")
        self.assertIn("Installed the Leash runtime", r.stdout)
        tool = os.path.join(self.home, ".leash/bin/mytool")
        self.assertTrue(os.path.isfile(tool), r.stdout + r.stderr)
        r2 = _run([tool], self.tmp.name, self.env, timeout=60)
        self.assertIn("tool-ok", r2.stdout)

    def test_installed_compiler_runs_from_any_directory(self):
        r = self._install()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        # Simulate an installed toolchain: put a compiler binary next to
        # the freshly installed runtime, then use it from an unrelated
        # cwd. The runtime must be found relative to the executable.
        bin_dir = os.path.join(self.home, ".leash/bin")
        shutil.copy(BIN_LEASHC, os.path.join(bin_dir, "leashc"))
        scratch = self._scratch()
        r2 = _run([os.path.join(bin_dir, "leashc"), "run", "hello.lsh"],
                  scratch, self.env)
        self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
        self.assertIn("run-ok", r2.stdout)

    def test_compiler_found_via_home_install_root(self):
        # A binary copied to an arbitrary PATH directory (no sibling
        # runtime) still works once ~/.leash/leash exists.
        r = self._install()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        elsewhere = os.path.join(self.tmp.name, "elsewhere")
        os.makedirs(elsewhere)
        exe = os.path.join(elsewhere, "leashc")
        shutil.copy(BIN_LEASHC, exe)
        scratch = self._scratch()
        r2 = _run([exe, "run", "hello.lsh"], scratch, self.env)
        self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
        self.assertIn("run-ok", r2.stdout)

    @unittest.skipUnless(shutil.which("bash"), "bash required")
    def test_bare_name_with_literal_tilde_path_entry(self):
        # The reported breakage: `export PATH="~/.leash/bin:$PATH"` keeps
        # the tilde literal. bash still finds `leash` (it expands `~` in
        # PATH during lookup), so argv[0] arrives bare and the compiler
        # must resolve it itself — `command -v` under dash would miss
        # the entry and runtime discovery would fail.
        r = self._install()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        bin_dir = os.path.join(self.home, ".leash/bin")
        shutil.copy(BIN_LEASHC, os.path.join(bin_dir, "leash"))
        scratch = self._scratch()
        env = dict(self.env)
        env["PATH"] = "~/.leash/bin:/usr/bin:/bin"
        r2 = _run(["bash", "-c", "leash run hello.lsh"], scratch, env)
        self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
        self.assertIn("run-ok", r2.stdout)

    def test_install_dir_arg_reports_progress(self):
        # `leash install <dir>` re-enters through the resolved
        # executable; a bare argv[0] used to crash the child shell with
        # `sh: 1: <cwd>/leash: not found`, and the inner install's
        # output used to be swallowed by the exit-code capture.
        r = _run([BIN_LEASHC, "install", self.proj], self.home, self.env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Installed mytool", r.stdout)
        self.assertIn("Installed the Leash runtime", r.stdout)


if __name__ == "__main__":
    unittest.main()
