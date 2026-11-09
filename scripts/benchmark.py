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

leash_binary = ROOT / "bin" / "leashc"

# ── compilers ──────────────────────────────────────────────────────
LANG_CC = shutil.which("gcc") or shutil.which("clang") or ""
LANG_CXX = shutil.which("g++") or shutil.which("clang++") or ""
LANG_RUST = shutil.which("rustc") or ""


def _find_leash_cmd():
    if leash_binary.exists():
        return [str(leash_binary)]
    candidate = shutil.which("leashc") or shutil.which("leash")
    if candidate:
        return [candidate]
    raise SystemExit(
        "error: no leash compiler found (need bin/leashc in the repo, or "
        "leash/leashc on PATH)")


LEASH_CMD = _find_leash_cmd()


# ── build helpers (return (exe_path, compile_seconds)) ──────────────

def _capped_run(argv, timeout=120, cwd=None):
    """subprocess.run capped at LEASH_MEM_CAP_MB (watchdog backend).

    Raises TimeoutExpired / RuntimeError("memory cap...") on exceed so
    run_benchmark records the failure instead of hanging the suite.
    """
    capped = _with_mem_cap(argv)
    # NOTE: _with_mem_cap resolves the env cap each call; keep cwd=None
    # (benchmark builds already run with cwd=ROOT below).
    if capped is None:
        return subprocess.run(argv, capture_output=True, text=True,
                              timeout=timeout, cwd=cwd)
    out, err, rc, _rss = capped
    class _R:
        pass
    r = _R()
    r.stdout, r.stderr, r.returncode = out, err, rc
    if "memory cap" in (err or ""):
        raise RuntimeError(f"memory cap exceeded: {err.strip()[:120]}")
    return r


def _build_leash(task_path, opt_level, build_dir):
    stem = task_path.stem
    exe = build_dir / f"leash_{stem}_O{opt_level}.exe"
    t0 = time.perf_counter()
    cmd = [*LEASH_CMD, "compile", str(task_path), f"-O{opt_level}", "to", str(exe)]
    if os.environ.get("LEASH_BENCH_ARENA"):
        cmd.append("--arena")
    result = _capped_run(cmd, timeout=120, cwd=ROOT)
    elapsed = time.perf_counter() - t0
    if result.returncode != 0:
        raise RuntimeError(f"Leash compile failed:\n{result.stderr}")
    return exe, elapsed


def _build_c(task_path, opt_level, build_dir):
    stem = task_path.stem
    exe = build_dir / f"c_{stem}_O{opt_level}.exe"
    t0 = time.perf_counter()
    result = _capped_run(
        [LANG_CC, f"-O{opt_level}", "-o", str(exe), str(task_path), "-lm"],
        timeout=120,
    )
    elapsed = time.perf_counter() - t0
    if result.returncode != 0:
        raise RuntimeError(f"C compile failed:\n{result.stderr}")
    return exe, elapsed


