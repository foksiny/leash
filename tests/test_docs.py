#!/usr/bin/env python3
"""Documentation doc-tests: every Leash snippet in docs/*.html is extracted,
checked with the compiler, and the result is asserted.

This turns documentation drift from an ongoing problem requiring human
vigilance into an automatic failure: if a docs example stops compiling
(behavior changed, syntax renamed), the test suite goes red — the same
contract rustdoc gives Rust.

Snippets that intentionally document failing code (they contain an
`error [LEASH-` diagnostic in a comment) are asserted to FAIL; everything
else containing a `fnc` declaration must type-check cleanly. Non-Leash
blocks (shell, config, plain prose) are skipped.
"""
import html as html_mod
import os
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(ROOT, "docs")

# Leash-ness heuristic: a block is Leash code if it declares functions or
# uses the language's signature keywords.
LEASH_HINT = re.compile(r"\b(fnc\s|def\s+\w+\s*:|show\(|vec<|foreach\s)")

# Snippets documenting errors on purpose: their comments carry the
# diagnostic format the compiler prints.
EXPECTED_FAIL = re.compile(r"(//|--|;)\s*error(\s*\[LEASH-|:)")

# Strip only the docs' own markup: bare '<' inside code (future<int>) is
# Leash syntax, not a tag — a generic tag regex would eat it together with
# the following <span>.
TAG = re.compile(r"</?span[^>]*>|</?code[^>]*>|<br/?>|</>")


def extract_snippets(path):
    """Extract (line_no, code) Leash snippets from a docs HTML file."""
    with open(path, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    snippets = []
    for m in re.finditer(r"<pre><code>(.*?)</code></pre>", text, re.S):
        raw = m.group(1)
        line_no = text.count("\n", 0, m.start()) + 1
        code = html_mod.unescape(TAG.sub("", raw))
        if LEASH_HINT.search(code):
            snippets.append((line_no, code))
    return snippets


def compiler_cmd():
    compiler = os.environ.get("LEASHC", os.path.join(ROOT, "bin", "leashc"))
    if os.path.exists(compiler):
        return [compiler]
    return [sys.executable, "-m", "leash.cli"]


def check_snippet(code):
    """Type-check a snippet. Returns (returncode, output)."""
    with tempfile.NamedTemporaryFile(
        "w", suffix=".lsh", delete=False, dir=tempfile.gettempdir()
    ) as fh:
        fh.write(code)
        path = fh.name
    try:
        # stdlib snippets import packages from the repo's lib root
        cmd = compiler_cmd() + ["compile", path, "--check"]
        if os.path.isdir(os.path.join(ROOT, "installthis")):
            cmd += ["--other-imports", "installthis"]
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120, cwd=ROOT,
        )
        return r.returncode, (r.stdout + r.stderr)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


class TestDocs(unittest.TestCase):
    def test_all_doc_snippets(self):
        failures = []
        checked = 0
        skipped = 0
        for name in sorted(os.listdir(DOCS)):
            if not name.endswith(".html"):
                continue
            path = os.path.join(DOCS, name)
            for line_no, code in extract_snippets(path):
                # Skip fragments without any function declaration: they
                # cannot type-check meaningfully on their own.
                if "fnc" not in code and "def " not in code:
                    skipped += 1
                    continue
                # Skip multi-program blocks (several independent examples
                # pasted into one pre block would collide on main).
                if code.count("fnc main") > 1:
                    skipped += 1
                    continue
                rc, out = check_snippet(code)
                where = f"{name}:{line_no}"
                # Registry packages aren't in a source checkout: a snippet
                # importing one can't resolve here and documents behavior
                # the package manager provides after `leashed install`.
                if ("could not find imported module" in out
                        or "not found or ambiguous" in out):
                    skipped += 1
                    continue
                checked += 1
                if EXPECTED_FAIL.search(code):
                    if rc == 0:
                        failures.append(
                            f"{where}: documents an error but compiles clean"
                        )
                else:
                    if rc != 0:
                        first = out.strip().splitlines()[:1]
                        failures.append(f"{where}: {first}")
        self.assertEqual(
            failures, [],
            msg="\n".join(failures)
            + f"\n(checked {checked}, skipped {skipped} fragments)",
        )
        # Sanity: the harness must actually be checking something
        self.assertGreater(checked, 0, "no doc snippets were checked")


if __name__ == "__main__":
    unittest.main(verbosity=2)
