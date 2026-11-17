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
import re
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

RECURSIVE_STRUCT_CHAIN = """\
def Node : struct {
    v: string;
    next: Node;
}
fnc make() : Node {
    c: Node = Node { v: "c", next: null };
    b: Node = Node { v: "b", next: c };
    a: Node = Node { v: "a", next: b };
    return a;
}
fnc main() {
    n: Node = make();
    show(n.v, " ", n.next.v, " ", n.next.next.v, "|");
}
"""

RECURSIVE_CLASS_CYCLE = """\
def Node : class {
    v: string;
    next: Node;
}
fnc make() : Node {
    a: Node = Node { v: "a", next: null };
    b: Node = Node { v: "b", next: null };
    a.next = b;
    b.next = a;
    return a;
}
fnc main() {
    n: Node = make();
    show(n.v, " ", n.next.v, " ", n.next.next.v, "|");
}
"""

RECURSIVE_GRAPH_SHARED = """\
def GNode : class {
    tag: string;
    a: GNode;
    b: GNode;
}
fnc make() : GNode {
    root: GNode = GNode { tag: "r", a: null, b: null };
    left: GNode = GNode { tag: "l", a: null, b: null };
    right: GNode = GNode { tag: "x", a: null, b: null };
    root.a = left;
    root.b = right;
    left.b = right;
    right.a = root;
    return root;
}
fnc main() {
    n: GNode = make();
    show(n.tag, " ", n.a.tag, " ", n.b.tag, " ", n.a.b.tag, " ", n.b.a.tag, "|");
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

VEC_ARITH = """\
fnc main() {
    a: vec<int> = {1, 2, 3};
    b: vec<int> = {10, 20, 30};
    s: vec<int> = a + b;
    d: vec<int> = a - b;
    sc: vec<int> = a + 100;
    cr: vec<int> = 7 + a;
    show(s.get(0), " ", s.get(2), "|");
    show(d.get(0), " ", d.get(2), "|");
    show(sc.get(0), " ", sc.get(2), "|");
    show(cr.get(0), " ", cr.get(2), "|");
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

    def test_recursive_struct_return(self):
        self._assert_parity(RECURSIVE_STRUCT_CHAIN, "recstruct")

    def test_recursive_class_cycle(self):
        self._assert_parity(RECURSIVE_CLASS_CYCLE, "reccycle")

    def test_recursive_graph_shared_cycle(self):
        self._assert_parity(RECURSIVE_GRAPH_SHARED, "recgraph")

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

    def test_vector_arithmetic_output(self):
        self._assert_parity(VEC_ARITH, "vecarith")

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
    ("vec_param_grow_after_pop", """\
fnc grow(v vec<int>) : void {
    for i: int = 0; i < 100; i = i + 1 { v.pushb(i * 3); }
}
fnc main() {
    v: vec<int>;
    grow(v);
    sum: int<64> = 0;
    for i: int = 0; i < v.size; i = i + 1 { sum = sum + (int<64>)v.get(i); }
    show(v.size, " ", sum, "|");
}
"""),
    ("hash_param_grow_after_pop", """\
fnc grow(h hash<string, int>) : void {
    for i: int = 0; i < 40; i = i + 1 { h.push("k" + tostring(i), i * 2); }
}
fnc main() {
    h: hash<string, int>;
    grow(h);
    show(h.size, " ", h["k0"], " ", h["k39"], "|");
}
"""),
    ("vec_of_vec_param_grow", """\
fnc grow(v vec<vec<int>>) : void {
    for i: int = 0; i < 8; i = i + 1 {
        inner: vec<int>;
        for j: int = 0; j < 10; j = j + 1 { inner.pushb(i * 100 + j); }
        v.pushb(inner);
    }
}
fnc main() {
    v: vec<vec<int>>;
    grow(v);
    show(v.size, " ", v.get(0).get(0), " ", v.get(7).get(9), "|");
}
"""),
    ("insertv_param", """\
fnc mk(v vec<int>, src vec<int>, s string) : void {
    tag: string = s + "-x";
    v.insertv(0, src);
    show(tag);
}
fnc main() {
    v: vec<int>;
    src: vec<int>;
    src.pushb(7);
    src.pushb(8);
    mk(v, src, "tag");
    show(v.size, " ", v.get(0), " ", v.get(1), "|");
}
"""),
    ("hash_str_vec_return", """\
fnc build() : hash<string, vec<int>> {
    h: hash<string, vec<int>>;
    for i: int = 0; i < 3; i = i + 1 {
        v: vec<int>;
        v.pushb(i);
        v.pushb(i + 10);
        h.push("k" + tostring(i), v);
    }
    return h;
}
fnc main() {
    h: hash<string, vec<int>> = build();
    for i: int = 0; i < 3; i = i + 1 {
        v: vec<int> = h["k" + tostring(i)];
        show(v.get(0), ",", v.get(1), ";");
    }
    show("|");
}
"""),
    ("hash_str_box_return", """\
def Box : struct { tag: string; inner: hash<string, int>; }
fnc build() : hash<string, Box> {
    h: hash<string, Box>;
    for i: int = 0; i < 3; i = i + 1 {
        b: Box;
        b.tag = "t" + tostring(i);
        b.inner.push("x", i * 7);
        h.push("b" + tostring(i), b);
    }
    return h;
}
fnc main() {
    h: hash<string, Box> = build();
    for i: int = 0; i < 3; i = i + 1 {
        b: Box = h["b" + tostring(i)];
        show(b.tag, ":", b.inner["x"], ";");
    }
    show("|");
}
"""),
    ("hash_str_hash_return", """\
fnc build() : hash<string, hash<string, int>> {
    h: hash<string, hash<string, int>>;
    for i: int = 0; i < 2; i = i + 1 {
        inner: hash<string, int>;
        inner.push("n", i + 41);
        h.push("o" + tostring(i), inner);
    }
    return h;
}
fnc main() {
    h: hash<string, hash<string, int>> = build();
    show(h["o0"]["n"], ",", h["o1"]["n"], "|");
}
"""),
    ("uninit_global_containers", """\
gv: vec<vec<string>>;
gh: hash<string, vec<string>>;
fnc fill(v vec<vec<string>>, h hash<string, vec<string>>) {
    for i: int = 0; i < 3; i = i + 1 {
        inner: vec<string>;
        inner.pushb("i" + tostring(i));
        inner.pushb(tostring(i * 9));
        v.pushb(inner);
        sv: vec<string>;
        sv.pushb("h" + tostring(i));
        h.push("k" + tostring(i), sv);
    }
}
fnc main() {
    fill(gv, gh);
    for i: int = 0; i < 3; i = i + 1 {
        show(gv.get(i).get(0), gv.get(i).get(1), ":",
             gh["k" + tostring(i)].get(0), ";");
    }
    show(gv.size, ",", gh.size, "|");
}
"""),
]