def _build_cpp(task_path, opt_level, build_dir):
    stem = task_path.stem
    exe = build_dir / f"cpp_{stem}_O{opt_level}.exe"
    t0 = time.perf_counter()
    result = _capped_run(
        [LANG_CXX, f"-O{opt_level}", "-o", str(exe), str(task_path), "-lm"],
        timeout=120,
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
    result = _capped_run(
        [LANG_RUST, "-C", rust_opt, "-o", str(exe), str(task_path)],
        timeout=120,
    )
    elapsed = time.perf_counter() - t0
    if result.returncode != 0:
        raise RuntimeError(f"Rust compile failed:\n{result.stderr}")
    return exe, elapsed


# ── run helpers (return (stdout, stderr, returncode, peak_rss_kb)) ──────

def _rss_kb(usage):
    """Normalize ru_maxrss to kilobytes (Linux reports KB, macOS bytes)."""
    if usage is None:
        return None
    rss = usage.ru_maxrss
    if sys.platform == "darwin":
        rss //= 1024
    return rss


def _run_exe(exe_path):
    """Run a compiled benchmark and report its own peak RSS.

    Uses GNU `time -v` when available: on Linux, Python's own
    ``os.wait4``/``resource.getrusage(RUSAGE_CHILDREN)`` returns the
    interpreter's high-water mark (the forked child inherits the
    ~13 MB Python parent RSS before exec, which becomes the reported
    ``ru_maxrss`` for every small child). A tiny C parent (GNU time)
    does not have this pollution, so its ``Maximum resident set size``
    is the true child peak. Falls back to ``os.wait4`` (with the caveat
    above) and to plain ``subprocess.run`` on Windows / when ``time``
    is missing.

    Memory cap: when ``systemd-run`` is available the child runs in a
    cgroup scope with ``MemoryMax`` (kernel kills on exceed); otherwise
    a watcher thread polls ``/proc/<pid>/status`` and kills on
    exceed. Cap (MiB) comes from ``--mem-cap`` / ``LEASH_MEM_CAP_MB``
    (default 1024). A capped run reports ``rc = -9`` with
    ``memory cap`` in stderr so the table shows ERR, never a pass.
    """
    exe = str(exe_path)
    # Prefer /usr/bin/time -v (correct child RSS on Linux/BSD)
    gnu_time = shutil.which("time")  # /usr/bin/time on Linux
    # `shutil.which("time")` may find a shell keyword; prefer /usr/bin/time
    if os.path.exists("/usr/bin/time"):
        gnu_time = "/usr/bin/time"
    argv = ([gnu_time, "-v", exe] if gnu_time else [exe])
    capped = _with_mem_cap(argv)
    if capped is not None:
        out, err2, rc, rss_kb = capped
        if err2:
            err_lines = err2.splitlines()
            if any("memory cap" in L.lower() for L in err_lines):
                err2 = "memory cap: " + err2
        return out, err2, rc, rss_kb
    # No cap helper available: plain run.
    if gnu_time:
        result = subprocess.run([gnu_time, "-v", exe], capture_output=True, text=True, timeout=120)
        rss_kb = None
        clean_err_lines = []
        for line in result.stderr.splitlines():
            stripped = line.strip()
            if "Maximum resident set size" in line:
                try:
                    # Format: "\tMaximum resident set size (kbytes): 1836"
                    num = line.rsplit(":", 1)[-1].strip().split()[0]
                    rss_kb = int(num)
                except (ValueError, IndexError):
                    rss_kb = None
                continue
            # Filter GNU time diagnostics so failure output stays readable.
            if stripped.startswith(("Command being", "User time", "System time",
                                    "Percent", "Elapsed", "Average", "Maximum",
                                    "Swaps", "File system", "Exit status",
                                    "Socket", "Piped")):
                continue
            clean_err_lines.append(line)
        # GNU time exits with the child's exit code (0 on success);
        # non-zero means the benchmark failed. stdout is the benchmark's.
        return result.stdout, "\n".join(clean_err_lines), result.returncode, rss_kb
    if not hasattr(os, "wait4"):
        result = subprocess.run([exe], capture_output=True, text=True, timeout=120)
        return result.stdout, result.stderr, result.returncode, None
    with tempfile.TemporaryFile() as fo, tempfile.TemporaryFile() as fe:
        proc = subprocess.Popen([exe], stdout=fo, stderr=fe)
        _, status, usage = os.wait4(proc.pid, 0)
        try:
            proc.returncode = os.waitstatus_to_exitcode(status)
        except AttributeError:  # Python < 3.9
            proc.returncode = -os.WTERMSIG(status) if os.WIFSIGNALED(status) else os.WEXITSTATUS(status)
        fo.seek(0)
        fe.seek(0)
        out = fo.read().decode("utf-8", errors="replace")
        err = fe.read().decode("utf-8", errors="replace")
    return out, err, proc.returncode, _rss_kb(usage)


# ── memory cap (systemd cgroup, else /proc polling) ───────────────────
_MEM_CAP_MB_DEFAULT = 1024
_mem_cap_mb_cache = None
_has_systemd = None


def _mem_cap_mb():
    global _mem_cap_mb_cache
    if _mem_cap_mb_cache is None:
        try:
            _mem_cap_mb_cache = int(os.environ.get("LEASH_MEM_CAP_MB", _MEM_CAP_MB_DEFAULT))
        except ValueError:
            _mem_cap_mb_cache = _MEM_CAP_MB_DEFAULT
    return _mem_cap_mb_cache


def _have_systemd():
    """True only if a transient scope's MemoryMax actually kills."""
    global _has_systemd
    if _has_systemd is None:
        _has_systemd = shutil.which("systemd-run") is not None
        if _has_systemd:
            try:
                # Probe: a 250 MiB allocation inside a 50 MiB scope must
                # fail. Creating the scope is not enough — some hosts
                # silently accept MemoryMax without enforcing it.
                r = subprocess.run(
                    ["systemd-run", "--user", "--scope",
                     "-p", "MemoryMax=50M",
                     "--", "/bin/bash", "-c",
                     "python3 -c 'a=bytearray(250_000_000); print(1)'"],
                    capture_output=True, text=True, timeout=30)
                _has_systemd = r.returncode != 0
            except Exception:
                _has_systemd = False
    return _has_systemd


def _with_mem_cap(argv):
    """Run argv under a memory cap. Returns None if only the poll fallback applies."""
    cap_mb = _mem_cap_mb()
    if cap_mb <= 0:
        return None  # cap disabled
    if _have_systemd():
        # Kernel-enforced: systemd kills the scope on MemoryMax exceed.
        r = subprocess.run(
            ["systemd-run", "--user", "--scope", "-p", f"MemoryMax={cap_mb}M",
             "--"] + argv,
            capture_output=True, text=True, timeout=120)
        rss_kb = None
        for line in r.stderr.splitlines():
            if "Maximum resident set size" in line:
                try:
                    rss_kb = int(line.rsplit(":", 1)[-1].strip().split()[0])
                except Exception:
                    pass
        killed = (r.returncode == 137
                  or "out of memory" in (r.stderr + r.stdout).lower()
                  or "memory cgroup" in (r.stderr + r.stdout).lower())
        if killed:
            return r.stdout, "memory cap: exceeded " + str(cap_mb) + "MiB", -9, rss_kb
        return r.stdout, r.stderr, r.returncode, rss_kb
    else:
        return _run_poll(argv, cap_mb << 20)


def _run_poll(argv, cap_bytes):
    """Watchdog: poll /proc VmRSS, kill on exceed. Returns ((out,err,rc,rss) or None)."""
    import threading

    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    exceeded = []

    def watch():
        try:
            while proc.poll() is None:
                try:
                    with open(f"/proc/{proc.pid}/status") as fh:
                        vmrss_kb = 0
                        for line in fh:
                            if line.startswith("VmRSS:"):
                                vmrss_kb = int(line.split()[1])
                                break
                except (FileNotFoundError, PermissionError):
                    return
                if vmrss_kb and (vmrss_kb << 10) > cap_bytes:
                    exceeded.append(True)
                    try:
                        os.kill(proc.pid, 9)
                    except ProcessLookupError:
                        pass
                    return
                time.sleep(0.02)
        except Exception:
            return

    t = threading.Thread(target=watch, daemon=True)
    t.start()
    try:
        out, err = proc.communicate(timeout=120)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()
        exceeded.append(True)
    out = out.decode("utf-8", errors="replace") if isinstance(out, bytes) else ""
    err = err.decode("utf-8", errors="replace") if isinstance(err, bytes) else ""
    rss_kb = None
    for line in err.splitlines():
        if "Maximum resident set size" in line:
            try:
                rss_kb = int(line.rsplit(":", 1)[-1].strip().split()[0])
            except Exception:
                pass
    if exceeded:
        return out, "memory cap: exceeded " + str(cap_bytes >> 20) + "MiB poll watchdog", -9, rss_kb
    return out, err, proc.returncode, rss_kb


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
    binary_bytes = None
    if lang["build"]:
        try:
            exe, compile_time = lang["build"](task_path, opt_level, BUILD_DIR)
            try:
                binary_bytes = exe.stat().st_size if exe is not None else None
            except OSError:
                binary_bytes = None
        except (subprocess.TimeoutExpired, RuntimeError) as e:
            return {"error": str(e), "compile": None, "run": None}

    # ── run step ────────────────────────────────────────────────
    run_times = []
    rss_values = []
    capped_failures = 0
    stdout = ""
    for _ in range(runs):
        try:
            t0 = time.perf_counter()
            if exe is not None:
                res = lang["run"](exe)
            else:
                res = lang["run"](task_path, opt_level, BUILD_DIR)
            elapsed = time.perf_counter() - t0
            out, err, rc = res[0], res[1], res[2]
            rss = res[3] if len(res) > 3 else None
            capped = err and "memory cap" in err
            if rc == 0:
                run_times.append(elapsed)
                if rss is not None:
                    rss_values.append(rss)
                stdout = out
            elif capped:
                run_times.append(None)
                stdout = err
                capped_failures += 1
            else:
                run_times.append(None)
                stdout = err
        except subprocess.TimeoutExpired:
            run_times.append(None)

    valid = [t for t in run_times if t is not None]
    if not valid:
        if capped_failures:
            return {"error": "memory cap exceeded (" + str(capped_failures) + " runs capped)", "compile": None, "run": None}
        return {"error": "all runs failed or timed out", "compile": None, "run": None}

    return {
        "error": None,
        "compile": {"seconds": compile_time, "binary_bytes": binary_bytes},
        "run": {
            "mean": sum(valid) / len(valid),
            "median": median(valid),
            "min": min(valid),
            "max": max(valid),
            "stdev": stdev(valid) if len(valid) > 1 else 0.0,
            "runs": len(valid),
            "peak_rss_kb": max(rss_values) if rss_values else None,
            "median_rss_kb": median(rss_values) if rss_values else None,
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


def fmt_rss(kb):
    if kb is None:
        return "     N/A "
    if kb >= 1024 * 1024:
        return f"{kb / 1024 / 1024:>6.2f} GB"
    if kb >= 1024:
        return f"{kb / 1024:>6.1f} MB"
    return f"{kb:>5.0f} KB"


def fmt_bytes(n):
    if n is None:
        return "     N/A "
    if n >= 1024 * 1024:
        return f"{n / 1024 / 1024:>6.2f} MB"
    if n >= 1024:
        return f"{n / 1024:>6.1f} KB"
    return f"{n:>5.0f} B "


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
                    if field == "binary":
                        d = r.get("compile")
                    else:
                        d = r.get("run") if field == "rss" else r.get(field)
                    if d is None:
                        row.append(f"{'N/A':>{col_w}s}")
                    elif field == "compile":
                        row.append(fmt_time_compact(d.get("seconds")))
                    elif field == "rss":
                        row.append(fmt_rss(d.get("peak_rss_kb")))
                    elif field == "binary":
                        row.append(fmt_bytes(d.get("binary_bytes")))
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
    parser.add_argument("--mem-cap", type=int, default=None,
                        help="Per-run memory cap in MiB (default 1024; env LEASH_MEM_CAP_MB). "
                             "Exceeding it kills the run (systemd cgroup, else /proc poll).")
    parser.add_argument("--json", type=str, default=None,
                        help="Save results as JSON to this file")
    args = parser.parse_args()
    if args.mem_cap is not None:
        globals()["_mem_cap_mb_cache"] = args.mem_cap

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

    print()
    print("═══ PEAK RSS (max over runs) ═══")
    print()
    print_table("rss", results, tasks, langs, "rss")

    print()
    print("═══ BINARY SIZE ═══")
    print()
    print_table("binary", results, tasks, langs, "binary")

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
