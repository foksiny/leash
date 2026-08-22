#!/usr/bin/env python3
"""Unit tests for the leashed package manager and registry validator.

Run directly:  python3 tests/test_leashed.py
Or via unittest discovery from the repo root:
    python3 -m unittest tests.test_leashed
"""
import importlib.util
import os
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from leash import leashed as L  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "validate_index",
    os.path.join(REPO_ROOT, "registry", "scripts", "validate_index.py"))
V = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(V)


class TestSemver(unittest.TestCase):
    def test_valid_versions(self):
        for v in ["0.1.0", "1.22.3", "10.0.99", "1.0.0-beta", "1.0.0-rc.1.2"]:
            self.assertTrue(L.validate_version(v), v)

    def test_invalid_versions(self):
        for v in [None, "", "v1.2.3", "1.2", "1.2.x", "banana", "01.2.3"]:
            self.assertFalse(L.validate_version(v), v)

    def test_ordering(self):
        self.assertTrue(L.is_newer_version("1.2.3", "1.2.2"))
        self.assertFalse(L.is_newer_version("1.2.3", "1.2.3"))
        self.assertFalse(L.is_newer_version("1.2.2", "1.2.3"))
        # release beats prerelease of same X.Y.Z (semver rule 9/11)
        self.assertTrue(L.is_newer_version("1.0.0", "1.0.0-rc.1"))
        self.assertTrue(L.is_newer_version("0.22.3", "0.22.3-beta"))
        # numeric identifiers compare numerically, not lexically
        self.assertTrue(L.is_newer_version("1.0.0-alpha.10", "1.0.0-alpha.2"))
        self.assertFalse(L.is_newer_version("1.0.0-alpha.2", "1.0.0-alpha.10"))
        # larger prerelease field set wins when prefix equal
        self.assertTrue(L.is_newer_version("1.0.0-alpha.1", "1.0.0-alpha"))

    def test_sorted_versions_newest_first(self):
        got = L.sorted_versions(["1.0.0", "0.9.0", "2.0.0-pre", "2.0.0"])
        self.assertEqual(got, ["2.0.0", "2.0.0-pre", "1.0.0", "0.9.0"])


class TestNames(unittest.TestCase):
    def test_good(self):
        for n in ["mylib", "_priv", "a-b-c", "lib123"]:
            self.assertEqual(L.validate_name(n), n)

    def test_bad_exits(self):
        for n in ["1abc", "has space", "dot.name", "", "a/b"]:
            with self.assertRaises(SystemExit):
                L.validate_name(n)


class TestConfigRoundTrip(unittest.TestCase):
    def test_round_trip_preserves_hash_in_quotes(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "leash-pkg.lshc")
        cfg = {
            "name": "x", "version": "1.0.0", "author": "me",
            "description": "desc with # hash", "main": "src/main.lsh",
            "repo": "https://github.com/me/x.git",
        }
        L.write_pkg_config(p, cfg)
        back = L.read_pkg_config(p)
        for k, v in cfg.items():
            self.assertEqual(back.get(k), v, k)

    def test_inline_comment_stripped_outside_quotes(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "leash-pkg.lshc")
        with open(p, "w") as f:
            f.write('name: "val" # trailing comment\n')
        self.assertEqual(L.read_pkg_config(p).get("name"), "val")


class TestTargetDetection(unittest.TestCase):
    def test_git_targets(self):
        for t in ["user/repo", "https://github.com/u/r.git",
                  "git@github.com:u/r.git", "u_r/r-x.y.git"]:
            self.assertTrue(L._looks_like_git_target(t), t)

    def test_non_git_targets(self):
        for t in ["mylib", "mylib@1.2.3", "my_lib-name"]:
            self.assertFalse(L._looks_like_git_target(t), t)


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

    def test_semver_agreement_between_client_and_validator(self):
        # Both modules must agree on semver semantics.
        pairs = [("1.2.3", "1.2.2", True), ("1.0.0", "1.0.0-rc.1", True),
                 ("1.0.0-a.2", "1.0.0-a.10", False)]
        for a, b, expected in pairs:
            self.assertEqual(L.is_newer_version(a, b), expected, (a, b))
            self.assertEqual(V.is_newer(a, b), expected, (a, b))


if __name__ == "__main__":
    unittest.main(verbosity=2)
