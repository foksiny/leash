#!/usr/bin/env python3
"""Arena worker/root stress and differential tests.

These go beyond the small parity cases: several workers read the same
root-owned pointees while the spawner churns its own frames, many root
blocks are created before a spawn, and a generated differential corpus
compares arena and GC across combinations of escaping container/class
types, worker counts and churn sizes.
"""
import os
import random
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEASHC = os.path.join(ROOT, "bin", "leashc")


def _run(source, gc=False, poison=False):
    with tempfile.TemporaryDirectory(prefix="leash_stress_") as td:
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
                           errors="replace", timeout=180, env=env)
        stdout = "\n".join(l for l in r.stdout.splitlines()
                           if not l.startswith("--- Executed at"))
        return r.returncode, sorted(l for l in stdout.splitlines() if l), r.stderr


MULTI = """\
def C : class { n: int; }
def B : struct { s: *string; v: *vec<int>; c: *C; }
g: B = B { s: null, v: null, c: null };
worker fnc w() : void {
    show(*(g.s));
    show((*(g.v)).get(0));
    show(g.c->n);
}
fnc churn() {
    i: int = 0;
    while i < %d {
        t: string = "x" + tostring(i);
        q: vec<int>; q.pushb(i);
        i = i + 1;
    }
}
fnc main() {
    s: string = "a" + "b";
    v: vec<int>;
    v.pushb(7);
    c: C = C { n: 91 };
    g.s = &s;
    g.v = &v;
    g.c = &c;
    i: int = 0;
    while i < %d { spawn w(); i = i + 1; }
    churn();
}
"""


MANY_ROOTS = """\
g: vec<string>;
worker fnc w() : void { show(g.get(0)); show(g.get(%d)); }
fnc main() {
    i: int = 0;
    while i < %d {
        s: string = "v" + tostring(i);
        g.pushb(s);
        i = i + 1;
    }
    spawn w();
}
"""


