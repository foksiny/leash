#!/usr/bin/env python3
"""Version consistency: every version string in the repo must agree.

The compiler version is declared once (compiler/main.lsh). Everything
that ships a version — the `leash pkg` package manifest, the VS Code
extension (package.json + package-lock.json), the README badge, the
docs' release badges, the CHANGELOG heading and the bootstrap seed
names — must carry the same number. A release that bumps one but not
the others is exactly the kind of drift this test exists to catch.
"""
import glob
import json
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN_LSH = os.path.join(ROOT, "compiler", "main.lsh")
PKG_LSHC = os.path.join(ROOT, "compiler", "leash-pkg.lshc")
EXT_DIR = os.path.join(ROOT, "syntax_highlighters", "vscode")
README = os.path.join(ROOT, "README.md")
CHANGELOG = os.path.join(ROOT, "CHANGELOG.md")
DOCS = os.path.join(ROOT, "docs")


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _compiler_version():
    """The single source of truth: leash_version() in compiler/main.lsh."""
    m = re.search(r'fnc leash_version\(\)\s*:\s*string\s*\{\s*return\s*"([^"]+)"',
                  _read(MAIN_LSH))
    assert m, "leash_version() not found in compiler/main.lsh"
    return m.group(1)


class VersionSyncTest(unittest.TestCase):
    def setUp(self):
        self.version = _compiler_version()

    def test_version_is_a_release_number(self):
        self.assertRegex(self.version, r"^\d+\.\d+\.\d+$")

    def test_leash_pkg_manifest_version(self):
        m = re.search(r'^version:\s*"([^"]+)"', _read(PKG_LSHC), re.M)
        self.assertIsNotNone(m, "no version field in compiler/leash-pkg.lshc")
        self.assertEqual(m.group(1), self.version)

    def test_package_manager_version_is_independent(self):
        # `leash pkg` is the package manager, not the compiler: it keeps
        # its own protocol version (0.5.0, inherited from the retired
        # Python entry point) and must never be aligned with the
        # compiler's release number.
        src = _read(os.path.join(ROOT, "compiler", "leashed.lsh"))
        m = re.search(r'fnc pkg_version\(\)\s*:\s*string\s*\{\s*return\s*"([^"]+)"',
                      src)
        self.assertIsNotNone(m, "pkg_version() not found in compiler/leashed.lsh")
        self.assertEqual(m.group(1), "0.5.0")
        self.assertNotEqual(m.group(1), self.version,
                            "the package manager's protocol version must stay "
                            "independent of the compiler version")

    def test_vscode_extension_version(self):
        pkg = json.loads(_read(os.path.join(EXT_DIR, "package.json")))
        self.assertEqual(pkg["version"], self.version,
                         "the VS Code extension version must track the "
                         "Leash version")
        lock = json.loads(_read(os.path.join(EXT_DIR, "package-lock.json")))
        self.assertEqual(lock.get("version"), self.version)
        root = lock.get("packages", {}).get("", {})
        if root:
            self.assertEqual(root.get("version"), self.version)

    def test_vscode_readme_names_the_matching_vsix(self):
        readme = _read(os.path.join(EXT_DIR, "README.md"))
        for m in re.finditer(r"leash-([0-9][0-9.]*)\.vsix", readme):
            self.assertEqual(m.group(1), self.version,
                             "the extension README points at a .vsix of a "
                             "different version")

    def test_readme_badge(self):
        m = re.search(r"\*\*Version ([0-9][0-9.]*)\*\*", _read(README))
        self.assertIsNotNone(m, "no '**Version X.Y.Z**' line in README.md")
        self.assertEqual(m.group(1), self.version)

    def test_docs_release_badges(self):
        pages = sorted(glob.glob(os.path.join(DOCS, "*.html")))
        self.assertTrue(pages, "no docs pages found")
        for page in pages:
            text = _read(page)
            for m in re.finditer(r"v([0-9][0-9.]*)\s+Release", text):
                self.assertEqual(
                    m.group(1), self.version,
                    "%s carries a release badge of a different version"
                    % os.path.basename(page))

    def test_changelog_has_the_section(self):
        text = _read(CHANGELOG)
        self.assertIn("## [%s]" % self.version, text,
                      "CHANGELOG.md has no section for the current version")

    def test_bootstrap_seeds_are_named_after_the_version(self):
        seed = os.path.join(ROOT, "bootstrap",
                            "leash-%s-target-x86_64" % self.version)
        self.assertTrue(os.path.exists(seed),
                        "bootstrap seed missing: %s" % seed)
        # No stale seed set from an older release is left behind.
        for old in glob.glob(os.path.join(ROOT, "bootstrap", "leash-*-target-*")):
            self.assertIn("leash-%s-target-" % self.version, old,
                          "stale bootstrap seed from another release: %s"
                          % os.path.basename(old))

    def test_compiler_reports_the_same_version(self):
        import subprocess
        leashc = os.path.join(ROOT, "bin", "leashc")
        if not os.path.exists(leashc):
            self.skipTest("bin/leashc not built")
        r = subprocess.run([leashc, "version"], capture_output=True, text=True,
                           timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(self.version, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
