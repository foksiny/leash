"""Leash 1.0 — packaging with the self-hosted compiler.

Installation flow (Linux):

  stage 0  the Python compiler compiles compiler/main.lsh  -> leashc_stage1
  stage 1  leashc_stage1 (self-hosted) compiles itself       -> leash/native/leashc

The shipped `leashc` binary IS the compiler that produced itself
(a bootstrap fixed point). `leash compile|run` delegate to it when present;
`LEASH_FORCE_PYTHON=1` opts out. On platforms where the native build is not
possible the install degrades gracefully to the Python implementation.
"""

import os
import subprocess
import sys

from setuptools import setup, find_packages

ROOT = os.path.dirname(os.path.abspath(__file__))
NATIVE_DIR = os.path.join(ROOT, "leash", "native")
NATIVE_BIN = os.path.join(NATIVE_DIR, "leashc")


def build_native_compiler():
    """Full 3-stage bootstrap. The SHIPPED binary is stage 3: compiled by
    the self-hosted compiler that was itself compiled by the self-hosted
    compiler, so it carries exactly the speed, size and feature set of
    the compiler compiling itself with its own optimizations. The fixed
    point (stage 2 IR == stage 3 IR) is verified like `leash self-host`.
    """
    os.makedirs(NATIVE_DIR, exist_ok=True)
    stage1 = os.path.join(NATIVE_DIR, "leashc_stage1")
    stage2 = os.path.join(NATIVE_DIR, "leashc_stage2")
    stage3 = NATIVE_BIN
    env = dict(os.environ)
    # Never recurse into the native launcher while building it
    env["LEASH_FORCE_PYTHON"] = "1"

    # Stage 0: the Python compiler builds the first self-hosted binary.
    # (The Python CLI spells output as `compile <file> to <out>`.)
    subprocess.run(
        [sys.executable, "-m", "leash.cli", "compile",
         "compiler/main.lsh", "to", stage1],
        cwd=ROOT, check=True, env=env,
    )
    os.chmod(stage1, 0o755)

    # Stage 1: first self-compilation
    subprocess.run([stage1, "compile", "compiler/main.lsh", "-o", stage2],
                   cwd=ROOT, check=True)
    os.chmod(stage2, 0o755)

    # Stage 2: SECOND self-compilation -- this is the binary that ships,
    # produced by a compiler that was itself produced by the self-hosted
    # compiler (its own codegen quality, size and link flags)
    subprocess.run([stage2, "compile", "compiler/main.lsh", "-o", stage3],
                   cwd=ROOT, check=True)
    os.chmod(stage3, 0o755)

    # Fixed-point verification: stages 2 and 3 emit identical IR
    ir2 = os.path.join(NATIVE_DIR, "fp_stage2")
    ir3 = os.path.join(NATIVE_DIR, "fp_stage3")
    try:
        subprocess.run([stage2, "compile", "compiler/main.lsh",
                        "--emit-llvm", "-o", ir2], cwd=ROOT, check=True)
        subprocess.run([stage3, "compile", "compiler/main.lsh",
                        "--emit-llvm", "-o", ir3], cwd=ROOT, check=True)
        with open(ir2 + ".ll") as f2, open(ir3 + ".ll") as f3:
            if f2.read() == f3.read():
                print("leash: bootstrap fixed point verified "
                      "(stage 2 IR == stage 3 IR)")
            else:
                sys.stderr.write("WARNING: bootstrap IR differs between "
                                 "stages 2 and 3\n")
    except Exception:
        sys.stderr.write("WARNING: could not verify the bootstrap fixed "
                         "point\n")
    finally:
        for tmp in (ir2, ir3, ir2 + ".ll", ir3 + ".ll", stage1, stage2):
            try:
                os.remove(tmp)
            except OSError:
                pass


def _native_is_stale():
    """True when the shipped binary predates any compiler/*.lsh source.

    Editable installs keep the binary inside the repository; without this
    check a `pip install -e .` after editing the compiler would silently
    keep shipping the old binary and `leash` would behave like `leashp`.
    """
    if not os.path.exists(NATIVE_BIN):
        return True
    bin_mtime = os.path.getmtime(NATIVE_BIN)
    src_dir = os.path.join(ROOT, "compiler")
    if not os.path.isdir(src_dir):
        return False
    for name in sorted(os.listdir(src_dir)):
        if name.endswith(".lsh"):
            if os.path.getmtime(os.path.join(src_dir, name)) > bin_mtime:
                return True
    return False


def _maybe_build_native():
    if os.environ.get("LEASH_SKIP_NATIVE"):
        return
    if os.path.exists(NATIVE_BIN) and not _native_is_stale():
        return
    try:
        build_native_compiler()
        print("leash: built self-hosted compiler -> %s" % NATIVE_BIN)
    except Exception as exc:  # missing clang/llvmlite, non-Linux, ...
        sys.stderr.write(
            "WARNING: skipping the self-hosted compiler build: %s\n"
            "         (the Python implementation will be used instead)\n" % exc
        )


_maybe_build_native()

_package_data = {
    "leash": [
        "gc.c", "gc.h", "cross_compile_stubs.c", "windows_stubs.c",
    ]
}
if os.path.exists(NATIVE_BIN):
    _package_data["leash"].append("native/leashc")

setup(
    name="leash",
    version="1.0.1",
    description="Leash programming language — self-hosted compiler, toolchain and package manager",
    packages=find_packages(),
    package_data=_package_data,
    python_requires=">=3.8",
    install_requires=["llvmlite"],
    entry_points={
        "console_scripts": [
            # `leash`  — the self-hosted compiler (native, Python fallback)
            # `leashp` — the pure Python compiler / toolchain
            # `leashc` — alias of `leash`
            "leash=leash.native:main",
            "leashp=leash.cli:main",
            "leashc=leash.native:main",
            "leashed=leash.leashed:main",
        ],
    },
)
