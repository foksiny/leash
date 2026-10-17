#!/usr/bin/env python3
"""Cross-compilation test: the self-hosted compiler targets win64.

Builds one small program two ways (self-hosted -> win64,
self-hosted -> native), checks the win64 artifact is a real PE32+
binary, runs it (under wine when the host cannot execute PE files
natively), and asserts identical output.

Skips cleanly when the MinGW cross-compiler or wine is unavailable.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN_LEASHC = os.path.join(ROOT, "bin", "leashc")

# Exercises ints, string concat, float formatting, class allocation
# with a constructor call, and vecs. Fully deterministic output.
PROG = """\
def Counter : class {
    pub count: int;
    Counter(start: int) {
        this.count = start;
    }
    pub fnc bump() : void {
        this.count = this.count + 1;
    }
}
fnc main() {
    show("add=", 20 + 22);
    show("str=", "foo" + "bar");
    show("float=", 2.5 * 4.0);
    c: Counter = create Counter(41);
    c.bump();
    show("counter=", c.count);
    v: vec<int>;
    v.pushb(1);
    v.pushb(2);
    v.pushb(3);
    show("vec=", v.size());
}
"""

EXPECTED_LINES = [
    "add=42",
    "str=foobar",
    "float=10.000000",
    "counter=42",
    "vec=3",
]


def _have_mingw():
    return bool(
        shutil.which("x86_64-w64-mingw32-gcc")
        or shutil.which("x86_64-w64-mingw32-clang")
    )


def _need_wine():
    # A win64 binary runs natively only on Windows.
    return sys.platform != "win32"


def _is_pe_amd64(path):
    """True when `path` is a PE32+ binary for x86-64 (MZ + PE\\0\\0 + AMD64)."""
    try:
        with open(path, "rb") as fh:
            data = fh.read(0x44)
    except OSError:
        return False
    if len(data) < 0x44 or data[0:2] != b"MZ":
        return False
    e_lfanew = int.from_bytes(data[0x3C:0x40], "little")
    if e_lfanew < 0 or e_lfanew + 6 > len(data):
        # Header larger than the probe: read enough for the COFF header.
        try:
            with open(path, "rb") as fh:
                data = fh.read(e_lfanew + 6)
        except OSError:
            return False
    if data[e_lfanew:e_lfanew + 4] != b"PE\x00\x00":
        return False
    machine = int.from_bytes(data[e_lfanew + 4:e_lfanew + 6], "little")
    return machine == 0x8664


class TestCrossWin64(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.exists(BIN_LEASHC):
            raise unittest.SkipTest("bin/leashc missing (run `leash self-host`)")
        if not _have_mingw():
            raise unittest.SkipTest(
                "MinGW cross-compiler missing "
                "(sudo apt install gcc-mingw-w64-x86-64)"
            )
        if _need_wine() and not shutil.which("wine"):
            raise unittest.SkipTest(
                "wine missing (sudo apt install wine) - "
                "needed to run win64 binaries on this host"
            )
        cls.tmp = tempfile.mkdtemp(prefix="leash_xwin_")
        with open(os.path.join(cls.tmp, "xwin.lsh"), "w") as fh:
            fh.write(PROG)

    def _compile(self, compiler, out_base, target=None):
        # NB: the python reference has no `-o` flag (it is silently
        # ignored); `to <path>` sets the output on both compilers.
        cmd = compiler + ["compile", os.path.join(self.tmp, "xwin.lsh"),
                          "to", os.path.join(self.tmp, out_base)]
        if target:
            cmd += ["--target", target]
        r = subprocess.run(cmd, capture_output=True, text=True,
                           cwd=ROOT, timeout=600)
        self.assertEqual(
            r.returncode, 0,
            msg=f"compile failed ({' '.join(cmd)}):\n{r.stderr}\n{r.stdout}",
        )
        return os.path.join(self.tmp, out_base + (".exe" if target == "win64" else ""))

    def _run(self, path):
        if _need_wine() and path.endswith(".exe"):
            cmd = ["wine", path]
        else:
            cmd = [path]
        r = subprocess.run(cmd, capture_output=True, text=True,
                           cwd=self.tmp, timeout=300)
        self.assertEqual(r.returncode, 0, msg=f"run failed: {r.stderr}")
        return r.stdout

    def test_selfhosted_win64(self):
        # The self-hosted compiler produces a real PE32+ binary ...
        exe = self._compile([BIN_LEASHC], "xw_s1", "win64")
        self.assertTrue(os.path.exists(exe), msg="expected .exe suffix")
        self.assertTrue(_is_pe_amd64(exe), msg="not a PE32+ AMD64 binary")
        # ... that runs correctly under wine ...
        out = self._run(exe)
        self.assertEqual(out.splitlines(), EXPECTED_LINES)
        # ... and line-identical (modulo CRLF) to the native build.
        native = self._compile([BIN_LEASHC], "xw_nat")
        self.assertEqual(self._run(native).splitlines(), EXPECTED_LINES)


if __name__ == "__main__":
    unittest.main(verbosity=2)
