#!/usr/bin/env python3
"""Validates index.json changes in a leash-packages registry pull request.

Designed to run inside a GitHub Actions workflow: it compares the base and
head versions of index.json, checks every added/changed library entry against
the self-service publishing rules, and (optionally) smoke-compiles the package.
If everything passes the script exits 0 so the workflow can approve and
auto-merge. Otherwise it writes a Markdown report listing every problem and
exits 1.

Rules enforced per entry:
  - name matches [a-zA-Z_][a-zA-Z0-9_-]*
  - version is valid semver (X.Y.Z with optional -prerelease)
  - required fields present: repo, version, publisher
  - repo URL points at https://github.com/<publisher>/<repo>
  - new entries: publisher must be the PR author (name claim)
  - existing entries: previous owner must be the PR author (no hijacking)
  - version must be strictly greater than the previously published one
  - "versions" map keeps all previously published versions and gains the new one
  - repo is reachable and contains library/package.lshc matching the entry

Usage:
  python validate_index.py --base <sha> --head <ref> --pr-author <login> \
      [--report out.md] [--skip-network] [--compile]
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

NAME_RE = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_-]*$')
SEMVER_RE = re.compile(r'^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z.-]+))?$')
REPO_RE_TMPL = r'^https://github\.com/{pub}/[A-Za-z0-9_.-]+?(\.git)?$'

LEASH_COMPILER_REPO = "https://github.com/foksiny/leash.git"


# ---------------------------------------------------------------- semver ----

def parse_version(v):
    if not isinstance(v, str):
        return None
    m = SEMVER_RE.match(v.strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4)


def _pre_key(pre):
    # Releases sort after prereleases of the same X.Y.Z.
    if pre is None:
        return (1,)
    parts = []
    for p in pre.split('.'):
        if p.isdigit():
            parts.append((0, int(p), ""))
        else:
            parts.append((1, 0, p))
    return (0,) + tuple(parts)


def version_key(v):
    parsed = parse_version(v)
    if parsed is None:
        return None
    maj, mino, pat, pre = parsed
    return (maj, mino, pat, _pre_key(pre))


def is_newer(a, b):
    """True if semver a is strictly greater than semver b."""
    ka, kb = version_key(a), version_key(b)
    if ka is None or kb is None:
        return False
    return ka > kb


# ------------------------------------------------------------- git helpers --

def run_git(cmd, cwd=None):
    try:
        res = subprocess.run(["git"] + cmd, cwd=cwd, capture_output=True,
                             text=True, timeout=120)
        return res.returncode, res.stdout.strip(), res.stderr.strip()
    except subprocess.TimeoutExpired:
        return 1, "", "timeout"
    except FileNotFoundError:
        return 1, "", "git not found"


def normalize_repo_url(url):
    url = (url or "").strip()
    if not url:
        return ""
    if url.endswith("/"):
        url = url[:-1]
    if not url.endswith(".git"):
        url += ".git"
    return url


def check_repo_exists(url):
    rc, _, err = run_git(["ls-remote", "--heads", url])
    return rc == 0, err


def clone_and_check(url, name, version, publisher, problems, warnings):
    """Shallow-clone the package repo and verify its published layout."""
    tmp = tempfile.mkdtemp(prefix="regval_")
    try:
        rc, _, err = run_git(["clone", "--depth", "1", url, tmp])
        if rc != 0:
            problems.append(f"`{name}`: could not clone `{url}` — {err}")
            return
        pkg_path = os.path.join(tmp, "library", "package.lshc")
        if not os.path.exists(pkg_path):
            problems.append(
                f"`{name}`: repository has no `library/package.lshc`. "
                "Publish with `leashed publish` to generate the correct layout.")
            return
        # A symlinked package.lshc could point anywhere (info disclosure);
        # legit `leashed publish` output is always a regular file.
        if os.path.islink(pkg_path) or os.path.islink(os.path.dirname(pkg_path)):
            problems.append(f"`{name}`: `library/package.lshc` must be a regular file, "
                            "not a symlink.")
            return
        try:
            with open(pkg_path, "r", encoding="utf-8") as f:
                pkg = json.load(f)
        except json.JSONDecodeError as e:
            problems.append(f"`{name}`: `library/package.lshc` is not valid JSON ({e})")
            return
        if pkg.get("name") != name:
            problems.append(
                f"`{name}`: package.lshc name mismatch "
                f"(index says '{name}', package says '{pkg.get('name')}')")
        if pkg.get("version") != version:
            problems.append(
                f"`{name}`: package.lshc version mismatch "
                f"(index says {version}, package says {pkg.get('version')})")
        pkg_publisher = pkg.get("publisher") or pkg.get("author")
        if publisher and pkg_publisher != publisher:
            problems.append(
                f"`{name}`: package.lshc publisher mismatch "
                f"(index says '{publisher}', package says '{pkg_publisher}')")
        main_file = pkg.get("main", "")
        if main_file:
            lib_main = os.path.join(tmp, "library", os.path.basename(main_file))
            if not os.path.exists(lib_main):
                problems.append(
                    f"`{name}`: missing compiled entry `library/"
                    f"{os.path.basename(main_file)}` in the repository")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------- core validation --

EMPTY_INDEX = {"libraries": {}}


def load_index_from_git(ref, path="index.json"):
    """Load index.json at `ref`. Returns (index, error); exactly one is None.

    A missing file yields (None, error); an unparseable file yields
    (None, json-error). Callers must treat head-side errors as blocking —
    otherwise a PR that breaks or deletes index.json would merge silently.
    """
    rc, out, err = run_git(["show", f"{ref}:{path}"])
    if rc != 0:
        return None, (err or f"could not read {path} at {ref}")
    try:
        return json.loads(out), None
    except json.JSONDecodeError as e:
        return None, f"not valid JSON ({e})"


def index_exists_at(ref, path="index.json"):
    rc, _, _ = run_git(["cat-file", "-e", f"{ref}:{path}"])
    return rc == 0


def compute_changes(base_index, head_index):
    base_libs = base_index.get("libraries", {}) if base_index else {}
    head_libs = head_index.get("libraries", {}) if head_index else {}
    added = sorted(set(head_libs) - set(base_libs))
    changed = sorted(n for n in set(base_libs) & set(head_libs)
                     if base_libs[n] != head_libs[n])
    deleted = sorted(set(base_libs) - set(head_libs))
    return base_libs, head_libs, added, changed, deleted


def prev_owner(prev):
    if not prev:
        return ""
    return prev.get("publisher") or prev.get("author", "")


def validate_entry(name, entry, prev, pr_author, do_network=True, warnings=None):
    """Return a list of problem strings for one library entry."""
    problems = []
    warn = warnings if warnings is not None else []

    if not NAME_RE.match(name):
        problems.append(f"`{name}`: invalid name (must match {NAME_RE.pattern})")
        return problems

    version = entry.get("version", "")
    publisher = entry.get("publisher", "")
    if parse_version(version) is None:
        problems.append(f"`{name}`: invalid version '{version}' (need semver X.Y.Z)")
    versions_map = entry.get("versions", {})
    if not isinstance(versions_map, dict):
        problems.append(f"`{name}`: 'versions' must be a map of version -> metadata")
        versions_map = {}
    for v in versions_map:
        if parse_version(v) is None:
            problems.append(f"`{name}`: invalid version '{v}' in versions map")
    # Metadata inside the versions map is never trusted blindly: `leashed
    # install name@version` clones versions[v]["repo"] verbatim, so a poisoned
    # entry there (e.g. an ext:: URL or someone else's repo) is remote code
    # execution on every client that pins that version.
    for v, meta in versions_map.items():
        if not isinstance(meta, dict):
            problems.append(f"`{name}`: versions['{v}'] must be an object "
                            "(repo/tag/published_at)")
            continue
        if publisher and meta.get("repo") is not None:
            vrepo = meta.get("repo")
            if not isinstance(vrepo, str) or not re.match(
                    REPO_RE_TMPL.format(pub=re.escape(publisher)), vrepo):
                problems.append(
                    f"`{name}`: versions['{v}'] repo URL must be "
                    f"https://github.com/{publisher}/<repo>")
        vtag = meta.get("tag")
        if vtag is not None and vtag != f"v{v}":
            problems.append(
                f"`{name}`: versions['{v}'] tag must be 'v{v}' (got '{vtag}')")

    if not publisher:
        problems.append(f"`{name}`: missing 'publisher' field (your GitHub login)")
    if not entry.get("repo"):
        problems.append(f"`{name}`: missing 'repo' field")
    if not entry.get("description"):
        warn.append(f"`{name}`: no description set (recommended)")

    if publisher:
        if publisher != pr_author:
            problems.append(
                f"`{name}`: publisher '{publisher}' does not match PR author "
                f"'{pr_author}'. You can only publish under your own login.")
        if entry.get("repo"):
            if not re.match(REPO_RE_TMPL.format(pub=re.escape(publisher)), entry["repo"]):
                problems.append(
                    f"`{name}`: repo URL must be https://github.com/{publisher}/<repo>")
    elif entry.get("repo"):
        problems.append(f"`{name}`: cannot verify repo ownership without 'publisher'")

    # Ownership / hijack protection
    if prev:
        owner = prev_owner(prev)
        if owner != pr_author:
            problems.append(
                f"`{name}`: already registered by '{owner}'. Only the original "
                "owner can update an entry.")
        old_version = prev.get("version", "")
        if parse_version(old_version) is not None and parse_version(version) is not None \
                and not is_newer(version, old_version):
            problems.append(
                f"`{name}`: version {version} must be strictly greater than "
                f"the published {old_version}")
        old_versions = prev.get("versions", {})
        new_versions = entry.get("versions", {})
        dropped = sorted(set(old_versions) - set(new_versions))
        if dropped:
            problems.append(
                f"`{name}`: previously published versions were removed from the "
                f"versions map: {', '.join(dropped)}")
        if parse_version(version) is not None and version not in entry.get("versions", {}):
            problems.append(
                f"`{name}`: versions map does not contain the current version {version}")
    else:
        if not entry.get("versions"):
            warn.append(f"`{name}`: no versions map (older client format)")

    if do_network and entry.get("repo") and publisher == pr_author:
        ok, err = check_repo_exists(normalize_repo_url(entry["repo"]))
        if not ok:
            problems.append(f"`{name}`: repo unreachable — {err}")
        else:
            clone_and_check(normalize_repo_url(entry["repo"]), name, version,
                            publisher, problems, warn)
    return problems


def smoke_compile(entry, warnings):
    """Best-effort compile of the published library. Never blocks merging."""
    name = entry.get("name", "?")
    repo = normalize_repo_url(entry.get("repo", ""))
    main_base = None
    tmp = tempfile.mkdtemp(prefix="regcompile_")
    try:
        rc, _, err = run_git(["clone", "--depth", "1", repo, tmp])
        if rc != 0:
            warnings.append(f"`{name}`: compile smoke test skipped (clone failed)")
            return
        pkg_path = os.path.join(tmp, "library", "package.lshc")
        if os.path.exists(pkg_path):
            try:
                with open(pkg_path, "r", encoding="utf-8") as f:
                    main_base = os.path.basename(json.load(f).get("main", ""))
            except Exception:
                pass
        target = os.path.join(tmp, "library", main_base) if main_base else None
        if not target or not os.path.exists(target):
            warnings.append(f"`{name}`: compile smoke test skipped (no entry file)")
            return
        pip = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet",
             f"git+{LEASH_COMPILER_REPO}"],
            capture_output=True, text=True, timeout=600)
        if pip.returncode != 0:
            warnings.append(f"`{name}`: compile smoke test skipped (compiler install failed)")
            return
        out_tmp = tempfile.mkdtemp(prefix="regbuild_")
        comp = subprocess.run(
            [sys.executable, "-m", "leash.cli", "compile", target,
             "to-static", os.path.join(out_tmp, name)],
            capture_output=True, text=True, timeout=600)
        if comp.returncode != 0:
            warnings.append(
                f"`{name}`: compile smoke test failed (non-blocking):\n"
                "```\n" + (comp.stderr.strip() or comp.stdout.strip())[:1500] + "\n```")
        else:
            warnings.append(f"`{name}`: compile smoke test passed")
    except Exception as e:  # never block on smoke test infrastructure issues
        warnings.append(f"`{name}`: compile smoke test error (non-blocking): {e}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------------------ report --

def write_report(path, title, sections):
    lines = [f"## {title}", ""]
    any_bad = False
    for heading, items in sections:
        bad = heading.startswith("Problems")
        if not items:
            continue
        lines.append(f"### {heading}")
        lines.append("")
        for item in items:
            prefix = "- ❌" if bad else "- ℹ️"
            lines.append(f"{prefix} {item}")
            if bad:
                any_bad = True
        lines.append("")
    if not any_bad and all(not items for h, items in sections if h.startswith("Problems")):
        lines.insert(2, "")
        lines.insert(2, "All checks passed. This PR will merge automatically.")
        lines.append("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return any_bad


# -------------------------------------------------------------------- main --

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default="HEAD~1", help="base ref/sha holding the old index.json")
    ap.add_argument("--head", default="HEAD", help="ref containing the new index.json")
    ap.add_argument("--pr-author", required=True, help="GitHub login of the PR author")
    ap.add_argument("--report", default="validation_report.md")
    ap.add_argument("--skip-network", action="store_true")
    ap.add_argument("--compile", action="store_true",
                    help="best-effort compile smoke test (never blocks)")
    args = ap.parse_args(argv)

    base_index, base_err = load_index_from_git(args.base)
    head_index, head_err = load_index_from_git(args.head)

    problems = []
    # Head-side load failures are always blocking: a PR that corrupts or
    # deletes index.json must never merge. The only exception is a registry
    # bootstrap, where the file does not exist at either ref.
    if head_err:
        head_exists = index_exists_at(args.head)
        base_exists = index_exists_at(args.base)
        if head_exists:
            problems.append(f"`index.json`: head version is {head_err}")
        elif base_exists:
            problems.append("`index.json`: this PR deletes index.json, which "
                            "is not supported (open an issue instead)")
        elif not base_exists:
            # Neither ref has the file — treat as an empty registry bootstrap.
            head_index = dict(EMPTY_INDEX)
    if base_err and not head_err:
        base_bootstrap_note = (f"`index.json`: base version is unreadable ({base_err}); "
                               "treating it as empty and validating every entry from scratch")
    else:
        base_bootstrap_note = None
    if base_index is None:
        base_index = dict(EMPTY_INDEX)
    if head_index is None:
        head_index = dict(EMPTY_INDEX)

    if not problems:
        if not isinstance(head_index, dict) or not isinstance(head_index.get("libraries", {}), dict):
            problems.append("`index.json`: top-level shape must be {\"libraries\": {...}}")

    warnings = []
    if base_bootstrap_note:
        warnings.append(base_bootstrap_note)
    details = []
    if not problems:
        base_libs, head_libs, added, changed, deleted = compute_changes(base_index, head_index)

        for name in deleted:
            problems.append(f"`{name}`: deleting registry entries is not supported; "
                            "open an issue instead")

        for name in added:
            entry = dict(head_libs[name])
            entry["name"] = name
            probs = validate_entry(name, entry, None, args.pr_author,
                                   do_network=not args.skip_network, warnings=warnings)
            problems.extend(probs)
            details.append((name, probs))
            if args.compile and not probs:
                smoke_compile(entry, warnings)

        for name in changed:
            entry = dict(head_libs[name])
            entry["name"] = name
            probs = validate_entry(name, entry, base_libs[name], args.pr_author,
                                   do_network=not args.skip_network, warnings=warnings)
            problems.extend(probs)
            details.append((name, probs))
            if args.compile and not probs:
                smoke_compile(entry, warnings)

        if not added and not changed and not deleted:
            warnings.append("No library entries appear to have changed in this PR.")

    summary = [
        ("Problems", problems),
        ("Notes", warnings),
        ("Reviewed entries", [f"`{n}` — " + ("failed validation" if p else "ok")
                              for n, p in details]),
    ]
    blocked = write_report(args.report, "Registry validation report", summary)
    print(open(args.report, encoding="utf-8").read())
    if blocked:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
