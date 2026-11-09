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


ESCAPE_CASES = [
    ("push_str_param", """\
fnc mk(v vec<string>, i int) : void {
    v.pushb(tostring(i * 7));
}
fnc main() {
    v: vec<string>;
    for i: int = 0; i < 10; i = i + 1 { mk(v, i); }
    for i: int = 0; i < v.size; i = i + 1 { show(" ", v.get(i)); }
    show("|");
}
"""),
    ("binop_str_param", """\
fnc mk(v vec<string>) : void { v.pushb("ab" + "x" + "y"); }
fnc main() {
    v: vec<string>;
    mk(v);
    show(v.get(0), "|");
}
"""),
    ("local_append_into_param", """\
fnc build(v vec<string>) : void {
    s: string = "";
    s = s + "a" + tostring(11) + "-";
    s = s + "bc";
    v.pushb(s);
}
fnc main() {
    v: vec<string>;
    build(v);
    show(v.get(0), "|");
}
"""),
    ("hash_str_val_param", """\
fnc mk(h hash<string, string>) : void {
    h.push("k", tostring(99));
}
fnc main() {
    h: hash<string, string>;
    mk(h);
    show(h["k"], "|");
}
"""),
    ("vec_in_vec", """\
fnc mk(v vec<vec<int>>, i int) : void {
    t: vec<int>;
    t.pushb(i + 10);
    v.pushb(t);
}
fnc main() {
    v: vec<vec<int>>;
    mk(v, 3);
    show(v.get(0).get(0), "|");
}
"""),
    ("vec_of_vec_of_str", """\
fnc mk(out vec<vec<string>>, i int) : void {
    inner: vec<string>;
    inner.pushb(tostring(i + 100));
    inner.pushb(tostring(i + 200));
    out.pushb(inner);
}
fnc main() {
    v: vec<vec<string>>;
    mk(v, 5);
    show(v.get(0).get(0), " ", v.get(0).get(1), "|");
}
"""),
    ("struct_push_param", """\
def Node : struct { tag: string; n: int; }
fnc mk(v vec<Node>, i int) : void {
    nd: Node = Node { tag: tostring(i + 100), n: i };
    v.pushb(nd);
}
fnc main() {
    v: vec<Node>;
    mk(v, 3);
    show(v.get(0).tag, " ", v.get(0).n, "|");
}
"""),
    ("nested_struct_return", """\
def Inner : struct { a: string; b: int; }
def Outer : struct { inner: Inner; c: string; }
fnc make() : Outer {
    return Outer { inner: Inner { a: tostring(7), b: 1 }, c: "out" };
}
fnc main() {
    o: Outer = make();
    show(o.inner.a, " ", o.inner.b, " ", o.c, "|");
}
"""),
    ("struct_field_store_param", """\
def Node : struct { tag: string; n: int; }
fnc fill(o Node) : void {
    o.tag = tostring(31337);
    o.n = 5;
}
fnc main() {
    nd: Node = Node { tag: "", n: 0 };
    fill(nd);
    show(nd.tag, " ", nd.n, "|");
}
"""),
    ("extendv_in_helper", """\
fnc build(v vec<int>) : void {
    src: vec<int>;
    for i: int = 0; i < 5; i = i + 1 { src.pushb(i * 2); }
    v.extendv(src);
}
fnc main() {
    v: vec<int>;
    build(v);
    sum: int<64> = 0;
    for i: int = 0; i < v.size; i = i + 1 { sum = sum + (int<64>)v.get(i); }
    show(v.size, " ", sum, "|");
}
"""),
    ("global_vec_grow", """\
gv: vec<int> = {0};
gs: vec<string> = {""};
fnc add_global(i int) : void {
    gv.pushb(i * 3);
    gs.pushb(tostring(i));
}
fnc main() {
    for i: int = 0; i < 5; i = i + 1 { add_global(i); }
    show(gv.size, " ", gs.size, " ", gs.get(1), " ", gs.get(4), "|");
}
"""),
    ("set_into_param", """\
fnc mk(v vec<string>) : void {
    v.pushb("a");
    v.pushb("b");
    v.set(0, tostring(555));
}
fnc main() {
    v: vec<string>;
    mk(v);
    show(v.get(0), " ", v.get(1), "|");
}
"""),
    ("pair_return", """\
def Pair : struct { a: string; b: int; }
fnc mk(i int) : Pair {
    return Pair { a: tostring(i + 1), b: i * 2 };
}
fnc main() {
    p: Pair = mk(5);
    show(p.a, " ", p.b, "|");
}
"""),
    ("many_helper_calls", """\
fnc mk(v vec<string>, i int) : void { v.pushb(tostring(i)); }
fnc main() {
    v: vec<string>;
    for i: int = 0; i < 100; i = i + 1 { mk(v, i); }
    show(v.size, " ", v.get(0), " ", v.get(99), "|");
}
"""),
    ("string_slice_store", """\
fnc mk(v vec<string>) : void {
    s: string = "abcdefgh";
    cs: char[] = cstr(s);
    sl: char[] = {'b', 'c', 'd'};
    v.pushb(lstr(sl));
}
fnc main() {
    v: vec<string>;
    mk(v);
    show(v.get(0), "|");
}
"""),
    ("loop_temp_store", """\
fnc mk(v vec<string>) : void {
    for i: int = 0; i < 20; i = i + 1 {
        v.pushb(tostring(i * i));
    }
}
fnc main() {
    v: vec<string>;
    mk(v);
    show(v.get(10), " ", v.get(19), " ", v.get(1), "|");
}
"""),
    ("vec_struct_return", """\
def Node : struct { tag: string; n: int; }
fnc build() : vec<Node> {
    v: vec<Node>;
    v.pushb(Node { tag: tostring(1), n: 1 });
    v.pushb(Node { tag: tostring(2), n: 2 });
    return v;
}
fnc main() {
    v: vec<Node> = build();
    show(v.get(0).tag, " ", v.get(1).tag, " ", v.get(0).n + v.get(1).n, "|");
}
"""),
    ("vec_of_hash_param", """\
fnc mk(v vec<hash<string, int>>, i int) : void {
    h: hash<string, int>;
    h.push("kk" + tostring(i), i + 5);
    v.pushb(h);
}
fnc main() {
    v: vec<hash<string, int>>;
    mk(v, 8);
    show(v.get(0)["kk8"], "|");
}
"""),
    ("string_swap_then_store", """\
fnc mk(v vec<string>) : void {
    v.pushb(tostring(11));
    v.pushb(tostring(22));
    tmp: string = v.get(0);
    v.set(0, v.get(1));
    v.set(1, tmp);
}
fnc main() {
    v: vec<string>;
    mk(v);
    show(v.get(0), " ", v.get(1), "|");
}
"""),
    ("growth_reserve_param", """\
fnc grow(v vec<int>) : void {
    for i: int = 0; i < 100; i = i + 1 { v.pushb(i); }
}
fnc main() {
    v: vec<int>;
    grow(v);
    sum: int<64> = 0;
    for i: int = 0; i < v.size; i = i + 1 { sum = sum + (int<64>)v.get(i); }
    show(v.size, " ", sum, "|");
}
"""),
    ("struct_in_hash", """\
def Node : struct { tag: string; n: int; }
fnc mk(h hash<string, Node>) : void {
    h.push("nd", Node { tag: tostring(77), n: 7 });
}
fnc main() {
    h: hash<string, Node>;
    mk(h);
    nd: Node = h["nd"];
    show(nd.tag, " ", nd.n, "|");
}
"""),
    ("string_ret_in_loop", """\
fnc at(i int) : string {
    return tostring(i * 11) + "!";
}
fnc main() {
    s: string = "";
    for i: int = 0; i < 4; i = i + 1 { s = s + at(i) + "|"; }
    show(s, "|");
}
"""),
    ("struct_with_hash_return", """\
def Box : struct { m: hash<string, int>; tag: string; }
fnc mkhash() : hash<string, int> {
    h: hash<string, int>;
    h.push("a", 1);
    h.push("b", 2);
    return h;
}
fnc make() : Box {
    b: Box;
    b.m = mkhash();
    b.tag = "box";
    return b;
}
fnc main() {
    b: Box = make();
    show(b.m["a"], " ", b.m["b"], " ", b.tag, "|");
}
"""),
    ("concat_mix", """\
fnc mk(v vec<string>) : void {
    v.pushb("X" + tostring(1) + "Y" + tostring(2) + "Z");
}
fnc main() {
    v: vec<string>;
    mk(v);
    show(v.get(0), "|");
}
"""),
    ("helper_self_vec", """\
fnc accumulate(n int) : vec<string> {
    v: vec<string>;
    for i: int = 0; i < n; i = i + 1 { v.pushb(tostring(i)); }
    return v;
}
fnc main() {
    v: vec<string> = accumulate(10);
    v.pushb(tostring(999));
    show(v.get(0), " ", v.get(9), " ", v.get(10), "|");
}
"""),
    ("nested_param_raise", """\
fnc inner(v vec<int>) : void {
    for i: int = 0; i < 50; i = i + 1 { v.pushb(i * 3); }
}
fnc outer(v vec<int>) : void {
    inner(v);
    v.pushb(1000);
}
fnc main() {
    v: vec<int>;
    outer(v);
    show(v.size, " ", v.get(49), " ", v.get(50), "|");
}
"""),
    ("string_struct_field_loop", """\
def Item : struct { name: string; }
fnc mk(v vec<Item>, n int) : void {
    for i: int = 0; i < n; i = i + 1 {
        v.pushb(Item { name: tostring(i) + "" });
    }
}
fnc main() {
    v: vec<Item>;
    mk(v, 7);
    show(v.get(3).name, " ", v.get(6).name, "|");
}
"""),
    ("global_string_field", """\
def Node : struct { tag: string; n: int; }
gn: Node = Node { tag: "init", n: 0 };
fnc make() : void {
    gn = Node { tag: tostring(123), n: 1 };
}
fnc main() {
    show(gn.tag, " ", gn.n, "|");
}
"""),
]


class TestArenaEscapes(unittest.TestCase):
    """Container stores of caller-frame temporaries.

    Storing a freshly allocated heap value into a caller-owned aggregate
    (parameter vec/hash, global vec, struct field) must not dangle when
    the allocating frame exits. Each case byte-for-byte compares arena
    output against the GC build.
    """

    @classmethod
    def setUpClass(cls):
        if not os.path.exists(BIN_LEASHC):
            raise unittest.SkipTest("bin/leashc missing (run `leash self-host`)")
        cls.tmp = tempfile.mkdtemp(prefix="leash_arena_esc_")

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
                         msg=f"compile failed ({name}): {r.stderr}\n{r.stdout}")
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

    def test_escapes(self):
        for name, source in ESCAPE_CASES:
            with self.subTest(case=name):
                self._assert_parity(source, "esc_" + name)
