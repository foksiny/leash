#!/usr/bin/env python3
"""Arena reference-semantics and lifetime regression tests.

A class instance is a reference value. These tests pin the cases where
arena mode used to split one object into unrelated root copies or keep a
raw pointer into a reclaimed frame:

* a field read bound to a local (``cur = root.nxt``) must be treated as
  an outer alias, so stores through it are rooted;
* a class stored into a container or field must keep its identity;
* the LLVM IR builder stdlib (parser + builder + many class bodies) must
  produce exactly the same text under arena and GC;
* the regex stdlib must not crash on a character class with a quantifier.
"""
import os
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEASHC = os.path.join(ROOT, "bin", "leashc")


def _run(source, gc=False, stdin=None, extra=None):
    with tempfile.TemporaryDirectory(prefix="leash_alias_") as td:
        path = os.path.join(td, "main.lsh")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(source)
        cmd = [LEASHC, "run", path]
        if gc:
            cmd.append("--gc")
        if extra:
            cmd += extra
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                           timeout=180, input=stdin)
        return r.returncode, r.stdout, r.stderr


class ArenaAliasParity(unittest.TestCase):
    def test_member_alias_store_is_rooted(self):
        # Local bound to a field of an outer-owned object; the second
        # node used to keep a raw pointer into the callee's frame.
        src = """pub def Node : class { pub nxt: Node; pub val: int; }
fnc append(root Node, v int) {
    it: Node; it.val = v;
    if root.nxt == null { root.nxt = it; }
    else {
        cur: Node = root.nxt;
        while cur.nxt != null { cur = cur.nxt; }
        cur.nxt = it;
    }
}
fnc churn() {
    junk: vec<int>; i: int = 0;
    while i < 32 { junk.pushb(i); i = i + 1; }
}
fnc main() {
    root: Node;
    append(root, 7); append(root, 9); churn();
    cur: Node = root.nxt; sum: int = 0;
    while cur != null { sum = sum + cur.val; cur = cur.nxt; }
    show(sum);
}
"""
        rc, out, err = _run(src)
        self.assertEqual(rc, 0, out + err)
        self.assertIn("16", out)
        rc2, out2, err2 = _run(src, gc=True)
        self.assertEqual(rc2, 0, out2 + err2)
        self.assertIn("16", out2)

    def test_class_identity_across_field_and_container(self):
        src = """def Fn : class { pub name: string; pub fnc set(s string) { this.name = s; } }
fnc stash(v vec<Fn>, f Fn) { v.pushb(f); }
fnc main() {
    v: vec<Fn> = (vec<Fn>){};
    f: Fn = Fn{name: "before"};
    stash(v, f);
    f.set("after");
    show(v.get(0).name);
}
"""
        rc, out, err = _run(src)
        self.assertEqual(rc, 0, out + err)
        self.assertIn("after", out)
        self.assertEqual(out.split("---")[0].strip(), _run(src, gc=True)[1].split("---")[0].strip())

    def test_regex_quantified_char_class_does_not_crash(self):
        src = 'use text::regex::Regex;\nfnc main() { show(Regex.is_match("[ab]+", "xabx")); }\n'
        rc, out, err = _run(src, extra=["--other-imports", os.path.join(ROOT, "installthis")])
        self.assertEqual(rc, 0, out + err)
        self.assertIn("true", out)

    def test_regex_possessive_lookahead_findall_parity(self):
        src = r'''use text::regex::Regex;
fnc main(): void {
  show("1:", Regex.is_match("a++", "aaa"));
  v := Regex.find_all("a++", "aaa b");
  show("2:", v.size);
  show("3:", Regex.is_match("\\d+(?= dollars)", "100 dollars"));
  show("4:", Regex.is_match("\\d+(?= euros)", "100 dollars"));
  show("5:", Regex.is_match("(?>a*)a", "aaa"));
}
'''
        outs = []
        for gc in (False, True):
            rc, out, err = _run(src, gc=gc,
                                extra=["--other-imports", os.path.join(ROOT, "installthis")])
            self.assertEqual(rc, 0, out + err)
            outs.append("\n".join(l for l in out.splitlines()
                                  if not l.startswith("--- Executed at")))
        self.assertEqual(outs[0], outs[1])

    def test_union_vec_and_class_payload_parity(self):
        src = '''def Box : class { n: int; }
def U : union { i: int; v: vec<string>; b: Box; }
fnc churn(): void {
  junk: vec<int>; i: int = 0;
  while i < 128 { junk.pushb(i); i = i + 1; }
}
fnc main(): void {
  uv: U = U { v: (vec<string>){} };
  uv.v.pushb("vec-payload");
  ub: U = U { b: Box { n: 42 } };
  ub.b.n = 43;
  churn();
  show(uv.v.get(0));
  show(tostring(ub.b.n));
  vs: vec<U>;
  vs.pushb(uv);
  vs.pushb(ub);
  churn();
  show(vs.get(0).v.get(0));
  show(tostring(vs.get(1).b.n));
}
'''
        outs = []
        for gc in (False, True):
            rc, out, err = _run(src, gc=gc)
            self.assertEqual(rc, 0, out + err)
            outs.append("\n".join(l for l in out.splitlines()
                                  if not l.startswith("--- Executed at")))
        self.assertEqual(outs[0], outs[1])
        self.assertEqual(outs[0], "vec-payload\n43\nvec-payload\n43")

    def test_ffi_native_pointer_field_parity(self):
        import shutil
        with tempfile.TemporaryDirectory(prefix="leash_ffi_") as td:
            csrc = os.path.join(td, "native.c")
            with open(csrc, "w") as fh:
                fh.write('static const char kMsg[] = "native-ok";\n'
                         'const char* lsh_test_ptr(void) { return kMsg; }\n')
            subprocess.run(["cc", "-c", csrc, "-o", os.path.join(td, "native.o")],
                           cwd=td, check=True, capture_output=True)
            subprocess.run(["ar", "rcs", os.path.join(td, "libfptr.a"),
                            os.path.join(td, "native.o")],
                           cwd=td, check=True, capture_output=True)
            src = os.path.join(td, "main.lsh")
            with open(src, "w") as fh:
                fh.write('@from("libfptr.a") { fnc lsh_test_ptr() : pointer<char>; };\n'
                         'def H : struct { p: pointer<char>; };\n'
                         'fnc main(): void {\n'
                         '  h: H = H { p: null };\n'
                         '  h.p = lsh_test_ptr();\n'
                         '  show(h.p);\n'
                         '}\n')
            outs = []
            for gc in (False, True):
                cmd = [LEASHC, "run", src] + (["--gc"] if gc else [])
                r = subprocess.run(cmd, cwd=td, capture_output=True, text=True, timeout=180)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                outs.append("\n".join(l for l in r.stdout.splitlines()
                                      if not l.startswith("--- Executed at")))
            self.assertEqual(outs[0], outs[1])
            self.assertEqual(outs[0], "native-ok")

    def test_union_string_payload_parity(self):
        src = '''def U : union { i: int; s: string; }
fnc churn(): void {
  v: vec<int>;
  i: int = 0;
  while i < 64 { v.pushb(i); i = i + 1; }
}
fnc main(): void {
  u: U = U { s: "hello-union-payload" };
  churn();
  show(u.s);
}
'''
        outs = []
        for gc in (False, True):
            rc, out, err = _run(src, gc=gc)
            self.assertEqual(rc, 0, out + err)
            outs.append("\n".join(l for l in out.splitlines()
                                  if not l.startswith("--- Executed at")))
        self.assertEqual(outs[0], outs[1])
        self.assertEqual(outs[0], "hello-union-payload")

    def test_worker_raw_pointer_argument_parity(self):
        src = '''worker fnc w(p: *int): void {
  show(tostring(*p));
}
fnc main(): void {
  x: int = 17;
  spawn w(&x);
}
'''
        outs = []
        for gc in (False, True):
            rc, out, err = _run(src, gc=gc)
            self.assertEqual(rc, 0, out + err)
            outs.append("\n".join(l for l in out.splitlines()
                                  if not l.startswith("--- Executed at")))
        self.assertEqual(outs[0], outs[1])
        self.assertEqual(outs[0], "17")

    def test_proven_safe_raw_pointer_return_parity(self):
        src = '''fnc make() : *int {
  p: *int = malloc(8);
  *p = 9;
  return p;
}
fnc main(): void {
  q: *int = make();
  show(tostring(*q));
}
'''
        outs = []
        for gc in (False, True):
            rc, out, err = _run(src, gc=gc)
            self.assertEqual(rc, 0, out + err)
            outs.append("\n".join(l for l in out.splitlines()
                                  if not l.startswith("--- Executed at")))
        self.assertEqual(outs[0], outs[1])
        self.assertEqual(outs[0], "9")

    def test_escaping_local_address_return_arena(self):
        # Arena extends the language: a returned &local is root-allocated
        # instead of dangling. GC mode still rejects this pattern.
        src = '''fnc make() : *int {
  x: int = 5;
  return &x;
}
fnc main(): void {
  show(tostring(*make()));
}
'''
        rc, out, err = _run(src)
        self.assertEqual(rc, 0, out + err)
        self.assertIn("5", out)

    def test_escaping_address_of_local_parity(self):
        src = '''def B : struct { p: *int; }
g: B = B { p: null };
def M : class { p: *int;
  fnc stash(v *int) { this.p = v; }
}
fnc main(): void {
  x: int = 3;
  g = B { p: &x };
  show(tostring(*g.p));
  x = 4;
  show(tostring(*g.p));
  m := create M();
  m.stash(&x);
  show(tostring(*m.p));
}
'''
        outs = []
        for gc in (False, True):
            rc, out, err = _run(src, gc=gc)
            self.assertEqual(rc, 0, out + err)
            outs.append("\n".join(l for l in out.splitlines()
                                  if not l.startswith("--- Executed at")))
        self.assertEqual(outs[0], outs[1])
        self.assertEqual(outs[0], "3\n4\n4")

    def test_libc_heap_pointer_parity(self):
        src = '''fnc main(): void {
  p: *int = malloc(8);
  *p = 7;
  show(tostring(*p));
  free(p);
}
'''
        outs = []
        for gc in (False, True):
            rc, out, err = _run(src, gc=gc)
            self.assertEqual(rc, 0, out + err)
            outs.append("\n".join(l for l in out.splitlines()
                                  if not l.startswith("--- Executed at")))
        self.assertEqual(outs[0], outs[1])
        self.assertEqual(outs[0], "7")

    def test_sql_ffi_pointer_fields_parity(self):
        prog = os.path.join(ROOT, "tests", "stdlib", "sql_sqlite.lsh")
        outs = []
        for gc in (False, True):
            cmd = [LEASHC, "run", prog, "--other-imports", os.path.join(ROOT, "installthis")]
            if gc:
                cmd.append("--gc")
            r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=240,
                               stdin=subprocess.DEVNULL)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            outs.append("\n".join(l for l in r.stdout.splitlines()
                                  if not l.startswith("--- Executed at")))
        self.assertEqual(outs[0], outs[1])

    def test_worker_class_state_parity(self):
        src = '''def Box : class { n: int; }
worker fnc w(): void {
  b: Box = Box { n: 7 };
  show(tostring(b.n));
}
fnc main(): void {
  m: Box = Box { n: 3 };
  show(tostring(m.n));
  spawn w();
}
'''
        outs = []
        for gc in (False, True):
            rc, out, err = _run(src, gc=gc)
            self.assertEqual(rc, 0, out + err)
            outs.append("\n".join(l for l in out.splitlines()
                                  if not l.startswith("--- Executed at")))
        self.assertEqual(sorted(outs[0].splitlines()), sorted(outs[1].splitlines()))

    def test_llvm_ir_module_builder_parity(self):
        prog = os.path.join(ROOT, "tests", "stdlib", "llvm_ir.lsh")
        outs = []
        for gc in (False, True):
            cmd = [LEASHC, "run", prog, "--other-imports", os.path.join(ROOT, "installthis")]
            if gc:
                cmd.append("--gc")
            r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=240,
                               stdin=subprocess.DEVNULL)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            outs.append("\n".join(l for l in r.stdout.splitlines()
                                  if not l.startswith("--- Executed at")))
        self.assertEqual(outs[0], outs[1])


if __name__ == "__main__":
    unittest.main()
