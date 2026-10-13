#!/usr/bin/env python3
"""Native library outputs: `to-dynamic` / `to-static` built by the
self-hosted compiler (no Python fallback since 1.2.0).

Builds a small library, asserts the exported symbols (`add`, `greet`,
`leash_init`, no `main`), links a real C consumer against it, and runs
the result. Linux shared objects must be PIC (no R_X86_64_32 relocs).
Win64 `.dll`/`.lib` cases run under wine and skip cleanly without the
MinGW toolchain.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN_LEASHC = os.path.join(ROOT, "bin", "leashc")
GC_C = os.path.join(ROOT, "leash", "gc.c")
STUBS_LINUX = os.path.join(ROOT, "leash", "cross_compile_stubs.c")

LIB_SRC = """\
pub fnc add(a: int, b: int) : int {
    return a + b;
}
pub fnc greet(name: string) : string {
    return "hi " + name;
}
fnc main() {
    show(add(1, 2));
}
"""

CALLER_SRC = """\
#include <stdio.h>
#include <stdint.h>
extern void leash_init(void);
extern int32_t add(int32_t a, int32_t b);
extern char* greet(char* name);
int main(void) {
    leash_init();
    printf("%d\\n", add(20, 22));
    printf("%s\\n", greet("bob"));
    leash_init();
    printf("%d\\n", add(1, 2));
    return 0;
}
"""

EXPECTED_OUT = "42\nhi bob\n3\n"


def _run(cmd, cwd, timeout=600):
    return subprocess.run(cmd, capture_output=True, text=True,
                          cwd=cwd, timeout=timeout)


class TestNativeLibs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.exists(BIN_LEASHC):
            raise unittest.SkipTest("bin/leashc missing (run `leash self-host`)")
        if not shutil.which("gcc"):
            raise unittest.SkipTest("gcc missing")
        cls.tmp = tempfile.mkdtemp(prefix="leash_native_libs_")
        with open(os.path.join(cls.tmp, "libadd.lsh"), "w") as fh:
            fh.write(LIB_SRC)
        with open(os.path.join(cls.tmp, "caller.c"), "w") as fh:
            fh.write(CALLER_SRC)

    def _compile_lib(self, kind, out_name, target=None):
        # kind: "to-dynamic" | "to-static"
        cmd = [BIN_LEASHC, "compile", os.path.join(self.tmp, "libadd.lsh"),
               kind, "to", os.path.join(self.tmp, out_name)]
        if target:
            cmd += ["--target", target]
        r = _run(cmd, ROOT)
        self.assertEqual(r.returncode, 0,
                         msg=f"lib compile failed: {r.stderr}\n{r.stdout}")
        base = os.path.join(self.tmp, out_name)
        if os.path.exists(base):
            return base
        # The compiler appends the target-gated extension when missing.
        if target == "win64":
            exts = (".lib", ".dll") if kind == "to-static" else (".dll", ".lib")
        elif kind == "to-static":
            exts = (".a",)
        else:
            exts = (".so", ".dylib")
        for ext in exts:
            if os.path.exists(base + ext):
                return base + ext
        self.fail(f"library output missing for {base} (tried {exts})")

    def _nm_defined(self, path):
        r = _run(["nm", path], self.tmp)
        self.assertEqual(r.returncode, 0, msg=f"nm failed: {r.stderr}")
        return r.stdout

    def test_static_lib_symbols(self):
        out = self._compile_lib("to-static", "libadd.a")
        self.assertTrue(out.endswith(".a"))
        if sys.platform == "win32":
            self.skipTest("linux archive layout")
        syms = self._nm_defined(out)
        for name in ("add", "greet", "leash_init"):
            self.assertIn(name, syms, msg=f"missing symbol {name}")
        # Libraries carry no @main wrapper.
        self.assertNotIn(" T main", syms)

    def test_static_lib_link_and_run(self):
        if sys.platform == "win32":
            self.skipTest("linux archive layout")
        lib = self._compile_lib("to-static", "libadd_run.a")
        consumer = os.path.join(self.tmp, "consumer_static")
        r = _run(["gcc", os.path.join(self.tmp, "caller.c"), lib,
                  GC_C, STUBS_LINUX, "-o", consumer], self.tmp)
        self.assertEqual(r.returncode, 0, msg=f"C link failed: {r.stderr}")
        r2 = _run([consumer], self.tmp, timeout=60)
        self.assertEqual(r2.returncode, 0, msg=f"run failed: {r2.stderr}")
        self.assertEqual(r2.stdout, EXPECTED_OUT)

    def test_shared_lib_symbols(self):
        if sys.platform == "win32":
            self.skipTest("linux shared layout")
        out = self._compile_lib("to-dynamic", "libadd.so")
        self.assertTrue(out.endswith(".so"))
        r = _run(["nm", "-D", out], self.tmp)
        self.assertEqual(r.returncode, 0, msg=f"nm -D failed: {r.stderr}")
        for name in ("add", "greet", "leash_init"):
            self.assertIn(name, r.stdout, msg=f"missing dynamic symbol {name}")
        self.assertNotIn(" T main", self._nm_defined(out))

    def test_shared_lib_is_pic(self):
        if sys.platform != "linux":
            self.skipTest("ELF-only check")
        out = self._compile_lib("to-dynamic", "libadd_pic.so")
        r = _run(["readelf", "--relocs", out], self.tmp)
        self.assertEqual(r.returncode, 0)
        self.assertNotIn("R_X86_64_32", r.stdout,
                         msg="non-PIC reloc in shared library")

    def test_shared_lib_link_and_run(self):
        if sys.platform == "win32":
            self.skipTest("linux shared layout")
        lib = self._compile_lib("to-dynamic", "libadd_link.so")
        consumer = os.path.join(self.tmp, "consumer_shared")
        r = _run(["gcc", os.path.join(self.tmp, "caller.c"), lib,
                  GC_C, STUBS_LINUX, "-o", consumer,
                  "-Wl,-rpath," + self.tmp], self.tmp)
        self.assertEqual(r.returncode, 0, msg=f"C link failed: {r.stderr}")
        r2 = _run([consumer], self.tmp, timeout=60)
        self.assertEqual(r2.returncode, 0, msg=f"run failed: {r2.stderr}")
        self.assertEqual(r2.stdout, EXPECTED_OUT)

    def _have_mingw_wine(self):
        return bool(shutil.which("x86_64-w64-mingw32-gcc")
                    and shutil.which("wine"))

    def test_win64_static_lib(self):
        if not self._have_mingw_wine():
            self.skipTest("mingw/wine missing")
        out = self._compile_lib("to-static", "libadd_win", target="win64")
        self.assertTrue(out.endswith(".lib"), msg=f"got {out}")
        r = _run(["x86_64-w64-mingw32-nm", out], self.tmp)
        self.assertEqual(r.returncode, 0)
        for name in ("add", "greet", "leash_init"):
            self.assertIn(name, r.stdout, msg=f"missing symbol {name}")

    def test_win64_shared_lib(self):
        if not self._have_mingw_wine():
            self.skipTest("mingw/wine missing")
        out = self._compile_lib("to-dynamic", "libadd_win", target="win64")
        self.assertTrue(out.endswith(".dll"), msg=f"got {out}")
        r = _run(["x86_64-w64-mingw32-nm", out], self.tmp)
        self.assertEqual(r.returncode, 0)
        for name in ("add", "greet", "leash_init"):
            self.assertIn(name, r.stdout, msg=f"missing symbol {name}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
