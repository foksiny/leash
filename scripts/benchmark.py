"""
Cross-language performance benchmark suite.

Tests each benchmark task across Leash, C, C++, and Rust at every
available optimization level (O0–O3 for C/C++/Rust, O0–O4 for Leash).
Reports compile time (single shot) and run time (median of N runs) separately.
Results are printed as two tables and optionally saved as JSON.
"""

import subprocess
import sys
import os
import time
import json
import shutil
import tempfile
import argparse
from pathlib import Path
from statistics import median, stdev

# ── paths ──────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
TASKS_DIR = ROOT / "benchmarking" / "tasks"
BUILD_DIR = Path(tempfile.mkdtemp(prefix="leash_bench_"))

leash_python = "python" if sys.platform == "win32" else "python3"

# ── compilers ──────────────────────────────────────────────────────
LANG_CC = shutil.which("gcc") or shutil.which("clang") or ""
LANG_CXX = shutil.which("g++") or shutil.which("clang++") or ""
LANG_RUST = shutil.which("rustc") or ""


def _find_leash_cmd():
    candidate = shutil.which("leash")
    if candidate:
        return [candidate]
    return [leash_python, "-m", "leash.cli"]


LEASH_CMD = _find_leash_cmd()


# ── build helpers (return (exe_path, compile_seconds)) ──────────────

def _build_leash(task_path, opt_level, build_dir):
    stem = task_path.stem
    exe = build_dir / f"leash_{stem}_O{opt_level}.exe"
    t0 = time.perf_counter()
    result = subprocess.run(
        [*LEASH_CMD, "compile", str(task_path), "-O", str(opt_level), "to", str(exe)],
        capture_output=True, text=True, timeout=120, cwd=ROOT,
    )
    elapsed = time.perf_counter() - t0
    if result.returncode != 0:
        raise RuntimeError(f"Leash compile failed:\n{result.stderr}")
    return exe, elapsed


def _build_c(task_path, opt_level, build_dir):
    stem = task_path.stem
    exe = build_dir / f"c_{stem}_O{opt_level}.exe"
    t0 = time.perf_counter()
    result = subprocess.run(
        [LANG_CC, f"-O{opt_level}", "-o", str(exe), str(task_path), "-lm"],
        capture_output=True, text=True, timeout=120,
    )
    elapsed = time.perf_counter() - t0
    if result.returncode != 0:
        raise RuntimeError(f"C compile failed:\n{result.stderr}")
    return exe, elapsed


def _build_cpp(task_path, opt_level, build_dir):
    stem = task_path.stem
    exe = build_dir / f"cpp_{stem}_O{opt_level}.exe"
    t0 = time.perf_counter()
    result = subprocess.run(
        [LANG_CXX, f"-O{opt_level}", "-o", str(exe), str(task_path), "-lm"],
        capture_output=True, text=True, timeout=120,
    )
    elapsed = time.perf_counter() - t0
    if result.returncode != 0:
        raise RuntimeError(f"C++ compile failed:\n{result.stderr}")
    return exe, elapsed


def _build_rust(task_path, opt_level, build_dir):
    stem = task_path.stem
    exe = build_dir / f"rust_{stem}_O{opt_level}.exe"
    rust_opt = f"opt-level={opt_level}" if opt_level else "opt-level=0"
    t0 = time.perf_counter()
    result = subprocess.run(
        [LANG_RUST, "-C", rust_opt, "-o", str(exe), str(task_path)],
        capture_output=True, text=True, timeout=120,
    )
    elapsed = time.perf_counter() - t0
    if result.returncode != 0:
        raise RuntimeError(f"Rust compile failed:\n{result.stderr}")
    return exe, elapsed


# ── run helpers (return (stdout, stderr, returncode)) ──────────────

def _run_exe(exe_path):
    result = subprocess.run([str(exe_path)], capture_output=True, text=True, timeout=120)
    return result.stdout, result.stderr, result.returncode


# ── language registry ──────────────────────────────────────────────

