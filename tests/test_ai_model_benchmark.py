#!/usr/bin/env python3
"""AI-model benchmark: speed, correctness and implementation size.

Compiles and runs benchmarking/tasks/ai_mlp.lsh (a small two-layer MLP)
with the default arena layout, compares its prediction to the Python
reference, and measures runtime at O0/O2/O4 against the same Python model.
O4 is the release profile and must not regress from O0.
"""
import json
import os
import re
import subprocess
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEASHC = os.path.join(ROOT, "bin", "leashc")
TASK = os.path.join(ROOT, "benchmarking", "tasks", "ai_mlp.lsh")
REF = os.path.join(ROOT, "benchmarking", "tasks", "ai_mlp.py")


def _mean(out):
    m = re.search(r"mean=([-0-9.eE+]+)", out)
    if not m:
        raise AssertionError("no mean in output: " + out)
    return float(m.group(1))


def _loc(path):
    lines = 0
    for line in open(path, encoding="utf-8"):
        s = line.strip()
        if s and not s.startswith("//") and not s.startswith("#") and \
           not s.startswith('"""'):
            lines += 1
    return lines


def _median_run(cmd, runs=15):
    times = []
    out = ""
    for _ in range(runs):
        t0 = time.perf_counter()
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                           errors="replace", timeout=120)
        times.append(time.perf_counter() - t0)
        self_assert(r.returncode == 0, r.stdout + r.stderr)
        out = r.stdout
    return sorted(times)[len(times) // 2], out


def self_assert(cond, msg):
    if not cond:
        raise AssertionError(msg)


class AiModelBenchmark(unittest.TestCase):
    def test_model_parity_speed_and_size(self):
        if not os.path.exists(LEASHC):
            self.skipTest("bin/leashc missing")
        with tempfile.TemporaryDirectory(prefix="leash_ai_bench_") as td:
            # Correctness on the public task.
            output = {}
            for o in ("0", "2", "4"):
                r = subprocess.run([LEASHC, "run", TASK, "-O" + o], cwd=ROOT,
                                   capture_output=True, text=True, timeout=180)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                output[o] = _mean(r.stdout)
            py = subprocess.run(["python3", REF], cwd=ROOT, capture_output=True,
                                text=True, timeout=120)
            self.assertEqual(py.returncode, 0, py.stdout + py.stderr)
            for o in output:
                self.assertAlmostEqual(output[o], _mean(py.stdout), places=5)

            # Speed on a larger workload (arena only, as for release builds).
            big = os.path.join(td, "ai_big.lsh")
            src = open(TASK, encoding="utf-8").read().replace(
                "n: int = 4000", "n: int = 400000")
            with open(big, "w", encoding="utf-8") as fh:
                fh.write(src)
            ref_big = os.path.join(td, "ai_big.py")
            with open(ref_big, "w", encoding="utf-8") as fh:
                fh.write(open(REF, encoding="utf-8").read().replace(
                    "n = 4000", "n = 400000"))
            timings = {}
            for o in ("0", "2", "4"):
                exe = os.path.join(td, "ai_O" + o)
                r = subprocess.run([LEASHC, "compile", big, "-O" + o, "to", exe],
                                   cwd=ROOT, capture_output=True, text=True,
                                   timeout=300)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                timings["O" + o], _ = _median_run([exe], runs=9)
            timings["python"], _ = _median_run(
                ["python3", ref_big], runs=5)

            # Ease: the Leash model must be no larger than the reference.
            self.assertLessEqual(_loc(TASK), _loc(REF) + 5)

            # O4 is the release profile: at least 2x faster than O0 and
            # comfortably faster than the pure-Python reference.
            self.assertLess(timings["O4"], timings["O0"] * 0.5)
            self.assertLess(timings["O4"], timings["python"] * 0.25)
            print("AI benchmark: " + json.dumps({
                "mean": output["4"],
                "seconds": {k: round(v, 4) for k, v in timings.items()},
                "leash_lines": _loc(TASK), "python_lines": _loc(REF)
            }))


if __name__ == "__main__":
    unittest.main()
