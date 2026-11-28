#!/usr/bin/env python3
"""All use-case examples must compile and run, with full target commands."""
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEASHC = os.path.join(ROOT, "bin", "leashc")
DIR = os.path.join(ROOT, "examples", "usecases")
TARGETS = ("linux64", "linux32", "linux-arm", "win64", "macos", "macos-arm", "jvm")

NAMES = (
    "minecraft_clone", "web_api", "sql_todo", "ai_trainer",
    "regex_log_miner", "json_pipeline", "graph_astar", "worker_pipeline",
    "bitmap_editor", "terminal_dashboard",
)


class UseCases(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.exists(LEASHC):
            raise unittest.SkipTest("bin/leashc missing")

    def test_ten_examples_with_all_target_commands(self):
        self.assertEqual(len(NAMES), 10)
        found = sorted(f[:-4] for f in os.listdir(DIR) if f.endswith(".lsh"))
        self.assertEqual(found, sorted(NAMES))
        for name in NAMES:
            path = os.path.join(DIR, name + ".lsh")
            head = open(path, encoding="utf-8").read().split("fnc main", 1)[0]
            for target in TARGETS:
                self.assertIn("--target " + target, head, name + " lacks " + target)

    def test_each_example_runs(self):
        with tempfile.TemporaryDirectory(prefix="leash_usecases_") as td:
            env = os.environ.copy()
            # The voxel example opens a window when a display exists; ask it
            # for the deterministic offscreen renderer in this test. Its real
            # window loop is exercised by test_minecraft_window below.
            env["LEASH_VOXEL_HEADLESS"] = "1"
            for name in NAMES:
                path = os.path.join(DIR, name + ".lsh")
                r = subprocess.run([LEASHC, "run", path, "--other-imports",
                                    os.path.join(ROOT, "installthis")],
                                   cwd=td, env=env, capture_output=True, text=True,
                                   errors="replace", timeout=180)
                with self.subTest(example=name):
                    self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                    self.assertTrue(any(l for l in r.stdout.splitlines()
                                        if not l.startswith("--- Executed at")),
                                    name + " printed nothing")
                    if name == "minecraft_clone":
                        self.assertIn("GL renderer=", r.stdout)
                        self.assertIn("faces=", r.stdout)
                        self.assertIn("window=unavailable", r.stdout)
                        self.assertIn("mined=", r.stdout)
                        self.assertIn("placed=", r.stdout)

    def test_minecraft_gameplay_selftest(self):
        with tempfile.TemporaryDirectory(prefix="leash_voxel_selftest_") as td:
            exe = os.path.join(td, "voxel")
            compiled = subprocess.run(
                [LEASHC, "compile", os.path.join(DIR, "minecraft_clone.lsh"),
                 "to", exe, "--target", "linux64",
                 "--other-imports", os.path.join(ROOT, "installthis")],
                cwd=ROOT, capture_output=True, text=True, errors="replace",
                timeout=180)
            self.assertEqual(compiled.returncode, 0,
                             compiled.stdout + compiled.stderr)
            r = subprocess.run([exe, "--selftest"], cwd=td,
                               capture_output=True, text=True, errors="replace",
                               timeout=120)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("selftest inventory=true craft=true seam=true load=true",
                          r.stdout)
            self.assertFalse(os.path.exists(os.path.join(td, "leash_voxel_save.txt")))

    def test_minecraft_window(self):
        xvfb = shutil.which("xvfb-run")
        if not xvfb or not os.path.exists(LEASHC):
            self.skipTest("xvfb-run or bin/leashc missing")
        with tempfile.TemporaryDirectory(prefix="leash_voxel_window_") as td:
            exe = os.path.join(td, "voxel")
            src = os.path.join(DIR, "minecraft_clone.lsh")
            compiled = subprocess.run(
                [LEASHC, "compile", src, "to", exe, "--target", "linux64",
                 "--other-imports", os.path.join(ROOT, "installthis")],
                cwd=ROOT, capture_output=True, text=True, errors="replace",
                timeout=180)
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            r = subprocess.run([xvfb, "-a", exe, "--frames", "4"],
                               cwd=td, capture_output=True, text=True,
                               errors="replace", timeout=180)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("window=open 960x600 vsync=off", r.stdout)
            self.assertIn("controls: WASD move", r.stdout)
            self.assertIn("faces=", r.stdout)
            pixels = [l.split("center_pixel=", 1)[1] for l in r.stdout.splitlines()
                      if "center_pixel=" in l]
            self.assertEqual(len(pixels), 1)
            value = int(pixels[0].split()[0], 10)
            self.assertGreater(value, 0)          # real geometry, not the clear color
            self.assertIn("window=closed frames=4", r.stdout)

    def test_minecraft_headless_fallback_without_display(self):
        with tempfile.TemporaryDirectory(prefix="leash_voxel_nodisplay_") as td:
            env = os.environ.copy()
            env.pop("DISPLAY", None)
            env.pop("LEASH_VOXEL_HEADLESS", None)
            path = os.path.join(DIR, "minecraft_clone.lsh")
            r = subprocess.run([LEASHC, "run", path, "--other-imports",
                                os.path.join(ROOT, "installthis")],
                               cwd=td, env=env, capture_output=True, text=True,
                               errors="replace", timeout=180)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("window=unavailable", r.stdout)
            self.assertIn("faces=", r.stdout)


if __name__ == "__main__":
    unittest.main()
