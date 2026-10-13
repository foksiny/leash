#!/usr/bin/env python3
"""Unit tests for the leashed package manager and registry validator.

Run directly:  python3 tests/test_leashed.py
Or via unittest discovery from the repo root:
    python3 -m unittest tests.test_leashed
"""
import importlib.util
import os
import shutil
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


class TestGitUrlValidation(unittest.TestCase):
    """The client must refuse git URLs that execute code or bypass review."""

    def assert_blocked(self, url, **kw):
        with self.assertRaises(SystemExit):
            L.validate_git_url(url, **kw)

    def test_safe_urls_accepted(self):
        for u in ["https://github.com/u/r.git",
                  "https://example.com/repo",
                  "ssh://git@example.com/u/r.git",
                  "git@github.com:u/r.git"]:
            self.assertEqual(L.validate_git_url(u), u, u)

    def test_git_transport_rejected(self):
        # git:// is unauthenticated and unencrypted: a network attacker can
        # substitute package content that gets linked into the user's binary.
        self.assert_blocked("git://example.com/u/r.git")

    def test_code_exec_transports_blocked(self):
        # ext:: runs an arbitrary local shell command; fd:: reads open FDs
        for u in ["ext::sh -c id", "ext::git-upload-pack '/x'",
                  "fd::17", "fd::/proc/self/environ"]:
            self.assert_blocked(u)

    def test_file_and_local_transports_blocked(self):
        for u in ["file:///tmp/evil", "file:///home/u/.ssh", "/tmp/local-repo",
                  "./relative/repo"]:
            self.assert_blocked(u)

    def test_option_injection_blocked(self):
        # A leading '-' makes git parse the "URL" as a command-line option
        # (--upload-pack=... executes an arbitrary command via git).
        for u in ["--upload-pack=touch /tmp/pwned.git",
                  "--exec=evil.git",
                  "-oProxyCommand=evil.git"]:
            self.assert_blocked(u)

    def test_control_chars_blocked(self):
        self.assert_blocked("https://github.com/u/r.git\n--upload-pack=evil")

    def test_registry_http_blocked(self):
        # URLs coming from the registry index must be https.
        self.assert_blocked("http://evil.com/r.git")

    def test_user_typed_http_allowed_with_warning(self):
        # The user typing http:// themselves is allowed (warned elsewhere).
        self.assertEqual(
            L.validate_git_url("http://example.com/r.git", allow_insecure=True),
            "http://example.com/r.git")


class TestSymlinkRejection(unittest.TestCase):
    def test_clean_tree_passes(self):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "main.lsh"), "w") as f:
            f.write("fnc main() : void { ignore; }\n")
        L.assert_no_symlinks(d)  # must not raise

    def test_escaping_symlink_refused(self):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "main.lsh"), "w") as f:
            f.write("fnc main() : void { ignore; }\n")
        os.symlink("/etc/passwd", os.path.join(d, "steal"))
        with self.assertRaises(SystemExit):
            L.assert_no_symlinks(d)

    def test_internal_symlink_also_refused(self):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "main.lsh"), "w") as f:
            f.write("fnc main() : void { ignore; }\n")
        os.symlink("main.lsh", os.path.join(d, "alias"))
        with self.assertRaises(SystemExit):
            L.assert_no_symlinks(d)


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


def self_validate_entry(name, e, prev=None):
    return V.validate_entry(name, e, prev, "alice", do_network=False)


class TestIndexShapeHardening(unittest.TestCase):
    def test_non_dict_entry_fails_cleanly(self):
        index = {"libraries": {"corrupt": "not-an-object"}}
        with self.assertRaises(SystemExit):
            L.get_index_entry(index, "corrupt")

    def test_dict_entry_passes(self):
        index = {"libraries": {"ok": {"version": "1.0.0"}}}
        self.assertEqual(L.get_index_entry(index, "ok"), {"version": "1.0.0"})


import io
import json
import contextlib


