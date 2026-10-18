#!/usr/bin/env python3
"""Unit tests for the registry index validator (registry/scripts/validate_index.py).

These were previously in tests/test_leashed.py alongside tests for the
Python `leashed` package manager reference. That reference has been removed
(the self-hosted `leash pkg` port in compiler/leashed.lsh replaces it), so
only the registry-validator tests — which exercise the separate CI script —
remain here.

Run directly:  python3 tests/test_registry_validator.py
Or via unittest discovery from the repo root:
    python3 -m unittest tests.test_registry_validator
"""
import importlib.util
import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

_spec = importlib.util.spec_from_file_location(
    "validate_index",
    os.path.join(REPO_ROOT, "registry", "scripts", "validate_index.py"))
V = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(V)


PREV = {
    "repo": "https://github.com/alice/mylib.git",
    "description": "d", "author": "alice", "publisher": "alice",
    "version": "1.0.0",
    "versions": {"1.0.0": {"repo": "https://github.com/alice/mylib.git",
                            "tag": "v1.0.0"}},
}


def entry(version="2.0.0", publisher="alice", author="alice",
          repo="https://github.com/alice/mylib.git", versions=None):
    if versions is None:
        versions = dict(PREV["versions"])
        versions[version] = {"repo": repo, "tag": f"v{version}"}
    return {"repo": repo, "description": "d", "author": author,
            "publisher": publisher, "version": version, "versions": versions}


def self_validate_entry(name, e, prev=None):
    return V.validate_entry(name, e, prev, "alice", do_network=False)


class TestRegistryValidator(unittest.TestCase):
    def validate(self, name, e, prev=None, author="alice"):
        return V.validate_entry(name, e, prev, author, do_network=False)

    def test_new_claim_ok(self):
        problems = self.validate("newlib", entry(publisher="alice"),
                                 prev=None)
        self.assertEqual(problems, [])

    def test_update_by_owner_ok(self):
        self.assertEqual(self.validate("mylib", entry(), prev=PREV), [])

    def test_hijack_rejected(self):
        # Mallory (not the owner) tries to take over the entry via his own PR
        problems = self.validate("mylib", entry(publisher="mallory",
                                                author="mallory",
                                                repo="https://github.com/mallory/mylib.git"),
                                 prev=PREV, author="mallory")
        self.assertTrue(any("already registered" in p for p in problems))

    def test_publish_under_other_login_rejected(self):
        problems = self.validate("newlib", entry(publisher="bob"), prev=None,
                                 author="alice")
        self.assertTrue(any("does not match PR author" in p for p in problems))

    def test_repo_must_be_under_publisher(self):
        problems = self.validate("newlib", entry(repo="https://github.com/bob/x.git"),
                                 prev=None)
        self.assertTrue(any("repo URL must be" in p for p in problems))

    def test_downgrade_rejected(self):
        problems = self.validate("mylib", entry(version="0.9.0"), prev=PREV)
        self.assertTrue(any("strictly greater" in p for p in problems))
        problems = self.validate("mylib", entry(version="1.0.0"), prev=PREV)
        self.assertTrue(any("strictly greater" in p for p in problems))

    def test_prerelease_bump_allowed(self):
        self.assertEqual(
            self.validate("mylib", entry(version="1.0.1-rc.1"), prev=PREV), [])

    def test_bad_semver_rejected(self):
        problems = self.validate("mylib", entry(version="notsemver"), prev=PREV)
        self.assertTrue(any("invalid version" in p for p in problems))

    def test_dropping_old_versions_rejected(self):
        problems = self.validate("mylib", entry(versions={"2.0.0": {}}),
                                 prev=PREV)
        self.assertTrue(any("versions were removed" in p for p in problems))

    def test_missing_publisher_rejected(self):
        e = entry()
        del e["publisher"]
        problems = self.validate("mylib", e, prev=PREV)
        self.assertTrue(any("missing 'publisher'" in p for p in problems))

    def test_deleted_entries_rejected_in_changes(self):
        base = {"libraries": {"gone": PREV}}
        head = {"libraries": {}}
        _, _, added, changed, deleted = V.compute_changes(base, head)
        self.assertEqual((added, changed), ([], []))
        self.assertEqual(deleted, ["gone"])

    def test_semver_semantics(self):
        # The validator must agree with semantic-versioning ordering.
        pairs = [("1.2.3", "1.2.2", True), ("1.0.0", "1.0.0-rc.1", True),
                 ("1.0.0-a.2", "1.0.0-a.10", False)]
        for a, b, expected in pairs:
            self.assertEqual(V.is_newer(a, b), expected, (a, b))


class TestVersionMapValidation(unittest.TestCase):
    """versions[v]['repo'] is cloned verbatim by clients on pinned installs."""

    def test_poisoned_version_repo_rejected(self):
        e = entry()
        e["versions"]["2.0.0"]["repo"] = "ext::sh -c 'touch /tmp/pwned'"
        problems = self_validate_entry("mylib", e, PREV)
        self.assertTrue(any("versions['2.0.0'] repo URL" in p for p in problems))

    def test_foreign_version_repo_rejected(self):
        e = entry()
        e["versions"]["2.0.0"]["repo"] = "https://github.com/mallory/evil.git"
        problems = self_validate_entry("mylib", e, PREV)
        self.assertTrue(any("versions['2.0.0'] repo URL" in p for p in problems))

    def test_bad_version_tag_rejected(self):
        e = entry()
        e["versions"]["2.0.0"]["tag"] = "v9.9.9"
        problems = self_validate_entry("mylib", e, PREV)
        self.assertTrue(any("versions['2.0.0'] tag" in p for p in problems))

    def test_wellformed_version_map_ok(self):
        problems = self_validate_entry("mylib", entry(), PREV)
        self.assertEqual(problems, [])

    def test_non_object_version_entry_rejected(self):
        e = entry()
        e["versions"]["2.0.0"] = "https://github.com/alice/mylib.git"
        problems = self_validate_entry("mylib", e, PREV)
        self.assertTrue(any("versions['2.0.0'] must be an object" in p for p in problems))


if __name__ == "__main__":
    unittest.main(verbosity=2)
