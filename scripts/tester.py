import subprocess
import os
import sys
import argparse
import re
from pathlib import Path

# Paths to important directories
WORKSPACE_DIR = Path("/home/jose/projects/leash")
EXAMPLES_DIR = WORKSPACE_DIR / "examples"
EXPECTED_DIR = WORKSPACE_DIR / "tests" / "expected"

# Command to run leash
LEASH_CMD = ["python3", "-m", "leash.cli", "run"]

# Pattern to match pointer values (e.g., 0x7ffe417814fc)
POINTER_PATTERN = re.compile(r"0x[0-9a-fA-F]+")


def normalize_pointers(text):
    """Replace pointer values with a placeholder for comparison."""
    return POINTER_PATTERN.sub("0xPOINTER", text)


def run_leash(file_path):
    """Run a leash file and return its combined output (stdout and stderr)."""
    try:
        result = subprocess.run(
            LEASH_CMD + [str(file_path)],
            capture_output=True,
            text=True,
            cwd=WORKSPACE_DIR,
            timeout=10,  # Reasonable timeout for leash programs
        )
        return result.stdout + result.stderr, result.returncode
    except subprocess.TimeoutExpired:
        return "ERROR: Program timed out!", 1
    except Exception as e:
        return f"ERROR: Running leash failed: {e}", 1


def record_outputs(files):
    """Record current outputs as the expected baseline."""
    os.makedirs(EXPECTED_DIR, exist_ok=True)
    print(f"--- Recording expected outputs for {len(files)} files ---")

    for f in files:
        # We skip files that are known to fail (like intentional error tests)
        # or we could record them as 'success if they fail with the right message'.
        output, _ = run_leash(f)
        out_file = EXPECTED_DIR / (f.name + ".out")
        with open(out_file, "w") as out:
            out.write(output)
        print(f"[RECORD] {f.name} -> {out_file.name}")


def test_files(files):
    """Test leash files against previously recorded outputs."""
    passed = 0
    failed = 0
    ignored = 0

    print(f"--- Testing {len(files)} files ---")

    for f in files:
        expected_file = EXPECTED_DIR / (f.name + ".out")
        if not expected_file.exists():
            print(f"[SKIP]  {f.name} (No expected output recorded)")
            ignored += 1
            continue

        with open(expected_file, "r") as exp:
            expected_output = exp.read()

        actual_output, _ = run_leash(f)

        # Normalize pointer values for comparison
        expected_normalized = normalize_pointers(expected_output)
        actual_normalized = normalize_pointers(actual_output)

        if actual_normalized == expected_normalized:
            print(f"[PASS]  {f.name}")
            passed += 1
        else:
            print(f"[FAIL]  {f.name}")
            print("-" * 20)
            print("EXPECTED:")
            print(expected_output)
            print("ACTUAL:")
            print(actual_output)
            print("-" * 20)
            failed += 1

    print("-" * 40)
    print(f"Summary: {passed} PASSED, {failed} FAILED, {ignored} IGNORED.")
    if failed > 0:
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Leash Smart Tester")
    parser.add_argument(
        "mode",
        choices=["test", "record"],
        help="'test' to verify, 'record' to save baseline",
    )
    parser.add_argument(
        "files",
        nargs="*",
        help="Optional list of files. If empty, runs all in examples/",
    )

    args = parser.parse_args()

    # Resolve files to test/record
    target_files = []
    if args.files:
        for f in args.files:
            target_files.append(Path(f))
    else:
        # Default to examples/ directory
        for f in EXAMPLES_DIR.glob("*.lsh"):
            target_files.append(f)

    if args.mode == "record":
        record_outputs(target_files)
    else:
        test_files(target_files)


if __name__ == "__main__":
    main()
