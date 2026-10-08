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
    """Bootstrap and self-compile the native compiler. Raises on failure."""
    os.makedirs(NATIVE_DIR, exist_ok=True)
    stage1 = os.path.join(NATIVE_DIR, "leashc_stage1")
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

    # Stage 1: the self-hosted compiler compiles itself (the binary that
    # ships is literally the product of the compiler it contains)
    subprocess.run(
        [stage1, "compile", "compiler/main.lsh", "-o", NATIVE_BIN],
        cwd=ROOT, check=True,
    )
    os.chmod(NATIVE_BIN, 0o755)
    os.remove(stage1)


def _maybe_build_native():
    if os.environ.get("LEASH_SKIP_NATIVE"):
        return
    if os.path.exists(NATIVE_BIN):
        return
    try:
        build_native_compiler()
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
    version="1.0.0",
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
