#!/usr/bin/env python3
"""Worker-shared globals and index stores (1.2.2).

`fusion` globals use sequentially-consistent atomics (spin-waits
observe worker writes); `shared` enforces its one-writer rule;
index assignment stores through vec/fixed/hash paths; prefix and
postfix inc/dec work on variables, fields, and elements.
"""
import os
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN_LEASHC = os.path.join(ROOT, "bin", "leashc")

FUSION_SPIN = """\
fusion out: int = 0;
worker fnc wadd(a int, b int) {
    out = a + b;
}
fnc main() : void {
    spawn wadd(40, 2);
    while out == 0 {
    }
    show(out);
}
"""

FUSION_COUNTER = """\
fusion c: int = 0;
fnc main() : void {
    c += 5;
    c++;
    --c;
    show(c);
}
"""

SHARED_SINGLE = """\
shared s: int = 0;
fnc writer() : void {
    s = 41;
}
fnc main() : void {
    writer();
    show(s + 1);
}
"""

SHARED_TWO_WRITERS = """\
shared s: int = 0;
fnc w1() : void {
    s = 1;
}
fnc w2() : void {
    s = 2;
}
fnc main() : void {
    w1();
    w2();
    show(s);
}
"""

INDEX_STORES = """\
fnc main() : void {
    v: vec<int>;
    v.pushb(10);
    v[0] = 42;
    v.pushb(1);
    v[1] += 5;
    show(v[0]);
    show(v[1]);
    a: int[2] = {1, 2};
    a[1] = 9;
    a[0]++;
    show(a[1]);
    show(a[0]);
    m: hash<string, int>;
    m["k"] = 42;
    m["j"] = 1;
    m["j"] += 2;
    show(m["k"]);
    show(m["j"]);
    m["k"] = 43;
    show(m["k"]);
    v[0]++;
    show(v[0]);
    ms: hash<string, string>;
    ms["a"] = "x";
    ms["a"] += "y";
    show(ms["a"]);
    m2: hash<string, int>;
    m2["n"] = 6;
    m2["n"] *= 7;
    show(m2["n"]);
}
"""

STRING_INDEX_REJECT = """\
fnc main() : void {
    s: string = "abc";
    s[0] = 'X';
    show(s);
}
"""

INCDEC = """\
def C : class {
    n: int;
}
fnc main() : void {
    x: int = 5;
    show(x++);
    show(x);
    show(++x);
    show(x);
    --x;
    show(x);
    c: C = create C();
    c.n = 10;
    c.n++;
    show(c.n);
    ++c.n;
    show(c.n);
    y: int = x++;
    show(y);
    show(x);
    f: float = 1.5;
    show(f++);
    show(f);
}
"""

EXPECTED_INDEX = "42\n6\n9\n2\n42\n3\n43\n43\nxy\n42\n"
EXPECTED_INCDEC = "5\n6\n7\n7\n6\n11\n12\n6\n7\n1.500000\n2.500000\n"


def _run(cmd, cwd, timeout=600):
    return subprocess.run(cmd, capture_output=True, text=True,
                          cwd=cwd, timeout=timeout)


class TestConcurrency(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.exists(BIN_LEASHC):
            raise unittest.SkipTest("bin/leashc missing (run `leash self-host`)")
        cls.tmp = tempfile.mkdtemp(prefix="leash_conc_")

    def _write(self, name, source):
        path = os.path.join(self.tmp, name + ".lsh")
        with open(path, "w") as fh:
            fh.write(source)
        return path

    def _compile(self, compiler, src, out, extra=()):
        cmd = compiler + ["compile", src, "to", out] + list(extra)
        return _run(cmd, ROOT)

    def _compile_and_run(self, compiler, source, name, timeout=120):
        src = self._write(name, source)
        out = os.path.join(self.tmp, name + "_bin")
        r = self._compile(compiler, src, out)
        self.assertEqual(r.returncode, 0,
                         msg=f"compile failed: {r.stderr}\n{r.stdout}")
        r2 = _run([out], self.tmp, timeout=timeout)
        self.assertEqual(r2.returncode, 0, msg=f"run failed: {r2.stderr}")
        return r2.stdout

    def test_fusion_spinwait_selfhosted(self):
        self.assertEqual(
            self._compile_and_run([BIN_LEASHC], FUSION_SPIN, "fuspin"), "42\n")

    def test_fusion_counter_ops(self):
        self.assertEqual(
            self._compile_and_run([BIN_LEASHC], FUSION_COUNTER, "fuscnt"), "5\n")

    def test_shared_single_writer(self):
        self.assertEqual(
            self._compile_and_run([BIN_LEASHC], SHARED_SINGLE, "shsingle"),
            "42\n")

    def test_shared_two_writers_rejected(self):
        src = self._write("shtwo", SHARED_TWO_WRITERS)
        r = self._compile([BIN_LEASHC], src, os.path.join(self.tmp, "shtwo_bin"))
        self.assertNotEqual(r.returncode, 0,
                            msg="compiler accepted two writers")
        self.assertIn("shared variable", r.stdout + r.stderr)

    def test_index_stores(self):
        self.assertEqual(
            self._compile_and_run([BIN_LEASHC], INDEX_STORES, "idxstores"),
            EXPECTED_INDEX)

    def test_string_index_rejected(self):
        src = self._write("stridx", STRING_INDEX_REJECT)
        r = self._compile([BIN_LEASHC], src, os.path.join(self.tmp, "stridx_bin"))
        self.assertNotEqual(r.returncode, 0,
                            msg="compiler accepted string store")
        self.assertIn("mmutable", r.stdout + r.stderr)

    def test_incdec(self):
        self.assertEqual(
            self._compile_and_run([BIN_LEASHC], INCDEC, "incdec"),
            EXPECTED_INCDEC)


if __name__ == "__main__":
    unittest.main(verbosity=2)
