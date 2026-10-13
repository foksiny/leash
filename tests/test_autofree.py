#!/usr/bin/env python3
"""`--autofree` parity: programs compiled with `--autofree` (GC-free,
malloc-backed, freed at function exit) must produce the same output as
plain builds. Returned values stay live for the caller; allocations
owned by heap objects (struct/class fields) are never swept out from
under their owner.
"""
import os
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN_LEASHC = os.path.join(ROOT, "bin", "leashc")

STRING_LOOP = """\
fnc work(n: int) : string {
    s: string = "";
    i: int = 0;
    while i < n {
        s = s + "x";
        i = i + 1;
    }
    return s;
}
fnc main() {
    show(work(50));
    v: vec<int>;
    v.pushb(1);
    v.pushb(2);
    show(v.size());
    m: hash<string, int>;
    m.push("k", 7);
    show(m["k"]);
    show(tostring(12345));
}
"""

CLASS_FIELDS = """\
def Box : class {
    items: vec<int>;
    static pub fnc new() : Box {
        return Box {};
    }
    pub fnc add(v int) : void {
        this.items.pushb(v);
    }
}
fnc main() : void {
    b: Box = Box.new();
    b.add(10);
    b.add(20);
    show("done");
}
"""

GENERIC_CLASS = """\
def Box : class<T1> {
    items: vec<T1>;
    static pub fnc new() : Box {
        return Box {};
    }
    pub fnc add(v T1) : void {
        this.items.pushb(v);
    }
    pub fnc get(i int) : T1 {
        return this.items.get(i);
    }
}
fnc main() : void {
    b: Box<int> = Box.new();
    b.add(10);
    b.add(20);
    show(b.get(0));
    show(b.get(1));
}
"""

EARLY_MULTI = """\
fnc early(flag: bool) : string {
    if flag { return "early"; }
    return "late" + "-path";
}
fnc multi() : (int, string) {
    return 1, "one";
}
fnc main() {
    show(early(true), " ", early(false));
    n, s : int, string = multi();
    show(n, " ", s);
}
"""

EXEC_PROG = """\
fnc main() : void {
    exit_c: string = exec("echo Hello World", "code");
    show(exit_c);
}
"""


def _run(cmd, cwd, timeout=600):
    return subprocess.run(cmd, capture_output=True, text=True,
                          cwd=cwd, timeout=timeout)


class TestAutofree(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.exists(BIN_LEASHC):
            raise unittest.SkipTest("bin/leashc missing (run `leash self-host`)")
        cls.tmp = tempfile.mkdtemp(prefix="leash_autofree_")

    def _build(self, source, name, autofree):
        src = os.path.join(self.tmp, name + ".lsh")
        with open(src, "w") as fh:
            fh.write(source)
        out = os.path.join(self.tmp, name + ("_af" if autofree else ""))
        cmd = [BIN_LEASHC, "compile", src, "to", out]
        if autofree:
            cmd += ["--autofree"]
        r = _run(cmd, ROOT)
        self.assertEqual(r.returncode, 0,
                         msg=f"compile failed: {r.stderr}\n{r.stdout}")
        return out

    def _run_bin(self, path):
        r = _run([path], self.tmp, timeout=120)
        self.assertEqual(r.returncode, 0, msg=f"run failed: {r.stderr}")
        return r.stdout

    def _assert_parity(self, source, name):
        plain = self._build(source, name + "_plain", False)
        af = self._build(source, name + "_af", True)
        self.assertEqual(self._run_bin(af), self._run_bin(plain),
                         msg=f"autofree/plain output mismatch for {name}")

    def test_string_loop_return_stays_live(self):
        self._assert_parity(STRING_LOOP, "strloop")

    def test_early_and_multi_return(self):
        self._assert_parity(EARLY_MULTI, "earlymulti")

    def test_class_vec_fields_not_swept(self):
        self._assert_parity(CLASS_FIELDS, "classfields")

    def test_generic_class(self):
        self._assert_parity(GENERIC_CLASS, "genclass")

    def test_exec_result(self):
        self._assert_parity(EXEC_PROG, "execprog")


if __name__ == "__main__":
    unittest.main(verbosity=2)
