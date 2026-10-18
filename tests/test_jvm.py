"""JVM bytecode backend tests (--target jvm).

These verify that the self-hosted compiler emits real, runnable JVM
class files whose observable behavior matches the native backend.
Skipped entirely when no `java` runtime is on PATH.
"""
import os
import shutil
import subprocess
import tempfile
import unittest

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

    def _compile_jvm(self, src, main_class=None, run_args=""):
        """Compile `src` to JVM bytecode; return (class_dir, main_class)."""
        name = main_class or self._next_name()
        path = os.path.join(self.tmp, f"{name}.lsh")
        with open(path, "w") as f:
            f.write(src)
        out = os.path.join(self.tmp, name)
        r = _sh([BIN_LEASHC, "compile", path, "--target", "jvm", "to", out])
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
        # Calling a native @from function on JVM compiles (mixed modules
        # stay importable) but aborts with a clear message when executed.
        src = ('use http::http::*;\n'
               'fnc main() : void {\n'
               '    r: HttpResponse = Http.get("http://127.0.0.1:1/x");\n'
               '    show(r.status);\n'
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
