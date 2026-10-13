"""The `leash` command — the self-hosted compiler.

Installation builds the self-hosted compiler with the Python compiler (the
sources live in `compiler/*.lsh`) and ships the resulting binary inside this
package as `leash/native/leashc`.

The native compiler implements the whole toolchain — compile, run, check,
dump, init, build, install, update, self-host, dbg — and every
cross-compile target (linux32, linux-arm, win64, macos, macos-arm) via
clang driver flags, so every `leash` command is served by it. The Python
implementation (`leashp`) is used only when:

  * the native binary is unavailable (non-Linux installs), or
  * LEASH_FORCE_PYTHON=1 is set (debugging the toolchain), or
  * --autofree is requested (allocation-tracking codegen lives in the
    Python compiler).
"""

import os
import sys

# --autofree needs the Python compiler's allocation-tracking codegen
_PYTHON_FLAGS = {"--autofree", "-af"}


def native_binary_path():
    return os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "native", "leashc"
    )


def have_native():
    path = native_binary_path()
    return os.path.exists(path) and os.access(path, os.X_OK)


def _compiler_sources_dir():
    """Directory holding compiler/*.lsh next to this package, if present.

    Editable installs and git checkouts keep the compiler sources next to
    the `leash` package; wheels do not ship them (fresh by construction).
    """
    pkg_dir = os.path.dirname(os.path.abspath(__file__))
    cand = os.path.join(os.path.dirname(pkg_dir), "compiler")
    if os.path.isdir(cand):
        return cand
    return None


def _native_is_stale(bin_path=None, src_dir=None):
    """True when the native binary predates any compiler/*.lsh source.

    A stale binary silently behaves like an older compiler release, so the
    dispatcher warns and falls back to Python instead of exec'ing it.
    Returns False when the sources are absent (wheels don't ship them) or
    on any filesystem error (fail open toward the fast path; a missing
    binary is already handled by have_native).
    """
    if bin_path is None:
        bin_path = native_binary_path()
    if src_dir is None:
        src_dir = _compiler_sources_dir()
        if src_dir is None:
            return False
    try:
        bin_mtime = os.path.getmtime(bin_path)
    except OSError:
        return False
    try:
        names = os.listdir(src_dir)
    except OSError:
        return False
    for name in names:
        if name.endswith(".lsh"):
            try:
                if os.path.getmtime(os.path.join(src_dir, name)) > bin_mtime:
                    return True
            except OSError:
                continue
    return False


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
    for a in argv:
        if a in _PYTHON_FLAGS:
            return True, "autofree instrumentation is implemented by the Python compiler"
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
        if _native_is_stale():
            sys.stderr.write(
                "leash: native compiler is older than the compiler sources; "
                "using the Python implementation.\n"
                "       Reinstall the package (`pip install -e .`) to rebuild it.\n"
            )
        elif exec_native(argv):  # returns False only if the binary vanished
            pass
    from .cli import main as _python_main
    _python_main()
