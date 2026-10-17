#!/usr/bin/env python3
"""Package manager (leash pkg) tests: the self-hosted port of leashed.py.

Two layers:
  1. Differential pure-logic tests: the same vectors run through Python
     leashed and the Leash port (via --other-imports compiler/); outputs
     must match exactly.
  2. CLI tests: `bin/leashc pkg ...` with redirected HOME. Network is
     never touched: the registry is served from file:// index fixtures
     (curl reads them offline) and git flows use local repositories
     where the design allows (remote clones stay untested by design —
     file:// transports are rejected, like the reference).

HOME is redirected per-test so the real ~/.leash is never touched.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN_LEASHC = os.path.join(ROOT, "bin", "leashc")
COMPILER_DIR = os.path.join(ROOT, "compiler")

sys.path.insert(0, ROOT)
import leash.leashed as L


def _run(cmd, cwd, env, stdin=None, timeout=300):
    return subprocess.run(cmd, capture_output=True, text=True, input=stdin,
                          cwd=cwd, env=env, timeout=timeout)


def _leash_run_source(src, extra_env=None):
    """Compile+run a Leash snippet importing the leashed module; return stdout."""
    with tempfile.TemporaryDirectory(prefix="leash_pkg_u_") as td:
        path = os.path.join(td, "t.lsh")
        with open(path, "w") as f:
            f.write(src)
        out = os.path.join(td, "t")
        env = dict(os.environ)
        if extra_env:
            env.update(extra_env)
        c = _run([BIN_LEASHC, "compile", path, "--other-imports", COMPILER_DIR,
                  "to", out], ROOT, env)
        if c.returncode != 0:
            raise AssertionError("leash compile failed:\n" + c.stdout + c.stderr)
        r = _run([out], td, env)
        if r.returncode != 0:
            raise AssertionError("leash run failed:\n" + r.stdout + r.stderr)
        return r.stdout


class PkgDifferentialTest(unittest.TestCase):
    """Same vectors, both implementations, byte-identical verdicts."""

    @classmethod
    def setUpClass(cls):
        if not os.path.exists(BIN_LEASHC):
            raise unittest.SkipTest("bin/leashc missing (run `leash self-host`)")

    @staticmethod
    def _leash_str(s):
        # Leash string literal escaping for test inputs (ASCII)
        return '"' + (s.replace("\\", "\\\\").replace('"', '\\"')
                      .replace("\n", "\\n").replace("\t", "\\t")
                      .replace("\r", "\\r")) + '"'

    def test_semver_newer(self):
        pairs = [("1.2.3", "1.2.2"), ("1.0.0", "1.0.0-rc.1"),
                 ("1.0.0-rc.2", "1.0.0-rc.10"), ("2.0.0", "10.0.0"),
                 ("1.0.0-alpha", "1.0.0-alpha.1"), ("1.0.0-1", "1.0.0-01"),
                 ("1.0.0-x.7.z.92", "1.0.0-x.7.z.91"), ("bogus", "1.0.0"),
                 ("1.0.0", "bogus"), ("1.0.0-rc.1+x", "1.0.0"),
                 ("0.0.0", "0.0.0")]
        lines = ["use leashed::*;", "fnc main() : void {"]
        for a, b in pairs:
            lines.append(
                f"    show(pkg_is_newer_version({self._leash_str(a)}, {self._leash_str(b)}));")
        lines.append("}")
        out = _leash_run_source("\n".join(lines) + "\n")
        got = out.splitlines()
        self.assertEqual(len(got), len(pairs))
        for (a, b), g in zip(pairs, got):
            self.assertEqual(g, str(L.is_newer_version(a, b)).lower(),
                             f"is_newer({a!r}, {b!r})")

    def test_sorted_versions(self):
        vecs = [["3.0.0", "1.0.0", "1.0.0-rc.1", "2.0.0", "bogus"],
                ["1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-alpha.beta",
                 "1.0.0-beta", "1.0.0"],
                ["0.1.0", "0.0.9", "0.0.10"],
                ["zzz", "1.0.0", "aaa", "0.9.9"],
                ["1.2.3-rc..1", "1.2.3-.rc", "1.2.3"]]
        for v in vecs:
            lines = ["use leashed::*;", "fnc main() : void {",
                     "    sv: vec<string>;"]
            for s in v:
                lines.append(f"    sv.pushb({self._leash_str(s)});")
            lines.append('    show(pkg_join(pkg_sorted_versions(sv), ","));')
            lines.append("}")
            out = _leash_run_source("\n".join(lines) + "\n")
            self.assertEqual(out.strip(), ",".join(L.sorted_versions(v)))

    def test_validate_name(self):
        names = ["foo", "_x", "a-b_c9", "", "9x", "-x", "a b", "a.b",
                 "x" * 64, "with/slash", "under_score-ok.9"]
        lines = ["use leashed::*;", "fnc main() : void {"]
        for s in names:
            lines.append(f"    show(pkg_validate_name_r({self._leash_str(s)}));")
        lines.append("}")
        out = _leash_run_source("\n".join(lines) + "\n")
        got = out.splitlines()
        self.assertEqual(len(got), len(names))
        for s, g in zip(names, got):
            try:
                L.validate_name(s)
                exp = ""
            except SystemExit:
                exp = None  # message check below via run
            if exp == "":
                self.assertEqual(g, "")
            else:
                # error text must start the same way
                self.assertTrue(g.startswith("error: Invalid name"),
                                f"{s!r}: {g!r}")

    def test_git_url_verdicts(self):
        cases = [
            ("https://github.com/u/r.git", False, "ok"),
            ("http://example.com/r.git", False, "err"),
            ("http://example.com/r.git", True, "warn"),
            ("git@github.com:u/r.git", False, "ok"),
            ("ssh://git@host/x.git", False, "ok"),
            ("https://user:tok@host/r.git", False, "err"),
            ("file:///etc/passwd", False, "err"),
            ("ext::sh -c", False, "err"),
            ("-u", False, "err"),
            ("ftp://x/y", False, "err"),
            ("", False, "err"),
            ("nota url!", False, "err"),
            ("HTTPS://GITHUB.COM/U/R.GIT", False, "ok"),
            ("user/repo", False, "look"),
            ("https://github.com/u/r", False, "look"),
            ("plainname", False, "look"),
            ("a::b", False, "look"),
        ]
        lines = ["use leashed::*;", "fnc main() : void {"]
        for i, (u, ins, _) in enumerate(cases):
            # Markers: verdict messages can span multiple lines (fix hints),
            # so the output is framed instead of relying on line counts.
            lines.append(f'    show("B{i}");')
            lines.append(
                f"    show(pkg_validate_git_url_r({self._leash_str(u)}, "
                f"{'true' if ins else 'false'}, \"repository\"));")
            lines.append(f'    show("E{i}");')
            lines.append(
                f"    show(pkg_looks_like_git_target({self._leash_str(u)}));")
        lines.append("}")
        out = _leash_run_source("\n".join(lines) + "\n")
        got = {}
        section = out.split("\n")
        i = 0
        while i < len(section):
            if re.fullmatch(r"B\d+", section[i]):
                idx = int(section[i][1:])
                i += 1
                start = i
                while not re.fullmatch(r"E\d+", section[i]):
                    i += 1
                verdict = "\n".join(section[start:i])
                got[idx] = (verdict, section[i + 1])
                i += 2
            else:
                i += 1
        self.assertEqual(len(got), len(cases))
        for i, (u, ins, kind) in enumerate(cases):
            verdict, look = got[i]
            if kind == "ok":
                self.assertEqual(verdict, "OK", u)
            elif kind == "warn":
                self.assertTrue(verdict.startswith("WARN"), u)
            elif kind == "err":
                self.assertTrue(verdict.startswith("error:"), u)
            # "look": only the git-target heuristic is meaningful here
            self.assertEqual(look,
                             str(L._looks_like_git_target(u)).lower(), u)

    def test_display_safe(self):
        esc, bel = chr(27), chr(7)
        cases = ["", "plain", esc + "[31mRED" + esc + "[0m",
                 esc + "]8;;http://x" + bel + "LINK" + esc + "]8;;" + bel,
                 esc + "(0Q", "a" + chr(1) + "b" + chr(127) + "c",
                 esc + "[1;2Hmove", "trailing" + esc]
        lines = ["use leashed::*;", "fnc main() : void {"]
        for s in cases:
            # build control chars from codes (Leash strings lack \x escapes)
            parts = []
            buf = ""
            for ch in s:
                if ord(ch) < 32 or ord(ch) == 27 or ord(ch) == 127:
                    if buf:
                        parts.append(self._leash_str(buf))
                        buf = ""
                    parts.append(f"(char)({ord(ch)})")
                else:
                    buf += ch
            if buf:
                parts.append(self._leash_str(buf))
            expr = parts[0] if parts else '""'
            for p in parts[1:]:
                expr = f"({expr} + {p})"
            lines.append(f"    show(pkg_display_safe({expr}));")
        lines.append("}")
        out = _leash_run_source("\n".join(lines) + "\n")
        got = out.splitlines()
        self.assertEqual(len(got), len(cases))
        for s, g in zip(cases, got):
            self.assertEqual(g, L.display_safe(s), repr(s))

    def test_pkg_config_round_trip(self):
        cfg = ('# comment\nname: "mypkg"\nversion: "1.2.3" # trailing\n'
               'custom: plain\nempty:\nbadline\nquoted: "a # b"\n'
               'tabs: "a\tb  c"\n')
        lines = ["use leashed::*;", "fnc main() : void {",
                 f"    es: vec<PkgEntry> = pkg_read_config({self._leash_str(cfg)});",
                 '    show(pkg_config_get(es, "name"));',
                 '    show(pkg_config_get(es, "version"));',
                 '    show(pkg_config_get(es, "custom"));',
                 '    show(pkg_config_get(es, "quoted"));',
                 '    show(pkg_config_get(es, "missing"));',
                 "    show(pkg_write_config(es));",
                 "}"]
        out = _leash_run_source("\n".join(lines) + "\n")
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "leash-pkg.lshc")
            with open(path, "w") as f:
                f.write(cfg)
            d = L.read_pkg_config(path)
            L.write_pkg_config(path, d)
            with open(path) as f:
                expected = f.read()
        got = out.splitlines()
        self.assertEqual(got[0:5],
                         [d.get("name", ""), d.get("version", ""),
                          d.get("custom", ""), d.get("quoted", ""), ""])
        # the tab+double-space value collapses like Python's _esc; the whole
        # written file must be byte-identical
        self.assertEqual(got[5:], expected.split("\n"))

    def test_lockfile_round_trip(self):
        lock = {"version": 1, "packages": {
            "zeta": {"version": "2.0.0", "repo": "https://x/z.git",
                     "tag": "v2.0.0", "rev": "a" * 40},
            "alpha": {"version": "1.0.0-rc.1", "repo": "https://x/a.git",
                      "tag": "v1.0.0-rc.1"}}}
        text = json.dumps(lock)
        lines = ["use leashed::*;", "fnc main() : void {",
                 f"    lock: PkgLock = pkg_parse_lockfile({self._leash_str(text)});",
                 "    show(lock.ok);",
                 "    show(pkg_lock_pin(lock, \"alpha\"));",
                 "    show(pkg_lock_rev(lock, \"zeta\"));",
                 "    show(pkg_lock_rev(lock, \"alpha\"));",
                 "    show(pkg_write_lockfile(lock.names, lock.entries));",
                 "}"]
        out = _leash_run_source("\n".join(lines) + "\n")
        got = out.splitlines()
        self.assertEqual(got[0], "true")
        self.assertEqual(got[1], "1.0.0-rc.1")
        self.assertEqual(got[2], "a" * 40)
        self.assertEqual(got[3], "")
        # byte-identical to the Python writer
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            L.write_lockfile(td, lock["packages"])
            with open(os.path.join(td, "leash.lock")) as f:
                expected = f.read()
        self.assertEqual(got[4:], expected.split("\n"))

    def test_registry_gates(self):
        # check_version_match / registry_owner_ok / check_registry_update
        lines = ["use leashed::*;", "fnc main() : void {",
                 '    show(pkg_check_version_match("1.0.0", "1.0.0", true));',
                 '    show(pkg_check_version_match("1.0.0", "2.0.0", true));',
                 '    show(pkg_check_version_match("1.0.0", "", false));',
                 '    show(pkg_check_version_match("", "9.9.9", true));',
                 "}"]
        out = _leash_run_source("\n".join(lines) + "\n")
        self.assertEqual(
            out.splitlines(),
            [L.check_version_match("1.0.0", "1.0.0") or "",
             L.check_version_match("1.0.0", "2.0.0"),
             L.check_version_match("1.0.0", None),
             L.check_version_match(None, "9.9.9") or ""])


class PkgCliTest(unittest.TestCase):
    """bin/leashc pkg ... with redirected HOME (offline)."""

    @classmethod
    def setUpClass(cls):
        if not os.path.exists(BIN_LEASHC):
            raise unittest.SkipTest("bin/leashc missing (run `leash self-host`)")

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="leash_pkg_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)
        self.work = os.path.join(self.tmp, "work")
        os.makedirs(self.work)
        self.env = dict(os.environ, HOME=self.home)

    def _pkg(self, *args, cwd=None):
        return _run([BIN_LEASHC, "pkg"] + list(args),
                    cwd or self.work, self.env)

    def test_pkg_help_and_version(self):
        r = self._pkg("--help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("leash pkg", r.stdout)
        r = self._pkg("--version")
        self.assertEqual(r.returncode, 0)
        self.assertIn(f"leash pkg v{L.LEASHED_VERSION}", r.stdout)
        r = self._pkg("bogus-cmd")
        self.assertNotEqual(r.returncode, 0)

    def test_init_and_list(self):
        proj = os.path.join(self.work, "mylib")
        r = self._pkg("init", proj)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(os.path.isfile(os.path.join(proj, "src", "main.lsh")))
        cfg = os.path.join(proj, "leash-pkg.lshc")
        self.assertTrue(os.path.isfile(cfg))
        with open(cfg) as f:
            text = f.read()
        self.assertIn('name: "mylib"', text)
        # list with nothing installed
        r = self._pkg("list")
        self.assertEqual(r.returncode, 0)
        self.assertIn("No libraries installed", r.stdout)
        # init refuses a non-empty dir and a bad name
        r = self._pkg("init", proj)
        self.assertNotEqual(r.returncode, 0)
        r = self._pkg("init", os.path.join(self.work, "9bad"))
        self.assertNotEqual(r.returncode, 0)

    def test_uninstall_missing_and_project_unpin(self):
        r = self._pkg("uninstall", "nope-not-here")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not installed", r.stdout)
        # unpin path: project lock loses the entry
        proj = os.path.join(self.work, "p")
        os.makedirs(proj)
        with open(os.path.join(proj, "leash-pkg.lshc"), "w") as f:
            f.write('name: "p"\nversion: "0.1.0"\nauthor: "t"\n')
        with open(os.path.join(proj, "leash.lock"), "w") as f:
            json.dump({"version": 1, "packages": {
                "alpha": {"version": "1.0.0",
                          "repo": "https://x/alpha.git",
                          "tag": "v1.0.0"}}}, f)
        # library not installed -> error before touching the lock
        r = self._pkg("uninstall", "alpha", cwd=proj)
        self.assertNotEqual(r.returncode, 0)
        with open(os.path.join(proj, "leash.lock")) as f:
            data = json.load(f)
        self.assertIn("alpha", data["packages"])

    def test_add_requires_project(self):
        r = self._pkg("add", "foo")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("leash-pkg.lshc", r.stdout)

    def test_search_and_info_offline_registry(self):
        # file:// index: curl reads it with no network
        reg = os.path.join(self.tmp, "reg")
        os.makedirs(reg)
        index = {"libraries": {
            "alpha": {"repo": "https://x/alpha.git", "description": "A lib",
                      "author": "ann", "publisher": "ann",
                      "version": "2.0.0",
                      "versions": {"1.0.0": {"repo": "https://x/alpha.git",
                                             "tag": "v1.0.0"},
                                   "2.0.0": {"repo": "https://x/alpha.git",
                                             "tag": "v2.0.0"}}},
            "beta": {"repo": "https://x/beta.git", "description": "B lib",
                     "author": "bob", "version": "0.3.0", "versions": {}},
        }}
        with open(os.path.join(reg, "index.json"), "w") as f:
            json.dump(index, f)
        env = dict(self.env,
                   LEASHED_REGISTRY_URL="file://" + os.path.join(reg, "index.json"))
        r = _run([BIN_LEASHC, "pkg", "search", "alp"], self.work, env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("alpha", r.stdout)
        self.assertIn("2.0.0", r.stdout)
        r = _run([BIN_LEASHC, "pkg", "search", "zzz-no-match"], self.work, env)
        self.assertEqual(r.returncode, 0)
        self.assertIn("No libraries found", r.stdout)
        r = _run([BIN_LEASHC, "pkg", "info", "alpha"], self.work, env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("2.0.0", r.stdout)
        self.assertIn("1.0.0", r.stdout)
        r = _run([BIN_LEASHC, "pkg", "info", "missing"], self.work, env)
        self.assertNotEqual(r.returncode, 0)

    def test_lock_needs_config_and_deps(self):
        r = self._pkg("lock")
        self.assertNotEqual(r.returncode, 0)
        proj = os.path.join(self.work, "lp")
        os.makedirs(proj)
        with open(os.path.join(proj, "leash-pkg.lshc"), "w") as f:
            f.write('name: "lp"\nversion: "0.1.0"\nauthor: "t"\n')
        r = self._pkg("lock", cwd=proj)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("No dependencies", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
