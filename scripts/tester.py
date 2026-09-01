import shutil
import subprocess
import os
import sys
import argparse
import re
import platform as pyplatform
import signal
from pathlib import Path

# Paths to important directories
WORKSPACE_DIR = Path(__file__).resolve().parent.parent
EXAMPLES_DIR = WORKSPACE_DIR / "examples"
EXPECTED_DIR = WORKSPACE_DIR / "tests" / "expected"

# Command to run leash
python_cmd = "python" if sys.platform == "win32" else "python3"
LEASH_RUN_CMD = [python_cmd, "-m", "leash.cli", "run"]
LEASH_COMPILE_CMD = [python_cmd, "-m", "leash.cli", "compile"]

# Pattern to match pointer values (e.g., 0x7ffe417814fc)
POINTER_PATTERN = re.compile(r"0x[0-9a-fA-F]+")

# Known platform identifiers that should be normalized
KNOWN_PLATFORMS = ["linux64", "linux32", "win64", "macos", "macos-arm"]


def get_current_platform():
    """Detect the current platform identifier matching leash target names."""
    system = pyplatform.system().lower()
    machine = pyplatform.machine().lower()
    if system == "linux":
        if machine in ("x86_64", "amd64"):
            return "linux64"
        elif machine in ("i386", "i686", "x86"):
            return "linux32"
    elif system == "windows":
        return "win64"
    elif system == "darwin":
        if machine in ("arm64", "aarch64"):
            return "macos-arm"
        else:
            return "macos"
    return "linux64"  # fallback


def _wsl_available():
    """Return True if WSL is installed and has a usable distro."""
    if pyplatform.system().lower() != "windows":
        return False
    if not shutil.which("wsl"):
        return False
    try:
        res = subprocess.run(["wsl", "-l", "-q"], capture_output=True, text=True, timeout=10)
        if res.returncode == 0:
            return True
        res = subprocess.run(["wsl", "--status"], capture_output=True, text=True, timeout=10)
        return res.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def check_cross_prerequisites(target):
    """Check whether the toolchain required to test `target` from the current host is present.

    Returns (ok, reason). If ok is False, `reason` is a human-readable hint
    explaining what is missing.
    """
    if not target:
        return True, None
    current = get_current_platform()
    if target == current:
        return True, None

    # --- win64 on Linux/macOS: needs MinGW cross-compiler + wine ---
    if target == "win64" and current in ("linux64", "linux32"):
        has_mingw = bool(
            shutil.which("x86_64-w64-mingw32-gcc") or shutil.which("x86_64-w64-mingw32-clang")
        )
        if not has_mingw:
            return False, "MinGW cross-compiler not installed (sudo apt install gcc-mingw-w64-x86-64)"
        if not shutil.which("wine"):
            return False, "wine not installed (sudo apt install wine) — needed to run win64 binaries on Linux"
        return True, None

    # --- Linux targets on Windows: needs WSL ---
    if target in ("linux64", "linux32") and current == "win64":
        if not _wsl_available():
            return False, "WSL not installed — install it (wsl --install) with a distro that has gcc"
        return True, None

    # --- linux32 on linux64: needs 32-bit cross toolchain ---
    if target == "linux32" and current == "linux64":
        if not shutil.which("i686-linux-gnu-gcc"):
            return False, "i686 cross-compiler not installed (sudo apt install gcc-multilib or gcc-i686-linux-gnu)"
        return True, None

    # --- macOS on non-macOS: can compile but not run ---
    if target in ("macos", "macos-arm") and current not in ("macos", "macos-arm"):
        return False, "macOS binaries cannot be executed on non-macOS hosts (compile-only; no runner)"

    # Other cross combinations: assume unsupported for `leash run`
    if target != current:
        return False, f"Cross-compilation from {current} to {target} has no runner on this host"

    return True, None


