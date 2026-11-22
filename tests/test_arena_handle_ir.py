#!/usr/bin/env python3
"""Arena IR must reach vec/hash access through generation handles."""
import os
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEASHC = os.path.join(ROOT, "bin", "leashc")

SOURCE = """\
fnc main() {
    v: vec<int> = {};
    v.pushb(1);
    show(v.get(0));
    h: hash<string, int> = {"a": 1};
    show(h["a"]);
}
"""


class TestArenaHandleIR(unittest.TestCase):
    def test_container_access_uses_generation_handles(self):
        with tempfile.TemporaryDirectory() as td:
            # bin/leashc is a frozen stage-0 binary; build a stage-1
            # compiler from the CURRENT source so its codegen is tested.
            stage = os.path.join(td, "stage")
            build = subprocess.run(
                [LEASHC, "compile", os.path.join("compiler", "main.lsh"),
                 "to", stage],
                cwd=ROOT, capture_output=True, text=True, timeout=900)
            self.assertEqual(build.returncode, 0, build.stderr)

            src = os.path.join(td, "prog.lsh")
            with open(src, "w") as fh:
                fh.write(SOURCE)
            out = os.path.join(td, "prog")
            emit = subprocess.run(
                [stage, "compile", src, "--arena", "--emit-llvm", "to", out],
                cwd=ROOT, capture_output=True, text=True, timeout=300)
            self.assertEqual(emit.returncode, 0, emit.stderr)
            with open(out + ".ll") as fh:
                ir = fh.read()
            self.assertIn("declare i8* @leasharena_container_ref(i8*)", ir)
            self.assertGreaterEqual(ir.count("call i8* @leasharena_container_ref("), 2)
            self.assertNotIn("call i8* @leasharena_checked(", ir)


if __name__ == "__main__":
    unittest.main()
