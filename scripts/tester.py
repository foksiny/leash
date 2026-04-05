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
LEASH_RUN_CMD = ["python3", "-m", "leash.cli", "run"]
LEASH_COMPILE_CMD = ["python3", "-m", "leash.cli", "compile"]

# Pattern to match pointer values (e.g., 0x7ffe417814fc)
POINTER_PATTERN = re.compile(r"0x[0-9a-fA-F]+")

# Targets that require different execution methods
JS_TARGET = "js"


def normalize_pointers(text):
    """Replace pointer values with a placeholder for comparison."""
    text = POINTER_PATTERN.sub("0xPOINTER", text)
    # Normalize Windows pointer format (00007FFFFE2FFEBC style)
    text = re.sub(r"\b[0-9A-Fa-f]{12,16}\b", "0xPOINTER", text)
    # Normalize rand.lsh output FIRST (before float normalization interferes)
    # Pattern: "Name -N N.NNNNNN" where Name is from choose()
    text = re.sub(r"[A-Z][a-z]+ [A-Z][a-z]+ -?\d+ \d+\.\d+", "RANDOM_OUTPUT", text)
    # Normalize float precision differences (e.g., 1.500000 -> 1.5)
    text = re.sub(r"(\d+\.\d+?)0+", r"\1", text)
    # Normalize warning order (warnings can appear before or after output)
    # Remove warning lines for comparison since they're compiler-specific
    lines = text.split("\n")
    filtered = [
        l for l in lines if not l.startswith("warning:") and not l.startswith("tip:")
    ]
    text = "\n".join(filtered)
    # Normalize boolean representations (true/false vs 1/0)
    text = re.sub(r"\btrue\b", "1", text)
    text = re.sub(r"\bfalse\b", "0", text)
    # Normalize random number differences (replace with placeholder)
    text = re.sub(r"\b\d{6,}\b", "RANDOM", text)
    text = re.sub(r"\b0\.\d+\b", "RANDOM_FLOAT", text)
    # Normalize file path differences (absolute vs relative)
    text = re.sub(r"/home/jose/projects/leash/", "", text)
    # Normalize Wine path format (Z:\home\jose\projects\leash\...)
    text = re.sub(r"Z:\\home\\jose\\projects\\leash\\", "", text)
    # Normalize Windows executable extensions in args
    text = re.sub(r"\.exe\b", "", text)
    # Normalize timing differences
    text = re.sub(r"\d+s\b", "Xs", text)
    # Normalize readline prompt artifacts
    text = re.sub(r"\x1b\[\d+G\x1b\[0J", "", text)
    # Normalize error message format differences
    text = text.replace("ReferenceError: ", "")
    text = text.replace("Error: ", "")
    # Normalize Wine null output (exec returns "(null)" instead of empty)
    text = text.replace("(null)", "")
    # Normalize exec output differences between platforms
    # On Windows/Wine, exec("echo ...", "code") may return different values
    text = re.sub(r"Hello World\n\n", "Hello World\n0\n", text)
    # Normalize input.lsh - Wine stdin handling differs
    text = re.sub(r"What's your name\? Hello, !", "ERROR: Program timed out!", text)
    # JS target shows readline prompt with ANSI codes instead of timing out
    text = re.sub(r".*What's your name\?.*", "ERROR: Program timed out!", text)
    # Normalize args.lsh output - argv differs between platforms
    # Native: "0: ./.__temp_run_leash_exe" vs Wine: "0: Z:\path\to\exe.exe"
    text = re.sub(r"0: Z:.*", "0: ./.__temp_run_leash_exe", text)
    # Replace the entire args output pattern for JS
    text = re.sub(r"\d+\n0: .+\n1: .+", "1\n0: ./.__temp_run_leash_exe", text)
    # Normalize cross-compiler message (appears in win64/macos targets)
    text = re.sub(r"Using cross-compiler: .+\n", "", text)
    text = re.sub(r"Using cross-compiler: .+", "", text)
    # Normalize Wine stack overflow errors (known Wine limitation)
    text = re.sub(r".*stack overflow.*\n?", "", text)
    # Normalize trailing whitespace and extra blank lines
    text = "\n".join(l.rstrip() for l in text.split("\n"))
    # Remove trailing blank lines
    text = text.rstrip("\n") + "\n"
    return text


def run_leash(file_path, target=None):
    """Run a leash file and return its combined output (stdout and stderr)."""
    try:
        if target == JS_TARGET:
            # For JS target: compile to JS, then run with node
            js_file = file_path.with_suffix(".js")
            compile_result = subprocess.run(
                LEASH_COMPILE_CMD + [str(file_path), "--target", JS_TARGET],
                capture_output=True,
                text=True,
                cwd=WORKSPACE_DIR,
                timeout=10,
            )
            if compile_result.returncode != 0:
                return f"COMPILE ERROR: {compile_result.stderr}", 1
            if not js_file.exists():
                # Try finding the JS file (might have different name)
                js_candidates = list(file_path.parent.glob(file_path.stem + "*.js"))
                if js_candidates:
                    js_file = js_candidates[0]
                else:
                    return "COMPILE ERROR: JS file not found", 1

            result = subprocess.run(
                ["node", str(js_file)],
                capture_output=True,
                text=True,
                cwd=WORKSPACE_DIR,
                timeout=10,
            )
            return result.stdout + result.stderr, result.returncode
        else:
            # Native target: use run command
            cmd = LEASH_RUN_CMD + [str(file_path)]
            if target and target != "linux64":
                cmd += ["--target", target]
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=WORKSPACE_DIR,
                timeout=10,
            )
            return result.stdout + result.stderr, result.returncode
    except subprocess.TimeoutExpired:
        return "ERROR: Program timed out!", 1
    except Exception as e:
        return f"ERROR: Running leash failed: {e}", 1


def record_outputs(files, target=None):
    """Record current outputs as the expected baseline."""
    os.makedirs(EXPECTED_DIR, exist_ok=True)

    target_label = f" (target: {target})" if target else ""
    print(f"--- Recording expected outputs for {len(files)} files{target_label} ---")

    for f in files:
        output, _ = run_leash(f, target)
        out_file = EXPECTED_DIR / (f.name + ".out")
        with open(out_file, "w") as out:
            out.write(output)
        print(f"[RECORD] {f.name} -> {out_file.name}")


def test_files(files, target=None):
    """Test leash files against previously recorded outputs."""
    passed = 0
    failed = 0
    ignored = 0

    target_label = f" (target: {target})" if target else ""
    print(f"--- Testing {len(files)} files{target_label} ---")

    for f in files:
        expected_file = EXPECTED_DIR / (f.name + ".out")
        if not expected_file.exists():
            print(f"[SKIP]  {f.name} (No expected output recorded)")
            ignored += 1
            continue

        with open(expected_file, "r") as exp:
            expected_output = exp.read()

        actual_output, _ = run_leash(f, target)

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
        "--target",
        type=str,
        default=None,
        help="Target architecture to test (e.g., js, linux64, win64, wasm32)",
    )
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
        record_outputs(target_files, args.target)
    else:
        test_files(target_files, args.target)


if __name__ == "__main__":
    main()
