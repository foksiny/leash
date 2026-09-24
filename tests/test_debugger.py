#!/usr/bin/env python3
"""End-to-end tests for the native debugger (leash dbg).

Runs the CLI on small programs with piped debugger commands and asserts on
the session transcript. Requires llvmlite in the interpreter running this.
"""
import os
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

PYTHON = sys.executable
CLI = [PYTHON, "-m", "leash.cli", "dbg"]

DBG_PROG = """fnc add(a int, b int) : int {
    r: int = a + b;
    return r;
}

fnc main() : void {
    x: int = add(19, 23);
    show(x);
    show("done");
}
"""


def run_dbg(commands, extra_args=None, source=DBG_PROG):
    with tempfile.NamedTemporaryFile(
            "w", suffix=".lsh", delete=False,
            dir=os.path.join(tempfile.gettempdir(), "")) as f:
        f.write(source)
        path = f.name
    try:
        cmd = CLI + [path] + (extra_args or [])
        if commands is None:
            # closed stdin — the debuggee must NOT block on it
            proc = subprocess.run(
                cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                cwd=REPO_ROOT, timeout=120)
        else:
            proc = subprocess.run(
                cmd, input=commands, capture_output=True, text=True,
                cwd=REPO_ROOT, timeout=120)
        return proc.stdout + proc.stderr
    finally:
        os.unlink(path)


class TestDebugger(unittest.TestCase):
    def test_step_through(self):
        out = run_dbg("s\ns\ns\ns\ns\ns\ns\nq\n")
        self.assertIn("-->", out)
        self.assertIn("in main", out)
        # stepping enters the callee
        self.assertIn("in add", out)

    def test_breakpoint_and_continue(self):
        out = run_dbg("c\n", extra_args=["--run", "--break", "7"])
        self.assertIn("hit breakpoint at line 7", out)

    def test_break_on_entry_via_env_flag(self):
        out = run_dbg("q\n", extra_args=["--run"])
        # with --run and no breakpoints the program runs to the end
        self.assertIn("done", out)

    def test_noninteractive_stdin_runs_through(self):
        # closed stdin must not hang the debuggee
        out = run_dbg(None)
        self.assertIn("done", out)

    def test_help_and_invalid(self):
        out = run_dbg("h\nzzz\nq\n")
        self.assertIn("commands:", out)
        self.assertIn("unknown command", out)

    def test_dbg_b_d_cycle(self):
        # set+delete a breakpoint at line 4, then continue -> runs to the end
        out = run_dbg("b 4\nd 4\nc\n")
        self.assertIn("breakpoint set", out)
        self.assertIn("breakpoint cleared", out)
        self.assertIn("done", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
