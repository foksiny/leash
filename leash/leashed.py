#!/usr/bin/env python3
import sys
import os
import json
import tempfile
import shutil
import subprocess
import re
import time
import stat
import urllib.request
import urllib.error
import urllib.parse

LEASHED_VERSION = "0.5.0"
LOCKFILE = "leash.lock"
LOCKFILE_VERSION = 1

def _env(name, default):
    v = os.environ.get(name)
    return v if v else default

DEFAULT_REGISTRY_REPO = "foksiny/leash-packages"
# Anyone can host their own registry by overriding these.
REGISTRY_REPO = _env("LEASHED_REGISTRY_REPO", DEFAULT_REGISTRY_REPO)
REGISTRY_OWNER = REGISTRY_REPO.split("/")[0]
REGISTRY_URL = _env("LEASHED_REGISTRY_URL",
                    f"https://raw.githubusercontent.com/{REGISTRY_REPO}/main/index.json")
REGISTRY_GIT = _env("LEASHED_REGISTRY_GIT", f"https://github.com/{REGISTRY_REPO}.git")
LEASH_LIBS_DIR = os.path.expanduser("~/.leash/libs")
LEASHED_CONFIG = "leash-pkg.lshc"
PACKAGE_CONFIG = "package.lshc"
PUBLISHER_FILE = "publisher"
LIBRARY_DIR = "library"
VERBOSE = False
# Hard cap on downloaded index size: a hostile/custom registry must not be
# able to exhaust memory by serving a multi-gigabyte JSON document.
MAX_INDEX_BYTES = 32 * 1024 * 1024


def _leashc_compiler():
    """Locate the self-hosted leash compiler (Python-free)."""
    env = os.environ.get("LEASHC")
    if env:
        return env
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cand = os.path.join(repo_root, "bin", "leashc")
    if os.path.exists(cand):
        return cand
    for name in ("leashc", "leash"):
        p = shutil.which(name)
        if p:
            return p
    return None


def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


def validate_name(name):
    if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_-]*$', name):
        eprint(f"error: Invalid name '{name}'. Must start with a letter or underscore and contain only letters, digits, hyphens, and underscores.")
        sys.exit(1)
    return name


# Git transports that are safe to clone from. Anything else is rejected:
#   - `ext::` / `fd::` transports execute arbitrary local commands (RCE)
#   - `file://` / local paths smuggle unreviewed local content into installs
#   - `http://` is trivially tampered in transit (registry URLs must be https)
#   - URLs starting with `-` would be parsed by git as command-line options
#     (e.g. `--upload-pack=sh -c ...`), which is again remote code execution
_SCP_STYLE_RE = re.compile(r'^[A-Za-z0-9_.-]+@[A-Za-z0-9_.-]+:[A-Za-z0-9_./~-]+$')
# git:// is deliberately NOT in the safe list: it is unauthenticated and
# unencrypted, so a network attacker can substitute package content that
# then gets linked into the user's binary.
_SAFE_GIT_SCHEMES = ("https://", "ssh://")


def validate_git_url(url, allow_insecure=False, source="repository"):
    """Validate a git URL before it is ever handed to `git clone`.

    Returns the (stripped) URL or exits with an error. With
    `allow_insecure=True` (for URLs the user typed themselves), plain http is
    permitted but warned about; URLs coming from the registry must be https.
    URLs with embedded credentials are always rejected: tokens end up
    printed, committed and published (use a credential helper instead).
    """
    if not isinstance(url, str):
        # Malformed registry entries (e.g. "repo": 123) must fail with a
        # clean error, not an AttributeError traceback.
        eprint(f"error: Invalid {source} URL")
        sys.exit(1)
    u = (url or "").strip()
    if not u:
        eprint(f"error: Empty {source} URL")
        sys.exit(1)
    if any(ord(c) < 0x20 or c == "\x7f" for c in u):
        eprint(f"error: Invalid {source} URL: contains control characters")
        sys.exit(1)
    if u.startswith("-"):
        eprint(f"error: Unsafe {source} URL rejected: '{u}' looks like a git "
               "command-line option (possible option-injection attack).")
        sys.exit(1)
    low = u.lower()
    if "://" in low:
        scheme = low.split("://", 1)[0] + "://"
        # Credentials baked into the URL (https://user:TOKEN@host/...)
        # leak into logs, the registry PR body, index.json and the
        # published repo. Plain `ssh://git@host/...` (no password) is the
        # standard ssh form and stays allowed.
        try:
            parts = urllib.parse.urlsplit(u)
            if parts.username and (parts.password or scheme == "https://"):
                eprint(f"error: Unsafe {source} URL rejected: credentials must "
                        "not be embedded in the URL (use a git credential helper).")
                sys.exit(1)
        except ValueError:
            eprint(f"error: Invalid {source} URL: '{u}'")
            sys.exit(1)
        if scheme == "http://":
            if not allow_insecure:
                eprint(f"error: Insecure {source} URL rejected: '{u}' (registry entries must use https).")
                sys.exit(1)
            print("[leashed] warning: using an unencrypted http:// git URL — "
                  "the download can be tampered with in transit.")
        elif scheme not in _SAFE_GIT_SCHEMES:
            eprint(f"error: Unsafe {source} URL rejected: '{u}'")
            eprint("  Only https:// and ssh:// URLs are allowed. Git "
                   "transports such as ext::/fd:: execute local commands, and "
                   "file:// URLs bypass the registry review entirely.")
            sys.exit(1)
    elif not _SCP_STYLE_RE.match(u):
        eprint(f"error: Unsafe {source} URL rejected: '{u}'")
        eprint("  Use an https:// URL (or git@host:owner/repo.git).")
        sys.exit(1)
    return u


# ------------------------------------------------------------- semver ----

SEMVER_RE = re.compile(r'^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z.-]+))?$')


def parse_version(v):
    """Parse 'X.Y.Z' or 'X.Y.Z-prerelease'. Returns tuple or None."""
    if not isinstance(v, str):
        return None
    m = SEMVER_RE.match(v.strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4)


def validate_version(v):
    return parse_version(v) is not None


def _pre_key(pre):
    # Releases sort after prereleases of the same X.Y.Z (semver 2.0.0 rule 11)
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


def is_newer_version(a, b):
    """True if semver a is strictly greater than semver b."""
    ka, kb = version_key(a), version_key(b)
    if ka is None or kb is None:
        return False
    return ka > kb


def sorted_versions(versions):
    """Sort an iterable of version strings newest-first; invalid ones go last."""
    valid = [v for v in versions if validate_version(v)]
    invalid = [v for v in versions if not validate_version(v)]
    return sorted(valid, key=version_key, reverse=True) + sorted(invalid)


_ANSI_RE = re.compile(r'\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\([0-9A-Z]|\x1b\[[0-9;?]*[ -/]*[@-~]')


def display_safe(s):
    """Strip terminal escape sequences and control characters for display.

    Registry and package metadata is rendered on the user's terminal with
    no human review in between, so OSC hyperlinks, cursor movement and
    other ANSI sequences must never reach the screen.
    """
    if s is None:
        return ""
    if not isinstance(s, str):
        s = str(s)
    s = _ANSI_RE.sub("", s)
    return re.sub(r'[\x00-\x1f\x7f]', ' ', s)


def validate_index_shape(index):
    """Check a decoded package index has the expected shape.

    Returns an error string, or None when the index is usable. Shared by
    the CDN fetch path and the registry-clone path so both fail cleanly
    on the same malformed data.
    """
    if not isinstance(index, dict):
        return "expected a JSON object"
    libs = index.get("libraries", {})
    if not isinstance(libs, dict):
        return 'expected {"libraries": {...}}'
    return None


def merge_registry_entry(fresh_entry, name, version, repo_url, description,
                         author, publisher):
    """Build the updated registry record for `name`, preserving history.

    `fresh_entry` is the record from the freshly cloned index (or None for
    a new registration) — never a stale cached copy, so concurrent
    publishes are preserved instead of overwritten. Returns
    (entry_dict, kept_versions) where kept_versions lists the previously
    published versions that survived the merge.
    """
    old_versions = {}
    if isinstance(fresh_entry, dict):
        prev = fresh_entry.get("versions", {})
        if isinstance(prev, dict):
            old_versions = dict(prev)
    kept = sorted(old_versions.keys())
    old_versions[version] = {
        "repo": repo_url,
        "tag": f"v{version}",
        "published_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    entry = {
        "repo": repo_url,
        "description": description,
        "author": author,
        "publisher": publisher,
        "version": version,
        "versions": old_versions,
    }
    return entry, kept


def check_version_match(requested, installed):
    """Compare a requested version against what a repo actually contains.

    Returns an error string, or None when they agree. A valid-but-different
    installed version must fail loudly: silently installing other bytes
    than requested defeats pinning, locks and reproducibility.
    """
    if requested is None:
        return None
    if installed in (None, "", "?"):
        return (f"could not determine the installed version "
                f"(requested {requested})")
    if installed != requested:
        return (f"version mismatch: requested {requested} but the repository "
                f"contains {installed}")
    return None


def registry_owner_ok(entry, config_author, gh_identity):
    """One ownership rule for pre-flight and registry-update checks.

    The gh identity matched against the stored publisher is the strong
    check; a bare author-string match is only accepted for legacy entries
    that recorded no publisher.
    """
    if not isinstance(entry, dict):
        return False
    stored_publisher = entry.get("publisher", "")
    if stored_publisher:
        return bool(gh_identity) and gh_identity == stored_publisher
    return entry.get("author", "") == config_author


def _del_rw(action, name, exc):
    os.chmod(name, stat.S_IWRITE)
    if os.path.isdir(name):
        os.rmdir(name)
    else:
        os.remove(name)


def tmp_cleanup(d):
    if d and os.path.exists(d):
        try:
            shutil.rmtree(d, onerror=_del_rw)
        except Exception:
            pass


def assert_no_symlinks(root):
    """Refuse to install any package tree containing symlinks.

    A symlink in a cloned repo (e.g. `library/data -> /home/victim/.ssh` or a
    symlink cycle) would be dereferenced by shutil.copytree, leaking files
    into the install directory or hanging the copy. Leash packages are plain
    source trees, so a symlink is never legitimate here.
    """
    # The walk root itself must be checked too: os.walk always descends
    # into the root even when it is a symlink (followlinks=False only
    # affects nested links), so `library -> /home/victim/.ssh` used to
    # pass the check and copytree then copied the target directory.
    if os.path.islink(root):
        eprint(f"error: refusing to install package: '{root}' is a symlink.")
        eprint("  Symlinks in packages can point outside the package "
               "(path traversal) and are not allowed.")
        sys.exit(1)
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in filenames + dirnames:
            p = os.path.join(dirpath, name)
            if os.path.islink(p):
                rel = os.path.relpath(p, root)
                eprint(f"error: refusing to install package: '{rel}' is a symlink.")
                eprint("  Symlinks in packages can point outside the package "
                       "(path traversal) and are not allowed.")
                sys.exit(1)


def run_git(cmd, cwd=None):
    try:
        res = subprocess.run(
            ["git"] + cmd, cwd=cwd, capture_output=True, text=True, timeout=120
        )
        return res.returncode, res.stdout.strip(), res.stderr.strip()
    except subprocess.TimeoutExpired:
        eprint("error: Git operation timed out")
        sys.exit(1)
    except FileNotFoundError:
        eprint("error: Git not found. Please install git (https://git-scm.com).")
        sys.exit(1)