class ArenaWorkerStress(unittest.TestCase):
    def _parity(self, src, expected, poison=True):
        for gc in (False, True):
            rc, lines, err = _run(src, gc=gc, poison=poison and not gc)
            self.assertEqual(rc, 0, err)
            self.assertEqual(lines, expected)

    def _arena_exact(self, src, expected, gc_must_survive=True):
        """Both memory modes must produce the exact value. GC used to read
        a reclaimed stack slot here; escaping address slots are now rooted
        heap cells in GC mode too."""
        rc, lines, err = _run(src, gc=False, poison=True)
        self.assertEqual(rc, 0, err)
        self.assertEqual(lines, expected)
        if gc_must_survive:
            rc, lines, err = _run(src, gc=True)
            self.assertEqual(rc, 0, err)
            self.assertEqual(lines, expected)

    def test_multi_worker_shared_root_pointees(self):
        src = MULTI % (1500, 4)
        for token, count in (("7", 4), ("91", 4), ("ab", 4)):
            self._arena_count(src, token, count)

    def _arena_count(self, src, token, count):
        """Workers share the show buffer, so short lines can concatenate.
        Count occurrences in the joined output instead of line boundaries."""
        for gc in (False, True):
            rc, lines, err = _run(src, gc=gc, poison=not gc)
            self.assertEqual(rc, 0, err)
            self.assertEqual("".join(lines).count(token), count)

    def test_root_blocks_published_after_many_allocations(self):
        n = 500
        src = MANY_ROOTS % (n - 1, n)
        self._parity(src, ["v0", "v" + str(n - 1)])

    def test_root_vec_growth_uses_root_buffer(self):
        # A pushb on a root-escaped vec must grow its buffer in the root
        # arena; otherwise a churning frame reclaims the element and the
        # worker observes a later iteration's value.
        src = """\
def B : struct { p: *vec<int>; }
g: B = B { p: null };
worker fnc w() : void {
    i: int = 0;
    while i < 200 { show((*(g.p)).get(0)); i = i + 1; }
}
fnc churn() {
    i: int = 0;
    while i < 3000 {
        q: vec<int>; q.pushb(i);
        i = i + 1;
    }
}
fnc main() {
    v: vec<int>;
    v.pushb(4);
    g.p = &v;
    spawn w();
    churn();
}
"""
        self._arena_exact(src, ["4"] * 200)

    def test_root_footprint_stays_bounded(self):
        import re
        n = 2000
        src = MANY_ROOTS % (n - 1, n)
        with tempfile.TemporaryDirectory(prefix="leash_rootmem_") as td:
            path = os.path.join(td, "main.lsh")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(src)
            env = dict(os.environ)
            env["LEASH_ARENA_STATS"] = "1"
            r = subprocess.run([LEASHC, "run", path], cwd=ROOT,
                               capture_output=True, text=True,
                               errors="replace", timeout=180, env=env)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            m = re.search(r"root peak (\d+) block bytes", r.stderr)
            self.assertIsNotNone(m, r.stderr)
            peak = int(m.group(1))
            self.assertLess(peak, 2 * 1024 * 1024)

    def test_root_string_growth_while_worker_reads(self):
        # Growing a sole root block must never move it while workers can
        # hold pointers into it; the runtime forces allocate-then-copy.
        src = """\
def B : struct { p: *string; }
g: B = B { p: null };
worker fnc w() : void {
    i: int = 0;
    while i < 1000 { q: string = *(g.p); i = i + 1; }
    show((*(g.p)).bsize);
}
fnc main() {
    s: string = "start";
    g.p = &s;
    i: int = 0;
    while i < 200 {
        s = s + "0123456789012345678901234567890123456789";
        i = i + 1;
    }
    spawn w();
}
"""
        self._parity(src, ["8005"])

    def test_generated_worker_pointee_corpus(self):
        rng = random.Random(20261123)
        kinds = ["string", "vec", "hash", "class", "struct"]
        for case in range(12):
            kind = kinds[case % len(kinds)]
            workers = 1 + (case % 3)
            churn = 40 + rng.randrange(200)
            if kind == "string":
                decl = 'def B : struct { p: *string; }\n'
                init = '    s: string = "v" + tostring(%d);\n    g.p = &s;\n' % case
                read = 'show(*(g.p));'
                expect = "v" + str(case)
                glob = 'g: B = B { p: null };\n'
            elif kind == "vec":
                decl = 'def B : struct { p: *vec<int>; }\n'
                init = '    v: vec<int>;\n    v.pushb(%d);\n    g.p = &v;\n' % (case + 3)
                read = 'show((*(g.p)).get(0));'
                expect = str(case + 3)
                glob = 'g: B = B { p: null };\n'
            elif kind == "hash":
                decl = 'def B : struct { p: *hash<string,int>; }\n'
                init = '    h: hash<string,int>;\n    h["k"] = %d;\n    g.p = &h;\n' % (case + 5)
                read = 'show((*(g.p))["k"]);'
                expect = str(case + 5)
                glob = 'g: B = B { p: null };\n'
            elif kind == "class":
                decl = 'def C : class { n: int; }\ndef B : struct { p: *C; }\n'
                init = '    c: C = C { n: %d };\n    g.p = &c;\n' % (case + 11)
                read = 'show(g.p->n);'
                expect = str(case + 11)
                glob = 'g: B = B { p: null };\n'
            else:
                decl = 'def V : struct { n: int; }\ndef B : struct { p: *V; }\n'
                init = '    v: V = V { n: %d };\n    g.p = &v;\n' % (case + 17)
                read = 'show(g.p->n);'
                expect = str(case + 17)
                glob = 'g: B = B { p: null };\n'
            src = (decl + glob +
                   'worker fnc w() : void { %s }\n' % read +
                   'fnc churn() {\n    i: int = 0;\n'
                   '    while i < %d {\n        t: string = "z" + tostring(i);\n'
                   '        q: vec<int>; q.pushb(i);\n        i = i + 1;\n    }\n}\n' % churn +
                   'fnc main() {\n' + init +
                   '    i: int = 0;\n    while i < %d { spawn w(); i = i + 1; }\n'
                   '    churn();\n}\n' % workers)
            with self.subTest(kind=kind, case=case, workers=workers):
                self._arena_count(src, expect, workers)


if __name__ == "__main__":
    unittest.main()
