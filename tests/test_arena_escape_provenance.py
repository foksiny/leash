#!/usr/bin/env python3
"""Arena: provenance through locals and escaping &local pointees.

The arena used to reject a raw pointer once it passed through a local, and
it rooted the slot for an escaping `&local` without rooting the value the
slot held. These tests pin both behaviours: safe provenance flows through
copies, and the pointee survives arena checkpoints.
"""
import os
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEASHC = os.path.join(ROOT, "bin", "leashc")


def _run(source, gc=False, poison=False):
    with tempfile.TemporaryDirectory(prefix="leash_prov_") as td:
        path = os.path.join(td, "main.lsh")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(source)
        cmd = [LEASHC, "run", path]
        if gc:
            cmd.append("--gc")
        env = dict(os.environ)
        if poison:
            env["LEASH_ARENA_POISON"] = "1"
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                           timeout=180, env=env)
        return r.returncode, r.stdout, r.stderr


def _out(r):
    return "\n".join(l for l in r[1].splitlines()
                     if not l.startswith("--- Executed at"))


CHURN = """\
i: int = 0;
while i < 2000 {
    t: string = "x" + tostring(i);
    i = i + 1;
}
"""


class ArenaEscapeProvenance(unittest.TestCase):
    def test_pointer_local_return(self):
        src = """\
fnc make() : *int {
    x: int = 5;
    p: *int = &x;
    return p;
}
fnc main() { r: *int = make(); show(*r); }
"""
        rc, out, err = _run(src)
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(_out((rc, out, err)), "5")

    def test_pointer_local_store(self):
        src = """\
def B : struct { p: *int; }
g: B = B { p: null };
fnc set() {
    x: int = 7;
    p: *int = &x;
    g.p = p;
}
fnc main() { set(); show(*(g.p)); }
"""
        rc, out, err = _run(src)
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(_out((rc, out, err)), "7")

    def test_pointer_local_container_push(self):
        src = """\
g: vec<*int>;
fnc set() {
    x: int = 8;
    p: *int = &x;
    g.pushb(p);
}
fnc main() { set(); show(*(g.get(0))); }
"""
        rc, out, err = _run(src)
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(_out((rc, out, err)), "8")

    def test_raw_pointer_global_null_initializer(self):
        src = """\
g: *int = null;
fnc main() { show(g == null); }
"""
        rc, out, err = _run(src)
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(_out((rc, out, err)), "true")

    def test_escaping_string_survives_churn(self):
        src = """\
def B : struct { p: *string; }
g: B = B { p: null };
fnc set() {
    s: string = "a";
    s = s + "b";
    g.p = &s;
}
fnc churn() {
""" + CHURN + """\
}
fnc main() { set(); churn(); show(*(g.p)); }
"""
        rc, out, err = _run(src, poison=True)
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(_out((rc, out, err)), "ab")

    def test_escaping_struct_survives_churn(self):
        src = """\
def V : struct { n: int; }
def B : struct { p: *V; }
g: B = B { p: null };
fnc set() {
    v: V = V { n: 37 };
    g.p = &v;
}
fnc churn() {
""" + CHURN + """\
}
fnc main() { set(); churn(); show(g.p->n); }
"""
        rc, out, err = _run(src, poison=True)
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(_out((rc, out, err)), "37")

    def test_escaping_vec_survives_churn(self):
        src = """\
def B : struct { p: *vec<int>; }
g: B = B { p: null };
fnc set() {
    v: vec<int>;
    v.pushb(42);
    g.p = &v;
}
fnc churn() {
""" + CHURN + """\
}
fnc main() { set(); churn(); show((*(g.p)).get(0)); }
"""
        rc, out, err = _run(src, poison=True)
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(_out((rc, out, err)), "42")

    def test_worker_scalar_address_escape(self):
        src = """\
def B : struct { p: *int; }
g: B = B { p: null };
worker fnc w() : void { show(*(g.p)); }
fnc main() {
    x: int = 11;
    g.p = &x;
    spawn w();
}
"""
        outs = []
        for gc in (False, True):
            r = _run(src, gc=gc)
            self.assertEqual(r[0], 0, r[1] + r[2])
            outs.append(sorted(_out(r).splitlines()))
        self.assertEqual(outs[0], outs[1])

    def test_worker_pod_struct_address_escape(self):
        src = """\
def V : struct { n: int; }
def B : struct { p: *V; }
g: B = B { p: null };
worker fnc w() : void { show(g.p->n); }
fnc main() {
    v: V = V { n: 23 };
    g.p = &v;
    spawn w();
}
"""
        outs = []
        for gc in (False, True):
            r = _run(src, gc=gc)
            self.assertEqual(r[0], 0, r[1] + r[2])
            outs.append(sorted(_out(r).splitlines()))
        self.assertEqual(outs[0], outs[1])

    def _worker_pointee_parity(self, src, expected):
        outs = []
        for gc in (False, True):
            r = _run(src, gc=gc, poison=not gc)
            self.assertEqual(r[0], 0, r[1] + r[2])
            outs.append(sorted(_out(r).splitlines()))
        self.assertEqual(outs[0], outs[1])
        self.assertEqual(outs[0], expected)

    def test_worker_string_pointee(self):
        self._worker_pointee_parity("""\
def B : struct { p: *string; }
g: B = B { p: null };
worker fnc w() : void { show(*(g.p)); }
fnc main() {
    s: string = "a" + "b";
    g.p = &s;
    spawn w();
}
""", ["ab"])

    def test_worker_vec_pointee(self):
        self._worker_pointee_parity("""\
def B : struct { p: *vec<int>; }
g: B = B { p: null };
worker fnc w() : void { show((*(g.p)).get(0)); }
fnc main() {
    v: vec<int>;
    v.pushb(42);
    g.p = &v;
    spawn w();
}
""", ["42"])

    def test_worker_hash_pointee(self):
        self._worker_pointee_parity("""\
def B : struct { p: *hash<string,int>; }
g: B = B { p: null };
worker fnc w() : void { show((*(g.p))["k"]); }
fnc main() {
    h: hash<string,int>;
    h["k"] = 7;
    g.p = &h;
    spawn w();
}
""", ["7"])

    def test_worker_class_pointee(self):
        self._worker_pointee_parity("""\
def C : class { n: int; }
def B : struct { p: *C; }
g: B = B { p: null };
worker fnc w() : void { show(g.p->n); }
fnc main() {
    c: C = C { n: 91 };
    g.p = &c;
    spawn w();
}
""", ["91"])

    def test_worker_container_pointer_argument(self):
        self._worker_pointee_parity("""\
worker fnc w(p *vec<int>) : void { v := *p; show(v.get(0)); }
fnc main() {
    v: vec<int>;
    v.pushb(33);
    spawn w(&v);
}
""", ["33"])


if __name__ == "__main__":
    unittest.main()
