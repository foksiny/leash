#!/usr/bin/env python3
"""Parity + differential-IR harness for the self-hosted compiler.

Primary metric: runtime output parity with the Python compiler
(tests/expected/*.out). Secondary: side-by-side IR dumps for debugging
when outputs diverge (the Python IR serves as the reference base).

Usage:
  python3 scripts/ir_diff.py                     # parity over expected corpus
  python3 scripts/ir_diff.py file.lsh [more...]  # specific files
  python3 scripts/ir_diff.py --ir file.lsh       # dump both IRs side by side
"""
import os
import re
import signal
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXPECTED = os.path.join(ROOT, "tests", "expected")

# Hard guardrails for every child process:
#   ulimit -v 512MB  -> runaway compiler/binary dies with OOM, not the machine
#   ulimit -f 50MB   -> runaway program output stops at 50MB (SIGXFSZ)
#   output goes to a temp FILE (never buffered unboundedly in harness RAM;
#     we read back at most CAPTURE_BYTES of it)
#   whole process group is SIGKILLed on timeout (no orphaned clang children)
MEM_MB = 512
FILE_MB = 50
CAPTURE_BYTES = 65536


def run(cmd, timeout=180, mem_mb=MEM_MB):
    wrapped = ["bash", "-c",
               f"ulimit -v {mem_mb * 1024} 2>/dev/null; "
               f"ulimit -f {FILE_MB * 2048} 2>/dev/null; "
               'exec "$@"', "--"] + list(cmd)
    with tempfile.TemporaryFile(mode="w+b") as fo, \
         tempfile.TemporaryFile(mode="w+b") as fe:
        p = subprocess.Popen(wrapped, cwd=ROOT, stdout=fo, stderr=fe,
                             start_new_session=True)
        try:
            p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(p.pid), signal.SIGKILL)
            except Exception:
                p.kill()
            p.wait()
            fo.seek(0)
            fe.seek(0)
            return subprocess.CompletedProcess(
                cmd, -9, fo.read(CAPTURE_BYTES).decode("utf-8", "replace"),
                fe.read(CAPTURE_BYTES).decode("utf-8", "replace"))
        fo.seek(0)
        fe.seek(0)
        return subprocess.CompletedProcess(
            cmd, p.returncode,
            fo.read(CAPTURE_BYTES).decode("utf-8", "replace"),
            fe.read(CAPTURE_BYTES).decode("utf-8", "replace"))


def normalize_out(s):
    # drop the python runner's timestamp epilogue and normalize argv[0] paths
    s = re.sub(r"--- Executed at .*? ---\n?", "", s)
    # drop compiler warning blocks (not program output; both compilers differ
    # in diagnostics but program output must match)
    lines = s.splitlines()
    kept = []
    in_block = False
    for ln in lines:
        if ln.startswith("warning:"):
            in_block = True
            continue
        if in_block and (re.match(r"\s*-->", ln) or re.match(r"\s*\|", ln) or
                         re.match(r"\s*=", ln) or ln.startswith("tip:") or
                         ln.strip() == "" or
                         re.match(r"\d+ \|", ln)):
            continue
        in_block = False
        kept.append(ln)
    s = "\n".join(kept)
    s = re.sub(r"^(\d+:) \S+.*$", r"\1 <argv0>", s, flags=re.M)
    s = re.sub(r"((/|\.)__temp_run_leash_exe_[0-9a-f]+|/tmp/parity_bin)", "<argv0>", s)
    # raw pointer prints differ every run under ASLR (e.g. show(p) -> %p)
    s = re.sub(r"0x[0-9a-fA-F]{6,}", "<ptr>", s)
    return s.strip()


def selfhosted_run(path):
    compiler = os.environ.get("LEASHC", "bin/leashc")
    r = run([compiler, "compile", path, "-o", "/tmp/parity_bin"], timeout=180)
    if r.returncode != 0:
        return None, (r.stdout + r.stderr)
    rr = run(["/tmp/parity_bin"], timeout=20)
    return rr.stdout + rr.stderr, None


def parity(path):
    exp_file = os.path.join(EXPECTED, os.path.basename(path) + ".out")
    if not os.path.exists(exp_file):
        return "NOEXP", None
    with open(exp_file) as fh:
        expected = normalize_out(fh.read())
    got, err = selfhosted_run(path)
    if got is None:
        return "COMPILE_FAIL", err.strip().splitlines()[:1]
    if normalize_out(got) == expected:
        return "MATCH", None
    return "MISMATCH", (expected.splitlines()[:2], normalize_out(got).splitlines()[:2])


def dump_ir(path):
    staged = "/tmp/irdiff_py_input.lsh"
    with open(os.path.join(ROOT, path)) as fh:
        open(staged, "w").write(fh.read())
    run([sys.executable, "-m", "leash.cli", "dump", staged], timeout=180)
    run(["bin/leashc", "compile", path, "--emit-llvm", "-o", "/tmp/irdiff_sh"], timeout=180)
    for label, p in [("PYTHON", "/tmp/irdiff_py_input.ll"), ("SELFHOSTED", "/tmp/irdiff_sh.ll")]:
        print(f"===== {label} IR: {p} =====")
        if os.path.exists(p):
            os.system(f"sed -n '/user_main/,/^}}/p' {p} | head -60")
        else:
            print("(missing)")


def main():
    args = sys.argv[1:]
    if args and args[0] == "--ir":
        dump_ir(args[1])
        return
    args = [a for a in args if a != "--all"]
    if not args:
        # Refuse to sweep the whole corpus by accident: pass files explicitly
        # (batches) or opt in with --all (still capped per child).
        if "--all" not in sys.argv:
            n = len([f for f in os.listdir(EXPECTED) if f.endswith(".lsh.out")])
            print(f"refusing to run all {n} examples at once; "
                  f"pass files in batches, or --all to sweep")
            sys.exit(2)
        args = sorted(
            os.path.join("examples", f)
            for f in os.listdir(EXPECTED)
            if f.endswith(".lsh.out")
            for f in [f[:-4]]
        )
    stats = {"MATCH": 0, "MISMATCH": 0, "COMPILE_FAIL": 0, "NOEXP": 0, "SKIP": 0}
    for p in args:
        if os.path.basename(p) in ("getkey.lsh", "input.lsh"):
            stats["SKIP"] += 1
            print(f"SKIP     {p} (requires manual input)")
            continue
        if os.path.basename(p) == "multithread.lsh":
            stats["SKIP"] += 1
            print(f"SKIP     {p} (nondeterministic thread interleaving)")
            continue
        try:
            status, info = parity(p)
        except Exception as e:
            status, info = "COMPILE_FAIL", [f"harness error: {e}"]
        stats[status] = stats.get(status, 0) + 1
        if status == "MATCH":
            print(f"MATCH    {p}")
        elif status == "COMPILE_FAIL":
            print(f"COMPILE  {p} :: {info}")
        elif status == "MISMATCH":
            print(f"MISMATCH {p} :: expected {info[0]} got {info[1]}")
    total = stats["MATCH"] + stats["MISMATCH"] + stats["COMPILE_FAIL"]
    skipped = f", {stats['SKIP']} skipped" if stats["SKIP"] else ""
    print(f"\n{stats['MATCH']}/{total} output parity "
          f"({stats['MISMATCH']} mismatch, {stats['COMPILE_FAIL']} compile-fail{skipped})")


if __name__ == "__main__":
    main()
