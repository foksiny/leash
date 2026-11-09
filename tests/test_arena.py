#!/usr/bin/env python3
"""`--arena` parity: programs compiled with `--arena` (experimental
scoped-bump arena, no GC, no per-object free) must produce the same
output as plain GC builds. The arena frees at scope exit, so return
escapes (strings, vecs, hashes, structs, slices, multi-heap tuples),
loop checkpoints, spawn arg blocks and future boxes are the danger
zones: every test here compares arena output byte-for-byte against
plain output.
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

TUPLE_PAIR = """\
fnc make_pair() : (string, string) {
    return "hello", "world";
}
fnc main() {
    x: string = "";
    y: string = "";
    x, y : string, string = make_pair();
    show(x, " ", y);
}
"""

HASH_RET = """\
fnc make_hash() : hash<string, int> {
    h: hash<string, int>;
    h.push("a", 1);
    h.push("b", 2);
    return h;
}
fnc main() {
    h: hash<string, int> = make_hash();
    show(h.size, " ", h["a"], " ", h["b"]);
}
"""

STRUCT_RET = """\
def Point : struct {
    x: int;
    y: int;
    label: string;
}
fnc make_point() : Point {
    p: Point = Point { x: 3, y: 4, label: "pt" };
    return p;
}
fnc main() {
    p: Point = make_point();
    show(p.x, " ", p.y);
}
"""

SLICE_RET = """\
fnc get_slice() : char[] {
    a: char[3] = {'x', 'y', 'z'};
    return a;
}
fnc main() {
    v: char[] = get_slice();
    show(v.size, " ", v[0], " ", v[1], " ", v[2]);
}
"""

LOOP_CHURN = """\
fnc main() {
    total: int = 0;
    churn: int = 0;
    for i: int = 0; i < 20000; i = i + 1 {
        tmp: string = tostring(i % 97) + "-" + tostring(i % 13);
        churn = churn + tmp.bsize;
        total = total + i % 7;
    }
    show(total, " ", churn);
    s: string = "";
    for j: int = 0; j < 1000; j = j + 1 {
        s = s + "x";
    }
    show(" ", s.bsize);
}
"""

CONCURRENCY = """\
worker fnc w(s string) : void {
    show("w:", s.bsize);
}
async fnc giver(s string) : string {
    return s + "!";
}
fnc main() {
    spawn w("hello spawn");
    f: future<string> = giver("hi");
    show(await f);
    show("done");
}
"""

SCALAR_RECURSION = """\
fnc fib_rec(n int) : int {
    if n <= 1 {
        return n;
    }
    return fib_rec(n - 1) + fib_rec(n - 2);
}
fnc main() {
    show(fib_rec(20));
}
"""


def _run(cmd, cwd, timeout=600):
    return subprocess.run(cmd, capture_output=True, text=True,
                          cwd=cwd, timeout=timeout)


class TestArena(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.exists(BIN_LEASHC):
            raise unittest.SkipTest("bin/leashc missing (run `leash self-host`)")
        cls.tmp = tempfile.mkdtemp(prefix="leash_arena_")

    def _build(self, source, name, arena):
        src = os.path.join(self.tmp, name + ".lsh")
        with open(src, "w") as fh:
            fh.write(source)
        out = os.path.join(self.tmp, name + ("_ar" if arena else ""))
        cmd = [BIN_LEASHC, "compile", src, "to", out]
        if arena:
            cmd += ["--arena"]
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
        ar = self._build(source, name + "_ar", True)
        self.assertEqual(self._run_bin(ar), self._run_bin(plain),
                         msg=f"arena/plain output mismatch for {name}")

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

    def test_multi_heap_tuple_return(self):
        self._assert_parity(TUPLE_PAIR, "tuplepair")

    def test_hash_return(self):
        self._assert_parity(HASH_RET, "hashret")

    def test_struct_return(self):
        self._assert_parity(STRUCT_RET, "structret")

    def test_slice_return(self):
        self._assert_parity(SLICE_RET, "sliseret")

    def test_loop_churn_and_accumulator(self):
        self._assert_parity(LOOP_CHURN, "loopchurn")

    def test_concurrency_outputs(self):
        plain = self._build(CONCURRENCY, "conc_plain", False)
        ar = self._build(CONCURRENCY, "conc_ar", True)
        # Worker output order is nondeterministic; compare line sets.
        self.assertEqual(sorted(self._run_bin(ar).splitlines()),
                         sorted(self._run_bin(plain).splitlines()),
                         msg="arena/plain concurrency output mismatch")

    def test_scalar_recursion_output(self):
        self._assert_parity(SCALAR_RECURSION, "fibsmall")

    def test_memory_modes_are_exclusive(self):
        src = os.path.join(self.tmp, "excl.lsh")
        with open(src, "w") as fh:
            fh.write('fnc main() { show("x"); }\n')
        for extra in (["--arena", "--autofree"], ["--arena", "-ngc"]):
            r = _run([BIN_LEASHC, "compile", src, "to",
                      os.path.join(self.tmp, "excl"), *extra], ROOT)
            self.assertNotEqual(r.returncode, 0,
                                msg=f"expected failure for {extra}")
            self.assertIn("exclusive", r.stdout + r.stderr,
                          msg=f"expected exclusivity error for {extra}")

    def test_arena_rejects_jvm(self):
        src = os.path.join(self.tmp, "jvm.lsh")
        with open(src, "w") as fh:
            fh.write('fnc main() { show("x"); }\n')
        r = _run([BIN_LEASHC, "compile", src, "to",
                  os.path.join(self.tmp, "jvm"), "--arena",
                  "--target", "jvm"], ROOT)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("native-only", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