LANGS = {
    "leash": {
        "label": "Leash",
        "source_ext": ".lsh",
        "levels": ["0", "1", "2", "3", "4"],
        "build": _build_leash,
        "run": _run_exe,
    },
    "c": {
        "label": "C",
        "source_ext": ".c",
        "levels": ["0", "1", "2", "3"],
        "build": None,
        "run": None,
    },
    "cpp": {
        "label": "C++",
        "source_ext": ".cpp",
        "levels": ["0", "1", "2", "3"],
        "build": None,
        "run": None,
    },
    "rust": {
        "label": "Rust",
        "source_ext": ".rs",
        "levels": ["0", "1", "2", "3"],
        "build": None,
        "run": None,
    },
}


def _resolve_compiler(lang_key):
    if lang_key == "c":
        if not LANG_CC:
            return False, "C compiler not found"
        LANGS["c"]["build"] = _build_c
        LANGS["c"]["run"] = _run_exe
        return True, ""
    if lang_key == "cpp":
        if not LANG_CXX:
            return False, "C++ compiler not found"
        LANGS["cpp"]["build"] = _build_cpp
        LANGS["cpp"]["run"] = _run_exe
        return True, ""
    if lang_key == "rust":
        if not LANG_RUST:
            return False, "rustc not found"
        LANGS["rust"]["build"] = _build_rust
        LANGS["rust"]["run"] = _run_exe
        return True, ""
    if lang_key == "leash":
        return True, ""
    return False, "unknown language"


# ── benchmark runner ───────────────────────────────────────────────

def run_benchmark(task_name, lang_key, opt_level, runs=5):
    """Run a single (task, lang, opt) combination.

    Returns a dict with two sub-dicts:
      compile : {seconds}               — single compile measurement
      run     : {mean, median, min, max, stdev, runs}
      stdout  : captured output
    """
    lang = LANGS[lang_key]
    task_path = TASKS_DIR / f"{task_name}{lang['source_ext']}"
    if not task_path.exists():
        return None

    # ── compile step ────────────────────────────────────────────
    compile_time = 0.0
    exe = None
    if lang["build"]:
        try:
            exe, compile_time = lang["build"](task_path, opt_level, BUILD_DIR)
        except (subprocess.TimeoutExpired, RuntimeError) as e:
            return {"error": str(e), "compile": None, "run": None}

    # ── run step ────────────────────────────────────────────────
    run_times = []
    stdout = ""
    for _ in range(runs):
        try:
            t0 = time.perf_counter()
            if exe is not None:
                out, err, rc = lang["run"](exe)
            else:
                out, err, rc = lang["run"](task_path, opt_level, BUILD_DIR)
            elapsed = time.perf_counter() - t0
            if rc == 0:
                run_times.append(elapsed)
                stdout = out
            else:
                run_times.append(None)
                stdout = err
        except subprocess.TimeoutExpired:
            run_times.append(None)

    valid = [t for t in run_times if t is not None]
    if not valid:
        return {"error": "all runs failed or timed out", "compile": None, "run": None}

    return {
        "error": None,
        "compile": {"seconds": compile_time},
        "run": {
            "mean": sum(valid) / len(valid),
            "median": median(valid),
            "min": min(valid),
            "max": max(valid),
            "stdev": stdev(valid) if len(valid) > 1 else 0.0,
            "runs": len(valid),
        },
        "stdout": stdout.strip(),
    }


# ── formatting ────────────────────────────────────────────────────

def fmt_time(seconds):
    if seconds is None:
        return "   FAILED  "
    if seconds < 0.001:
        return f"{seconds*1e6:>8.1f} us"
    if seconds < 1.0:
        return f"{seconds*1e3:>8.2f} ms"
    return f"{seconds:>8.3f} s "


def fmt_time_compact(seconds):
    return fmt_time(seconds)