def run_gh(cmd, required=True):
    try:
        res = subprocess.run(
            ["gh"] + cmd, capture_output=True, text=True, timeout=60
        )
        return res.returncode, res.stdout.strip(), res.stderr.strip()
    except subprocess.TimeoutExpired:
        if required:
            eprint("error: gh operation timed out")
            sys.exit(1)
        return 1, "", "timeout"
    except FileNotFoundError:
        if required:
            eprint("error: GitHub CLI (gh) not found. Install from https://cli.github.com")
            sys.exit(1)
        return 1, "", "not found"


_IDENTITY_CACHE = {}


def get_identity():
    if "identity" not in _IDENTITY_CACHE:
        rc, out, _ = run_gh(["api", "user", "--jq", ".login"], required=False)
        if rc == 0 and out:
            _IDENTITY_CACHE["identity"] = out
        else:
            rc, out, _ = run_git(["config", "--global", "user.name"])
            if rc == 0 and out:
                _IDENTITY_CACHE["identity"] = out
            else:
                rc, out, _ = run_git(["config", "--global", "user.email"])
                _IDENTITY_CACHE["identity"] = out if rc == 0 and out else None
    return _IDENTITY_CACHE["identity"]


def get_gh_user():
    if "gh_user" not in _IDENTITY_CACHE:
        rc, out, _ = run_gh(["api", "user", "--jq", ".login"], required=False)
        _IDENTITY_CACHE["gh_user"] = out if rc == 0 else None
    return _IDENTITY_CACHE["gh_user"]


