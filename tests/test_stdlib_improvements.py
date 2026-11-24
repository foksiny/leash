"""Regression tests for small correctness fixes in installthis/ stdlib modules.

Tests compile and run tiny Leash programs with bin/leashc, using the repo's
installthis/ tree as the import root so the fixed sources are exercised
(rather than any previously installed ~/.leash/libs copy).
"""

import os
import subprocess
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEASHC = os.path.join(REPO_ROOT, "bin", "leashc")
STDLIB = os.path.join(REPO_ROOT, "installthis")


def run_leash(source, gc=False, timeout=60):
    """Write a temporary .lsh file, run it, return stdout without the footer."""
    with tempfile.NamedTemporaryFile(
        "w", suffix=".lsh", delete=False, dir=tempfile.gettempdir()
    ) as fh:
        fh.write(source)
        path = fh.name
    try:
        cmd = [LEASHC, "run", path, "--other-imports", STDLIB]
        if gc:
            cmd.append("--gc")
        proc = subprocess.run(
            cmd, cwd=REPO_ROOT, capture_output=True, text=True,
            errors="replace", timeout=timeout,
        )
        out = proc.stdout
        if proc.returncode != 0:
            raise AssertionError(
                "leashc failed (rc=%d):\n%s\n%s"
                % (proc.returncode, out, proc.stderr)
            )
        # leashc appends a "--- Executed at <timestamp> ---" footer.
        lines = [
            line for line in out.splitlines()
            if not line.startswith("--- Executed at")
        ]
        return [line for line in lines if line != ""]
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


class TestStdlibImprovements(unittest.TestCase):
    def _check_both_layouts(self, source, expected):
        for gc in (False, True):
            with self.subTest(gc=gc):
                self.assertEqual(run_leash(source, gc=gc), expected)

    def test_perfect_square_near_int_max(self):
        # The old loop used i*i <= n and overflowed into an infinite loop
        # for n near the 32-bit limit.
        source = """
use algo::series::Series;
fnc main() {
    show(Series.is_perfect_square(2147483647));
    show(Series.is_perfect_square(46340 * 46340));
    show(Series.is_perfect_square(0));
    show(Series.is_perfect_square(-1));
    show(Series.is_perfect_square(145));
}
"""
        self._check_both_layouts(
            source, ["false", "true", "true", "false", "false"])

    def test_bitset_negative_index(self):
        # Negative indexes used to reach a negative shift amount and crash
        # the process with "Shift amount out of range".
        source = """
use collections::bitset::BitSet;
fnc main() {
    b: BitSet = BitSet.make(8);
    show(b.popcount());
    show(b.get(-1));
    b.set(-1);
    b.toggle(-1);
    b.clear(-1);
    show(b.get(-1));
    show(b.popcount());
    b.set(3);
    show(b.get(3), " ", b.popcount());
    show(b.get(8));
    e: BitSet = BitSet.make(0);
    show(e.popcount(), " ", e.get(-1));
}
"""
        self._check_both_layouts(
            source,
            ["0", "false", "false", "0", "true 1", "false", "0 false"])

    def test_base64_valid_matches_decode(self):
        # valid() used to check only the alphabet, accepting strings that
        # decode_bytes() always rejects.
        source = """
use utils::base64::Base64;
fnc main() {
    show(Base64.valid("A="), " ", Base64.valid("A"), " ", Base64.valid("="));
    show(Base64.valid("aGVsbG8="), " ", Base64.valid("aGU="),
         " ", Base64.valid("a=b"));
    show(Base64.valid(""), " ", Base64.decode("aGVsbG8=") == "hello");
}
"""
        self._check_both_layouts(
            source, ["false false false", "true true false", "true true"])

    def test_escape_json_control_chars(self):
        # Escape.json used to emit \\xNN, which Json.parse rejects; JSON
        # requires \\u00NN. unjson() now round-trips that form too.
        source = """
use encoding::escape::Escape;
use json::*;
fnc main() {
    s: string = "" + (char)1;
    e: string = Escape.json(s);
    show(e);
    v: JsonValue = Json.parse("\\\"" + e + "\\\"");
    show(v.ok, " ", v.as_string() == s);
    show(Escape.unjson(e) == s);
    show(Escape.json("a\\\"b"));
    show(Escape.unjson(""));
}
"""
        # show() prints empty strings as blank lines; run_leash drops them.
        self._check_both_layouts(
            source, [r"\u0001", "true true", "true", r'a\"b'])


if __name__ == "__main__":
    unittest.main()