def print_table(title, results, tasks, langs, field):
    """Print a table extracting *field* from each result."""
    col_w = 12
    sep = " | "

    header = [f"{'':14s}"]
    for lang in langs:
        for lvl in LANGS[lang]["levels"]:
            label = f"{LANGS[lang]['label']} O{lvl}" if lvl else f"{LANGS[lang]['label']}"
            header.append(f"{label:>{col_w}s}")
    print(sep.join(header))
    print("-" * (14 + (col_w + len(sep)) * (len(header) - 1)))

    for task in tasks:
        row = [f"{task:14s}"]
        for lang in langs:
            for lvl in LANGS[lang]["levels"]:
                key = (task, lang, lvl)
                r = results.get(key)
                if r is None:
                    row.append(f"{'N/A':>{col_w}s}")
                elif r.get("error"):
                    row.append(f"{'ERR':>{col_w}s}")
                else:
                    d = r.get(field)
                    if d is None:
                        row.append(f"{'N/A':>{col_w}s}")
                    elif field == "compile":
                        row.append(fmt_time_compact(d.get("seconds")))
                    else:
                        row.append(fmt_time_compact(d.get("median")))
        print(sep.join(row))


# ── main ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Cross-language benchmark suite")
    parser.add_argument("tasks", nargs="*",
                        help="Benchmark tasks to run (default: all in tasks/)")
    parser.add_argument("--langs", nargs="+", default=list(LANGS.keys()),
                        choices=list(LANGS.keys()),
                        help="Languages to benchmark")
    parser.add_argument("--runs", type=int, default=5,
                        help="Number of runs per (task, lang, opt) combination")
    parser.add_argument("--json", type=str, default=None,
                        help="Save results as JSON to this file")
    args = parser.parse_args()

    # resolve tasks
    if args.tasks:
        tasks = args.tasks
    else:
        tasks = sorted({p.stem for p in TASKS_DIR.glob("*.lsh")})

    # resolve languages
    langs = []
    skipped = []
    for lk in args.langs:
        ok, msg = _resolve_compiler(lk)
        if ok:
            langs.append(lk)
        else:
            skipped.append((lk, msg))
    if not langs:
        print("error: No languages available to benchmark")
        sys.exit(1)
    if skipped:
        for lk, msg in skipped:
            print(f"skipping {lk}: {msg}", file=sys.stderr)

    print(f"Benchmarking {len(tasks)} tasks × {len(langs)} languages")
    print(f"Runs per combination: {args.runs}\n")

    results = {}
    total = len(tasks) * len(langs)
    done = 0

    for task in tasks:
        for lang in langs:
            for lvl in LANGS[lang]["levels"]:
                done += 1
                pct = done / total * 100
                label = f"{LANGS[lang]['label']} O{lvl}" if lvl else f"{LANGS[lang]['label']}"
                print(f"  [{done}/{total} {pct:5.1f}%] {task} ({label})...",
                      end="", flush=True)
                r = run_benchmark(task, lang, lvl, runs=args.runs)
                results[(task, lang, lvl)] = r
                if r is None:
                    print(" SKIPPED")
                elif r.get("error"):
                    print(f" FAILED ({r['error']})")
                else:
                    ct = r["compile"]["seconds"]
                    rt = r["run"]["median"]
                    print(f"  compile {fmt_time_compact(ct).strip()}  run {fmt_time_compact(rt).strip()}")

    print()
    print("═══ COMPILE TIMES ═══")
    print()
    print_table("compile", results, tasks, langs, "compile")

    print()
    print("═══ RUN TIMES (median) ═══")
    print()
    print_table("run", results, tasks, langs, "run")

    # optional json output
    if args.json:
        serializable = {}
        for (task, lang, lvl), r in results.items():
            key = f"{task}/{lang}/{lvl}"
            if r is None:
                serializable[key] = None
            else:
                d = {k: v for k, v in r.items() if k != "stdout"}
                serializable[key] = d
        with open(args.json, "w") as f:
            json.dump(serializable, f, indent=2)
        print(f"\nResults saved to {args.json}")

    # clean up build dir
    try:
        shutil.rmtree(BUILD_DIR)
    except OSError:
        pass


if __name__ == "__main__":
    main()
