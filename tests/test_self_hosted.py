import os
import subprocess
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN_LEASHC_S1 = os.path.join(REPO_ROOT, "bin", "leashc_stage1")
BIN_LEASHC = os.path.join(REPO_ROOT, "bin", "leashc")


# Hard address-space cap for every child process so a runaway compiler,
# clang, or compiled binary dies with OOM instead of eating host memory.
MEM_LIMIT_MB = 768


def _sh(cmd, cwd=None, timeout=600):
    wrapped = ["bash", "-c",
               f"ulimit -v {MEM_LIMIT_MB * 1024} 2>/dev/null; exec \"$@\"",
               "--"] + list(cmd)
    return subprocess.run(wrapped, cwd=cwd or REPO_ROOT, capture_output=True,
                          text=True, errors="replace", timeout=timeout)


class TestSelfHostedCompiler(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.exists(BIN_LEASHC_S1):
            raise unittest.SkipTest("bin/leashc_stage1 missing (run scripts/bootstrap.sh)")

    def _compile_and_run(self, source, timeout=180):
        tmp_src = os.path.join("/tmp", "leashc_test_src.lsh")
        with open(tmp_src, "w") as fh:
            fh.write(source)
        out_bin = "/tmp/leashc_test_bin"
        r = _sh([BIN_LEASHC, "compile", tmp_src, "-o", out_bin], timeout=timeout)
        if r.returncode != 0:
            self.fail(f"compile failed: {r.stderr}\n{r.stdout}")
        r2 = _sh([out_bin], timeout=30)
        return r2.stdout

    def test_arithmetic(self):
        out = self._compile_and_run(
            'fnc main() : int {\n    x: int = 7 * 6;\n    show(x);\n    return 0;\n}\n')
        self.assertIn("42", out)

    def test_functions(self):
        out = self._compile_and_run(
            'fnc add(a int, b int) : int { return a + b; }\n'
            'fnc main() : int { show(add(2, 3)); return 0; }\n')
        self.assertIn("5", out)

    def test_strings(self):
        out = self._compile_and_run(
            'fnc main() : int {\n    s: string = "foo" + "bar";\n    show(s);\n    return 0;\n}\n')
        self.assertIn("foobar", out)

    def test_structs(self):
        out = self._compile_and_run(
            'def Point : struct {\n    x: int;\n    y: int;\n}\n'
            'fnc main() : int {\n    p: Point = Point { x: 1, y: 2 };\n    show(p.x + p.y);\n    return 0;\n}\n')
        self.assertIn("3", out)

    def test_classes_and_vec(self):
        out = self._compile_and_run(
            'pub def C : class {\n    pub vals: int[];\n'
            '    static pub fnc make() : C { return C {}; }\n'
            '}\n'
            'fnc main() : int {\n    c: C = C.make();\n    foreach i, v in<vector> c.vals { show(v); }\n    return 0;\n}\n')
        self.assertEqual(out.strip(), "")

    def test_loops(self):
        out = self._compile_and_run(
            'fnc main() : int {\n    i: int = 0;\n    while i < 3 { show(i); i = i + 1; }\n    return 0;\n}\n')
        self.assertIn("0", out)
        self.assertIn("2", out)

    def test_foreach_continue(self):
        out = self._compile_and_run(
            'fnc main() : int {\n    v: int[] = {1, 2, 3, 4};\n'
            '    foreach i, x in<array> v { if x == 2 { continue; } show(x); }\n    return 0;\n}\n')
        lines = [l.strip() for l in out.strip().splitlines() if l.strip()]
        self.assertEqual(lines, ["1", "3", "4"])

    def test_static_class_field(self):
        out = self._compile_and_run(
            'pub def K : class {\n    static pub A: int = 7;\n}\n'
            'fnc main() : int { show(K.A); return 0; }\n')
        self.assertIn("7", out)

    def test_self_compile(self):
        # Stage 2 must be able to compile the compiler itself (fixed-point stage)
        r = _sh([BIN_LEASHC, "compile", "compiler/main.lsh", "-o", "/tmp/leashc_self_compile_check"], timeout=900)
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)

    def test_version_and_help(self):
        r = _sh([BIN_LEASHC, "--version"])
        self.assertIn("self-hosted", r.stdout)
        r2 = _sh([BIN_LEASHC, "--help"])
        self.assertIn("compile", r2.stdout)


if __name__ == "__main__":
    unittest.main()
