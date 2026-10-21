#!/usr/bin/env python3
"""Shared variables: `fnc f(...) : T | x: int, y: string { ... }`.

A shared variable is program-wide storage with a declared type that only
the functions naming it in their signature may touch. The tests cover the
native backend, the JVM backend, the cross targets, and the diagnostics for
misuse (access from a function that does not declare it, conflicting
declarations, a name that collides with a program global).
"""
import os
import shutil
import subprocess
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN_LEASHC = os.path.join(REPO_ROOT, "bin", "leashc")
EXAMPLE = os.path.join(REPO_ROOT, "examples", "shared_vars.lsh")

EXAMPLE_SRC = """\
fnc add(a int, b int) : void | result: int {
    result = a + b;
}

fnc gresult() : int | result: int {
    return result;
}

fnc main {
    add(10, 20);
    show(gresult());

    add(gresult(), 20);
    show(gresult());
}
"""

# Two shared variables in one signature, of three different types.
MULTI_SRC = """\
fnc put() : void | n: int, label: string, items: vec<int> {
    n = 7;
    label = "seven";
    items.pushb(n);
}

fnc get_n() : int | n: int {
    return n;
}

fnc get_label() : string | label: string {
    return label;
}

fnc get_items() : int | items: vec<int> {
    return items.get(0);
}

fnc main {
    put();
    show(tostring(get_n()));
    show(get_label());
    show(tostring(get_items()));
}
"""


def _leashc(*args, cwd=None, timeout=600):
    return subprocess.run(
        [BIN_LEASHC, *args],
        capture_output=True, text=True, cwd=cwd, timeout=timeout,
        errors="replace",
    )


def _have(tool):
    return shutil.which(tool) is not None


class SharedVarsTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, src):
        path = os.path.join(self.dir, name)
        with open(path, "w") as fh:
            fh.write(src)
        return path

    def run_src(self, name, src):
        """Compile-and-run a program natively; return (rc, stdout)."""
        path = self.write(name, src)
        r = _leashc("run", path, cwd=self.dir)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out = r.stdout
        if "--- Executed at" in out:
            out = out.split("--- Executed at")[0]
        return r.returncode, out.strip()


@unittest.skipUnless(os.path.exists(BIN_LEASHC), "bin/leashc not built")
class SharedVarsNativeTest(SharedVarsTestCase):
    def test_example_file_runs(self):
        r = _leashc("run", EXAMPLE, cwd=self.dir)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("30", r.stdout)
        self.assertIn("50", r.stdout)

    def test_shared_value_crosses_functions(self):
        _, out = self.run_src("cross.lsh", EXAMPLE_SRC)
        self.assertEqual(out.split("\n"), ["30", "50"])

    def test_multiple_shared_variables_and_types(self):
        _, out = self.run_src("multi.lsh", MULTI_SRC)
        self.assertEqual(out.split("\n"), ["7", "seven", "7"])

    def test_shared_variable_is_zero_initialized(self):
        src = """\
fnc read() : int | v: int {
    return v;
}

fnc main {
    show(tostring(read()));
}
"""
        _, out = self.run_src("zero.lsh", src)
        self.assertEqual(out.strip(), "0")

    def test_local_shadows_shared_variable(self):
        src = """\
fnc f(v int) : int | v: int {
    return v;
}

fnc main {
    show(tostring(f(5)));
}
"""
        _, out = self.run_src("shadow.lsh", src)
        self.assertEqual(out.strip(), "5")

    def test_shared_variable_in_class_method(self):
        src = """\
def Counter : class {
    pub n: int;
}

fnc bump(c: Counter) : void | total: int {
    total = total + c.n;
}

fnc read_total() : int | total: int {
    return total;
}

fnc main {
    c: Counter = Counter{};
    c.n = 4;
    bump(c);
    show(tostring(read_total()));
}
"""
        _, out = self.run_src("cls.lsh", src)
        self.assertEqual(out.strip(), "4")

    def test_access_from_undeclared_function_is_an_error(self):
        src = """\
fnc setit() : void | result: int {
    result = 42;
}

fnc peek() : int {
    return result;
}

fnc main {
    setit();
    show(tostring(peek()));
}
"""
        path = self.write("bad.lsh", src)
        r = _leashc("check", path, cwd=self.dir)
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("not accessible", r.stdout + r.stderr)

    def test_conflicting_declarations_are_an_error(self):
        src = """\
fnc a() : void | result: int {
    result = 1;
}

fnc b() : void | result: string {
    result = "x";
}

fnc main {
    a();
    b();
}
"""
        path = self.write("conflict.lsh", src)
        r = _leashc("check", path, cwd=self.dir)
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("conflicting types", r.stdout + r.stderr)

    def test_name_clash_with_global_is_an_error(self):
        src = """\
result: int = 5;

fnc a() : void | result: int {
    result = 1;
}

fnc main {
    a();
}
"""
        path = self.write("clash.lsh", src)
        r = _leashc("check", path, cwd=self.dir)
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("conflicts with the global variable", r.stdout + r.stderr)

    def test_unknown_type_is_an_error(self):
        src = """\
fnc a() : void | result: NoSuchType {
    result = 1;
}

fnc main {
    a();
}
"""
        path = self.write("badtype.lsh", src)
        r = _leashc("check", path, cwd=self.dir)
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("unknown type", r.stdout + r.stderr)

    def test_imut_shared_variable_is_an_error(self):
        src = """\
fnc a() : void | result: imut int {
    result = 1;
}

fnc main {
    a();
}
"""
        path = self.write("imut.lsh", src)
        r = _leashc("check", path, cwd=self.dir)
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("cannot be declared `imut`", r.stdout + r.stderr)


