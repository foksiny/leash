"""The `leash` command — the self-hosted compiler.

Installation builds the self-hosted compiler with the Python compiler (the
sources live in `compiler/*.lsh`) and ships the resulting binary inside this
package as `leash/native/leashc`.

The native compiler implements the whole toolchain — compile, run, check,
dump, init, build, install, update, self-host, dbg — so every `leash`
command is served by it. The Python implementation (`leashp`) is used only
when:

  * the native binary is unavailable (non-Linux installs), or
  * LEASH_FORCE_PYTHON=1 is set (debugging the toolchain), or
  * a cross-compile target other than linux64 is requested (the native
    compiler links with the host clang for linux64).
"""

import os
import sys

# Non-linux64 cross-compile targets stay on the Python toolchain
_NATIVE_TARGETS = {"linux64"}


def native_binary_path():
    return os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "native", "leashc"
    )


def have_native():
    path = native_binary_path()
    return os.path.exists(path) and os.access(path, os.X_OK)


def exec_native(argv):
    """Replace this process with the native compiler.

    Returns False when the binary is unavailable (never returns otherwise).
    Exports LEASH_RUNTIME so the compiler finds gc.c / link stubs next to
    this file from any working directory.
    """
    if not have_native():
        return False
    path = native_binary_path()
    pkg_dir = os.path.dirname(os.path.abspath(__file__))
    env = dict(os.environ)
    env.setdefault("LEASH_RUNTIME", pkg_dir)
    os.execve(path, [path] + list(argv), env)
    return True


def _python_needed(argv):
    """(needs_python, reason): why this invocation must run on Python."""
    if os.environ.get("LEASH_FORCE_PYTHON"):
        return True, "LEASH_FORCE_PYTHON is set"
    if not have_native():
        return True, "the native compiler was not built for this install"
    for i, a in enumerate(argv):
        if a == "--target" and i + 1 < len(argv):
            if argv[i + 1] not in _NATIVE_TARGETS:
                return True, "cross-compile target %r uses the Python toolchain" % argv[i + 1]
    return False, ""


def main():
    argv = sys.argv[1:]
    needs_python, reason = _python_needed(argv)
    if needs_python:
        if not have_native() and not os.environ.get("LEASH_FORCE_PYTHON"):
            # Be loud: a missing native binary is an install problem, not a
            # routing decision. Reinstall the package to rebuild it.
            sys.stderr.write(
                "leash: native self-hosted compiler not found "
                "(%s/leash/native/leashc) -- using the Python implementation.\n"
                "       Reinstall the package (`pip install -e .`) to rebuild it.\n"
                % os.path.dirname(os.path.abspath(__file__))
            )
        else:
            sys.stderr.write("leash: using the Python toolchain (%s)\n" % reason)
    else:
        exec_native(argv)  # returns False only if the binary vanished
    from .cli import main as _python_main
    _python_main()