class TestLockfile(unittest.TestCase):
    """leash.lock read/write semantics and install-time pinning."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="leashed_lock_")
        self._cwd = os.getcwd()
        os.chdir(self.tmp)
        with open(os.path.join(self.tmp, "leash-pkg.lshc"), "w") as f:
            f.write('name: "testpkg"\nversion: "0.1.0"\nauthor: "tester"\n')

    def tearDown(self):
        os.chdir(self._cwd)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_write_read_round_trip(self):
        L.write_lockfile(self.tmp, {
            "beta": {"version": "2.0.0", "repo": "https://github.com/x/beta.git"},
            "alpha": {"version": "1.0.0", "repo": "https://github.com/x/alpha.git"},
        })
        data = L.read_lockfile(self.tmp)
        self.assertEqual(data["version"], L.LOCKFILE_VERSION)
        # keys are written sorted for stable diffs
        self.assertEqual(list(data["packages"].keys()), ["alpha", "beta"])
        self.assertEqual(data["packages"]["beta"]["tag"], "v2.0.0")

    def test_read_missing_returns_none(self):
        self.assertIsNone(L.read_lockfile(self.tmp))

    def test_read_corrupt_exits(self):
        with open(os.path.join(self.tmp, "leash.lock"), "w") as f:
            f.write("{not json")
        with self.assertRaises(SystemExit):
            L.read_lockfile(self.tmp)

    def test_invalid_versions_are_dropped(self):
        L.write_lockfile(self.tmp, {"bad": {"version": "nope", "repo": "x"}})
        self.assertEqual(L.read_lockfile(self.tmp)["packages"], {})

    def test_pin_record_unpin(self):
        self.assertIsNone(L.lockfile_pin(self.tmp, "alpha"))
        L.lockfile_record(self.tmp, "alpha", "1.2.3", "https://github.com/x/alpha.git")
        self.assertEqual(L.lockfile_pin(self.tmp, "alpha"), "1.2.3")
        L.lockfile_record(self.tmp, "alpha", "1.2.4", "https://github.com/x/alpha.git")
        self.assertEqual(L.lockfile_pin(self.tmp, "alpha"), "1.2.4")
        L.lockfile_unpin(self.tmp, "alpha")
        self.assertIsNone(L.lockfile_pin(self.tmp, "alpha"))

    def test_install_resolves_deps_and_writes_lock(self):
        calls = []

        def fake_registry(name, ver=None):
            calls.append((name, ver))
            return name, ver or "9.9.9", f"https://github.com/x/{name}.git", ""

        orig = L._install_registry
        L._install_registry = fake_registry
        try:
            with open(os.path.join(self.tmp, "leash-pkg.lshc"), "w") as f:
                f.write('name: "testpkg"\nversion: "0.1.0"\nauthor: "t"\n'
                        'dependencies: "alpha, beta@2.0.0"\n')
            with contextlib.redirect_stdout(io.StringIO()):
                L.cmd_install_project_deps(self.tmp, strict=False)
        finally:
            L._install_registry = orig

        self.assertIn(("beta", "2.0.0"), calls)
        self.assertIn(("alpha", None), calls)
        data = L.read_lockfile(self.tmp)
        self.assertEqual(data["packages"]["beta"]["version"], "2.0.0")
        self.assertEqual(data["packages"]["alpha"]["version"], "9.9.9")

    def test_install_locked_requires_lockfile(self):
        with self.assertRaises(SystemExit):
            L.cmd_install_project_deps(self.tmp, strict=True)

    def test_restore_from_lock_uses_pinned_versions(self):
        calls = []

        def fake_install(repo, requested_version=None, libname=None, expect_sha=None):
            calls.append((libname, requested_version, repo, expect_sha))
            return libname, requested_version, ""

        orig = L._install_from_repo
        L._install_from_repo = fake_install
        try:
            L.write_lockfile(self.tmp, {
                "alpha": {"version": "1.2.3", "repo": "https://github.com/x/alpha.git"},
            })
            with contextlib.redirect_stdout(io.StringIO()):
                L.cmd_install_project_deps(self.tmp, strict=True)
        finally:
            L._install_from_repo = orig

        self.assertEqual(calls, [("alpha", "1.2.3", "https://github.com/x/alpha.git", None)])

    def test_bare_install_prefers_locked_version(self):
        seen = []

        def fake_install(repo, requested_version=None, libname=None, expect_sha=None):
            seen.append((libname, requested_version, repo, expect_sha))
            return libname, requested_version, ""

        orig = L._install_from_repo
        L._install_from_repo = fake_install
        try:
            L.write_lockfile(self.tmp, {
                "alpha": {"version": "1.0.0", "repo": "https://github.com/x/alpha.git"},
            })
            with contextlib.redirect_stdout(io.StringIO()):
                L.cmd_install(["alpha"])
        finally:
            L._install_from_repo = orig
        # installs the locked repo at the pinned tag — never touches the registry
        self.assertEqual(seen, [("alpha", "1.0.0", "https://github.com/x/alpha.git", None)])
        self.assertEqual(L.lockfile_pin(self.tmp, "alpha"), "1.0.0")

    def test_bare_install_locked_is_pin_authoritative(self):
        # With a pinned lock, the lockfile's repo+version wins even if the
        # registry is unreachable — the whole point of leash.lock.
        def exploding_index():
            raise AssertionError("registry must not be contacted")

        def fake_install(repo, requested_version=None, libname=None, expect_sha=None):
            return libname, requested_version, ""

        orig_i = L._install_from_repo
        orig_f = L.fetch_index
        L._install_from_repo = fake_install
        L.fetch_index = exploding_index
        try:
            L.write_lockfile(self.tmp, {
                "alpha": {"version": "1.0.0", "repo": "https://github.com/x/alpha.git"},
            })
            with contextlib.redirect_stdout(io.StringIO()):
                L.cmd_install(["alpha"])
        finally:
            L._install_from_repo = orig_i
            L.fetch_index = orig_f

    def test_missing_lock_version_field_exits(self):
        with open(os.path.join(self.tmp, "leash.lock"), "w") as f:
            json.dump({"packages": {}}, f)
        with self.assertRaises(SystemExit):
            L.read_lockfile(self.tmp)

    def test_future_lock_version_exits(self):
        with open(os.path.join(self.tmp, "leash.lock"), "w") as f:
            json.dump({"version": 999, "packages": {}}, f)
        with self.assertRaises(SystemExit):
            L.read_lockfile(self.tmp)

    def test_git_url_install_records_in_project(self):
        # Direct installs inside a project are recorded (lock + dependency)
        # so a later restore reproduces them.
        calls = []

        def fake_install(url, requested_version=None, libname=None, expect_sha=None):
            calls.append(url)
            return (libname or "urlpkg"), (requested_version or "0.1.0"), ""

        orig = L._install_from_repo
        L._install_from_repo = fake_install
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                L.cmd_install(["https://github.com/someone/somelib.git"])
        finally:
            L._install_from_repo = orig
        self.assertEqual(calls, ["https://github.com/someone/somelib.git"])
        data = L.read_lockfile(self.tmp)
        self.assertEqual(data["packages"]["urlpkg"]["version"], "0.1.0")
        self.assertEqual(
            data["packages"]["urlpkg"]["repo"],
            "https://github.com/someone/somelib.git")
        cfg = L.read_pkg_config(os.path.join(self.tmp, "leash-pkg.lshc"))
        self.assertIn("urlpkg@0.1.0", cfg.get("dependencies", ""))

    def test_git_url_install_outside_project_writes_nothing(self):
        # Outside a project there is nowhere to record: pure global install.
        proj = self.tmp
        bare = tempfile.mkdtemp(prefix="leashed_bare_")
        os.chdir(bare)
        try:
            orig = L._install_from_repo
            L._install_from_repo = lambda url, **kw: ("urlpkg", "0.1.0", "")
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    L.cmd_install(["https://github.com/someone/somelib.git"])
            finally:
                L._install_from_repo = orig
            self.assertFalse(os.path.exists(os.path.join(bare, "leash.lock")))
            self.assertFalse(os.path.exists(os.path.join(proj, "leash.lock")))
        finally:
            os.chdir(proj)
            shutil.rmtree(bare, ignore_errors=True)

    def test_locked_flag_without_entry_exits(self):
        def fake_registry(name, ver=None):
            return name, "1.0.0", "https://github.com/x/foo.git", ""

        orig = L._install_registry
        L._install_registry = fake_registry
        try:
            # no leash.lock -> --locked must fail
            with self.assertRaises(SystemExit):
                with contextlib.redirect_stdout(io.StringIO()):
                    L.cmd_install(["foo", "--locked"])
        finally:
            L._install_registry = orig


class TestPhase5SecurityRegressions(unittest.TestCase):
    """Security regressions for the Phase 5 audit fixes."""

    def test_symlinked_root_rejected(self):
        # H2: os.walk always descends into the walk root, so a symlinked
        # package ROOT (library -> /home/victim/.ssh) passed the check and
        # copytree then copied the target directory.
        with tempfile.TemporaryDirectory() as td:
            real = os.path.join(td, "real")
            os.makedirs(real)
            open(os.path.join(real, "secret.txt"), "w").write("top secret")
            link = os.path.join(td, "library")
            os.symlink(real, link)
            with self.assertRaises(SystemExit):
                L.assert_no_symlinks(link)

    def test_write_stub_sanitizes_metadata(self):
        # M7: a newline in package metadata escaped the `//` comment and
        # injected arbitrary Leash source into every consumer's build.
        with tempfile.TemporaryDirectory() as td:
            old_libs = L.LEASH_LIBS_DIR
            L.LEASH_LIBS_DIR = td
            try:
                pkg_dir = os.path.join(td, "pkg")
                os.makedirs(pkg_dir)
                with open(os.path.join(pkg_dir, "package.lshc"), "w") as f:
                    f.write('name: "pkg"\nmain: "src/main.lsh"\n')
                L._write_stub(
                    "pkg",
                    "1.2.3",
                    "evil\nshow(injected)",
                    "desc with\nuse evil::*;",
                    pkg_dir,
                )
                stub_path = os.path.join(td, "pkg.lsh")
                self.assertTrue(os.path.exists(stub_path))
                text = open(stub_path).read()
                # newline was neutralized: the injected text stays inside
                # the comment and never reaches its own source line
                # every line must be a comment or the use statement
                for line in text.splitlines():
                    s = line.strip()
                    self.assertTrue(
                        s.startswith("//") or s.startswith("use "),
                        f"injected line: {s!r}",
                    )
            finally:
                L.LEASH_LIBS_DIR = old_libs

    def test_validate_git_url_non_string_fails_cleanly(self):
        # M8: a malformed registry entry ("repo": 123) crashed with an
        # AttributeError traceback.
        for bad in (123, ["https://x"], None, {"a": 1}):
            with self.assertRaises(SystemExit):
                L.validate_git_url(bad)

    def test_search_non_string_description(self):
        # M8: `description: 123` in the index crashed search with
        # AttributeError when the query did not match the key.
        import unittest.mock as _mock
        fake_index = {
            "libraries": {
                "good": {"description": "a good lib"},
                "badint": {"description": 123},
                "badlist": {"description": ["x"]},
            }
        }
        with _mock.patch.object(L, "fetch_index", return_value=fake_index):
            with _mock.patch("builtins.print"):
                L.cmd_search(["zzz-no-match"])

    def test_write_pkg_config_preserves_custom_keys_and_escapes(self):
        # L15: unknown keys used to be silently deleted on rewrite, and
        # values containing quotes/newlines corrupted the round-trip.
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "leash-pkg.lshc")
            cfg = {
                "name": "lib",
                "version": "1.0.0",
                "custom_key": "keep me",
                "description": 'has "quotes" and\na newline',
            }
            L.write_pkg_config(path, cfg)
            text = open(path).read()
            self.assertIn('custom_key: "keep me"', text)
            # the value must stay on ONE line (real newlines are collapsed)
            desc_lines = [ln for ln in text.splitlines() if ln.startswith("description")]
            self.assertEqual(len(desc_lines), 1)
            self.assertIn('has \\"quotes\\" and', desc_lines[0])
            # round-trip must parse back without corruption
            back = L.read_pkg_config(path)
            self.assertEqual(back.get("custom_key"), "keep me")
            self.assertEqual(back.get("name"), "lib")
            self.assertIn("quotes", back.get("description", ""))


class TestIntegrityHardening(unittest.TestCase):
    """Regression tests for the install/publish integrity fixes."""

    def test_git_url_with_password_rejected(self):
        for bad in ("https://user:TOKEN@github.com/a/b.git",
                    "https://oauth2:ghp_xxx@github.com/a/b.git",
                    "ssh://user:pass@github.com/a/b.git"):
            with self.assertRaises(SystemExit, msg=bad):
                L.validate_git_url(bad)

    def test_git_url_plain_ssh_user_allowed(self):
        self.assertEqual(
            L.validate_git_url("ssh://git@github.com/a/b.git"),
            "ssh://git@github.com/a/b.git")
        self.assertEqual(
            L.validate_git_url("git@github.com:a/b.git"),
            "git@github.com:a/b.git")

    def test_git_url_token_as_username_rejected(self):
        with self.assertRaises(SystemExit):
            L.validate_git_url("https://ghp_xxx@github.com/a/b.git")

    def test_display_safe_strips_escapes(self):
        self.assertEqual(L.display_safe("a\x1b[2Jb"), "ab")
        self.assertEqual(L.display_safe("x\x1b]8;;http://e\x07y"), "xy")
        self.assertEqual(L.display_safe("a\x00b\x7fc"), "a b c")
        self.assertEqual(L.display_safe("plain"), "plain")
        self.assertEqual(L.display_safe(None), "")
        self.assertEqual(L.display_safe(123), "123")

    def test_index_shape(self):
        self.assertIsNone(L.validate_index_shape({"libraries": {}}))
        self.assertIsNone(L.validate_index_shape({"libraries": {"a": {}}}))
        self.assertIsNone(L.validate_index_shape({}))
        self.assertIsNotNone(L.validate_index_shape([]))
        self.assertIsNotNone(L.validate_index_shape({"libraries": []}))
        self.assertIsNotNone(L.validate_index_shape({"libraries": None}))

    def test_merge_registry_entry_preserves_history(self):
        fresh = {"version": "2.0.0", "versions": {
            "1.0.0": {"repo": "r", "tag": "v1.0.0"},
            "2.0.0": {"repo": "r", "tag": "v2.0.0"},
        }}
        entry, kept = L.merge_registry_entry(
            fresh, "foo", "1.5.0", "r", "d", "a", "p")
        # concurrent 2.0.0 survives; new version recorded on top
        self.assertEqual(entry["version"], "1.5.0")
        self.assertIn("2.0.0", entry["versions"])
        self.assertIn("1.0.0", entry["versions"])
        self.assertIn("1.5.0", entry["versions"])
        self.assertEqual(sorted(kept), ["1.0.0", "2.0.0"])

    def test_merge_registry_entry_new_package(self):
        entry, kept = L.merge_registry_entry(
            None, "new", "0.1.0", "r", "d", "a", "p")
        self.assertEqual(entry["version"], "0.1.0")
        self.assertEqual(list(entry["versions"].keys()), ["0.1.0"])
        self.assertEqual(kept, [])

    def test_check_version_match(self):
        self.assertIsNone(L.check_version_match("1.2.3", "1.2.3"))
        self.assertIsNone(L.check_version_match(None, "9.9.9"))
        self.assertIsNotNone(L.check_version_match("1.2.3", "9.9.9"))
        self.assertIsNotNone(L.check_version_match("1.2.3", "?"))
        self.assertIsNotNone(L.check_version_match("1.2.3", None))

    def test_registry_owner_ok(self):
        # gh identity matching the stored publisher: strong pass
        self.assertTrue(L.registry_owner_ok(
            {"author": "a", "publisher": "gh-alice"}, "mallory", "gh-alice"))
        # legacy entry without publisher: author string suffices
        self.assertTrue(L.registry_owner_ok({"author": "a"}, "a", "anyone"))
        # spoofed author string against a publisher entry: rejected
        self.assertFalse(L.registry_owner_ok(
            {"author": "alice", "publisher": "gh-alice"}, "alice", "mallory"))
        # non-dict / empty entries never pass
        self.assertFalse(L.registry_owner_ok(None, "a", "a"))
        self.assertFalse(L.registry_owner_ok({}, "a", "a"))

    def test_check_registry_update(self):
        idx = {"libraries": {"foo": {"author": "a", "publisher": "p",
                                     "version": "1.0.0",
                                     "versions": {"1.0.0": {}}}}}
        self.assertIsNone(L.check_registry_update(
            idx, "foo", "a", "p", "1.1.0", "1.0.0"))
        # new package: no conflict
        self.assertIsNone(L.check_registry_update(
            {"libraries": {}}, "bar", "a", "p", "0.1.0"))
        # stale overwrite attempt
        err = L.check_registry_update(idx, "foo", "a", "p", "0.9.0", "1.0.0")
        self.assertIsNotNone(err)
        self.assertIn("1.0.0", err)
        # wrong owner
        err = L.check_registry_update(idx, "foo", "mallory", "mallory", "2.0.0")
        self.assertIsNotNone(err)
        # corrupt entry / corrupt version
        self.assertIsNotNone(L.check_registry_update(
            {"libraries": {"foo": "junk"}}, "foo", "a", "p", "2.0.0"))
        badver = {"libraries": {"foo": {"author": "a", "version": "latest"}}}
        self.assertIsNotNone(L.check_registry_update(
            badver, "foo", "a", None, "2.0.0"))

    def test_lockfile_rev_round_trip(self):
        tmp = tempfile.mkdtemp(prefix="leashed_rev_")
        self.addCleanup(shutil.rmtree, tmp, True)
        sha = "a" * 40
        L.write_lockfile(tmp, {"foo": {"version": "1.2.3",
                                       "repo": "https://x/y.git",
                                       "rev": sha}})
        data = L.read_lockfile(tmp)
        self.assertEqual(data["packages"]["foo"]["rev"], sha)
        self.assertEqual(L.lockfile_rev(tmp, "foo"), sha)
        self.assertIsNone(L.lockfile_rev(tmp, "missing"))
        # malformed rev is stored verbatim but never trusted on read
        L.write_lockfile(tmp, {"bar": {"version": "1.0.0",
                                       "repo": "https://x/y.git",
                                       "rev": "not-a-sha"}})
        data = L.read_lockfile(tmp)
        self.assertEqual(data["packages"]["bar"]["rev"], "not-a-sha")
        self.assertIsNone(L.lockfile_rev(tmp, "bar"))

    def test_lock_drops_invalid_version_count(self):
        tmp = tempfile.mkdtemp(prefix="leashed_lockcount_")
        self.addCleanup(shutil.rmtree, tmp, True)
        L.write_lockfile(tmp, {"good": {"version": "1.0.0", "repo": "r"},
                               "bad": {"version": "?", "repo": "r"}})
        data = L.read_lockfile(tmp)
        self.assertIn("good", data["packages"])
        self.assertNotIn("bad", data["packages"])

    def test_parse_global_install_map(self):
        self.assertEqual(
            L._parse_global_install_map("{a: src/a.lsh, b: src/b.lsh}"),
            {"a": "src/a.lsh", "b": "src/b.lsh"})
        self.assertEqual(
            L._parse_global_install_map('{"a": "src/a.lsh",}'),
            {"a": "src/a.lsh"})
        self.assertEqual(L._parse_global_install_map("{}"), {})
        with self.assertRaises(ValueError):
            L._parse_global_install_map("a: src/a.lsh")
        with self.assertRaises(ValueError):
            L._parse_global_install_map("{a}")
        with self.assertRaises(ValueError):
            L._parse_global_install_map("{a: }")

    def test_map_tool_sources(self):
        tmp = tempfile.mkdtemp(prefix="leashed_tools_")
        self.addCleanup(shutil.rmtree, tmp, True)
        os.makedirs(os.path.join(tmp, "src"))
        open(os.path.join(tmp, "src", "t.lsh"), "w").write("x")
        src_root = os.path.realpath(os.path.join(tmp, "src"))
        self.assertEqual(
            L._map_tool_sources(tmp, src_root, {"t": "src/t.lsh"}),
            {"t": "t.lsh"})
        with self.assertRaises(ValueError):
            L._map_tool_sources(tmp, src_root, {"../evil": "src/t.lsh"})
        with self.assertRaises(ValueError):
            L._map_tool_sources(tmp, src_root, {"t": "../outside.lsh"})
        with self.assertRaises(ValueError):
            L._map_tool_sources(tmp, src_root, {"t": "src/missing.lsh"})
        with self.assertRaises(ValueError):
            L._map_tool_sources(tmp, src_root, {"t": "other/t.lsh"})

    def test_install_global_commands_with_fake_build(self):
        tmp = tempfile.mkdtemp(prefix="leashed_gbin_")
        self.addCleanup(shutil.rmtree, tmp, True)
        dest = os.path.join(tmp, "pkg")
        os.makedirs(os.path.join(dest, "src"))
        open(os.path.join(dest, "src", "t.lsh"), "w").write("x")
        built = []

        import leash.cli as cli_mod
        orig_compile = cli_mod.compile_file

        def fake_compile(src, out, *a, **k):
            built.append((src, out))
            with open(out, "w") as fh:
                fh.write("bin")

        cli_mod.compile_file = fake_compile
        orig_libs = L.LEASH_LIBS_DIR
        L.LEASH_LIBS_DIR = os.path.join(tmp, "libs")
        try:
            installed, failed = L._install_global_commands(
                dest, {"t": "src/t.lsh", "../evil": "src/t.lsh",
                       "ghost": "src/missing.lsh"})
        finally:
            cli_mod.compile_file = orig_compile
            L.LEASH_LIBS_DIR = orig_libs
        self.assertEqual(failed, ["../evil", "ghost"])
        self.assertEqual(len(installed), 1)
        self.assertTrue(installed[0].endswith("t"))
        self.assertTrue(os.path.exists(installed[0]))
        bindir = os.path.join(tmp, "bin")
        self.assertEqual(os.path.dirname(installed[0]), bindir)

    def test_remove_global_commands(self):
        tmp = tempfile.mkdtemp(prefix="leashed_grm_")
        self.addCleanup(shutil.rmtree, tmp, True)
        dest = os.path.join(tmp, "pkg")
        os.makedirs(dest)
        with open(os.path.join(dest, "package.lshc"), "w") as fh:
            json.dump({"global_install": {"t": "src/t.lsh"}}, fh)
        orig_libs = L.LEASH_LIBS_DIR
        L.LEASH_LIBS_DIR = os.path.join(tmp, "libs")
        try:
            bindir = os.path.join(tmp, "bin")
            os.makedirs(bindir)
            keeper = os.path.join(bindir, "other")
            target = os.path.join(bindir, "t")
            target_exe = os.path.join(bindir, "t.exe")
            for p in (keeper, target, target_exe):
                open(p, "w").write("x")
            removed = L._remove_global_commands(dest)
            self.assertEqual(sorted(removed), sorted([target, target_exe]))
            self.assertTrue(os.path.exists(keeper))
            self.assertFalse(os.path.exists(target))
        finally:
            L.LEASH_LIBS_DIR = orig_libs

    def test_uninstall_removes_global_commands(self):
        tmp = tempfile.mkdtemp(prefix="leashed_uninst_")
        self.addCleanup(shutil.rmtree, tmp, True)
        orig_libs = L.LEASH_LIBS_DIR
        L.LEASH_LIBS_DIR = os.path.join(tmp, "libs")
        try:
            dest = os.path.join(L.LEASH_LIBS_DIR, "foo")
            os.makedirs(dest)
            with open(os.path.join(dest, "package.lshc"), "w") as fh:
                json.dump({"global_install": {"t": "src/t.lsh"}}, fh)
            with open(os.path.join(L.LEASH_LIBS_DIR, "foo.lsh"), "w") as fh:
                fh.write("// stub\n")
            bindir = os.path.join(tmp, "bin")
            os.makedirs(bindir)
            tool = os.path.join(bindir, "t")
            open(tool, "w").write("x")
            with contextlib.redirect_stdout(io.StringIO()):
                L.cmd_uninstall(["foo"])
            self.assertFalse(os.path.exists(tool))
            self.assertFalse(os.path.exists(dest))
        finally:
            L.LEASH_LIBS_DIR = orig_libs


if __name__ == "__main__":
    unittest.main(verbosity=2)