def fetch_index():
    if REGISTRY_URL.startswith("http://"):
        eprint("[leashed] warning: LEASHED_REGISTRY_URL uses plain http:// — "
               "the package index can be tampered with in transit.")
    try:
        req = urllib.request.Request(REGISTRY_URL, headers={"User-Agent": "leashed"})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read(MAX_INDEX_BYTES + 1)
            if len(data) > MAX_INDEX_BYTES:
                eprint(f"error: Package index exceeds the maximum allowed size ({MAX_INDEX_BYTES // (1024 * 1024)} MiB)")
                sys.exit(1)
            index = json.loads(data.decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            # A missing index means an empty registry (brand-new registries
            # start this way) — but say so loudly: a custom URL 404ing is
            # more often a misconfiguration than a fresh start.
            if "LEASHED_REGISTRY_URL" in os.environ or REGISTRY_REPO != DEFAULT_REGISTRY_REPO:
                print(f"[leashed] warning: package index not found (HTTP 404): {REGISTRY_URL}")
                print("  Treating the registry as empty. If this URL is wrong, fix "
                      "LEASHED_REGISTRY_URL / LEASHED_REGISTRY_REPO.")
            return {"libraries": {}}
        eprint(f"error: Failed to fetch package index (HTTP {e.code})")
        sys.exit(1)
    except (urllib.error.URLError, json.JSONDecodeError, UnicodeDecodeError) as e:
        eprint(f"error: Failed to fetch package index: {e}")
        sys.exit(1)
    shape_err = validate_index_shape(index)
    if shape_err:
        eprint(f"error: Package index has an invalid format ({shape_err})")
        sys.exit(1)
    return index


def get_index_entry(index, libname):
    """Fetch a library entry from the index with defensive type checks."""
    entry = index.get("libraries", {}).get(libname)
    if not isinstance(entry, dict):
        eprint(f"error: Registry entry for '{libname}' is corrupt; refusing to use it")
        sys.exit(1)
    return entry


def read_pkg_config(path):
    config = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            # Strip inline comments (' #' outside of double quotes)
            cut = None
            in_q = False
            for i, c in enumerate(s):
                if c == '"':
                    in_q = not in_q
                elif c == "#" and not in_q and i > 0 and s[i - 1] in (" ", "\t"):
                    cut = i
                    break
            if cut is not None:
                s = s[:cut].strip()
            if ":" not in s:
                continue
            k, _, v = s.partition(":")
            k = k.strip()
            v = v.strip()
            if not k:
                continue
            if v.startswith('"') and v.endswith('"'):
                v = v[1:-1]
            config[k] = v
    return config


def write_pkg_config(path, config):
    def _esc(v):
        s = str(v).replace("\\", "\\\\").replace('"', '\\"')
        if any(ord(c) < 0x20 or c == "\x7f" for c in s):
            # control characters (including newlines) cannot round-trip
            # through the line-based format — collapse them to spaces
            s = " ".join(s.split())
        return s

    with open(path, "w", encoding="utf-8") as f:
        f.write("# Leashed package configuration\n")
        # Write ALL existing keys (custom keys used to be silently deleted)
        # with values that survive the read round-trip.
        for key, val in config.items():
            if not key or any(c in key for c in ' "\r\n\x00-\x1f\x7f'):
                print(f"[leashed] warning: dropping malformed config key: {key!r}")
                continue
            if val is None:
                continue
            if not isinstance(val, str):
                val = " ".join(str(x) for x in val) if isinstance(val, (list, tuple)) else str(val)
            f.write(f'{key}: "{_esc(val)}"\n')


def run_leash_check(filepath, extra_import_dirs=None):
    compiler = _leashc_compiler()
    if compiler is None:
        eprint("error: cannot check source: no self-hosted compiler available")
        sys.exit(1)
    cmd = [compiler, "check", filepath]
    if extra_import_dirs:
        for d in extra_import_dirs:
            cmd.extend(["-oi", d])
    rc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if rc.returncode != 0:
        eprint("error: Source code check failed. Fix errors before publishing.")
        eprint(rc.stderr or rc.stdout)
        sys.exit(1)
    return True


def run_leash_compile(filepath, output_stem, extra_import_dirs=None):
    compiler = _leashc_compiler()
    if compiler is None:
        eprint("error: cannot compile: no self-hosted compiler available")
        return False
    cmd = [compiler, "compile", filepath, "to-static", output_stem]
    if extra_import_dirs:
        for d in extra_import_dirs:
            cmd.extend(["-oi", d])
    rc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if rc.returncode != 0:
        eprint("error: Compilation failed.")
        eprint(rc.stderr or rc.stdout)
        sys.exit(1)
    return True


def cmd_init(args):
    if len(args) < 1:
        eprint("Usage: leashed init <path>")
        sys.exit(1)
    project_dir = os.path.abspath(args[0])
    if os.path.exists(project_dir) and os.listdir(project_dir):
        eprint(f"error: Directory '{project_dir}' is not empty")
        sys.exit(1)
    os.makedirs(project_dir, exist_ok=True)
    default_name = os.path.basename(project_dir)
    validate_name(default_name)
    src_dir = os.path.join(project_dir, "src")
    os.makedirs(src_dir, exist_ok=True)
    main_lsh = os.path.join(src_dir, "main.lsh")
    with open(main_lsh, "w", encoding="utf-8") as f:
        f.write('pub fnc greet(name: string) : string {\n')
        f.write('    return "Hello, " + name + "!";\n')
        f.write('}\n')
    config_path = os.path.join(project_dir, LEASHED_CONFIG)
    ident = get_identity() or "anonymous"
    config = {
        "name": default_name,
        "version": "0.1.0",
        "author": ident,
        "description": f"The {default_name} library",
        "main": "src/main.lsh",
    }
    write_pkg_config(config_path, config)
    with open(os.path.join(project_dir, ".gitignore"), "w", encoding="utf-8") as f:
        f.write("__pycache__/\n*.exe\nout/\n")
    print(f"Initialized leash package in '{project_dir}'")
    print(f"  {main_lsh}")
    print(f"  {config_path}")
    print(f"  {src_dir}/")
    print()
    print("Edit leash-pkg.lshc to add a 'repo' field with your GitHub repo URL,")
    print("then run 'leashed publish' to publish your library.")


def _clone_registry_for_owner():
    """Shallow-clone the registry and check out its default branch.

    The branch name comes from the remote itself (symref), so registries
    on `trunk`/`develop`/etc. work instead of gaining a stray `main`.
    Returns (reg_tmp, push_branch). Exits cleanly on failure.
    """
    reg_tmp = tempfile.mkdtemp(prefix="leashed_reg_")
    rc, _, err = run_git(["clone", "--depth", "1", REGISTRY_GIT, reg_tmp])
    if rc != 0:
        # Repo might be empty (no commits yet)
        rc, _, err = run_git(["init"], cwd=reg_tmp)
        if rc != 0:
            eprint(f"error: Failed to init registry repo: {err}")
            tmp_cleanup(reg_tmp)
            sys.exit(1)
        rc, _, err = run_git(["remote", "add", "origin", REGISTRY_GIT], cwd=reg_tmp)
        if rc != 0:
            eprint(f"error: Failed to add remote: {err}")
            tmp_cleanup(reg_tmp)
            sys.exit(1)
        return reg_tmp, "main"
    branch = ""
    rc, out, _ = run_git(["ls-remote", "--symref", "origin", "HEAD"], cwd=reg_tmp)
    if rc == 0:
        for ln in out.splitlines():
            ln = ln.strip()
            if ln.startswith("ref:"):
                ref = ln.split()[-1]
                if ref.startswith("refs/heads/") and len(ref) > len("refs/heads/"):
                    branch = ref[len("refs/heads/"):]
                break
    for candidate in ([branch] if branch else []) + ["main", "master"]:
        rc, _, _ = run_git(["fetch", "--depth", "1", "origin", candidate], cwd=reg_tmp)
        if rc == 0:
            rc, _, err = run_git(["checkout", "-b", candidate, f"origin/{candidate}"], cwd=reg_tmp)
            if rc != 0:
                eprint(f"error: Failed to check out registry branch '{candidate}': {err}")
                tmp_cleanup(reg_tmp)
                sys.exit(1)
            return reg_tmp, candidate
    return reg_tmp, "main"


def _read_registry_index(reg_dir):
    """Read + validate index.json from a registry clone. Exits cleanly."""
    index_path = os.path.join(reg_dir, "index.json")
    if not os.path.exists(index_path):
        return {"libraries": {}}
    try:
        with open(index_path, "r", encoding="utf-8") as f:
            index = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        eprint(f"error: Registry index.json is corrupt: {e}")
        sys.exit(1)
    shape_err = validate_index_shape(index)
    if shape_err:
        eprint(f"error: Registry index.json is corrupt: {shape_err}")
        sys.exit(1)
    return index


def check_registry_update(fresh_index, name, author, publisher, version,
                          preflight_version=""):
    """Ownership + version-conflict gate against fresh registry data.

    `fresh_index` must come from a fresh clone (never a cached copy).
    Returns an error string, or None when the publish may proceed.
    """
    fresh_entry = fresh_index.get("libraries", {}).get(name)
    if fresh_entry is not None and not isinstance(fresh_entry, dict):
        return f"Registry entry for '{name}' is corrupt; cannot publish over it."
    if fresh_entry is None:
        return None
    if not registry_owner_ok(fresh_entry, author, publisher):
        stored = fresh_entry.get("publisher") or fresh_entry.get("author", "")
        return (f"Library '{name}' is already registered by '{stored}'. "
                "Only the original owner can update a package. Pick another name.")
    fresh_ver = fresh_entry.get("version", "")
    if fresh_ver and not validate_version(fresh_ver):
        return (f"Registry entry for '{name}' has a corrupt version "
                f"('{fresh_ver}'); cannot publish over it.")
    if fresh_ver and not is_newer_version(version, fresh_ver):
        moved = ""
        if preflight_version and preflight_version != fresh_ver:
            moved = f" (moved from v{preflight_version} while this publish was running)"
        return (f"Version conflict: the registry now has '{name}' v{fresh_ver}{moved}; "
                f"refusing to overwrite with v{version}. Bump the 'version' field "
                "past the registry version and try again.")
    return None


def cmd_publish(args):
    if args:
        print(f"[leashed] warning: ignoring extra arguments: {' '.join(args)}")
    project_dir = os.getcwd()
    config_path = os.path.join(project_dir, LEASHED_CONFIG)
    if not os.path.exists(config_path):
        eprint(f"error: No '{LEASHED_CONFIG}' found in '{project_dir}'")
        eprint("  Run 'leashed init' first or change to a leash package directory")
        sys.exit(1)
    config = read_pkg_config(config_path)
    for key in ["name", "version", "author"]:
        if key not in config:
            eprint(f"error: '{key}' not set in {LEASHED_CONFIG}")
            sys.exit(1)
    name = validate_name(config["name"])
    version = config["version"].strip()
    if not validate_version(version):
        eprint(f"error: Invalid version '{version}'. Use semantic versioning: X.Y.Z (optionally -prerelease)")
        sys.exit(1)
    author = config["author"]
    description = config.get("description", "")
    main_file = config.get("main", "")
    if not main_file:
        eprint("error: 'main' not set in leash-pkg.lshc")
        sys.exit(1)
    main_path = os.path.join(project_dir, main_file)
    # `main: "../other/src/main.lsh"` used to make the source-copy phase
    # walk (and publish) a directory OUTSIDE the project.
    _real_main = os.path.realpath(main_path)
    _real_proj = os.path.realpath(project_dir)
    if _real_main != _real_proj and not _real_main.startswith(_real_proj + os.sep):
        eprint(f"error: 'main' must stay inside the project directory (got '{main_file}').")
        sys.exit(1)
    if not os.path.exists(main_path):
        eprint(f"error: Main file '{main_path}' not found")
        sys.exit(1)
    publisher = get_identity()
    if not publisher:
        eprint("error: Could not determine your identity. Install 'gh' (GitHub CLI) and authenticate, or set git user.name/user.email")
        sys.exit(1)
    gh_user = get_gh_user()
    if not gh_user:
        eprint("error: GitHub CLI (gh) is required for publishing. Authenticate with 'gh auth login'")
        sys.exit(1)
    # Read extra import dirs from config
    imports_str = config.get("imports", "")
    extra_import_dirs = [d.strip() for d in imports_str.split(",") if d.strip()] if imports_str else None
    # Make relative paths absolute from project_dir, contained like `main`:
    # an outside dir would make the build depend on files that are never
    # published, and error output could echo outside content.
    if extra_import_dirs:
        abs_dirs = [os.path.join(project_dir, d) if not os.path.isabs(d) else d for d in extra_import_dirs]
        for orig, absd in zip(extra_import_dirs, abs_dirs):
            rd = os.path.realpath(absd)
            if rd != _real_proj and not rd.startswith(_real_proj + os.sep):
                eprint(f"error: 'imports' entry must stay inside the project directory (got '{orig}').")
                sys.exit(1)
        extra_import_dirs = abs_dirs

    # Pre-flight registry check: ownership + strictly increasing version.
    # Fails fast before any compiling/pushing so mistakes cost nothing.
    index = fetch_index()
    libs = index.get("libraries", {})
    existing_entry = libs.get(name)
    if existing_entry is not None and not isinstance(existing_entry, dict):
        eprint(f"error: Registry entry for '{name}' is corrupt; cannot publish over it.")
        sys.exit(1)
    preflight_version = ""
    if existing_entry:
        if not registry_owner_ok(existing_entry, author, publisher):
            stored = existing_entry.get("publisher") or existing_entry.get("author", "")
            eprint(f"error: Library '{name}' is already registered by '{stored}'.")
            eprint("  Only the original owner can update a package. Pick another name.")
            sys.exit(1)
        old_version = existing_entry.get("version", "0.0.0")
        if not validate_version(old_version):
            eprint(f"error: Registry entry for '{name}' has a corrupt version ('{old_version}'); cannot publish over it.")
            sys.exit(1)
        preflight_version = old_version
        if not is_newer_version(version, old_version):
            eprint(f"error: Version {version} is not greater than the published version {old_version}.")
            eprint("  Bump the 'version' field in leash-pkg.lshc and try again.")
            sys.exit(1)

    print(f"[leashed] Publishing '{name}' v{version} by {author}")
    print(f"[leashed] Publisher: {publisher}")

    # The publish path never checked for symlinks: shutil.copy2/copytree
    # both FOLLOW them, so a tracked `src -> /home/victim/.ssh` would be
    # silently committed and force-pushed to the (public) repository.
    # Validated here — before any temp dirs exist, so a refusal leaks nothing.
    assert_no_symlinks(project_dir)

    # Validate a configured repo URL up front for the same reason.
    repo_url_cfg = config.get("repo", "")
    if repo_url_cfg:
        # The config value used to go straight into `git remote add` ->
        # `git fetch` -> `git push --force`, so a tampered leash-pkg.lshc
        # could force-push to arbitrary local / file:// / ext:: remotes.
        # Same strict validation as the install path (H1).
        repo_url_cfg = validate_git_url(repo_url_cfg, allow_insecure=False, source="publish repo")

    # Step 1: Verify source code
    print("[leashed] Verifying source code...")
    run_leash_check(main_path, extra_import_dirs=extra_import_dirs)

    # Step 2: Compile to static library
    print("[leashed] Compiling library...")
    out_dir = tempfile.mkdtemp(prefix="leashed_bld_")
    lib_out = os.path.join(out_dir, LIBRARY_DIR)
    os.makedirs(lib_out, exist_ok=True)
    output_stem = os.path.join(lib_out, name)
    try:
        run_leash_compile(main_path, output_stem, extra_import_dirs=extra_import_dirs)
    except Exception as e:
        eprint(f"error: Compilation failed: {e}")
        tmp_cleanup(out_dir)
        sys.exit(1)

    # Re-check for symlinks right before copying: validated up front too,
    # but compilation and network calls happen in between, so narrow the
    # window in which a planted link could reach the published repo.
    # (shutil.copy2/copytree FOLLOW links.)
    assert_no_symlinks(project_dir)

    src_root = os.path.dirname(main_path)
    for root, dirs, files in os.walk(src_root):
        rel = os.path.relpath(root, src_root)
        dst = os.path.join(lib_out, rel) if rel != "." else lib_out
        os.makedirs(dst, exist_ok=True)
        for f in files:
            shutil.copy2(os.path.join(root, f), os.path.join(dst, f))

    pkg_config = {
        "name": name,
        "version": version,
        "author": author,
        "publisher": publisher,
        "description": description,
        "main": main_file,
        "published_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    # Global commands ride into the install manifest as library-relative
    # paths, so installs can build them without the source manifest.
    gi_raw = config.get("global_install", "")
    if gi_raw.strip():
        try:
            gi_map = _parse_global_install_map(gi_raw)
        except ValueError as e:
            eprint(f"error: invalid 'global_install' in leash-pkg.lshc: {e}")
            tmp_cleanup(out_dir)
            sys.exit(1)
        try:
            pkg_config["global_install"] = _map_tool_sources(
                project_dir, os.path.realpath(src_root), gi_map)
        except ValueError as e:
            eprint(f"error: invalid 'global_install' in leash-pkg.lshc: {e}")
            tmp_cleanup(out_dir)
            sys.exit(1)
        print(f"[leashed] Registered {len(pkg_config['global_install'])} global command(s)")
    with open(os.path.join(lib_out, PACKAGE_CONFIG), "w", encoding="utf-8") as f:
        json.dump(pkg_config, f, indent=2)
    with open(os.path.join(lib_out, PUBLISHER_FILE), "w", encoding="utf-8") as f:
        f.write(publisher + "\n")

    print("[leashed] Library compiled successfully")

    # Step 3: Determine or create the library repo
    repo_url = repo_url_cfg
    if repo_url:
        print(f"[leashed] Using repo: {repo_url}")
    else:
        # Auto-create a public repo under the user's GitHub account
        print(f"[leashed] No 'repo' set in config — creating GitHub repo '{name}'...")
        rc, out, err = run_gh(["repo", "create", name, "--public", "--description", description or f"The {name} library"])
        if rc != 0:
            eprint(f"error: Failed to create repo: {err}")
            eprint("  Create a repo manually, add it to leash-pkg.lshc as 'repo: <url>', and try again.")
            tmp_cleanup(out_dir)
            sys.exit(1)
        repo_url = f"https://github.com/{gh_user}/{name}.git"
        print(f"[leashed] Created repo: {repo_url}")
        # Save repo URL to config for future publishes — but only when it
        # actually changed, so a failed publish doesn't gratuitously
        # rewrite (and reformat) the user's config file.
        if config.get("repo", "") != repo_url:
            config["repo"] = repo_url
            write_pkg_config(config_path, config)
            print(f"[leashed] Saved 'repo' to {LEASHED_CONFIG}")

    is_owner = (gh_user == REGISTRY_OWNER)
    # Pre-push registry gate (owners): the registry clone is cheap
    # (depth 1), and failing here — before the library repo is pushed —
    # avoids a half-published state no re-run can cleanly resume.
    if is_owner:
        gate_tmp = _clone_registry_for_owner()
        try:
            gate_err = check_registry_update(
                _read_registry_index(gate_tmp), name, author, publisher,
                version, preflight_version)
            if gate_err:
                eprint(f"error: {gate_err}")
                sys.exit(1)
        finally:
            tmp_cleanup(gate_tmp)

    # Step 4: Push source + compiled library to the library repo
    repo_tmp = tempfile.mkdtemp(prefix="leashed_repo_")
    rc, _, err = run_git(["init"], cwd=repo_tmp)
    if rc != 0:
        eprint(f"error: Git init failed: {err}")
        tmp_cleanup(out_dir)
        tmp_cleanup(repo_tmp)
        sys.exit(1)

    # Publish set: only sources, manifests and docs ride into the public
    # repo. Everything else (examples/, tests/, local tooling, build
    # outputs, and any secret-shaped files) stays home — a stray `.env`
    # must never be force-pushed publicly. Extra paths opt in explicitly
    # via `publish_include` in leash-pkg.lshc.
    _SECRET_NAME_RE = re.compile(
        r'(^\.env(\.|$))|(\.pem$)|(\.key$)|(credentials)|(secret)|(token)|'
        r'(^\.aws$)|(^\.ssh$)|(^\.gnupg$)'
    , re.IGNORECASE)
    publish_extra = [d.strip() for d in config.get("publish_include", "").split(",") if d.strip()]
    extra_dirs = set()
    for extra in publish_extra:
        xp = os.path.realpath(os.path.join(project_dir, extra))
        if xp != _real_proj and not xp.startswith(_real_proj + os.sep):
            eprint(f"error: 'publish_include' entry must stay inside the project directory (got '{extra}').")
            tmp_cleanup(out_dir)
            tmp_cleanup(repo_tmp)
            sys.exit(1)
        extra_dirs.add(os.path.relpath(xp, _real_proj).split(os.sep)[0])

    def _secret_shaped(name):
        return bool(_SECRET_NAME_RE.search(name))

    skipped_top = []
    warned_secrets = []

    def _copy_publish_tree(sp, dp):
        # Recursive copy honoring the secret denylist (nested `.env`
        # files and `.git` histories are never published).
        for root, dirs, files in os.walk(sp, followlinks=False):
            dirs[:] = [d for d in dirs if d != ".git" and not os.path.islink(os.path.join(root, d))]
            rel = os.path.relpath(root, sp)
            dst_root = os.path.join(dp, rel) if rel != "." else dp
            os.makedirs(dst_root, exist_ok=True)
            for f in files:
                if _secret_shaped(f):
                    warned_secrets.append(os.path.join(rel, f) if rel != "." else f)
                    continue
                fp = os.path.join(root, f)
                if os.path.islink(fp):
                    warned_secrets.append(os.path.join(rel, f) if rel != "." else f)
                    continue
                shutil.copy2(fp, os.path.join(dst_root, f))

    for item in sorted(os.listdir(project_dir)):
        if item in (".git", "__pycache__", "out", "library"):
            continue
        sp = os.path.join(project_dir, item)
        dp = os.path.join(repo_tmp, item)
        keep = False
        if os.path.isfile(sp) and not os.path.islink(sp):
            if (item == LEASHED_CONFIG or item in extra_dirs or
                    item == "package.lshc" or
                    item.startswith("README") or item.startswith("LICENSE") or
                    item.startswith("CHANGELOG") or
                    item in (".gitignore", ".gitattributes", ".editorconfig")):
                if _secret_shaped(item):
                    warned_secrets.append(item)
                else:
                    shutil.copy2(sp, dp)
                    keep = True
        elif os.path.isdir(sp) and not os.path.islink(sp):
            if item in ("src",) or item in extra_dirs:
                _copy_publish_tree(sp, dp)
                keep = True
        if not keep:
            if _secret_shaped(item) or item.startswith("."):
                warned_secrets.append(item)
            else:
                skipped_top.append(item)

    # The main file rides along wherever it lives inside the project
    # (it passed containment above).
    main_rel = os.path.relpath(_real_main, _real_proj)
    main_dp = os.path.join(repo_tmp, main_rel)
    os.makedirs(os.path.dirname(main_dp) or repo_tmp, exist_ok=True)
    shutil.copy2(_real_main, main_dp)

    for s in warned_secrets:
        print(f"[leashed] warning: not publishing suspected secret '{s}'")
    if skipped_top:
        shown = ", ".join(skipped_top[:5])
        if len(skipped_top) > 5:
            shown += f", and {len(skipped_top) - 5} more"
        print(f"[leashed] Skipped {len(skipped_top)} file(s) not in the publish set: {shown}")
        print(f"[leashed]   (publish src/, {LEASHED_CONFIG}, README, LICENSE, CHANGELOG; "
              f"anything else needs 'publish_include' in {LEASHED_CONFIG})")

    shutil.copytree(os.path.join(out_dir, LIBRARY_DIR), os.path.join(repo_tmp, LIBRARY_DIR))

    rc, _, err = run_git(["add", "-A"], cwd=repo_tmp)
    if rc != 0:
        eprint(f"error: Git add failed: {err}")
        tmp_cleanup(out_dir)
        tmp_cleanup(repo_tmp)
        sys.exit(1)

    rc, diff_stat, _ = run_git(["diff", "--cached", "--stat"], cwd=repo_tmp)
    if not diff_stat:
        print("[leashed] No changes to publish")
        tmp_cleanup(out_dir)
        tmp_cleanup(repo_tmp)
        return

    rc, _, err = run_git(["remote", "add", "origin", repo_url], cwd=repo_tmp)
    if rc != 0:
        eprint(f"error: Failed to add remote: {err}")
        tmp_cleanup(out_dir)
        tmp_cleanup(repo_tmp)
        sys.exit(1)

    # Try to fetch default branch to see if repo already has content.
    # The branch name comes from the remote itself (symref), so repos on
    # `trunk`/`develop`/etc. work instead of gaining a stray `main`.
    default_branch = "main"
    rc, out, _ = run_git(["ls-remote", "--symref", "origin", "HEAD"], cwd=repo_tmp)
    if rc == 0:
        for ln in out.splitlines():
            ln = ln.strip()
            if ln.startswith("ref:"):
                ref = ln.split()[-1]
                if ref.startswith("refs/heads/") and len(ref) > len("refs/heads/"):
                    default_branch = ref[len("refs/heads/"):]
                break
    rc, _, _ = run_git(["fetch", "--depth", "1", "origin", default_branch], cwd=repo_tmp)
    has_remote = rc == 0
    if not has_remote and default_branch != "master":
        rc, _, _ = run_git(["fetch", "--depth", "1", "origin", "master"], cwd=repo_tmp)
        if rc == 0:
            default_branch = "master"
            has_remote = True
    if has_remote:
        rc, _, err = run_git(["checkout", "-b", default_branch, f"origin/{default_branch}"], cwd=repo_tmp)
        if rc != 0:
            rc, _, err = run_git(["checkout", default_branch], cwd=repo_tmp)
            if rc != 0:
                eprint(f"error: Failed to check out '{default_branch}': {err}")
                tmp_cleanup(out_dir)
                tmp_cleanup(repo_tmp)
                sys.exit(1)
    else:
        # New repo — use main
        default_branch = "main"
        rc, _, err = run_git(["checkout", "-b", "main"], cwd=repo_tmp)
        if rc != 0:
            eprint(f"error: Failed to create branch: {err}")
            tmp_cleanup(out_dir)
            tmp_cleanup(repo_tmp)
            sys.exit(1)

    # Re-add everything (fetch/checkout might have reset)
    shutil.copytree(os.path.join(out_dir, LIBRARY_DIR), os.path.join(repo_tmp, LIBRARY_DIR), dirs_exist_ok=True)
    rc, _, err = run_git(["add", "-A"], cwd=repo_tmp)
    if rc != 0:
        eprint(f"error: Git add failed: {err}")
        tmp_cleanup(out_dir)
        tmp_cleanup(repo_tmp)
        sys.exit(1)

    # Commit message carries no free text: multi-line author/description
    # strings could forge commit trailers (`Fixes:`, `Co-authored-by:`).
    safe_pub = " ".join(str(publisher).split())
    msg = f"Publish {name} v{version}\n\nPublisher: {safe_pub}"
    rc, _, err = run_git(["-c", "user.name=leashed", "-c", "user.email=leashed@localhost", "commit", "-m", msg], cwd=repo_tmp)
    if rc != 0:
        eprint(f"error: Git commit failed: {err}")
        tmp_cleanup(out_dir)
        tmp_cleanup(repo_tmp)
        sys.exit(1)

    print(f"[leashed] Pushing to '{repo_url}'...")
    rc, out, err = run_git(["push", "--force-with-lease", "-u", "origin", default_branch], cwd=repo_tmp)
    if rc != 0:
        eprint(f"error: Failed to push: {err}")
        eprint("  Make sure you have write access to the repository.")
        tmp_cleanup(out_dir)
        tmp_cleanup(repo_tmp)
        sys.exit(1)

    # Tag this release so `leashed install name@version` can fetch it.
    # Published tags are immutable: moving one would silently change what
    # every pinned install resolves to. The only exception is a retry
    # whose remote tag already points at byte-identical content (e.g. a
    # publish whose registry step failed) — there is nothing to move.
    tag = f"v{version}"
    rc, out, _ = run_git(["ls-remote", "--tags", "origin", tag], cwd=repo_tmp)
    tag_exists = rc == 0 and any(ln.strip().endswith(f"refs/tags/{tag}") for ln in out.splitlines())
    if tag_exists:
        rc, _, _ = run_git(["fetch", "--depth", "1", "origin", "tag", tag], cwd=repo_tmp)
        same = False
        if rc == 0:
            rc1, new_tree, _ = run_git(["rev-parse", "HEAD^{tree}"], cwd=repo_tmp)
            rc2, remote_tree, _ = run_git(["rev-parse", "FETCH_HEAD^{tree}"], cwd=repo_tmp)
            same = rc1 == 0 and rc2 == 0 and bool(new_tree) and new_tree == remote_tree
        if not same:
            eprint(f"error: Tag '{tag}' already exists on the remote; refusing to move a published tag.")
            eprint("  Bump the 'version' field in leash-pkg.lshc and publish again.")
            tmp_cleanup(out_dir)
            tmp_cleanup(repo_tmp)
            sys.exit(1)
        print(f"[leashed] Tag '{tag}' already points at this content; keeping it")
    else:
        rc, _, err = run_git(["tag", tag], cwd=repo_tmp)
        if rc != 0:
            eprint(f"warning: Could not create version tag '{tag}': {err}")
            eprint("  The library was published; installing by exact version may not work.")
        else:
            rc, _, err = run_git(["push", "origin", tag], cwd=repo_tmp)
            if rc != 0:
                eprint(f"warning: Could not push version tag '{tag}': {err}")
                eprint("  The library was published; installing by exact version may not work.")

    print(f"[leashed] Successfully published '{name}' v{version}!")
    print(f"[leashed]   Repo: {repo_url}")

    # Step 5: Register in the central registry
    print(f"[leashed] Registering '{name}' in the package index...")

    if is_owner:
        # Push directly to the upstream repo (no fork needed for owner).
        # Re-cloned here so the mutation below applies to current data
        # (the pre-push gate already passed on an identical clone).
        reg_tmp, push_branch = _clone_registry_for_owner()
    else:
        reg_tmp = tempfile.mkdtemp(prefix="leashed_reg_")
        # Non-owner: fork + PR flow (a bot auto-merges the PR if it validates)
        rc, _, err = run_gh(["repo", "fork", REGISTRY_REPO, "--clone=false"])
        if rc != 0:
            eprint(f"error: Failed to fork registry: {err}")
            eprint("  Your library was published, but not registered in the index.")
            eprint(f"  Submit a PR to {REGISTRY_REPO} adding to index.json manually.")
            tmp_cleanup(out_dir)
            tmp_cleanup(repo_tmp)
            tmp_cleanup(reg_tmp)
            sys.exit(1)

        # Sync the fork with upstream so the PR only contains our change
        reg_parts = REGISTRY_REPO.split("/")
        if len(reg_parts) != 2 or not reg_parts[0] or not reg_parts[1]:
            eprint(f"error: Invalid registry '{REGISTRY_REPO}' (expected 'owner/repo').")
            tmp_cleanup(out_dir)
            tmp_cleanup(repo_tmp)
            tmp_cleanup(reg_tmp)
            sys.exit(1)
        run_gh(["repo", "sync", f"{gh_user}/{reg_parts[1]}",
                "--source", REGISTRY_REPO], required=False)

        fork_url = f"https://github.com/{gh_user}/{reg_parts[1]}.git"
        rc, _, err = run_git(["clone", "--depth", "1", fork_url, reg_tmp])
        if rc != 0:
            eprint(f"error: Failed to clone fork: {err}")
            tmp_cleanup(out_dir)
            tmp_cleanup(repo_tmp)
            tmp_cleanup(reg_tmp)
            sys.exit(1)

        rc, _, err = run_git(["remote", "add", "upstream", REGISTRY_GIT], cwd=reg_tmp)
        if rc != 0:
            eprint(f"error: Failed to add upstream remote: {err}")
            tmp_cleanup(out_dir)
            tmp_cleanup(repo_tmp)
            tmp_cleanup(reg_tmp)
            sys.exit(1)

        rc, _, _ = run_git(["fetch", "--depth", "1", "upstream", "main"], cwd=reg_tmp)
        has_main = rc == 0
        if has_main:
            rc, _, err = run_git(["checkout", "-b", f"register-{name}", "upstream/main"], cwd=reg_tmp)
            if rc != 0:
                eprint(f"error: Failed to check out registry branch: {err}")
                tmp_cleanup(out_dir)
                tmp_cleanup(repo_tmp)
                tmp_cleanup(reg_tmp)
                sys.exit(1)
            push_branch = f"register-{name}"
        else:
            rc, _, _ = run_git(["fetch", "--depth", "1", "upstream", "master"], cwd=reg_tmp)
            if rc == 0:
                rc, _, err = run_git(["checkout", "-b", f"register-{name}", "upstream/master"], cwd=reg_tmp)
                if rc != 0:
                    eprint(f"error: Failed to check out registry branch: {err}")
                    tmp_cleanup(out_dir)
                    tmp_cleanup(repo_tmp)
                    tmp_cleanup(reg_tmp)
                    sys.exit(1)
                push_branch = f"register-{name}"
            else:
                rc, _, err = run_git(["checkout", "-b", f"register-{name}"], cwd=reg_tmp)
                if rc != 0:
                    eprint(f"error: Failed to create registry branch: {err}")
                    tmp_cleanup(out_dir)
                    tmp_cleanup(repo_tmp)
                    tmp_cleanup(reg_tmp)
                    sys.exit(1)
                push_branch = f"register-{name}"

    # Read the freshly cloned index, re-gate, and merge. The versions map
    # is rebuilt from THIS data (never the pre-flight copy), so concurrent
    # publishes survive instead of being overwritten.
    index = _read_registry_index(reg_tmp)

    gate_err = check_registry_update(index, name, author, publisher,
                                     version, preflight_version)
    if gate_err:
        eprint(f"error: {gate_err}")
        eprint("  Your library repo was pushed, but the index was not updated.")
        eprint("  Fix the conflict above and re-run 'leashed publish' to complete registration.")
        tmp_cleanup(out_dir)
        tmp_cleanup(repo_tmp)
        tmp_cleanup(reg_tmp)
        sys.exit(1)

    if "libraries" not in index:
        index["libraries"] = {}
    index["libraries"][name], _kept = merge_registry_entry(
        index["libraries"].get(name), name, version, repo_url,
        description, author, publisher)

    index_path = os.path.join(reg_tmp, "index.json")
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2)
        f.write("\n")

    rc, _, err = run_git(["add", "index.json"], cwd=reg_tmp)
    if rc != 0:
        eprint(f"error: Git add failed: {err}")
        tmp_cleanup(out_dir)
        tmp_cleanup(repo_tmp)
        tmp_cleanup(reg_tmp)
        sys.exit(1)

    rc, out, err = run_git(["-c", "user.name=leashed", "-c", "user.email=leashed@localhost", "commit", "-m", f"Register {name} v{version}"], cwd=reg_tmp)
    if rc != 0:
        if "nothing to commit" in err.lower() or "nothing added" in err.lower():
            print("[leashed] No index changes needed (already up to date)")
            tmp_cleanup(out_dir)
            tmp_cleanup(repo_tmp)
            tmp_cleanup(reg_tmp)
            return
        eprint(f"error: Git commit failed: {err}")
        tmp_cleanup(out_dir)
        tmp_cleanup(repo_tmp)
        tmp_cleanup(reg_tmp)
        sys.exit(1)

    rc, _, err = run_git(["push", "--force-with-lease", "-u", "origin", push_branch], cwd=reg_tmp)
    if rc != 0:
        eprint(f"error: Failed to push registry update: {err}")
        tmp_cleanup(out_dir)
        tmp_cleanup(repo_tmp)
        tmp_cleanup(reg_tmp)
        sys.exit(1)

    if is_owner:
        print(f"[leashed] Registered '{name}' in the package index")
    else:
        pr_body = (
            f"## Register {name} v{version}\n\n"
            f"- **Library:** {name}\n"
            f"- **Version:** {version}\n"
            f"- **Author:** {author}\n"
            f"- **Description:** {description}\n"
            f"- **Repo:** {repo_url}\n\n"
            f"Published by {publisher}."
        )
        rc, out, err = run_gh(["pr", "create",
                               "--repo", REGISTRY_REPO,
                               "--head", f"{gh_user}:{push_branch}",
                               "--base", "main",
                               "--title", f"Register {name} v{version}",
                               "--body", pr_body])
        if rc != 0:
            rc, out, err = run_gh(["pr", "create",
                                   "--repo", REGISTRY_REPO,
                                   "--head", f"{gh_user}:{push_branch}",
                                   "--base", "master",
                                   "--title", f"Register {name} v{version}",
                                   "--body", pr_body])
        if rc == 0:
            pr_url = out.strip()
            print(f"[leashed] Registration PR created: {pr_url}")
            print("[leashed] A validation bot will review it automatically —")
            print("[leashed]   if all checks pass, the PR merges itself within a minute.")
        else:
            eprint(f"warning: Failed to create PR: {err}")
            eprint(f"  Submit a PR manually to {REGISTRY_REPO} updating index.json.")

    tmp_cleanup(out_dir)
    tmp_cleanup(repo_tmp)
    tmp_cleanup(reg_tmp)


# ------------------------------------------------------------ lockfile ----

def read_lockfile(project_dir):
    """Read leash.lock. Returns None when it does not exist; exits on a
    corrupt file (a lockfile you can't parse must fail loudly, not silently
    resolve different versions)."""
    path = os.path.join(project_dir, LOCKFILE)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        eprint(f"error: Corrupt {LOCKFILE}: {e}")
        sys.exit(1)
    if not isinstance(data, dict) or not isinstance(data.get("packages", {}), dict):
        eprint(f"error: {LOCKFILE} has an invalid format (expected {{\"packages\": {{...}}}})")
        sys.exit(1)
    if data.get("version") != LOCKFILE_VERSION:
        eprint(f"error: {LOCKFILE} has an unsupported version field "
               f"({data.get('version')!r}; expected {LOCKFILE_VERSION})")
        sys.exit(1)
    return data


def write_lockfile(project_dir, packages):
    """Write leash.lock deterministically (sorted keys, fixed shape).
    `packages` maps name -> {"version", "repo"} with an optional "rev"
    commit-SHA pin (recorded at install/lock time, verified on restore)."""
    out = {"version": LOCKFILE_VERSION, "packages": {}}
    for name in sorted(packages):
        p = packages[name] if isinstance(packages[name], dict) else {}
        version = p.get("version", "")
        if not validate_version(version):
            continue
        entry = {
            "version": version,
            "repo": p.get("repo", ""),
            "tag": p.get("tag") or f"v{version}",
        }
        if p.get("rev"):
            entry["rev"] = p["rev"]
        out["packages"][name] = entry
    path = os.path.join(project_dir, LOCKFILE)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
        f.write("\n")
    return path


def _project_dir_or_none():
    """The lockfile lives in leashed projects (a dir with leash-pkg.lshc) and
    in dirs that already have one."""
    cwd = os.getcwd()
    if os.path.exists(os.path.join(cwd, LEASHED_CONFIG)) or \
            os.path.exists(os.path.join(cwd, LOCKFILE)):
        return cwd
    return None


def lockfile_entry(project_dir, name):
    """Return the lockfile entry dict for `name`, or None."""
    data = read_lockfile(project_dir)
    if not data:
        return None
    entry = data.get("packages", {}).get(name)
    return entry if isinstance(entry, dict) else None


def lockfile_pin(project_dir, name):
    """Return the locked version of `name` when the project has an entry."""
    entry = lockfile_entry(project_dir, name)
    if entry is not None and validate_version(entry.get("version", "")):
        return entry["version"]
    return None


def lockfile_rev(project_dir, name):
    """Return the locked commit SHA of `name`, or None when unpinned."""
    entry = lockfile_entry(project_dir, name)
    if entry is not None:
        rev = entry.get("rev", "")
        if isinstance(rev, str) and re.fullmatch(r'[0-9a-f]{40}', rev):
            return rev
    return None


def lockfile_record(project_dir, name, version, repo, sha=None):
    """Insert or update one entry in the project lockfile."""
    if not validate_version(version):
        eprint(f"warning: not recording {name}@{version} in {LOCKFILE}: invalid version")
        return
    pkgs = {}
    data = read_lockfile(project_dir)
    if data:
        pkgs = dict(data.get("packages", {}))
    entry = {"version": version, "repo": repo}
    if sha:
        entry["rev"] = sha
    pkgs[name] = entry
    write_lockfile(project_dir, pkgs)
    print(f"[leashed] Wrote {LOCKFILE}: {name}@{version}")


def lockfile_unpin(project_dir, name):
    """Remove one entry from the lockfile (no-op when absent)."""
    data = read_lockfile(project_dir)
    if not data:
        return
    pkgs = dict(data.get("packages", {}))
    if name in pkgs:
        del pkgs[name]
        write_lockfile(project_dir, pkgs)


def _install_registry(libname, req_version=None):
    """Shared registry install path. Returns (name, version, repo_url, sha)."""
    index = fetch_index()
    libs = index.get("libraries", {})
    if libname not in libs:
        eprint(f"error: Library '{libname}' not found in the package index")
        eprint("  Run 'leashed search' to find available libraries.")
        eprint("  Or install directly from a URL: leashed install https://github.com/user/lib.git")
        sys.exit(1)
    entry = get_index_entry(index, libname)
    if req_version is not None:
        versions = entry.get("versions", {})
        if not isinstance(versions, dict):
            eprint(f"error: Registry version list for '{libname}' is corrupt")
            sys.exit(1)
        info = versions.get(req_version)
        if not isinstance(info, dict):
            info = None
        if info is None and req_version != entry.get("version"):
            available = ", ".join(sorted_versions(list(versions.keys()))[:10]) or "none"
            eprint(f"error: '{libname}' has no published version {req_version}")
            eprint(f"  Available versions: {available}")
            sys.exit(1)
        repo_url = (info or {}).get("repo") or entry.get("repo", "")
        n, v, sha = _install_from_repo(repo_url, requested_version=req_version, libname=libname)
        return n, v, repo_url, sha
    repo_url = entry.get("repo", "")
    ver = entry.get("version", "?")
    author = display_safe(entry.get("author", "?"))
    desc = display_safe(entry.get("description", ""))
    print(f"[leashed] Found {libname} v{ver} by {author}")
    if desc:
        print(f"  {desc}")
    n, v, sha = _install_from_repo(repo_url, libname=libname)
    return n, v, repo_url, sha


def cmd_install_project_deps(project_dir, strict):
    """'leashed install' with no target inside a project: restore
    dependencies from leash.lock when present (reproducible build), else
    resolve the dependencies listed in leash-pkg.lshc and write the lock."""
    lock = read_lockfile(project_dir)
    if lock is not None and strict and not lock.get("packages"):
        eprint(f"error: {LOCKFILE} contains no packages (--locked)")
        sys.exit(1)
    if strict and lock is None:
        eprint(f"error: --locked requires a {LOCKFILE} in the project (run 'leashed lock')")
        sys.exit(1)
    if lock and lock.get("packages"):
        pkgs = lock["packages"]
        print(f"[leashed] Restoring {len(pkgs)} locked package(s) from {LOCKFILE}...")
        for name in sorted(pkgs):
            entry = pkgs[name]
            if not isinstance(entry, dict):
                eprint(f"error: {LOCKFILE} entry for '{name}' is corrupt")
                sys.exit(1)
            version = entry.get("version", "")
            repo = entry.get("repo", "")
            if not validate_version(version) or not repo:
                eprint(f"error: {LOCKFILE} entry for '{name}' is missing version/repo")
                sys.exit(1)
            rev = entry.get("rev", "")
            if rev and not (isinstance(rev, str) and re.fullmatch(r'[0-9a-f]{40}', rev)):
                eprint(f"error: {LOCKFILE} entry for '{name}' has a malformed revision pin")
                sys.exit(1)
            print(f"[leashed] Installing {name}@{version} (locked)...")
            _install_from_repo(repo, requested_version=version,
                               libname=validate_name(name),
                               expect_sha=rev or None)
        print(f"[leashed] Done — environment matches {LOCKFILE}")
        return

    config_path = os.path.join(project_dir, LEASHED_CONFIG)
    if not os.path.exists(config_path):
        eprint(f"error: No {LEASHED_CONFIG} or {LOCKFILE} in '{project_dir}'")
        eprint("  Usage: leashed install <name|name@version|git-url|user/repo>")
        sys.exit(1)
    config = read_pkg_config(config_path)
    deps = config.get("dependencies", "")
    deps_list = [d.strip() for d in deps.split(",") if d.strip()]
    if not deps_list:
        print("[leashed] Project has no dependencies")
        return
    resolved = {}
    for dep in deps_list:
        if "@" in dep:
            name, _, ver = dep.partition("@")
        else:
            name, ver = dep, None
        name = validate_name(name.strip())
        ver = ver.strip() if ver else None
        if ver is not None and not validate_version(ver):
            eprint(f"error: Invalid version '{ver}' for dependency '{name}'")
            sys.exit(1)
        print(f"[leashed] Resolving {name}...")
        _n, v, repo, sha = _install_registry(name, ver)
        resolved[name] = {"version": v, "repo": repo, "rev": sha}
    write_lockfile(project_dir, resolved)
    print(f"[leashed] Installed {len(resolved)} package(s) and wrote {LOCKFILE}")


def _read_installed_pkg_config(dest_root):
    pkg_config_path = os.path.join(dest_root, PACKAGE_CONFIG)
    pkg = {}
    if os.path.exists(pkg_config_path):
        try:
            with open(pkg_config_path, "r", encoding="utf-8") as f:
                pkg = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return pkg


def _global_bin_dir():
    """Directory for globally installed command binaries (sibling of libs)."""
    return os.path.join(os.path.dirname(LEASH_LIBS_DIR), "bin")


def _parse_global_install_map(raw):
    """Parse a single-line `{name: src, ...}` map (quotes tolerated).

    Returns a {name: src} dict. Raises ValueError on malformed entries;
    empty parts (e.g. trailing commas) are skipped silently.
    """
    s = raw.strip()
    if not s.startswith("{"):
        raise ValueError("must be a single-line {name: src, ...} map")
    end = s.rfind("}")
    if end < 1:
        raise ValueError("must be a single-line {name: src, ...} map")
    body = s[1:end]
    parts, cur, in_q = [], "", False
    for ch in body:
        if ch == '"':
            in_q = not in_q
            cur += ch
        elif ch == "," and not in_q:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    out = {}
    for p in parts:
        p = p.strip()
        if not p:
            continue
        ci, q2 = 0, False
        while ci < len(p):
            c = p[ci]
            if c == '"':
                q2 = not q2
            elif c == ":" and not q2:
                break
            ci += 1
        if ci >= len(p):
            raise ValueError(f"malformed entry {p!r} (expected name: src)")
        name = p[:ci].strip()
        src = p[ci + 1:].strip()
        if len(name) >= 2 and name.startswith('"') and name.endswith('"'):
            name = name[1:-1]
        if len(src) >= 2 and src.startswith('"') and src.endswith('"'):
            src = src[1:-1]
        if not name or not src:
            raise ValueError(f"malformed entry {p!r} (expected name: src)")
        out[name] = src
    return out


def _map_tool_sources(project_dir, src_root_real, tool_map):
    """Map publisher-declared tool sources to library-relative paths.

    Tool sources must live under the main source tree (only published
    files are installable). Returns {name: lib_rel_path}. Raises
    ValueError with a clear message on any violation.
    """
    mapped = {}
    for tname, tsrc in tool_map.items():
        if not re.match(r'^[A-Za-z_][A-Za-z0-9_-]*$', tname or ""):
            raise ValueError(f"invalid tool name {tname!r} (letters, digits, _, -)")
        if not isinstance(tsrc, str) or not tsrc:
            raise ValueError(f"invalid source for tool {tname!r}")
        abs_p = os.path.realpath(os.path.join(project_dir, tsrc))
        if abs_p != src_root_real and not abs_p.startswith(src_root_real + os.sep):
            raise ValueError(f"tool source '{tsrc}' must live under the main source tree")
        if not os.path.isfile(abs_p):
            raise ValueError(f"tool source not found: '{tsrc}'")
        mapped[tname] = os.path.relpath(abs_p, src_root_real).replace(os.sep, "/")
    return mapped


def _install_global_commands(dest_root, tools):
    """Build each {name: src} tool to the global bin dir. Returns (installed, failed)."""
    if not isinstance(tools, dict) or not tools:
        return [], []
    compiler = _leashc_compiler()
    if compiler is None:
        eprint("error: cannot build global commands: no self-hosted compiler available")
        return [], list(tools.keys())
    bindir = _global_bin_dir()
    os.makedirs(bindir, exist_ok=True)
    suffix = ".exe" if sys.platform == "win32" else ""
    installed, failed = [], []
    # keep a reference to the compiler for the loop body
    _compiler = compiler
    for tname, tsrc in tools.items():
        if not re.match(r'^[A-Za-z_][A-Za-z0-9_-]*$', tname or ""):
            eprint(f"error: skipping global command with invalid name: {tname!r}")
            failed.append(tname)
            continue
        if not isinstance(tsrc, str) or not tsrc:
            eprint(f"error: skipping global command '{tname}': invalid source")
            failed.append(tname)
            continue
        src_abs = os.path.realpath(os.path.join(dest_root, tsrc))
        if src_abs != dest_root and not src_abs.startswith(dest_root + os.sep):
            eprint(f"error: skipping global command '{tname}': source escapes the package")
            failed.append(tname)
            continue
        if not os.path.isfile(src_abs):
            eprint(f"error: skipping global command '{tname}': source not found")
            failed.append(tname)
            continue
        out = os.path.join(bindir, tname + suffix)
        if os.path.exists(out):
            print(f"[leashed] warning: overwriting existing global command '{tname}'")
        try:
            rc = subprocess.run(
                [_compiler, "compile", src_abs, "to", out, "-oi", dest_root],
                capture_output=True, text=True, timeout=300).returncode
            if rc != 0:
                raise SystemExit(rc)
        except SystemExit as e:
            # compile_file reports its own diagnostics, then exits: a
            # broken tool must not take down the library install (nor the
            # remaining tools), but it stays loud.
            if e.code not in (None, 0):
                eprint(f"error: failed to build global command '{tname}'")
                failed.append(tname)
                continue
        except Exception as e:
            eprint(f"error: failed to build global command '{tname}': {e}")
            failed.append(tname)
            continue
        print(f"[leashed] Installed global command '{tname}' -> {out}")
        installed.append(out)
    if installed and bindir not in os.environ.get("PATH", "").split(os.pathsep):
        if sys.platform == "win32":
            print(f"  Run: setx PATH \"{bindir};%PATH%\" (then reopen the terminal)")
        else:
            print(f"  Run: export PATH=\"{bindir}:$PATH\"")
    return installed, failed


def _remove_global_commands(dest_root):
    """Remove binaries this package installed globally. Returns [removed paths]."""
    try:
        with open(os.path.join(dest_root, PACKAGE_CONFIG), "r", encoding="utf-8") as f:
            pkg = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(pkg, dict):
        return []
    tools = pkg.get("global_install", {})
    if not isinstance(tools, dict):
        return []
    bindir = _global_bin_dir()
    removed = []
    for tname in tools:
        if not isinstance(tname, str) or not re.match(r'^[A-Za-z_][A-Za-z0-9_-]*$', tname):
            continue
        for cand in (os.path.join(bindir, tname),
                     os.path.join(bindir, tname + ".exe")):
            if os.path.exists(cand):
                try:
                    os.remove(cand)
                    removed.append(cand)
                except OSError as e:
                    eprint(f"error: could not remove {cand}: {e}")
    return removed


def _write_stub(libname, version, author, desc, dest_root):
    entry_main = _read_installed_pkg_config(dest_root).get("main", "src/main.lsh")
    # The main file path in config is relative to project dir, but files are
    # copied to the library root (subdirectory stripped). Use just the filename.
    entry_module = os.path.splitext(os.path.basename(entry_main))[0]
    # A tampered package.lshc could set 'main' to a traversal payload like
    # "../../evil" — never let it influence the generated stub file.
    if not re.match(r'^[A-Za-z_][A-Za-z0-9_]*$', entry_module):
        print(f"[leashed] warning: skipping import stub: invalid module name "
              f"'{entry_module}' in installed package.lshc")
        return
    # Defense in depth: never trust the caller's metadata either.
    def _clean(s, default):
        s = s if isinstance(s, str) else default
        s = re.sub(r'[\r\n\x00-\x1f\x7f]', ' ', s).strip()
        return s or default
    author = _clean(author, "?")
    desc = _clean(desc, "")
    if not (isinstance(version, str) and validate_version(version)):
        version = "?"
    stub_path = os.path.join(LEASH_LIBS_DIR, f"{libname}.lsh")
    with open(stub_path, "w", encoding="utf-8") as f:
        f.write(f"// {libname} {version} by {author}\n")
        if desc:
            f.write(f"// {desc}\n")
        f.write(f"use {libname}::{entry_module}::*;\n")


def _looks_like_git_target(target):
    if "://" in target or target.endswith(".git"):
        return True
    # user/repo shorthand for github
    return bool(re.match(r'^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$', target)) and "::" not in target


def _install_from_repo(repo_url, requested_version=None, libname=None,
                     expect_sha=None):
    """Clone a library repo and install it into ~/.leash/libs.

    Works both for registry-published repos (library/ layout) and any plain
    git repo containing a package.lshc or .lsh sources at its root.

    Security: the URL is validated before cloning (no ext::/fd::/file://
    transports, no git option injection), and packages containing symlinks
    are refused (path traversal via copytree).

    Version integrity: when a version is requested, the `v{version}` ref
    must be a real tag (a same-named branch does not satisfy the request),
    its commit is resolved, an `expect_sha` pin (from leash.lock) is
    verified when given, and the installed package.lshc version must equal
    the requested one. Returns (libname, version, sha).
    """
    if requested_version is not None and not validate_version(requested_version):
        eprint(f"error: Invalid version '{requested_version}'. Use semver: X.Y.Z")
        sys.exit(1)
    # Registry-sourced URLs must be https; this also blocks ext::/fd::/file://
    # and leading-dash option injection. `allow_insecure` only ever matters
    # for URLs the user typed on the command line themselves.
    strict_https = libname is not None or requested_version is not None
    repo_url = validate_git_url(repo_url, allow_insecure=not strict_https)
    repo_tmp = tempfile.mkdtemp(prefix="leashed_repo_")
    try:
        clone_cmd = ["clone", "--depth", "1"]
        if requested_version:
            clone_cmd += ["--branch", f"v{requested_version}"]
        clone_cmd += [repo_url, repo_tmp]
        rc, _, err = run_git(clone_cmd)
        if rc != 0:
            if requested_version:
                eprint(f"error: Version v{requested_version} not found in '{repo_url}' ({err})")
            else:
                eprint(f"error: Failed to clone repository: {err}")
            sys.exit(1)

        # Resolve the tag to a commit SHA inside the fresh clone. A branch
        # named vX.Y.Z must not satisfy a version request, and a moved tag
        # must not silently resolve to different bytes than pinned.
        sha = ""
        if requested_version:
            tag_ref = f"refs/tags/v{requested_version}^{{commit}}"
            rc, out, err = run_git(["rev-parse", "--verify", tag_ref], cwd=repo_tmp)
            if rc != 0:
                eprint(f"error: Version v{requested_version} is not a tag in '{repo_url}' ({err})")
                sys.exit(1)
            sha = out.strip()
            if expect_sha and sha != expect_sha:
                eprint(f"error: Version v{requested_version} of '{repo_url}' does not match the locked revision")
                eprint(f"  Locked:   {expect_sha}")
                eprint(f"  Resolved: {sha}")
                eprint("  The tag may have been moved. Run 'leashed update' to re-pin, or verify the repository.")
                sys.exit(1)

        src = os.path.join(repo_tmp, LIBRARY_DIR)
        fallback_root = False
        if not os.path.exists(src):
            src = repo_tmp
            fallback_root = True

        pkg = _read_installed_pkg_config(src) if not fallback_root else {}
        if not pkg:
            pkg = _read_installed_pkg_config(repo_tmp)
        if not isinstance(pkg, dict):
            pkg = {}
        if not libname:
            libname = pkg.get("name", "")
        if not libname:
            base = os.path.basename(repo_url.rstrip("/"))
            if base.endswith(".git"):
                base = base[:-4]
            libname = re.sub(r'[^a-zA-Z0-9_-]', '_', base)
        libname = validate_name(libname)

        raw_version = pkg.get("version", None)
        if isinstance(raw_version, str) and validate_version(raw_version):
            version = raw_version
        else:
            version = requested_version if (isinstance(requested_version, str) and validate_version(requested_version)) else "?"
        mismatch = check_version_match(requested_version,
                                       version if version != "?" else None)
        if mismatch:
            eprint(f"error: {mismatch}")
            sys.exit(1)
        author = pkg.get("author", "?")
        desc = pkg.get("description", "")

        print(f"[leashed] Installing '{libname}' v{version} from {repo_url}")
        assert_no_symlinks(src)
        os.makedirs(LEASH_LIBS_DIR, exist_ok=True)
        dest_root = os.path.join(LEASH_LIBS_DIR, libname)
        # Atomic replacement: build the new tree next to the destination
        # and rename over it, so a failed copy (disk-full, crash) never
        # deletes a working install. `.git` metadata is never shipped.
        staging_root = dest_root + ".new"
        if os.path.exists(staging_root):
            shutil.rmtree(staging_root, onerror=_del_rw)
        try:
            shutil.copytree(src, staging_root,
                            ignore=shutil.ignore_patterns(".git"))
        except OSError as e:
            tmp_cleanup(staging_root)
            eprint(f"error: Failed to install '{libname}': {e}")
            sys.exit(1)
        if os.path.exists(dest_root):
            shutil.rmtree(dest_root, onerror=_del_rw)
        os.replace(staging_root, dest_root)

        _write_stub(libname, version, author, desc, dest_root)
        print(f"[leashed] Successfully installed '{libname}' v{version}")
        # Global commands declared by the package (`global_install` in its
        # manifest) compile to host binaries next to the package install.
        tools = pkg.get("global_install", {}) if isinstance(pkg, dict) else {}
        if isinstance(tools, dict) and tools:
            _install_global_commands(dest_root, tools)
        return libname, version, sha
    finally:
        tmp_cleanup(repo_tmp)


def _declare_dependency(project_dir, libname, version):
    """Append `name@version` to leash-pkg.lshc dependencies unless present."""
    config_path = os.path.join(project_dir, LEASHED_CONFIG)
    config = read_pkg_config(config_path)
    deps = config.get("dependencies", "")
    deps_list = [d.strip() for d in deps.split(",") if d.strip()]
    existing = {d.split("@", 1)[0] for d in deps_list}
    if libname in existing:
        return False
    deps_list.append(f"{libname}@{version}")
    config["dependencies"] = ", ".join(deps_list)
    write_pkg_config(config_path, config)
    return True


def cmd_install(args):
    locked_strict = False
    args = list(args)
    while "--locked" in args:
        args.remove("--locked")
        locked_strict = True

    if len(args) < 1:
        # No target: inside a project this restores the lockfile /
        # resolves leash-pkg.lshc dependencies (and writes the lockfile).
        project_dir = _project_dir_or_none()
        if project_dir is None:
            eprint("Usage: leashed install <library_name>[@<version> | <git-url> | <user>/<repo>]")
            sys.exit(1)
        cmd_install_project_deps(project_dir, strict=locked_strict)
        return
    if len(args) > 1:
        print(f"[leashed] warning: ignoring extra arguments: {' '.join(args[1:])}")
    target = args[0].strip()

    # 1) Direct git URL or user/repo shorthand — decentralized, no registry needed
    if _looks_like_git_target(target):
        url = target
        if "://" not in url and not url.endswith(".git"):
            url = f"https://github.com/{url}.git"
        n, v, s = _install_from_repo(url)
        project_dir = _project_dir_or_none()
        if project_dir is not None:
            lockfile_record(project_dir, n, v, url, s or None)
            if _declare_dependency(project_dir, n, v):
                print(f"[leashed] Added '{n}' ({v}) as a dependency")
        return

    # 2) Registry lookup: name or name@version
    if "@" in target:
        libname, _, req_version = target.partition("@")
        libname = validate_name(libname.strip())
        if not validate_version(req_version):
            eprint(f"error: Invalid version '{req_version}'. Use semver: X.Y.Z")
            sys.exit(1)
    else:
        libname = validate_name(target)
        req_version = None

    project_dir = _project_dir_or_none()

    if locked_strict and project_dir is None:
        eprint(f"error: --locked requires a project directory ({LEASHED_CONFIG} or {LOCKFILE})")
        sys.exit(1)

    # The lockfile pins the version when present. `leashed install --locked`
    # turns "no pin" into an error: CI reproducibility.
    if req_version is None and project_dir is not None:
        lock = read_lockfile(project_dir)
        lock_entry = None
        if lock:
            lock_entry = lock.get("packages", {}).get(libname)
        if lock_entry is not None and not isinstance(lock_entry, dict):
            eprint(f"error: {LOCKFILE} entry for '{libname}' is corrupt; refusing to use it")
            sys.exit(1)
        if lock_entry is not None:
            pin = lock_entry.get("version", "")
            repo = lock_entry.get("repo", "")
            rev = lock_entry.get("rev", "")
            if validate_version(pin) and repo:
                print(f"[leashed] Using locked version {libname}@{pin} from {LOCKFILE}")
                _n, v, s = _install_from_repo(repo, requested_version=pin,
                                              libname=libname,
                                              expect_sha=rev or None)
                lockfile_record(project_dir, libname, v, repo, s or None)
                return
            # malformed lockfile entry: fall through and re-resolve below
            eprint(f"warning: {LOCKFILE} entry for '{libname}' is incomplete — resolving from registry")
        elif locked_strict:
            eprint(f"error: '{libname}' is not in {LOCKFILE} (--locked)")
            eprint(f"  Run 'leashed add {libname}' or 'leashed lock' first.")
            sys.exit(1)

    print(f"[leashed] Looking up '{libname}'...")
    if req_version is not None and project_dir is not None:
        old_pin = lockfile_pin(project_dir, libname)
        if old_pin is not None and old_pin != req_version:
            print(f"[leashed] warning: explicit {libname}@{req_version} "
                  f"overrides the locked version {libname}@{old_pin}")
    _n, v, repo, s = _install_registry(libname, req_version)
    if project_dir is not None:
        lockfile_record(project_dir, libname, v, repo, s or None)


def cmd_uninstall(args):
    if len(args) < 1:
        eprint("Usage: leashed uninstall <library_name>")
        sys.exit(1)
    if len(args) > 1:
        print(f"[leashed] warning: ignoring extra arguments: {' '.join(args[1:])}")
    libname = validate_name(args[0])
    dest_root = os.path.join(LEASH_LIBS_DIR, libname)
    stub_path = os.path.join(LEASH_LIBS_DIR, f"{libname}.lsh")
    if not os.path.exists(dest_root) and not os.path.exists(stub_path):
        eprint(f"error: Library '{libname}' is not installed")
        sys.exit(1)
    # Global commands installed with the package go away with it.
    for gone in _remove_global_commands(dest_root):
        print(f"[leashed] Removed global command '{gone}'")
    if os.path.exists(dest_root):
        shutil.rmtree(dest_root, onerror=_del_rw)
    if os.path.exists(stub_path):
        os.remove(stub_path)
    project_dir = _project_dir_or_none()
    if project_dir is not None:
        lockfile_unpin(project_dir, libname)
    print(f"[leashed] Uninstalled '{libname}'")


def cmd_list(args):
    if args:
        print(f"[leashed] warning: ignoring extra arguments: {' '.join(args)}")
    if not os.path.isdir(LEASH_LIBS_DIR):
        print("[leashed] No libraries installed (~/.leash/libs does not exist)")
        return
    entries = []
    for name in sorted(os.listdir(LEASH_LIBS_DIR)):
        dest_root = os.path.join(LEASH_LIBS_DIR, name)
        if not os.path.isdir(dest_root):
            continue
        pkg = _read_installed_pkg_config(dest_root)
        version = pkg.get("version", "?")
        desc = pkg.get("description", "")
        entries.append((name, version, desc))
    if not entries:
        print("[leashed] No libraries installed")
        return
    print(f"[leashed] Installed libraries in {LEASH_LIBS_DIR}:")
    for name, version, desc in entries:
        print(f"  - {display_safe(name)} v{display_safe(version)}" + (f"  {display_safe(desc)}" if desc else ""))


def cmd_info(args):
    if len(args) < 1:
        eprint("Usage: leashed info <library_name>")
        sys.exit(1)
    if len(args) > 1:
        print(f"[leashed] warning: ignoring extra arguments: {' '.join(args[1:])}")
    libname = validate_name(args[0])
    index = fetch_index()
    libs = index.get("libraries", {})
    if libname not in libs:
        eprint(f"error: Library '{libname}' not found in the package index")
        sys.exit(1)
    e = get_index_entry(index, libname)
    print(f"{display_safe(libname)}")
    print(f"  Latest version: {display_safe(e.get('version', '?'))}")
    print(f"  Author:         {display_safe(e.get('author', '?'))}")
    if e.get("publisher") and e["publisher"] != e.get("author"):
        print(f"  Publisher:      {display_safe(e['publisher'])}")
    if e.get("description"):
        print(f"  Description:    {display_safe(e['description'])}")
    print(f"  Repo:           {display_safe(e.get('repo', '?'))}")
    versions = sorted_versions(list((e.get("versions") or {}).keys()))
    if versions:
        print(f"  Versions:       {', '.join(versions)}")
    else:
        print(f"  Versions:       {e.get('version', '?')} (index predates version history)")


def cmd_update(args):
    project_dir = os.getcwd()
    config_path = os.path.join(project_dir, LEASHED_CONFIG)
    in_project = os.path.exists(config_path)

    targets = list(args)
    update_deps_field = False
    if not targets:
        if not in_project:
            eprint("Usage: leashed update [<name> ...]")
            eprint("  (run inside a project to update all of its dependencies)")
            sys.exit(1)
        config = read_pkg_config(config_path)
        deps = config.get("dependencies", "")
        targets = [d.split("@", 1)[0].strip() for d in deps.split(",") if d.strip()]
        update_deps_field = bool(targets)
        if not targets:
            print("[leashed] Project has no dependencies to update")
            return
        print(f"[leashed] Updating all project dependencies: {', '.join(targets)}")

    new_versions = {}
    new_repos = {}
    new_shas = {}
    for t in targets:
        want = None
        if "@" in t:
            # An explicit pin is an exact install, not an update: silently
            # installing another version than asked for breaks pinning.
            t, _, want = t.partition("@")
            want = want.strip()
            if not validate_version(want):
                eprint(f"error: Invalid version '{want}' for '{t.strip()}'")
                sys.exit(1)
        libname = validate_name(t.strip())
        if want is not None:
            _, version, repo, sha = _install_registry(libname, want)
        else:
            current = None
            inst_pkg = _read_installed_pkg_config(os.path.join(LEASH_LIBS_DIR, libname))
            if isinstance(inst_pkg, dict) and validate_version(inst_pkg.get("version", "")):
                current = inst_pkg["version"]
            if current is None:
                pin = lockfile_pin(project_dir if in_project else os.getcwd(), libname)
                if pin:
                    current = pin
            _, version, repo, sha = _install_registry(libname)
            if current is not None and not is_newer_version(version, current):
                print(f"[leashed] '{libname}' is already up to date ({current}); skipping")
                continue
        new_versions[libname] = version
        new_repos[libname] = repo
        new_shas[libname] = sha

    if update_deps_field:
        config = read_pkg_config(config_path)
        kept_deps = []
        for d in config.get("dependencies", "").split(","):
            d = d.strip()
            if not d:
                continue
            dn = d.split("@", 1)[0]
            if dn in new_versions and new_versions[dn] != "?":
                kept_deps.append(f"{dn}@{new_versions[dn]}")
            else:
                kept_deps.append(d)
        config["dependencies"] = ", ".join(kept_deps)
        write_pkg_config(config_path, config)
        print(f"[leashed] Updated dependencies in {LEASHED_CONFIG}")

    if in_project or os.path.exists(os.path.join(project_dir, LOCKFILE)):
        # Only keep the lockfile in sync for actual project dependencies;
        # `leashed update otherpkg` outside the dep list must not add it.
        dep_names = set()
        if in_project:
            config = read_pkg_config(config_path)
            dep_names = {d.split("@", 1)[0] for d in config.get("dependencies", "").split(",") if d.strip()}
        for n, v in new_versions.items():
            if v != "?" and (update_deps_field or n in dep_names):
                lockfile_record(project_dir, n, v, new_repos.get(n, ""),
                                new_shas.get(n) or None)


def cmd_lock(args):
    """Resolve the project's dependencies, install them, and write leash.lock."""
    project_dir = os.getcwd()
    config_path = os.path.join(project_dir, LEASHED_CONFIG)
    if not os.path.exists(config_path):
        eprint(f"error: No '{LEASHED_CONFIG}' found in '{project_dir}'")
        eprint("  Run 'leashed init' first or change to a leash package directory")
        sys.exit(1)
    config = read_pkg_config(config_path)
    deps = config.get("dependencies", "")
    deps_list = [d.strip() for d in deps.split(",") if d.strip()]
    if not deps_list:
        eprint("error: No dependencies to lock.")
        eprint(f"  Add libraries first: leashed add <name>")
        sys.exit(1)
    resolved = {}
    for dep in deps_list:
        if "@" in dep:
            name, _, ver = dep.partition("@")
        else:
            name, ver = dep, None
        name = validate_name(name.strip())
        ver = ver.strip() if ver else None
        if ver is not None and not validate_version(ver):
            eprint(f"error: Invalid version '{ver}' for dependency '{name}'")
            sys.exit(1)
        print(f"[leashed] Locking {name}...")
        _n, v, repo, sha = _install_registry(name, ver)
        resolved[name] = {"version": v, "repo": repo, "rev": sha}
    write_lockfile(project_dir, resolved)
    kept = sum(1 for r in resolved.values()
               if validate_version(r.get("version", "")))
    print(f"[leashed] Locked {kept} package(s) in {LOCKFILE}")
    print(f"[leashed] Commit {LOCKFILE} — fresh clones restore it with 'leashed install'")


def cmd_add(args):
    if len(args) < 1:
        eprint("Usage: leashed add <library_name>")
        sys.exit(1)
    if len(args) > 1:
        print(f"[leashed] warning: ignoring extra arguments: {' '.join(args[1:])}")
    libname = validate_name(args[0])
    project_dir = os.getcwd()
    config_path = os.path.join(project_dir, LEASHED_CONFIG)
    if not os.path.exists(config_path):
        eprint(f"error: No '{LEASHED_CONFIG}' found in current directory")
        eprint("  Run 'leashed init' first or change to a leash package directory")
        sys.exit(1)
    config = read_pkg_config(config_path)

    installed_version = None
    installed_repo = ""
    installed_sha = None
    if not os.path.exists(os.path.join(LEASH_LIBS_DIR, f"{libname}.lsh")):
        print(f"[leashed] Library '{libname}' not installed globally. Installing first...")
        pin = lockfile_pin(project_dir, libname)
        if pin:
            print(f"[leashed] Using locked version {libname}@{pin} from {LOCKFILE}")
            _n, _v, _r, _s = _install_registry(libname, pin)
        else:
            _n, _v, _r, _s = _install_registry(libname)
        installed_version = _v
        installed_repo = _r
        installed_sha = _s or None
    else:
        # Already on disk: record what's actually installed, not what the
        # index advertises — otherwise the lock claims bytes that are not
        # present until the next restore.
        inst_pkg = _read_installed_pkg_config(os.path.join(LEASH_LIBS_DIR, libname))
        if isinstance(inst_pkg, dict) and validate_version(inst_pkg.get("version", "")):
            installed_version = inst_pkg["version"]
            print(f"[leashed] Library '{libname}' already installed (v{installed_version}); recording that version")
        else:
            print(f"[leashed] warning: could not determine the installed version of '{libname}'; installing the index version...")
            pin = lockfile_pin(project_dir, libname)
            if pin:
                _n, _v, _r, _s = _install_registry(libname, pin)
            else:
                _n, _v, _r, _s = _install_registry(libname)
            installed_version = _v
            installed_repo = _r
            installed_sha = _s or None

    version = "?"
    index = fetch_index()
    libs = index.get("libraries", {})
    if libname in libs and isinstance(libs[libname], dict):
        version = libs[libname].get("version", "?")
    if installed_version:
        version = installed_version

    if not _declare_dependency(project_dir, libname, version):
        print(f"[leashed] '{libname}' is already a dependency of this project")
        return

    main_file = config.get("main", "")
    if main_file:
        # Contain the target inside the project: a malicious `main:`
        # (absolute path, `..` segments, or a symlink pointing out)
        # must not turn `add` into an arbitrary-file rewrite.
        main_path = os.path.realpath(os.path.join(project_dir, main_file))
        if main_path != project_dir and not main_path.startswith(project_dir + os.sep):
            print(f"[leashed] warning: skipping 'use' insertion: '{main_file}' is outside the project")
        elif os.path.isfile(main_path):
            with open(main_path, "r", encoding="utf-8") as f:
                content = f.read()
            line = f"use {libname}::*; // added by leashed\n"
            if line not in content and f"use {libname}::" not in content:
                with open(main_path, "w", encoding="utf-8") as f:
                    f.write(line + content)
                print(f"[leashed] Added 'use {libname}::*;' to {main_file}")

    if version != "?":
        lockfile_record(project_dir, libname, version,
                        installed_repo or (libs[libname].get("repo", "") if libname in libs and isinstance(libs[libname], dict) else ""),
                        installed_sha)

    print(f"[leashed] Added '{libname}' ({version}) as a dependency")


def cmd_search(args):
    if len(args) < 1:
        eprint("Usage: leashed search <query>")
        sys.exit(1)
    if len(args) > 1:
        print(f"[leashed] warning: ignoring extra arguments: {' '.join(args[1:])}")
    query = args[0].lower()
    print(f"[leashed] Searching for '{query}'...")

    index = fetch_index()
    libs = index.get("libraries", {})

    matching = {}
    for k, v in libs.items():
        if not isinstance(v, dict):
            continue  # corrupt entry — never render or use it
        # A non-string description must not crash the search (M8).
        _desc = v.get("description", "")
        _desc = _desc if isinstance(_desc, str) else ""
        if query in k.lower() or query in _desc.lower():
            matching[k] = v

    if not matching:
        print(f"[leashed] No libraries found matching '{display_safe(query)}'")
        all_libs = list(libs.keys())[:15]
        if all_libs:
            print(f"  Available: {', '.join(display_safe(n) for n in all_libs)}")
            if len(libs) > 15:
                print(f"  ... and {len(libs) - 15} more")
        return

    print(f"[leashed] Found {len(matching)} library(ies):")
    for lib_name, info in sorted(matching.items()):
        ver = info.get("version", "?")
        author = info.get("author", "?")
        desc = info.get("description", "")
        print(f"  - {display_safe(lib_name)} v{display_safe(ver)} by {display_safe(author)}")
        if desc:
            print(f"    {display_safe(desc)}")


def usage():
    print(f"leashed v{LEASHED_VERSION}")
    print("Usage: leashed <command> [options]")
    print()
    print("Commands:")
    print("  init <path>       Initialize a new leash package project")
    print("  publish           Compile and publish the current package")
    print("                    (registered automatically, no human review)")
    print("  install [<target>]  Install a library globally (~/.leash/libs)")
    print("                    <name>, <name>@1.2.3, <user>/<repo> or a git URL;")
    print("                    with no target (inside a project), restores the")
    print("                    versions pinned in leash.lock")
    print("  uninstall <name>  Remove an installed library")
    print("  list              List installed libraries")
    print("  info <name>       Show registry metadata for a library")
    print("  update [names]    Update installed libs / all project dependencies")
    print("  add <name>        Add a library to the current project")
    print("  lock              Resolve dependencies and write leash.lock")
    print("  search <query>    Search for libraries")
    print()
    print("Global Options:")
    print("  --locked (install) Fail instead of resolving a version that is")
    print("                    not pinned in leash.lock (CI reproducibility)")
    print("  --verbose/-vb     Enable verbose output")
    print()
    print("Environment:")
    print("  LEASHED_REGISTRY_REPO   Use a custom registry (owner/repo)")
    print(f"                          (default: {DEFAULT_REGISTRY_REPO})")
    print("  LEASHED_REGISTRY_URL    Custom index.json URL")
    print("  LEASHED_REGISTRY_GIT    Custom registry git URL")


def main():
    global VERBOSE
    for arg in list(sys.argv):
        if arg in ("--verbose", "-vb"):
            VERBOSE = True
            sys.argv.remove(arg)
    if len(sys.argv) < 2:
        usage()
        sys.exit(1)
    cmd = sys.argv[1]
    cmd_args = sys.argv[2:]
    if cmd in ("--help", "-h"):
        usage()
        sys.exit(0)
    if cmd in ("--version", "-v"):
        print(f"leashed v{LEASHED_VERSION}")
        sys.exit(0)
    table = {
        "init": cmd_init,
        "publish": cmd_publish,
        "install": cmd_install,
        "uninstall": cmd_uninstall,
        "list": cmd_list,
        "info": cmd_info,
        "update": cmd_update,
        "add": cmd_add,
        "lock": cmd_lock,
        "search": cmd_search,
    }
    fn = table.get(cmd)
    if fn:
        fn(cmd_args)
    else:
        eprint(f"Unknown command: {cmd}")
        usage()
        sys.exit(1)


if __name__ == "__main__":
    main()
