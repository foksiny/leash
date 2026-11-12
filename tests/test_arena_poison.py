#!/usr/bin/env python3
"""Build and run the arena poison-mode harness (tests/test_arena_poison.c).

The arena reclaims a scope by rewinding the bump pointer.  Reclaimed
bytes are filled with 0xDD by default (LEASH_ARENA_POISON=0 opts out),
so a stale pointer into a dead scope reads as obvious garbage instead
of whatever the allocator put there next.

Run directly:  python3 tests/test_arena_poison.py
Or via unittest discovery from the repo root:
    python3 -m unittest tests.test_arena_poison
"""
import os
import shutil
import subprocess
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestArenaPoison(unittest.TestCase):
    def _compiler(self):
        cc = os.environ.get("CC")
        if cc:
            return cc.split()
        for cand in ("cc", "gcc", "clang"):
            path = shutil.which(cand)
            if path:
                return [path]
        self.skipTest("no C compiler available")

    def _build(self, cc, td):
        exe = os.path.join(td, "test_arena_poison")
        build = subprocess.run(
            cc + ["tests/test_arena_poison.c", "leash/gc.c",
                  "-DLEASH_ARENA", "-O1", "-o", exe, "-pthread"],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=300)
        self.assertEqual(build.returncode, 0, build.stderr)
        return exe

    def test_poison_harness(self):
        """Poison-on-by-default, opt-out, escapes, and loop reset."""
        cc = self._compiler()
        with tempfile.TemporaryDirectory() as td:
            exe = self._build(cc, td)
            run = subprocess.run([exe], cwd=REPO_ROOT, capture_output=True,
                                 text=True, timeout=300)
            self.assertEqual(run.returncode, 0,
                             run.stdout + "\n" + run.stderr)
            self.assertIn("all tests passed", run.stdout)

    def test_poison_mode_preserves_program_output(self):
        """A poisoned arena build must still print exactly what the plain
        build prints: the fill must never touch live data."""
        leashc = os.path.join(REPO_ROOT, "bin", "leashc")
        if not os.path.exists(leashc):
            self.skipTest("bin/leashc missing (run `leash self-host`)")
        src = ("fnc work(n: int) : string {\n"
               "    s: string = \"\";\n"
               "    i: int = 0;\n"
               "    while i < n {\n"
               "        s = s + \"x\";\n"
               "        i = i + 1;\n"
               "    }\n"
               "    return s;\n"
               "}\n"
               "fnc main() {\n"
               "    show(work(64), \"|\");\n"
               "    v: vec<string>;\n"
               "    for i: int = 0; i < 8; i = i + 1 {\n"
               "        v.pushb(tostring(i));\n"
               "    }\n"
               "    for i: int = 0; i < v.size; i = i + 1 {\n"
               "        show(v.get(i), \" \");\n"
               "    }\n"
               "    show(\"|\");\n"
               "}\n")
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "poison.lsh")
            with open(path, "w") as fh:
                fh.write(src)
            outs = {}
            for arena in (False, True):
                exe = os.path.join(td, "out_ar" if arena else "out_plain")
                cmd = [leashc, "compile", path, "to", exe]
                if arena:
                    cmd.append("--arena")
                build = subprocess.run(cmd, cwd=REPO_ROOT,
                                       capture_output=True, text=True,
                                       timeout=600)
                self.assertEqual(build.returncode, 0,
                                 build.stderr + "\n" + build.stdout)
                env = dict(os.environ)
                env["LEASH_ARENA_POISON"] = "1"
                run = subprocess.run([exe], cwd=td, capture_output=True,
                                     text=True, timeout=120, env=env)
                self.assertEqual(run.returncode, 0, run.stderr)
                outs[arena] = run.stdout
            self.assertEqual(outs[True], outs[False])


    def test_poison_opt_out_preserves_program_output(self):
        """Arena runs correctly with poison disabled (LEASH_ARENA_POISON=0)."""
        leashc = os.path.join(REPO_ROOT, "bin", "leashc")
        if not os.path.exists(leashc):
            self.skipTest("bin/leashc missing (run `leash self-host`)")
        src = ("fnc work(n: int) : string {\n"
               "    s: string = \"\";\n"
               "    i: int = 0;\n"
               "    while i < n { s = s + \"x\"; i = i + 1; }\n"
               "    return s;\n"
               "}\n"
               "fnc main() {\n"
               "    show(work(64), \"|\");\n"
               "    v: vec<int>;\n"
               "    for i: int = 0; i < 20; i = i + 1 { v.pushb(i * 3); }\n"
               "    sum: int = 0;\n"
               "    for i: int = 0; i < v.size; i = i + 1 { sum = sum + v.get(i); }\n"
               "    show(sum, \"|\");\n"
               "}\n")
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "p.lsh")
            with open(path, "w") as fh:
                fh.write(src)
            outs = {}
            for arena in (False, True):
                exe = os.path.join(td, "out_ar" if arena else "out_plain")
                cmd = [leashc, "compile", path, "to", exe]
                if arena:
                    cmd.append("--arena")
                build = subprocess.run(cmd, cwd=REPO_ROOT,
                                       capture_output=True, text=True,
                                       timeout=600)
                self.assertEqual(build.returncode, 0, build.stderr)
                env = dict(os.environ)
                env["LEASH_ARENA_POISON"] = "0"
                run = subprocess.run([exe], cwd=td, capture_output=True,
                                     text=True, timeout=120, env=env)
                self.assertEqual(run.returncode, 0, run.stderr)
                outs[arena] = run.stdout
            self.assertEqual(outs[True], outs[False])


if __name__ == "__main__":
    unittest.main()