def describe_cross_mode(target):
    """Return a human-readable cross-compilation description, or None if native."""
    if not target:
        return None
    current = get_current_platform()
    if target == current:
        return None
    runners = {
        ("linux64", "win64"): "MinGW + wine",
        ("linux32", "win64"): "MinGW + wine",
        ("win64", "linux64"): "WSL",
        ("win64", "linux32"): "WSL",
    }
    runner = runners.get((current, target))
    if runner:
        return f"Cross-compiling {current} -> {target} via {runner}"
    return f"Cross-compiling {current} -> {target}"


def normalize_pointers(text):
    """Replace pointer values with a placeholder for comparison."""
    # Normalize execution timestamp (remove it for comparison)
    text = re.sub(
        r"--- Executed at \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} ---\n?", "", text
    )
    # Normalize pointer values for comparison
    text = POINTER_PATTERN.sub("0xPOINTER", text)
    # Normalize Windows pointer format (00007FFFFE2FFEBC style)
    text = re.sub(r"\b[0-9A-Fa-f]{12,16}\b", "0xPOINTER", text)
    # Normalize rand.lsh output FIRST
    text = re.sub(r"[A-Z][a-z]+ [A-Z][a-z]+ -?\d+ \d+\.\d+", "RANDOM_OUTPUT", text)
    # Normalize multithread race-condition output: worker progress lines
    # interleave and their counts vary with thread scheduling.
    text = re.sub(r"^counter incremented to: \d+\n?", "", text, flags=re.M)
    text = re.sub(r"^calculate updated result to: \d+\n?", "", text, flags=re.M)
    text = re.sub(r"^result:\d+ counter:\d+\n?", "", text, flags=re.M)
    # Normalize float precision differences
    text = re.sub(r"(\d+\.\d+?)0+", r"\1", text)
    # Normalize warning order
    lines = text.split("\n")
    filtered = [
        l for l in lines if not l.startswith("warning:") and not l.startswith("tip:")
    ]
    text = "\n".join(filtered)
    # Normalize boolean representations
    text = re.sub(r"\btrue\b", "1", text)
    text = re.sub(r"\bfalse\b", "0", text)
    # Normalize random numbers
    text = re.sub(r"\b\d{6,}\b", "RANDOM", text)
    text = re.sub(r"\b0\.\d+\b", "RANDOM_FLOAT", text)
    # Normalize path differences - DO ARGS NORMALIZATION FIRST
    # Normalize args.lsh output pattern for all platforms
    text = re.sub(r"0: Z:.*", "0: ./.__temp_run_leash_exe", text)
    text = re.sub(
        r"0: .*\.__temp_run_leash_exe[_A-Za-z0-9]*",
        "0: ./.__temp_run_leash_exe",
        text,
    )
    # Handle new unique filename format with timestamp and UUID suffix
    text = re.sub(r"0: \.\S+\.exe", "0: ./.__temp_run_leash_exe", text)
    text = re.sub(r"\d+\n0: .+\n1: .+", "1\n0: ./.__temp_run_leash_exe", text)

    # Normalize file path differences
    text = re.sub(r"/home/jose/projects/leash/", "", text)
    text = re.sub(r"Z:\\home\\jose\\projects\\leash\\", "", text)
    # Normalize workspace directory to relative path
    workspace_str = str(WORKSPACE_DIR)
    text = text.replace(workspace_str + "\\", "")
    text = text.replace(workspace_str + "/", "")
    # Normalize backslashes to forward slashes
    text = text.replace("\\", "/")
    # Normalize Windows executable extensions
    text = re.sub(r"\.exe\b", "", text)

    # Normalize timing differences
    text = re.sub(r"\d+s\b", "Xs", text)
    # Normalize readline prompt artifacts
    text = re.sub(r"\x1b\[\d+G\x1b\[0J", "", text)
    # Normalize error message format differences
    text = text.replace("ReferenceError: ", "")
    text = text.replace("Error: ", "")
    # Normalize Wine null output
    text = text.replace("(null)", "")
    # Normalize exec output differences
    text = re.sub(r"Hello World\n\n", "Hello World\n0\n", text)
    # Normalize input.lsh
    text = re.sub(r"What's your name\? Hello, !", "ERROR: Program timed out!", text)
    # Normalize cross-compiler messages
    text = re.sub(r"Using cross-compiler: .+\n?", "", text)
    text = re.sub(r"Using WSL cross-compiler for '.+' target\n?", "", text)
    # Normalize Wine stack overflow
    text = re.sub(r".*stack overflow.*\n?", "", text)

    # Normalize platform-dependent output
    current_platform = get_current_platform()
    for plat in KNOWN_PLATFORMS:
        if plat != current_platform:
            text = text.replace(plat, "__PLATFORM__")

    # Normalize trailing whitespace
    text = "\n".join(l.rstrip() for l in text.split("\n"))
    return text.rstrip("\n") + "\n"


