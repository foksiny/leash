"""Leash standard-library test runner.

Runs every .lsh smoke test in tests/stdlib/ against the stdlib sources in
installthis/ and compares the output with the recorded baselines in
tests/stdlib/expected/.

Usage:
    python3 scripts/test_stdlib.py record [files...]   # (re)record baselines
    python3 scripts/test_stdlib.py test   [files...]   # verify against baselines
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent
TESTS_DIR = WORKSPACE / "tests" / "stdlib"
EXPECTED_DIR = TESTS_DIR / "expected"
STDLIB_DIR = WORKSPACE / "installthis"

PYTHON = "python" if sys.platform == "win32" else "python3"


def normalize(text: str) -> str:
    """Normalize compiler/runtime noise so comparisons are stable."""
    # Drop compiler warnings/tips and the run timestamp line.
    lines = []
    for line in text.split("\n"):
        if line.startswith("warning:") or line.startswith("tip:"):
            continue
        if line.strip().startswith("|") or line.strip().startswith("-->") or line.strip().startswith("^"):
            continue
        lines.append(line)
    text = "\n".join(lines)
    text = re.sub(r"--- Executed at .*? ---\n?", "", text)
    return "\n".join(l.rstrip() for l in text.split("\n")).rstrip("\n") + "\n"


def run_test(path: Path):
    cmd = [PYTHON, "-m", "leash.cli", "run", str(path), "--other-imports", str(STDLIB_DIR)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=WORKSPACE, timeout=30)
        return r.stdout + r.stderr
    except subprocess.TimeoutExpired:
        return "TIMEOUT\n"


def main():
    ap = argparse.ArgumentParser(description="Leash stdlib test runner")
    ap.add_argument("mode", choices=["record", "test"])
    ap.add_argument("files", nargs="*", help="optional subset of test files")
    args = ap.parse_args()

    files = []
    if args.files:
        for f in args.files:
            files.append(Path(f))
    else:
        files = sorted(TESTS_DIR.glob("*.lsh"))
    if not files:
        print("No tests found.")
        return 1

    passed = failed = 0
    label = "RECORD" if args.mode == "record" else ""
    for f in files:
        out = run_test(f)
        if args.mode == "record":
            EXPECTED_DIR.mkdir(parents=True, exist_ok=True)
            (EXPECTED_DIR / (f.name + ".out")).write_text(out)
            print(f"[{label}] {f.name}")
            passed += 1
            continue
        expected_file = EXPECTED_DIR / (f.name + ".out")
        if not expected_file.exists():
            print(f"[SKIP] {f.name} (no baseline)")
            continue
        expected = normalize(expected_file.read_text())
        actual = normalize(out)
        if actual == expected:
            print(f"[PASS] {f.name}")
            passed += 1
        else:
            failed += 1
            print(f"[FAIL] {f.name}")
            print("--- expected ---")
            print(expected)
            print("--- actual ---")
            print(actual)

    print("-" * 40)
    if args.mode == "record":
        print(f"Recorded {passed} baselines.")
    else:
        print(f"Summary: {passed} PASSED, {failed} FAILED.")
        if failed:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