@unittest.skipUnless(os.path.exists(BIN_LEASHC), "bin/leashc not built")
class SharedVarsJvmTest(SharedVarsTestCase):
    def test_jvm_backend(self):
        java = shutil.which("java")
        if not java:
            self.skipTest("java not installed")
        path = self.write("jvm.lsh", EXAMPLE_SRC)
        r = _leashc("compile", path, "--target", "jvm", cwd=self.dir)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out = os.path.join(self.dir, "jvm.class")
        self.assertTrue(os.path.exists(out), os.listdir(self.dir))
        res = subprocess.run([java, "-cp", self.dir, "jvm"],
                             capture_output=True, text=True, timeout=300)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(res.stdout.strip().split("\n"), ["30", "50"])

    def test_jvm_multiple_shared_variables(self):
        java = shutil.which("java")
        if not java:
            self.skipTest("java not installed")
        path = self.write("jvmmulti.lsh", MULTI_SRC)
        r = _leashc("compile", path, "--target", "jvm", cwd=self.dir)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        res = subprocess.run([java, "-cp", self.dir, "jvmmulti"],
                             capture_output=True, text=True, timeout=300)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(res.stdout.strip().split("\n"), ["7", "seven", "7"])


@unittest.skipUnless(os.path.exists(BIN_LEASHC), "bin/leashc not built")
class SharedVarsCrossTargetTest(SharedVarsTestCase):
    """The feature must work for every cross-compilation target."""

    def _compile_target(self, target, out_name):
        path = self.write("cross.lsh", EXAMPLE_SRC)
        r = _leashc("compile", path, "to", os.path.join(self.dir, out_name),
                    "--target", target, cwd=self.dir)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return os.path.join(self.dir, out_name)

    def test_linux32(self):
        exe = self._compile_target("linux32", "sv32")
        res = subprocess.run([exe], capture_output=True, text=True, timeout=300)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(res.stdout.strip().split("\n"), ["30", "50"])

    def test_linux_arm(self):
        exe = self._compile_target("linux-arm", "svarm")
        qemu = shutil.which("qemu-aarch64") or shutil.which("qemu-aarch64-static")
        if not qemu:
            self.skipTest("qemu-aarch64 not installed")
        res = subprocess.run([qemu, "-L", "/usr/aarch64-linux-gnu", exe],
                             capture_output=True, text=True, timeout=300)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(res.stdout.strip().split("\n"), ["30", "50"])

    def test_win64(self):
        if not (_have("x86_64-w64-mingw32-gcc") or _have("x86_64-w64-mingw32-clang")):
            self.skipTest("MinGW cross-compiler missing")
        exe = self._compile_target("win64", "svwin64.exe")
        with open(exe, "rb") as fh:
            self.assertEqual(fh.read(2), b"MZ")
        if shutil.which("wine"):
            res = subprocess.run(["wine", exe], capture_output=True, text=True,
                                 timeout=600)
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            self.assertEqual(res.stdout.strip().split("\n"), ["30", "50"])


if __name__ == "__main__":
    unittest.main()