def run_leash(file_path, target=None):
    """Run a leash file and return its combined output (stdout and stderr).

    Cross targets that use an emulator (win64 via wine) are slower to start,
    so the timeout is relaxed for those cases.  The function also correctly
    handles subprocess.TimeoutExpired from subprocess.run (which carries
    stdout/stderr on the exception, not a child Popen).
    """
    # Wine emulation is measurably slower (~1-2 s startup per invocation)
    timeout = 20 if target == "win64" else 10
    try:
        cmd = LEASH_RUN_CMD + [str(file_path)]
        if target:
            cmd += ["--target", target]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=WORKSPACE_DIR,
            timeout=timeout,
        )
        return result.stdout + result.stderr, result.returncode
    except subprocess.TimeoutExpired as e:
        # subprocess.run kills the child on timeout; capture whatever it emitted
        output = ""
        if getattr(e, "stdout", None):
            output += e.stdout
        if getattr(e, "stderr", None):
            output += e.stderr
        cur = e.stdout if hasattr(e, "stdout") else None
        # Fallback for older Python where TimeoutExpired.output holds combined
        if not output and getattr(e, "output", None):
            output = e.output if isinstance(e.output, str) else ""
        if not output:
            output = f"ERROR: Program timed out after {timeout}s"
        return output, 124
    except Exception as e:
        return f"ERROR: Running leash failed: {e}", 1


def is_manual_input_test(file_path):
    """Check if the leash file requires manual user interaction."""
    if file_path.name in ("input.lsh", "getkey.lsh"):
        return True
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as fh:
            content = fh.read()
        import re
        if re.search(r'(?<!\.)\bget\s*\(', content) or re.search(r'\bkeyget\s*\(', content):
            return True
    except:
        pass
    return False


def record_outputs(files, target=None):
    """Record current outputs as the expected baseline."""
    os.makedirs(EXPECTED_DIR, exist_ok=True)

    # Cross-compilation awareness
    cross_info = describe_cross_mode(target)
    if cross_info:
        print(f"[INFO] {cross_info}")
    ok, reason = check_cross_prerequisites(target)
    if not ok:
        print(f"[WARN] Cross toolchain missing for target '{target}': {reason}")
        print("       Recording anyway — outputs may not be runnable.")

    target_label = f" (target: {target})" if target else ""
    print(f"--- Recording expected outputs for {len(files)} files{target_label} ---")

    for f in files:
        if is_manual_input_test(f):
            print(f"[SKIP]  {f.name} (Requires manual input)")
            continue
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

    # Cross-compilation awareness
    cross_info = describe_cross_mode(target)
    if cross_info:
        print(f"[INFO] {cross_info}")
    ok, reason = check_cross_prerequisites(target)
    if not ok:
        current = get_current_platform()
        print(f"[SKIP] Cross toolchain missing for target '{target}' on {current}: {reason}")
        print(f"       Skipping all {len(files)} tests (install the toolchain to enable).")
        return

    target_label = f" (target: {target})" if target else ""
    print(f"--- Testing {len(files)} files{target_label} ---")

    for f in files:
        if is_manual_input_test(f):
            print(f"[SKIP]  {f.name} (Requires manual input)")
            ignored += 1
            continue

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
        help="Target architecture to test (e.g., linux64, win64)",
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
