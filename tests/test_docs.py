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
sys.path.insert(0, ROOT)
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
    return [compiler]


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

    def test_package_manager_page(self):
        # package-manager.html documents machine-checked contracts (the
        # leash.lock shape, the --version output), so it gets its own
        # assertions: docs drift here used to go unnoticed.
        from leash import leashed as leashed_mod

        with open(os.path.join(DOCS, "package-manager.html"),
                  encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        blocks = []
        for m in re.finditer(r"<pre><code>(.*?)</code></pre>", text, re.S):
            blocks.append(html_mod.unescape(TAG.sub("", m.group(1))))

        # 1. The leash.lock example must parse and match write_lockfile's
        # contract: version field, per-entry version/repo/tag/rev, and a
        # 40-hex revision pin consistent with the entry version.
        lock_blocks = [b for b in blocks if '"packages"' in b]
        self.assertTrue(lock_blocks, "leash.lock example missing from docs")
        import json
        doc = json.loads(re.sub(r"#[^\n]*", "", lock_blocks[0]))
        self.assertEqual(doc["version"], leashed_mod.LOCKFILE_VERSION)
        self.assertTrue(doc["packages"], "lock example has no packages")
        for pkg_name, entry in doc["packages"].items():
            for field in ("version", "repo", "tag", "rev"):
                self.assertIn(field, entry,
                              f"lock example entry {pkg_name!r} lacks {field!r}")
            self.assertEqual(entry["tag"], "v" + entry["version"])
            self.assertRegex(entry["rev"], r"^[0-9a-f]{40}$")

        # 2. The `leash pkg --version` / `leashed --version` examples must
        # match the protocol version both implementations carry.
        ver_blocks = [b for b in blocks if "leash pkg --version" in b]
        self.assertTrue(ver_blocks, "--version example missing from docs")
        m = re.search(r"# leash pkg v(\d+\.\d+\.\d+)", ver_blocks[0])
        self.assertIsNotNone(m, "--version example output missing from docs")
        self.assertEqual(m.group(1), leashed_mod.LEASHED_VERSION)
        m = re.search(r"# leashed v(\d+\.\d+\.\d+)", ver_blocks[0])
        self.assertIsNotNone(m, "leashed --version example output missing")
        self.assertEqual(m.group(1), leashed_mod.LEASHED_VERSION)


if __name__ == "__main__":
    unittest.main(verbosity=2)
