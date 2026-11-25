#!/usr/bin/env python3
"""Seeded generated programs checked under arena and GC."""
import os
import random
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEASHC = os.path.join(ROOT, "bin", "leashc")


def _run(src, gc):
    with tempfile.TemporaryDirectory(prefix="leash_fuzz_") as td:
        path = os.path.join(td, "main.lsh")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(src)
        cmd = [LEASHC, "run", path, "--other-imports",
               os.path.join(ROOT, "installthis")]
        if gc:
            cmd.append("--gc")
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                           errors="replace", timeout=120)
        out = "\n".join(l for l in r.stdout.splitlines()
                        if not l.startswith("--- Executed at"))
        return r.returncode, sorted(out.splitlines()), r.stderr


class GeneratedDifferential(unittest.TestCase):
    def test_seeded_corpus(self):
        rng = random.Random(20261124)
        kinds = ["int", "string", "vec", "struct"]
        for case in range(200):
            kind = kinds[case % len(kinds)]
            n = rng.randrange(1, 40)
            churn = rng.randrange(0, 60)
            top = []
            body = []
            if kind == "int":
                body = ["  x: int = %d;" % n, "  show(x);"]
            elif kind == "string":
                body = ['  s: string = "v" + tostring(%d);' % n, "  show(s);"]
            elif kind == "vec":
                body = ["  v: vec<int>;", "  v.pushb(%d);" % n,
                        "  show(v.get(0));", "  show(v.size);"]
            else:
                top = ["def P : struct { n: int; };"]
                body = ["  p: P = P { n: %d };" % n, "  show(p.n);"]
            src = "\n".join(top + [
                "fnc churn() {",
                "  i: int = 0;",
                "  while i < %d { q: vec<int>; q.pushb(i); i = i + 1; }" % churn,
                "}",
                "fnc main() {",
            ] + body + ["  churn();", "}"]) + "\n"
            with self.subTest(case=case, kind=kind, n=n, churn=churn):
                a = _run(src, False)
                g = _run(src, True)
                self.assertEqual(a[0], 0, a[2])
                self.assertEqual(g[0], 0, g[2])
                self.assertEqual(a[1], g[1], (a[1], g[1], src))


if __name__ == "__main__":
    unittest.main()
