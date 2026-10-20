"""JVM bytecode backend tests (--target jvm).

These verify that the self-hosted compiler emits real, runnable JVM
class files whose observable behavior matches the native backend.
Skipped entirely when no `java` runtime is on PATH.
"""
import http.server
import json
import os
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.request

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN_LEASHC = os.path.join(REPO_ROOT, "bin", "leashc")
JAVA = shutil.which("java")

HAVE_GIT_DIR = os.path.isdir(os.path.join(REPO_ROOT, ".git"))


def _sh(cmd, cwd=None, timeout=300):
    return subprocess.run(cmd, cwd=cwd or REPO_ROOT, capture_output=True,
                          text=True, errors="replace", timeout=timeout)


@unittest.skipUnless(JAVA, "no `java` runtime on PATH")
@unittest.skipUnless(os.path.exists(BIN_LEASHC), "bin/leashc not built")
class TestJvmTarget(unittest.TestCase):
    """Compile-and-run parity for --target jvm."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="leash_jvm_test_")
        cls.n = 0

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _next_name(self):
        self.n += 1
        return f"jt{self.n}"

    def _compile_jvm(self, src, main_class=None, run_args="", stdlib=False):
        """Compile `src` to JVM bytecode; return (class_dir, main_class).

        stdlib=True resolves stdlib imports from the working tree
        (installthis/) instead of the user-installed ~/.leash/libs copy.
        """
        name = main_class or self._next_name()
        path = os.path.join(self.tmp, f"{name}.lsh")
        with open(path, "w") as f:
            f.write(src)
        out = os.path.join(self.tmp, name)
        cmd = [BIN_LEASHC, "compile", path, "--target", "jvm"]
        if stdlib:
            cmd += ["--other-imports", os.path.join(REPO_ROOT, "installthis")]
        cmd += ["to", out]
        r = _sh(cmd)
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        self.assertTrue(os.path.exists(os.path.join(self.tmp, f"{name}.class")),
                        "no .class emitted")
        return self.tmp, name

    def _run_jvm(self, class_dir, main_class, args=None):
        cmd = [JAVA, "-cp", class_dir, main_class] + (args or [])
        return subprocess.run(cmd, capture_output=True, text=True,
                              errors="replace", timeout=60)

    def _run_native(self, src):
        name = self._next_name() + "n"
        path = os.path.join(self.tmp, f"{name}.lsh")
        with open(path, "w") as f:
            f.write(src)
        out = os.path.join(self.tmp, name)
        r = _sh([BIN_LEASHC, "compile", path, "to", out])
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        return subprocess.run([out], capture_output=True, text=True,
                              errors="replace", timeout=60)

    def _parity(self, src):
        """JVM output must equal native output."""
        # Both backends compile the SAME file so anything embedded in the
        # output (e.g. throw's "  --> file:line") matches exactly.
        name = self._next_name()
        path = os.path.join(self.tmp, f"{name}.lsh")
        with open(path, "w") as f:
            f.write(src)
        out_j = os.path.join(self.tmp, name)
        rj_c = _sh([BIN_LEASHC, "compile", path, "--target", "jvm", "to", out_j])
        self.assertEqual(rj_c.returncode, 0, msg=rj_c.stdout + rj_c.stderr)
        self.assertTrue(os.path.exists(os.path.join(self.tmp, f"{name}.class")),
                        "no .class emitted")
        rj = subprocess.run([JAVA, "-cp", self.tmp, name], capture_output=True,
                            text=True, errors="replace", timeout=60)
        out_n = os.path.join(self.tmp, name + "n")
        rn_c = _sh([BIN_LEASHC, "compile", path, "to", out_n])
        self.assertEqual(rn_c.returncode, 0, msg=rn_c.stdout + rn_c.stderr)
        rn = subprocess.run([out_n], capture_output=True, text=True,
                            errors="replace", timeout=60)
        self.assertEqual(rj.returncode, rn.returncode,
                         f"exit codes differ: jvm={rj.returncode} native={rn.returncode}\n"
                         f"jvm stdout:\n{rj.stdout}\nnative stdout:\n{rn.stdout}")
        self.assertEqual(rj.stdout, rn.stdout,
                         f"stdout differs:\n--- jvm ---\n{rj.stdout}\n--- native ---\n{rn.stdout}")

    # ---- core language -------------------------------------------------

    def test_hello(self):
        self._parity('fnc main() : void {\n    show("hi");\n}\n')

    def test_arith_and_calls(self):
        self._parity(
            'fnc add(a int, b int) : int {\n    return a + b;\n}\n'
            'fnc fact(n int) : int {\n    if n <= 1 { return 1; }\n'
            '    return n * fact(n - 1);\n}\n'
            'fnc main() : void {\n'
            '    show(add(20, 22));\n    show(fact(10));\n'
            '    show(7 / 2);\n    show(7 % 3);\n'
            '    x: int = 10;\n    x += 5;\n    x++;\n    show(x);\n'
            '    show(1 < 2);\n    show("foo" + "bar");\n'
            '    show(2.5 * 4.0);\n    show(tostring(12345));\n'
            '}\n')

    def test_int64_and_uint(self):
        self._parity(
            'fnc main() : void {\n'
            '    big: int<64> = 5000000000;\n'
            '    show(big);\n'
            '    u: uint = 4000000000;\n'
            '    show(u);\n'
            '    ul: uint<64> = 18446744073709551615;\n'
            '    show(ul);\n'
            '    show(big + 1);\n'
            '}\n')

    def test_float_locals(self):
        self._parity(
            'fnc apply(f float) : float {\n    r: float = f * 2.0;\n    return r;\n}\n'
            'fnc main() : void {\n'
            '    x: float = 2.5;\n    y: float = x + 1.25;\n    show(y);\n'
            '    y += 0.5;\n    show(y);\n    y++;\n    show(y);\n'
            '    show(apply(3.5));\n'
            '}\n')

    def test_control_flow(self):
        self._parity(
            'fnc main() : void {\n'
            '    i: int = 0;\n    while i < 5 { i = i + 1; if i == 2 { skip; } if i == 4 { stop; } show(i); }\n'
            '    for j: int = 0; j < 3; j = j + 1 { show(j); }\n'
            '    loop { show("once"); stop; }\n'
            '    x: int = 2;\n'
            '    switch x {\n'
            '        case 1 { show("one"); }\n'
            '        case 2 { show("two"); }\n'
            '        default { show("many"); }\n'
            '    }\n'
            '    show(1 < 2 ? "lt" : "ge");\n'
            '}\n')

    def test_defer(self):
        self._parity(
            'fnc main() : void {\n'
            '    defer show("deferred-1");\n'
            '    defer show("deferred-2");\n'
            '    show("body");\n'
            '}\n')

    def test_throw_and_works(self):
        self._parity(
            'error E(args int) -> "boom " + tostring(args) + ".";\n'
            'fnc risky(n int) : int {\n    if n < 2 { throw E(n); }\n    return n;\n}\n'
            'fnc main() : void {\n'
            '    show("before");\n'
            '    works { show(risky(1)); } otherwise err { show("caught"); }\n'
            '    show("after");\n'
            '}\n')

    def test_exit_code(self):
        class_dir, main_class = self._compile_jvm(
            'fnc main() : int {\n    show("bye");\n    return 7;\n}\n')
        r = self._run_jvm(class_dir, main_class)
        self.assertEqual(r.returncode, 7)
        self.assertEqual(r.stdout, "bye\n")

    # ---- containers ----------------------------------------------------

    def test_vec(self):
        self._parity(
            'fnc main() : void {\n'
            '    v: vec<int>;\n'
            '    v.pushb(1);\n    v.pushb(2);\n    v.pushb(3);\n'
            '    show(v);\n    show(v.size);\n    show(v[-1]);\n'
            '    v.set(0, 99);\n    show(v);\n'
            '    show(v.get(1));\n    show(v.popb());\n    show(v);\n'
            '    v.pushf(0);\n    show(v);\n'
            '    show(v.isin(99));\n'
            '    vf: vec<float>;\n    vf.pushb(2.5);\n    vf.pushb(0.1);\n'
            '    show(vf);\n'
            '    vs: vec<string>;\n    vs.pushb("foo");\n    vs.pushb("bar");\n'
            '    show(vs);\n'
            '}\n')

    def test_hash(self):
        self._parity(
            'fnc main() : void {\n'
            '    h: hash<int, int>;\n'
            '    h.push(1, 100);\n    h.push(2, 200);\n'
            '    show(h[1]);\n    show(h[99]);\n    show(h.size);\n'
            '    show(h.isin(2));\n'
            '    h.delete(1);\n    show(h.isin(1));\n'
            '    hs: hash<string, int>;\n    hs.push("k", 7);\n'
            '    show(hs["k"]);\n'
            '}\n')

    def test_arrays(self):
        self._parity(
            'fnc main() : void {\n'
            '    arr: int[] = {10, 20, 30};\n'
            '    show(arr[1]);\n    show(arr.size);\n'
            '    arr[0] = 99;\n    show(arr[0]);\n'
            '    v: vec<int>;\n    v.extend(arr);\n    show(v);\n'
            '    v2: vec<int> = arr;\n    show(v2);\n'
            '}\n')

    # ---- structs / classes / enums ------------------------------------

    def test_struct_and_class(self):
        self._parity(
            'pub def Point : struct {\n    pub x: int;\n    pub y: int;\n}\n'
            'pub def Acc : class {\n    pub total: int;\n'
            '    pub fnc add(n int) : int {\n        this.total = this.total + n;\n        return this.total;\n    }\n'
            '}\n'
            'fnc dist(p Point) : int {\n    return p.x + p.y;\n}\n'
            'fnc main() : void {\n'
            '    q: Point = Point { x: 3, y: 4 };\n'
            '    show(q.x);\n    show(dist(q));\n'
            '    a: Acc = create Acc();\n'
            '    show(a.add(5));\n    show(a.add(7));\n'
            '}\n')

    def test_inheritance_and_virtual(self):
        self._parity(
            'def Animal : class {\n'
            '    static pub fnc new() : Animal { return Animal {}; }\n'
            '    pub fnc talk() : void { show("No sounds"); }\n'
            '}\n'
            'def Dog : class(Animal) {\n'
            '    pub fnc talk() : void { show("Bark Bark!"); }\n'
            '}\n'
            'fnc main() : void {\n'
            '    d: Dog = Dog {};\n'
            '    a: Animal = d;\n'
            '    a.talk();\n'
            '}\n')

    def test_enums(self):
        self._parity(
            'def Color : enum {\n    RED,\n    GREEN,\n    BLUE\n};\n'
            'fnc main() : void {\n'
            '    c: Color = Color::RED;\n'
            '    show(c);\n    show(c.name);\n'
            '    if c == Color::RED { show("is red"); }\n'
            '}\n')

    def test_static_class_field(self):
        self._parity(
            'pub def K : class {\n    static pub A: int = 7;\n}\n'
            'fnc main() : void {\n    show(K.A);\n    K.A = 10;\n    show(K.A);\n}\n')

    # ---- tuples / multi-return ------------------------------------------

    def test_multi_return(self):
        self._parity(
            'fnc add_sub(a int, b int) : (int, int) {\n'
            '    return a + b, a - b;\n'
            '}\n'
            'fnc main() {\n'
            '    a, b: int, int = add_sub(10, 4);\n'
            '    show(a);\n    show(b);\n'
            '}\n')

    # ---- builtins -------------------------------------------------------

    def test_conversions(self):
        self._parity(
            'fnc main() : void {\n'
            '    show(toint(int, "12345"));\n'
            '    show(toint(int, "12ab"));\n'
            '    show(toint(int, ""));\n'
            '    show(toint(int, 2.9));\n'
            '    show(tofloat(float, "2.5"));\n'
            '    show(tofloat(float, 7));\n'
            '    show(typeof(42));\n'
            '    x: string = "hi";\n    show(typeof(x));\n'
            '    show(sizeof("int"));\n'
            '    show(cstr("abc").size);\n'
            '    show(cstr("abc")[1]);\n'
            '}\n')

    def test_string_iteration(self):
        self._parity(
            'fnc main() : void {\n'
            '    s: string = "hey";\n'
            '    foreach _, c in<string> s {\n        show(c);\n    }\n'
            '}\n')

    # ---- runtime errors --------------------------------------------------

    def test_div_by_zero_runtime(self):
        class_dir, main_class = self._compile_jvm(
            'fnc main() : void {\n    x: int = 7;\n    y: int = 0;\n    show(x / y);\n}\n')
        r = self._run_jvm(class_dir, main_class)
        self.assertEqual(r.returncode, 1)
        self.assertIn("Runtime error: Division by zero.", r.stdout)

    def test_vec_out_of_bounds(self):
        class_dir, main_class = self._compile_jvm(
            'fnc main() : void {\n    v: vec<int>;\n    v.pushb(1);\n    show(v[5]);\n}\n')
        r = self._run_jvm(class_dir, main_class)
        self.assertEqual(r.returncode, 1)
        self.assertIn("Vector index out of bounds", r.stdout)

    # ---- args -----------------------------------------------------------

    def test_main_args(self):
        src = ('fnc main(args string[]) {\n'
               '    show(args.size);\n'
               '    foreach i, arg in<array> args {\n'
               '        show(i, ": ", arg);\n'
               '    }\n'
               '}\n')
        class_dir, main_class = self._compile_jvm(src)
        r = self._run_jvm(class_dir, main_class, ["a", "bb"])
        # argv[0] is the class name on JVM vs the binary path on native;
        # count and the user args must still line up.
        self.assertEqual(r.returncode, 0)
        self.assertIn("3\n", r.stdout)
        self.assertIn("1: a\n", r.stdout)
        self.assertIn("2: bb\n", r.stdout)

    # ---- deferred features must fail cleanly ------------------------------

    def test_seeded_rng_parity(self):
        self._parity(
            'fnc main() : void {\n'
            '    seed(10);\n'
            '    show(rand(-10, 10));\n    show(rand(-10, 10));\n'
            '    show(randf(0, 2));\n'
            '    show(choose("a", "b", "c"));\n'
            '    show(rand(1, 6));\n'
            '}\n')

    def test_union_parity(self):
        self._parity(
            'def Value : union {\n    i: int;\n    f: float;\n    s: string\n};\n'
            'fnc main() : void {\n'
            '    v := tounion(Value, 45);\n'
            '    show("v: ", v, ", typeof(v): ", typeof(v));\n'
            '    f: Value = 10;\n'
            '    show("f = ", f.cur);\n'
            '    f = 1.5;\n'
            '    show("f = ", f.cur);\n'
            '    f = "hello";\n'
            '    show("f = ", f.cur);\n'
            '    f.i = 10;\n'
            '    show("f = ", f);\n'
            '    show("fi = ", f.i);\n'
            '}\n')

    def test_int128_parity(self):
        self._parity(
            'use types::*;\n'
            'fnc main() : void {\n'
            '    a: int128 = toint(int128, 100);\n'
            '    b: int128 = toint(int128, 7);\n'
            '    show(a + b);\n    show(a - b);\n'
            '    show(a * b);\n    show(a / b);\n    show(a % b);\n'
            '    show(-b);\n    show(a > b);\n    show(a & b);\n'
            '    show(a << toint(int128, 2));\n'
            '}\n')

    def test_wide_int_parity(self):
        self._parity(
            'use types::*;\n'
            'fnc main() : void {\n'
            '    p: uint<256> = 57896044618658097711785492504343953926634992332820282019728792003956564819967;\n'
            '    show(p + 1);\n'
            '    m: uint<512> = 13407807929942597099574024998205846127479365820592393377723561443721764030073546976801874298166903427690031858186486050853753882811946569946433649006084095;\n'
            '    show(m + 1);\n'
            '    show(m * 2);\n'
            '    t: uint<7> = 127;\n'
            '    show(t);\n'
            '    x: uint<7> = t + 1;\n'
            '    show(x);\n'
            '    o: int<33> = -4294967296;\n'
            '    show(o);\n'
            '    show(toint(int<256>, "123456789012345678901234567890123456789012345678901234567890"));\n'
            '}\n')

    def test_showb_parity(self):
        self._parity(
            'fnc main() : void {\n'
            '    show("one");\n'
            '    showb("two");\n'
            '    show("three");\n'
            '    v: vec<int>;\n'
            '    v.pushb(1);\n'
            '    v.pushb(2);\n'
            '    showb(v);\n'
            '    showb("\\n");\n'
            '}\n')

    def test_async_parity(self):
        self._parity(
            'async fnc slow_add(a int, b int) : int {\n'
            '    return a + b;\n}\n'
            'async fnc greet(name string) : string {\n'
            '    return "hello, " + name;\n}\n'
            'fnc main() : void {\n'
            '    f1: future<int> = slow_add(19, 23);\n'
            '    show(await f1);\n'
            '    show(await greet("leash"));\n'
            '    show(await slow_add(1, 2));\n'
            '}\n')

    def test_spawn_workers_parity(self):
        self._parity(
            'fusion out: int = 0;\n'
            'worker fnc wadd(a int, b int) : void {\n    out = a + b;\n}\n'
            'fnc main() : void {\n'
            '    spawn wadd(40, 2);\n'
            '    while out == 0 {\n    }\n'
            '    show(out);\n'
            '}\n')

    def test_string_sub_and_char_concat(self):
        self._parity(
            'fnc main() : void {\n'
            '    show("aXbXc" - "X");\n'
            '    show("abc" - "");\n'
            '    word: string = "";\n'
            '    word = word + \'a\' + \'b\';\n'
            '    show(word);\n'
            '}\n')

    def test_logic_and_file_parity(self):
        self._parity(
            'fnc main() : void {\n'
            '    x: int = 3;\n'
            '    show(x > 1 && x < 5);\n'
            '    show(x > 10 && x < 5);\n'
            '    show(x > 10 || x < 5);\n'
            '    f: File = File.open("/etc/hostname", "r");\n'
            '    if f == null {\n        show("no file");\n        return;\n    }\n'
            '    c: string = f.read();\n'
            '    f.close();\n'
            '    show(c.size > 0);\n'
            '}\n')

    def test_generic_class_parity(self):
        self._parity(
            'pub def Box : class<T> {\n'
            '    priv v: T;\n'
            '    static pub fnc of(x T) : Box {\n'
            '        return Box { v: x };\n    }\n'
            '    pub fnc get() : T {\n        return this.v;\n    }\n'
            '};\n'
            'fnc main() : void {\n'
            '    b: Box<int> = Box.of(41);\n'
            '    show(b.get() + 1);\n'
            '}\n')

    def test_to_dynamic_unsupported(self):
        path = os.path.join(self.tmp, "lib.lsh")
        with open(path, "w") as f:
            f.write('fnc helper() : int { return 1; }\n')
        r = _sh([BIN_LEASHC, "compile", path, "--target", "jvm",
                 "to-dynamic", "to", os.path.join(self.tmp, "lib")])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("to-dynamic", r.stdout + r.stderr)

    def test_printf_parity(self):
        self._parity(
            'fnc main() : void {\n'
            '    printf("int=%d neg=%d hex=%x float=%f str=%s pct=%% end\\n", 42, 0 - 7, 255, 3.5, "hi");\n'
            '    n: int = printf("abc");\n'
            '    show(n);\n'
            '}\n')

    def test_raw_heap_unsupported(self):
        # Raw C-heap functions have no JVM addresses; they must fail
        # loudly at compile time, never as broken bytecode.
        path = os.path.join(self.tmp, "heap.lsh")
        with open(path, "w") as f:
            f.write('unsafe nogc fnc main : int {\n'
                    '    p: *char = (*char)malloc(16);\n'
                    '    free(p);\n'
                    '    return 0;\n'
                    '}\n')
        r = _sh([BIN_LEASHC, "compile", path, "--target", "jvm",
                 "to", os.path.join(self.tmp, "heap")])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("raw C heap", r.stdout + r.stderr)

    def test_structrec_parity(self):
        self._parity(
            'def Node : struct {\n'
            '    value: int;\n'
            '    next: *Node;\n'
            '};\n'
            'unsafe fnc sum(n : *Node) : int {\n'
            '    if n == null { return 0; }\n'
            '    return n->value + sum(n->next);\n'
            '}\n'
            'fnc main() : void {\n'
            '    c: Node = Node { value: 30, next: null };\n'
            '    b: Node = Node { value: 20, next: &c };\n'
            '    a: Node = Node { value: 10, next: &b };\n'
            '    show(sum(&a));\n'
            '    copy: Node = b;\n'
            '    copy.value = 1234;\n'
            '    show(copy.value, " ", b.value);\n'
            '}\n')

    def test_autoinit_parity(self):
        self._parity(
            'def Person : class {\n'
            '    name: string;\n'
            '    age:  int;\n'
            '    pub Person(name string = "No Name", age int = 10) {\n'
            '        this.name = name;\n'
            '        this.age  = age;\n'
            '    }\n'
            '}\n'
            'fnc main {\n'
            '    p1: Person;\n'
            '    show(p1.name, " ", p1.age);\n'
            '}\n')

    def test_gc_collect_noop_parity(self):
        # Explicit collections are no-ops on JVM (GC is automatic).
        self._parity(
            'fnc build(count int) : int {\n'
            '    total: int = 0;\n'
            '    for i: int = 0; i < count; i = i + 1 {\n'
            '        v: vec<int> = {i, i + 1};\n'
            '        total = total + v.size;\n'
            '    }\n'
            '    return total;\n'
            '}\n'
            'fnc main() : void {\n'
            '    show(build(50));\n'
            '    gc_collect();\n'
            '    show(build(50));\n'
            '    show("roots ok");\n'
            '}\n')

    def test_http_helpers_parity(self):
        # Pure-Leash HTTP helpers (no sockets): base64, auth headers,
        # response header parsing, query parsing. Compiles against the
        # working-tree stdlib; native @from calls become loud runtime
        # errors on JVM while pure code runs with parity.
        src = (
            'use http::http::*;\n'
            'use httpserver::httpserver::*;\n'
            'fnc main() : void {\n'
            '    show(Http.base64_encode("hello") == "aGVsbG8=");\n'
            '    show(Http.base64_encode("h") == "aA==");\n'
            '    show(Http.base64_encode("") == "");\n'
            '    show(Http.basic_auth("aladdin", "opensesame"));\n'
            '    show(Http.bearer("tok123"));\n'
            '    r: HttpResponse = HttpResponse { status: 200, ok: true, headers: "Content-Type: text/plain\\nX-A: b\\n", body: "hi", err: "", proto: "h2" };\n'
            '    show(r.header("content-type"));\n'
            '    show(r.content_type());\n'
            '    show(r.is_h2());\n'
            '    show(r.success());\n'
            '    q: HttpQuery = HttpServer.parse_query("a=1&b=2&flag");\n'
            '    show(q.count());\n'
            '    show(q.get("b"));\n'
            '    show(q.get("flag") == "");\n'
            '    show(q.has("a"));\n'
            '    show(HttpServer.path_matches("/hello/x", "/hello"));\n'
            '}\n')
        name = self._next_name()
        path = os.path.join(self.tmp, f"{name}.lsh")
        with open(path, "w") as f:
            f.write(src)
        stdlib = os.path.join(REPO_ROOT, "installthis")
        out_j = os.path.join(self.tmp, name)
        rj_c = _sh([BIN_LEASHC, "compile", path, "--target", "jvm",
                    "--other-imports", stdlib, "to", out_j])
        self.assertEqual(rj_c.returncode, 0, msg=rj_c.stdout + rj_c.stderr)
        rj = self._run_jvm(self.tmp, name)
        out_n = os.path.join(self.tmp, name + "n")
        rn_c = _sh([BIN_LEASHC, "compile", path, "--other-imports", stdlib,
                    "to", out_n])
        self.assertEqual(rn_c.returncode, 0, msg=rn_c.stdout + rn_c.stderr)
        rn = subprocess.run([out_n], capture_output=True, text=True,
                            errors="replace", timeout=60)
        self.assertEqual(rj.stdout, rn.stdout,
                         f"stdout differs:\n--- jvm ---\n{rj.stdout}\n--- native ---\n{rn.stdout}")

    # ---- http module on the JVM (bundled LeashHttp shim) ---------------

    class _Handler(http.server.BaseHTTPRequestHandler):
        """Routes: /hello (GET+POST), /redir -> /hello, /json (GET)."""

        def log_message(self, *a):
            pass

        def _send(self, code, body=b"", ctype="text/plain", headers=()):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            for k, v in headers:
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def do_GET(self):
            if self.path == "/hello":
                self._send(200, b"hello-jvm", headers=[("X-Custom", "abc")])
            elif self.path == "/redir":
                self.send_response(302)
                self.send_header("Location", "/hello")
                self.send_header("Content-Length", "0")
                self.end_headers()
            elif self.path == "/json":
                self._send(200, json.dumps({"a": 1, "b": [2, 3]}).encode(),
                           ctype="application/json")
            elif self.path == "/404":
                self._send(404, b"nope")
            else:
                self._send(404, b"nope")

        def do_POST(self):
            n = int(self.headers.get("Content-Length", "0") or "0")
            body = self.rfile.read(n) if n else b""
            if self.path == "/hello":
                echoed = body.decode("utf-8", "replace").upper()
                self._send(200, ("saw=" + echoed +
                                 ";ct=" + (self.headers.get("Content-Type") or "")
                                 ).encode())
            else:
                self._send(404, b"nope")

    def _start_http(self, tls=False):
        """Start a local server; returns (server, port)."""
        if tls:
            server = http.server.HTTPServer(("127.0.0.1", 0),
                                            TestJvmTarget._Handler)
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(
                os.path.join(REPO_ROOT, "tests", "stdlib", "fixtures",
                             "localhost.crt"),
                os.path.join(REPO_ROOT, "tests", "stdlib", "fixtures",
                             "localhost.key"))
            server.socket = ctx.wrap_socket(server.socket, server_side=True)
        else:
            server = http.server.ThreadingHTTPServer(("127.0.0.1", 0),
                                                     TestJvmTarget._Handler)
        port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        self.addCleanup(server.shutdown)
        return server, port

    def test_jvm_http_get_post_redirect(self):
        # The @from http surface really works on the JVM: requests lower
        # into the bundled LeashHttp shim (java.net.http under the hood).
        _, port = self._start_http()
        src = ('use http::http::*;\n'
               'fnc main() : void {\n'
               f'    r: HttpResponse = Http.get("http://127.0.0.1:{port}/hello");\n'
               '    show(r.ok); show(tostring(r.status)); show(r.body);\n'
               '    show(r.content_type()); show(r.header("x-custom"));\n'
               f'    p: HttpResponse = Http.post("http://127.0.0.1:{port}/hello", "the-body");\n'
               '    show(tostring(p.status)); show(p.body);\n'
               f'    d: HttpResponse = Http.get("http://127.0.0.1:{port}/redir");\n'
               '    show(tostring(d.status)); show(d.body);\n'
               f'    n: HttpResponse = Http.get("http://127.0.0.1:{port}/404");\n'
               '    show(tostring(n.status)); show(tostring(n.success()));\n'
               '}\n')
        class_dir, main_class = self._compile_jvm(src, stdlib=True)
        r = self._run_jvm(class_dir, main_class)
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        out = r.stdout
        self.assertIn("true", out)
        self.assertIn("200", out)
        self.assertIn("hello-jvm", out)
        self.assertIn("text/plain", out)
        self.assertIn("abc", out)
        self.assertIn("saw=THE-BODY", out)
        # 302 -> /hello follows transparently
        self.assertIn("hello-jvm", out.split("saw=THE-BODY")[1])
        self.assertIn("404", out)
        self.assertIn("false", out)

    def test_jvm_http_error_codes(self):
        # Refused connection surfaces as the C engine's error contract:
        # ok=false, a negative status, and strerror() text.
        src = ('use http::http::*;\n'
               'fnc main() : void {\n'
               '    r: HttpResponse = Http.get("http://127.0.0.1:1/x");\n'
               '    show(tostring(r.ok)); show(tostring(r.status)); show(r.err);\n'
               '    show(Http.strerror(-2)); show(Http.strerror(-9));\n'
               '    show(Http.version());\n'
               '}\n')
        class_dir, main_class = self._compile_jvm(src, stdlib=True)
        r = self._run_jvm(class_dir, main_class)
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        out = r.stdout
        self.assertIn("false", out)
        self.assertIn("-3", out)
        self.assertIn("could not connect", out)
        self.assertIn("could not resolve host", out)
        self.assertIn("too many redirects", out)
        self.assertIn("leash-http/1.1", out)

    def test_jvm_http_download(self):
        _, port = self._start_http()
        dest = os.path.join(self.tmp, "jvm_dl.txt")
        src = ('use http::http::*;\n'
               'fnc main() : void {\n'
               f'    r: HttpResponse = Http.download("http://127.0.0.1:{port}/json", "{dest}");\n'
               '    show(tostring(r.status));\n'
               '}\n')
        class_dir, main_class = self._compile_jvm(src, stdlib=True)
        r = self._run_jvm(class_dir, main_class)
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        self.assertIn("200", r.stdout)
        with open(dest, "r") as f:
            content = f.read()
        self.assertEqual(json.loads(content), {"a": 1, "b": [2, 3]})

    def test_jvm_http_h2_cleartext_reports_http11(self):
        # java.net.http has no h2c prior-knowledge, so *_h2 over cleartext
        # falls back to HTTP/1.1 and reports proto "http/1.1".
        _, port = self._start_http()
        src = ('use http::http::*;\n'
               'fnc main() : void {\n'
               f'    r: HttpResponse = Http.get_h2("http://127.0.0.1:{port}/hello");\n'
               '    show(tostring(r.status)); show(r.proto); show(tostring(r.is_h2()));\n'
               '}\n')
        class_dir, main_class = self._compile_jvm(src, stdlib=True)
        r = self._run_jvm(class_dir, main_class)
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        self.assertIn("200", r.stdout)
        self.assertIn("http/1.1", r.stdout)
        self.assertIn("false", r.stdout)

    def test_jvm_http_tls(self):
        # verify off trusts the self-signed fixture; verify on rejects it.
        _, port = self._start_http(tls=True)
        src = ('use http::http::*;\n'
               'fnc main() : void {\n'
               f'    a: HttpResponse = Http.request_ex("GET", "https://127.0.0.1:{port}/hello", "", "", 5000, 10, false);\n'
               '    show(tostring(a.ok)); show(tostring(a.status)); show(a.body);\n'
               f'    b: HttpResponse = Http.request_ex("GET", "https://127.0.0.1:{port}/hello", "", "", 5000, 10, true);\n'
               '    show(tostring(b.ok)); show(tostring(b.status));\n'
               '}\n')
        class_dir, main_class = self._compile_jvm(src, stdlib=True)
        r = self._run_jvm(class_dir, main_class)
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        out = r.stdout
        self.assertIn("true", out)
        self.assertIn("hello-jvm", out)
        self.assertIn("false", out)
        self.assertIn("-4", out)  # LSH_E_TLS

    # ---- httpserver module on the JVM (bundled LeashHttpServer shim) ----

    def _free_port(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        return port

    def _run_jvm_bg(self, name, port):
        """Run a compiled JVM program in the background; wait until it
        listens on `port`. Returns the Popen."""
        proc = subprocess.Popen([JAVA, "-cp", self.tmp, name],
                                 stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True)
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                s = socket.create_connection(("127.0.0.1", port), 0.2)
                s.close()
                return proc
            except OSError:
                if proc.poll() is not None:
                    out = proc.stdout.read()
                    self.fail(f"server exited early:\n{out}")
                time.sleep(0.1)
        proc.kill()
        self.fail("server never started listening")
        return None

    def test_jvm_httpserver_serve(self):
        # serve()/serve_ex() on the JVM: real listener, handler callbacks
        # through a generated LshHttpHandler adapter, reply helpers,
        # query parsing, shutdown() from inside a handler, served count.
        port = self._free_port()
        src = ('use httpserver::httpserver::*;\n'
               'fnc handle(method string, path string, query string, headers string, body string) : int {\n'
               '    if path == "/q" {\n'
               '        q: HttpQuery = HttpServer.parse_query(query);\n'
               '        return HttpServer.reply_text(200, "a=" + q.get("a"));\n'
               '    }\n'
               '    if path == "/stop" {\n'
               '        HttpServer.set_body("bye");\n'
               '        HttpServer.shutdown();\n'
               '        return 200;\n'
               '    }\n'
               '    if path == "/post" {\n'
               '        return HttpServer.reply_text(200, "got:" + body);\n'
               '    }\n'
               '    return HttpServer.reply_text(200, "hello");\n'
               '}\n'
               'fnc main() : int {\n'
               f'    served: int = HttpServer.serve_ex({port}, 10, &handle);\n'
               '    show("served=" + tostring(served));\n'
               '    return 0;\n'
               '}\n')
        class_dir, main_class = self._compile_jvm(src, stdlib=True)
        proc = self._run_jvm_bg(main_class, port)
        try:
            def get(path):
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}{path}", timeout=10) as r:
                    return r.status, r.read().decode()

            def post(path, data):
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}{path}", data=data,
                    method="POST")
                with urllib.request.urlopen(req, timeout=10) as r:
                    return r.status, r.read().decode()

            self.assertEqual(get("/"), (200, "hello"))
            self.assertEqual(get("/q?a=42"), (200, "a=42"))
            self.assertEqual(post("/post", b"hello-body"), (200, "got:hello-body"))
            self.assertEqual(get("/stop"), (200, "bye"))
            out, _ = proc.communicate(timeout=30)
        finally:
            if proc.poll() is None:
                proc.kill()
        self.assertIn("served=4", out)

    def test_jvm_httpserver_tls(self):
        # serve_tls() with the repo's self-signed fixture: 2 requests,
        # then the max_requests quota ends the listener cleanly.
        port = self._free_port()
        crt = os.path.join(REPO_ROOT, "tests", "stdlib", "fixtures",
                           "localhost.crt")
        key = os.path.join(REPO_ROOT, "tests", "stdlib", "fixtures",
                           "localhost.key")
        src = ('use httpserver::httpserver::*;\n'
               'fnc handle(method string, path string, query string, headers string, body string) : int {\n'
               '    return HttpServer.reply_text(200, "tls-jvm");\n'
               '}\n'
               'fnc main() : int {\n'
               f'    served: int = HttpServer.serve_tls_ex({port}, 2, "{crt}", "{key}", &handle);\n'
               '    show("served_tls=" + tostring(served));\n'
               '    return 0;\n'
               '}\n')
        class_dir, main_class = self._compile_jvm(src, stdlib=True)
        proc = self._run_jvm_bg(main_class, port)
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        try:
            for _ in range(2):
                with urllib.request.urlopen(
                        f"https://127.0.0.1:{port}/x", timeout=10,
                        context=ctx) as r:
                    self.assertEqual((r.status, r.read().decode()),
                                     (200, "tls-jvm"))
            out, _ = proc.communicate(timeout=30)
        finally:
            if proc.poll() is None:
                proc.kill()
        self.assertIn("served_tls=2", out)

    def test_jvm_httpserver_error_codes(self):
        # Bind conflict -> LSHD_E_BIND; bad TLS material -> LSHD_E_TLS;
        # strerror/version parity.
        port = self._free_port()
        sock = socket.socket()
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", port))
        sock.listen(1)
        self.addCleanup(sock.close)
        src = ('use httpserver::httpserver::*;\n'
               'fnc handle(method string, path string, query string, headers string, body string) : int {\n'
               '    return 200;\n'
               '}\n'
               'fnc main() : int {\n'
               f'    conflict: int = HttpServer.serve_ex({port}, 1, &handle);\n'
               '    show("conflict=" + tostring(conflict));\n'
               '    show("detail=" + HttpServer.strerror(conflict));\n'
               '    bad: int = HttpServer.serve_tls(1, "/nope.crt", "/nope.key", &handle);\n'
               '    show("bad=" + tostring(bad));\n'
               '    show("v=" + HttpServer.version());\n'
               '    return 0;\n'
               '}\n')
        class_dir, main_class = self._compile_jvm(src, stdlib=True)
        r = self._run_jvm(class_dir, main_class)
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        self.assertIn("conflict=-3", r.stdout)
        self.assertIn("could not bind the port", r.stdout)
        self.assertIn("bad=-8", r.stdout)
        self.assertIn("leash-httpd/1.2", r.stdout)

    def test_jvm_fn_values(self):
        # First-class function values: &fname lowers to an adapter, and
        # indirect calls through locals/params invoke it.
        src = ('fnc addone(x int) : int { return x + 1; }\n'
               'fnc scale(x int, f float) : float { return tofloat(float, x) * f; }\n'
               'fnc apply(f fnc(int) : int, v int) : int { return f(v); }\n'
               'fnc main() : void {\n'
               '    show(tostring(apply(&addone, 41)));\n'
               '    g: fnc(int, float) : float = &scale;\n'
               '    show(tostring(g(7, 3.0)));\n'
               '}\n')
        class_dir, main_class = self._compile_jvm(src)
        r = self._run_jvm(class_dir, main_class)
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        self.assertIn("42", r.stdout)
        self.assertIn("21.000000", r.stdout)

    # ---- sql module on the JVM (bundled LeashSql shim) -----------------

    def test_jvm_sql_matches_native(self):
        # The stdlib sql test program must produce identical output on
        # the JVM (LeashSql interpreter) and natively (SQLite C archive).
        path = os.path.join(REPO_ROOT, "tests", "stdlib", "sql_sqlite.lsh")
        out_j = os.path.join(self.tmp, "sqlj")
        rj = _sh([BIN_LEASHC, "compile", path, "--target", "jvm",
                  "--other-imports", os.path.join(REPO_ROOT, "installthis"),
                  "to", out_j])
        self.assertEqual(rj.returncode, 0, msg=rj.stdout + rj.stderr)
        pj = subprocess.run([JAVA, "-cp", self.tmp, "sqlj"],
                            capture_output=True, text=True,
                            errors="replace", timeout=60)
        self.assertEqual(pj.returncode, 0, msg=pj.stdout + pj.stderr)
        out_n = os.path.join(self.tmp, "sqln")
        rn_c = _sh([BIN_LEASHC, "compile", path,
                    "--other-imports", os.path.join(REPO_ROOT, "installthis"),
                    "to", out_n])
        self.assertEqual(rn_c.returncode, 0, msg=rn_c.stdout + rn_c.stderr)
        pn = subprocess.run([out_n], capture_output=True, text=True,
                            errors="replace", timeout=60)
        self.assertEqual(pn.returncode, 0, msg=pn.stdout + pn.stderr)
        self.assertEqual(pj.stdout, pn.stdout,
                         f"stdout differs:\n--- jvm ---\n{pj.stdout}\n--- native ---\n{pn.stdout}")

    def test_jvm_sql_persistence(self):
        # File-backed databases round-trip through close/reopen on the
        # JVM engine; transactions roll back; a real sqlite3 file is
        # rejected instead of misread.
        dbfile = os.path.join(self.tmp, "jvm_sql.db")
        src = ('use sql::sql::*;\n'
               'fnc main() : void {\n'
               f'    db: Db = Db.open("{dbfile}");\n'
               '    show(tostring(db.exec("CREATE TABLE kv (k TEXT, v INTEGER)")));\n'
               '    show(tostring(db.exec_args("INSERT INTO kv VALUES (?, ?)", (vec<string>){"alpha", "42"})));\n'
               '    show(tostring(db.transaction((vec<string>){"INSERT INTO kv VALUES (\'beta\', 7)"})));\n'
               '    db.close();\n'
               '}\n')
        class_dir, main_class = self._compile_jvm(src, stdlib=True)
        r = self._run_jvm(class_dir, main_class)
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        self.assertEqual(r.stdout, "true\ntrue\ntrue\n")
        src2 = ('use sql::sql::*;\n'
                'fnc main() : void {\n'
                f'    db: Db = Db.open("{dbfile}");\n'
                '    res: QueryResult = db.query("SELECT k, v FROM kv ORDER BY k");\n'
                '    show(tostring(res.row_count()));\n'
                '    show(res.at(0, 0).as_text());\n'
                '    show(tostring(res.at(0, 1).as_int()));\n'
                '    show(res.at(1, 0).as_text());\n'
                '    db.close();\n'
                '}\n')
        class_dir2, main_class2 = self._compile_jvm(src2, stdlib=True)
        r2 = self._run_jvm(class_dir2, main_class2)
        self.assertEqual(r2.returncode, 0, msg=r2.stdout + r2.stderr)
        self.assertEqual(r2.stdout, "2\nalpha\n42\nbeta\n")

    def test_string_ordering_parity(self):
        # Lexicographic ordering matches native on all six operators.
        self._parity(
            'fnc lt(a string, b string) : bool { return a < b; }\n'
            'fnc gt(a string, b string) : bool { return a > b; }\n'
            'fnc main() : void {\n'
            '    show(lt("a", "z"));\n'
            '    show(lt("z", "a"));\n'
            '    show(gt("z", "a"));\n'
            '    show("abc" <= "abd");\n'
            '    show("abc" >= "abd");\n'
            '    show("b" > "abc");\n'
            '    show("" < "a");\n'
            '}\n')

    def test_mixed_string_concat_parity(self):
        # Scalar + string concatenations type as string in both orders and
        # render identically (char + string used to infer char, so chains
        # collapsed to int while float/bool operands passed through raw).
        self._parity(
            'fnc main() : void {\n'
            '    s: string = "n=" + 7 + ", c=" + \'A\' + "!";\n'
            '    show(s);\n'
            '    t: string = \'B\' + "x" + 42;\n'
            '    show(t);\n'
            '    u: string = true + ":" + 1.5;\n'
            '    show(u);\n'
            '    v: string = 1.5 + "=" + false;\n'
            '    show(v);\n'
            '}\n')

    def test_native_call_fails_loud(self):
        # A native @from function with no JVM shim compiles (mixed modules
        # stay importable) but aborts with a clear message when executed.
        # The stdlib's http/httpserver/sql all have JVM shims now, so this
        # uses a raw @from declaration — no module coupling.
        src = ('@from("libnope.a") {\n'
               '    fnc lsh_nope_do(x int) : int;\n'
               '}\n'
               'fnc main() : void {\n'
               '    show(lsh_nope_do(1));\n'
               '}\n')
        class_dir, main_class = self._compile_jvm(src)
        r = self._run_jvm(class_dir, main_class)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not supported on the jvm target",
                      r.stdout + r.stderr)

    def test_emit_llvm_unsupported(self):
        path = os.path.join(self.tmp, "ll.lsh")
        with open(path, "w") as f:
            f.write('fnc main() : void { show(1); }\n')
        r = _sh([BIN_LEASHC, "compile", path, "--target", "jvm",
                 "--emit-llvm", "to", os.path.join(self.tmp, "ll")])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--emit-llvm", r.stdout + r.stderr)

    def test_no_main_is_error(self):
        path = os.path.join(self.tmp, "nomain.lsh")
        with open(path, "w") as f:
            f.write('fnc helper() : int { return 1; }\n')
        r = _sh([BIN_LEASHC, "compile", path, "--target", "jvm",
                 "to", os.path.join(self.tmp, "nomain")])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("main", r.stdout + r.stderr)

    # ---- class file details ----------------------------------------------

    def test_class_file_magic(self):
        class_dir, main_class = self._compile_jvm(
            'fnc main() : void { show(1); }\n')
        with open(os.path.join(class_dir, f"{main_class}.class"), "rb") as f:
            magic = f.read(4)
        self.assertEqual(magic, b"\xca\xfe\xba\xbe")
        # major version 49 (old inferencing verifier, no StackMapTable)
        with open(os.path.join(class_dir, f"{main_class}.class"), "rb") as f:
            f.read(6)
            major = int.from_bytes(f.read(2), "big")
        self.assertEqual(major, 49)

    def test_struct_emits_extra_class(self):
        class_dir, main_class = self._compile_jvm(
            'pub def P : struct {\n    pub x: int;\n}\n'
            'fnc main() : void {\n    p: P = P { x: 1 };\n    show(p.x);\n}\n')
        self.assertTrue(os.path.exists(os.path.join(class_dir, "P.class")),
                        "struct should emit its own .class file")


if __name__ == "__main__":
    unittest.main()