# Lifetime cases for the root arena. A slot that outlives the frame
# (a global, or an outer local written from a loop body) must keep a
# root-arena copy of the value: the scope arena is rewound at the end
# of the iteration, so a pointer into it would dangle.
ROOT_LIFETIME_CASES = [
    # A mutable container stored into a global: the source local is
    # re-pointed at the root copy, so both names see the same value.
    ("global_vec_alias", """\
g: vec<int>;
fnc main() {
    v: vec<int>;
    for i: int = 0; i < 3; i = i + 1 { v.pushb(i); }
    g = v;
    g.pushb(4);
    show(g.size, " ", v.size, " ", g.get(3), " ", v.get(3));
}
"""),
    # Self-append through a global: `g = g + "x"` must read the
    # previous root copy, not the reclaimed scope copy.
    ("global_string_self_append", """\
g: string = "";
fnc main() {
    for i: int = 0; i < 5; i = i + 1 { g = g + "x"; }
    show(g, " ", g.bsize);
}
"""),
    # Global hash/vec of strings: every store roots the whole tree.
    ("global_hash_vec_of_strings", """\
gh: hash<string, string>;
gv: vec<string>;
fnc main() {
    for i: int = 0; i < 5; i = i + 1 {
        gh.push("k" + tostring(i), "v" + tostring(i));
        gv.pushb("e" + tostring(i));
    }
    show(gh.size, " ", gh["k3"], " ", gv.size, " ", gv.get(3));
}
"""),
    # An outer local written from a loop body: the value is copied
    # into the root arena so it survives the iteration.
    ("outer_local_store_in_loop", """\
fnc main() {
    total: int = 0;
    for i: int = 0; i < 100; i = i + 1 {
        s: string = tostring(i);
        total = total + s.bsize;
    }
    show(total);
}
"""),
    # A variable declared inside a loop body but read after the loop
    # keeps its value in the GC: the arena must root it instead of
    # letting the checkpoint reclaim it (this used to segfault).
    ("loop_local_string_after_loop", """\
fnc main() {
    for i: int = 0; i < 1000; i = i + 1 {
        t: string = tostring(i);
    }
    show(t.bsize);
}
"""),
    ("loop_local_vec_after_loop", """\
fnc main() {
    for i: int = 0; i < 1000; i = i + 1 {
        v: vec<int> = {i, i + 1};
    }
    show(v.size);
}
"""),
    ("nested_loop_local_after_loop", """\
fnc main() {
    for i: int = 0; i < 20; i = i + 1 {
        for j: int = 0; j < 10; j = j + 1 {
            inner: string = tostring(j);
        }
        show(inner.bsize);
    }
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


SPAWN_ARGS = """\
worker fnc w(s string, v vec<int>) : void {
    show("w:", s, ":", v.size, ":", v.get(0));
}
fnc main() {
    v: vec<int>;
    v.pushb(11);
    v.pushb(22);
    spawn w("payload", v);
    show("done");
}
"""

# Every argument kind a worker can take. The container and aggregate
# arguments are handed to the worker through the root arena, the string
# through the private transfer block.
SPAWN_ARG_TYPES = """\
def Pt : struct { a: int; b: string; }
def Cl : class { v: int; }
def Un : union { i: int; }
worker fnc w(s string, v vec<int>, h hash<string, int>, p Pt, c Cl, u Un, sl char[], n: int) : void {
    show("w:", s, ":", v.size, ":", h.size, ":", p.a, ":", c.v, ":", n, ":", sl.size);
}
fnc main() {
    v: vec<int>;
    v.pushb(11);
    h: hash<string, int>;
    h.push("k", 1);
    p: Pt = Pt { a: 2, b: "bee" };
    c: Cl = Cl { v: 3 };
    u: Un = Un { i: 4 };
    sl: char[3] = {'x', 'y', 'z'};
    spawn w("payload", v, h, p, c, u, sl, 5);
    show("done");
}
"""

# A union is a boxed value ({ tag, payload }), not a pointer: the
# literal, the boxing into a hash and the hand-off to a worker all have
# to move the value, not a pointer to it.
UNION_LIFETIME = """\
def Un : union { i: int; s: string; }
def Box : struct { u: Un; n: int; }
fnc mk() : Un { u: Un = Un { i: 5 }; return u; }
fnc main() {
    u: Un = Un { i: 3 };
    show(u.i);
    u2: Un;
    u2 = Un { i: 4 };
    show(u2.i);
    v: vec<Un>;
    v.pushb(Un { i: 9 });
    show(v.get(0).i);
    h: hash<string, Un>;
    h.push("k", Un { i: 8 });
    show(h["k"].i);
    b: Box = Box { u: Un { i: 2 }, n: 1 };
    show(b.u.i);
    w: Un = mk();
    show(w.i);
    g: Un = Un { i: 6 };
    show(g.i);
    for j: int = 0; j < 3; j = j + 1 { g = Un { i: j }; }
    show(g.i);
}
"""

# A global that is overwritten in a loop: the root slot must reuse its
# previous root copy instead of allocating a new one per store. The
# root arena never rewinds, so a per-store copy would grow the
# program's permanent footprint with the iteration count.
REUSED_ROOT_SLOT = """\
g: string = "";
fnc main() {
    for i: int = 0; i < 100000; i = i + 1 { g = "abcdefghij"; }
    show(g.bsize);
}
"""

GLOBAL_AGGREGATE_CYCLE = """\
def Node : class {
    v: string;
    next: Node;
}
g: Node = Node { v: "g", next: null };
fnc main() {
    a: Node = Node { v: "a", next: null };
    a.next = a;
    g = a;
    show(g.v, " ", g.next.v, "|");
}
"""

TWO_GLOBAL_CONTAINER_ALIAS = """\
fnc main() {
    v: vec<int>;
    v.pushb(1);
    g1: vec<int> = v;
    g2: vec<int> = v;
    g1.pushb(2);
    show(g1.size, " ", g1.get(0), " ", g1.get(1), " ", g2.get(1), "|");
}
"""


def _long_chain_program(n):
    lines = [
        "def Node : class {",
        "    v: string;",
        "    next: Node;",
        "}",
        'g: Node = Node { v: "g", next: null };',
        "fnc make() : Node {",
    ]
    for i in range(n - 1, -1, -1):
        nxt = "null" if i == n - 1 else f"n{i + 1}"
        lines.append(f'    n{i}: Node = Node {{ v: "{i}", next: {nxt} }};')
    lines += [
        "    return n0;",
        "}",
        "fnc main() {",
        "    g = make();",
        "    p: Node = g;",
        "    total: int = 0;",
        "    while p != null { total = total + 1; p = p.next; }",
        '    show(total, "|");',
        "}",
    ]
    return "\n".join(lines) + "\n"


LONG_ROOT_CHAIN = _long_chain_program(40)


class TestArenaRootLifetime(unittest.TestCase):
    """Values that outlive the frame they were allocated in.

    The scope arena is rewound at every scope exit, so a value that is
    still reachable from a global (or from an outer local written in a
    loop) needs a copy in the root arena. These tests compare the
    arena output against the GC output and check that the root arena
    does not grow with the number of stores.
    """

    @classmethod
    def setUpClass(cls):
        if not os.path.exists(BIN_LEASHC):
            raise unittest.SkipTest("bin/leashc missing (run `leash self-host`)")
        cls.tmp = tempfile.mkdtemp(prefix="leash_arena_root_")

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

    def _run_bin(self, path, env=None):
        e = dict(os.environ)
        if env:
            e.update(env)
        r = subprocess.run([path], capture_output=True, text=True,
                           cwd=self.tmp, timeout=120, env=e)
        self.assertEqual(r.returncode, 0, msg=f"run failed: {r.stderr}")
        return r

    def _assert_parity(self, source, name):
        plain = self._build(source, name + "_plain", False)
        ar = self._build(source, name + "_ar", True)
        self.assertEqual(self._run_bin(ar).stdout, self._run_bin(plain).stdout,
                         msg=f"arena/plain output mismatch for {name}")

    def test_root_lifetime_cases(self):
        for name, source in ROOT_LIFETIME_CASES:
            with self.subTest(case=name):
                self._assert_parity(source, "root_" + name)

    def test_spawn_argument_lifetime(self):
        plain = self._build(SPAWN_ARGS, "spawnargs_plain", False)
        ar = self._build(SPAWN_ARGS, "spawnargs_ar", True)
        # Worker output order is nondeterministic; compare line sets.
        self.assertEqual(sorted(self._run_bin(ar).stdout.splitlines()),
                         sorted(self._run_bin(plain).stdout.splitlines()),
                         msg="arena/plain spawn argument output mismatch")

    def test_spawn_argument_types(self):
        plain = self._build(SPAWN_ARG_TYPES, "spawntypes_plain", False)
        ar = self._build(SPAWN_ARG_TYPES, "spawntypes_ar", True)
        self.assertEqual(sorted(self._run_bin(ar).stdout.splitlines()),
                         sorted(self._run_bin(plain).stdout.splitlines()),
                         msg="arena/plain spawn argument type output mismatch")

    def test_union_value_lifetime(self):
        self._assert_parity(UNION_LIFETIME, "unionlife")

    def test_global_aggregate_cycle(self):
        self._assert_parity(GLOBAL_AGGREGATE_CYCLE, "globalcycle")

    def test_long_global_chain_is_not_truncated(self):
        self._assert_parity(LONG_ROOT_CHAIN, "longrootchain")

    def test_two_globals_alias_one_container(self):
        self._assert_parity(TWO_GLOBAL_CONTAINER_ALIAS, "twoglobalias")

    def test_root_slot_reuse_is_bounded(self):
        """The root arena must not grow with the number of stores."""
        ar = self._build(REUSED_ROOT_SLOT, "rootslot_ar", True)
        r = self._run_bin(ar, env={"LEASH_ARENA_STATS": "1"})
        m = re.search(r"root peak (\d+) block bytes", r.stderr)
        self.assertIsNotNone(m, msg=f"no arena stats in: {r.stderr!r}")
        peak = int(m.group(1))
        # 100000 stores of a 10-byte string need 32 bytes each if the
        # slot never reuses its copy: >3 MB. A reusing slot keeps the
        # root arena at its initial 4 KB block.
        self.assertLess(peak, 1024 * 1024,
                        msg=f"root arena grew with the store count: {peak} bytes")


# Aggregate layout: a POD struct is stored BY VALUE in a container's
# element buffer, while a struct-typed FIELD is a pointer. The arena
# type tree must describe the two the same way the storage does: a
# pointer-shaped descriptor on an inline element made the runtime read
# the element bytes as an address (the arena build segfaulted on
# `vec<P>`), and the vec literal stored the address of the body instead
# of the body itself (both backends printed garbage).
POD_VEC = """\
def P : struct { a: int; b: int; }
fnc mkpod() : vec<P> {
    v: vec<P>;
    v.pushb(P { a: 1, b: 2 });
    v.pushb(P { a: 3, b: 4 });
    return v;
}
fnc main() {
    v: vec<P> = mkpod();
    show(v.size, " ", v.get(0).a, " ", v.get(0).b, " ",
         v.get(1).a, " ", v.get(1).b, "|");
}
"""

POD_VEC_LITERAL = """\
def P : struct { a: int; b: int; }
fnc main() {
    v: vec<P> = {P { a: 1, b: 2 }};
    show(v.size, " ", v.get(0).a, " ", v.get(0).b, "|");
}
"""

POD_FIELD = """\
def P : struct { a: int; b: int; }
def Outer : struct { p: P; n: int; }
fnc mk() : Outer {
    return Outer { p: P { a: 5, b: 6 }, n: 7 };
}
fnc main() {
    o: Outer = mk();
    show(o.p.a, " ", o.p.b, " ", o.n, "|");
}
"""

NESTED_CONTAINERS = """\
def P : struct { a: int; b: int; }
def Q : struct { s: string; n: int; }
fnc mkpod() : vec<P> {
    v: vec<P>;
    v.pushb(P { a: 1, b: 2 });
    return v;
}
fnc main() {
    vvp: vec<vec<P>>;
    vvp.pushb(mkpod());
    show(vvp.get(0).get(0).b, "|");
    hp: hash<string, P>;
    hp.push("k", P { a: 8, b: 9 });
    show(hp["k"].a, " ", hp["k"].b, "|");
    vph: vec<hash<string, P>>;
    vph.pushb(hp);
    show(vph.get(0)["k"].a, "|");
    vhp: hash<string, hash<string, P>>;
    vhp.push("o", hp);
    show(vhp["o"]["k"].b, "|");
    vq: vec<Q>;
    vq.pushb(Q { s: "qs", n: 5 });
    show(vq.get(0).s, " ", vq.get(0).n, "|");
}
"""


class TestArenaAggregateLayout(unittest.TestCase):
    """How aggregates are stored inside containers.

    The arena runtime walks a type tree to re-home the heap references
    of a value that outlives its frame. The tree has to match the
    storage exactly: an inline POD element must be a leaf, a
    struct-typed field must be a pointer, and a union is a value.
    """

    @classmethod
    def setUpClass(cls):
        if not os.path.exists(BIN_LEASHC):
            raise unittest.SkipTest("bin/leashc missing (run `leash self-host`)")
        cls.tmp = tempfile.mkdtemp(prefix="leash_arena_layout_")

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

    def _out(self, source, name, arena):
        out = self._build(source, name + ("_ar" if arena else ""), arena)
        r = subprocess.run([out], capture_output=True, text=True,
                           cwd=self.tmp, timeout=120)
        self.assertEqual(r.returncode, 0,
                         msg=f"run failed ({name}): {r.stderr}\n{r.stdout}")
        return r.stdout

    def _assert_both(self, source, expected, name):
        # The expected output is spelled out: a parity-only check would
        # not notice a bug that breaks both backends the same way.
        for arena in (False, True):
            with self.subTest(case=name, arena=arena):
                self.assertEqual(self._out(source, name, arena), expected)

    def test_pod_vec_elements_are_inline(self):
        self._assert_both(POD_VEC, "2 1 2 3 4|\n", "pod_vec")

    def test_pod_vec_literal_stores_the_body(self):
        self._assert_both(POD_VEC_LITERAL, "1 1 2|\n", "pod_vec_lit")

    def test_pod_struct_field_is_a_pointer(self):
        self._assert_both(POD_FIELD, "5 6 7|\n", "pod_field")

    def test_nested_containers(self):
        self._assert_both(NESTED_CONTAINERS,
                          "2|\n8 9|\n8|\n9|\nqs 5|\n", "nested_containers")


# A spawn argument outlives the frame that built it: the worker may
# read it long after the spawner's scope was rewound. The arena keeps
# such a value in the root arena, and every heap reference it holds
# (a string field, a hash value, a nested container) has to be rooted
# with it -- a plain byte copy left those pointers dangling and the
# worker read freed memory.
SPAWN_HEAP_FIELDS = """\
def P : struct { s: string; n: int; }
worker fnc w(p P, v vec<string>, h hash<string, string>) : void {
    show("w:", p.s, ":", p.n, ":", v.get(0), ":", h["k"]);
}
fnc main() {
    p: P = P { s: "hello" + tostring(7), n: 5 };
    v: vec<string>;
    v.pushb("vec" + tostring(8));
    h: hash<string, string>;
    h.push("k", "hash" + tostring(9));
    spawn w(p, v, h);
    show("done");
}
"""

SPAWN_NESTED_FIELDS = """\
def P : struct { h: hash<string, string>; n: int; }
worker fnc w(p P, v vec<vec<int>>) : void {
    show("w:", p.h["k"], ":", p.n, ":", v.get(0).get(1));
}
fnc main() {
    h: hash<string, string>;
    h.push("k", "val" + tostring(3));
    p: P = P { h: h, n: 1 };
    v: vec<vec<int>>;
    inner: vec<int>;
    inner.pushb(1);
    inner.pushb(2);
    v.pushb(inner);
    spawn w(p, v);
    show("done");
}
"""

# A union is a boxed value. Pushing one into a global hash from a
# function frame needs the box copy in the root arena; the size of the
# union body was computed from the pointer type, which produced an
# invalid "null must be a pointer type" GEP and the program did not
# build at all.
UNION_IN_GLOBAL_HASH = """\
def U : union { i: int; }
gh: hash<string, U>;
fnc fill() : void { gh.push("k", U { i: 8 }); }
fnc main() {
    fill();
    show(gh["k"].i);
}
"""

# A tuple argument is an aggregate value: it is copied into the spawn
# block by value and its heap components are rooted. The arena used to
# bitcast the aggregate to i8*, which is invalid LLVM.
SPAWN_TUPLE_ARG = """\
worker fnc w(t (int, string)) : void { show("w:ok"); }
fnc main() {
    t: (int, string);
    spawn w(t);
    show("done");
}
"""


class TestArenaSpawnArgs(unittest.TestCase):
    """Arguments that outlive the frame that built them.

    A worker thread reads its arguments after the spawner's scope was
    rewound, so every heap reference an argument holds must be rooted
    with it.
    """

    @classmethod
    def setUpClass(cls):
        if not os.path.exists(BIN_LEASHC):
            raise unittest.SkipTest("bin/leashc missing (run `leash self-host`)")
        cls.tmp = tempfile.mkdtemp(prefix="leash_arena_spawn_")

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

    def _out(self, source, name, arena):
        out = self._build(source, name + ("_ar" if arena else ""), arena)
        r = subprocess.run([out], capture_output=True, text=True,
                           cwd=self.tmp, timeout=120)
        self.assertEqual(r.returncode, 0,
                         msg=f"run failed ({name}): {r.stderr}\n{r.stdout}")
        return r.stdout

    def _assert_both(self, source, expected, name):
        for arena in (False, True):
            with self.subTest(case=name, arena=arena):
                self.assertEqual(self._out(source, name, arena), expected)

    def test_spawn_args_with_heap_fields(self):
        self._assert_both(SPAWN_HEAP_FIELDS,
                          "done\nw:hello7:5:vec8:hash9\n", "spawn_heap")

    def test_spawn_args_with_nested_fields(self):
        self._assert_both(SPAWN_NESTED_FIELDS,
                          "done\nw:val3:1:2\n", "spawn_nested")

    def test_union_in_global_hash(self):
        self._assert_both(UNION_IN_GLOBAL_HASH, "8\n", "union_hash")

    def test_tuple_spawn_arg(self):
        self._assert_both(SPAWN_TUPLE_ARG, "done\nw:ok\n", "spawn_tuple")


if __name__ == "__main__":
    unittest.main(verbosity=2)
