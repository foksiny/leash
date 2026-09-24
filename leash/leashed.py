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

LEASHED_VERSION = "0.3.0"
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
_SAFE_GIT_SCHEMES = ("https://", "ssh://", "git://")


def validate_git_url(url, allow_insecure=False, source="repository"):
    """Validate a git URL before it is ever handed to `git clone`.

    Returns the (stripped) URL or exits with an error. With
    `allow_insecure=True` (for URLs the user typed themselves), plain http is
    permitted but warned about; URLs coming from the registry must be https.
    """
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
        if scheme == "http://":
            if not allow_insecure:
                eprint(f"error: Insecure {source} URL rejected: '{u}' (registry entries must use https).")
                sys.exit(1)
            print("[leashed] warning: using an unencrypted http:// git URL — "
                  "the download can be tampered with in transit.")
        elif scheme not in _SAFE_GIT_SCHEMES:
            eprint(f"error: Unsafe {source} URL rejected: '{u}'")
            eprint("  Only https://, ssh:// and git:// URLs are allowed. Git "
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


def get_identity():
    rc, out, _ = run_gh(["api", "user", "--jq", ".login"], required=False)
    if rc == 0 and out:
        return out
    rc, out, _ = run_git(["config", "--global", "user.name"])
    if rc == 0 and out:
        return out
    rc, out, _ = run_git(["config", "--global", "user.email"])
    if rc == 0 and out:
        return out
    return None


def get_gh_user():
    rc, out, _ = run_gh(["api", "user", "--jq", ".login"], required=False)
    return out if rc == 0 else None


def fetch_index():
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
            return {"libraries": {}}
        eprint(f"error: Failed to fetch package index (HTTP {e.code})")
        sys.exit(1)
    except (urllib.error.URLError, json.JSONDecodeError, UnicodeDecodeError) as e:
        eprint(f"error: Failed to fetch package index: {e}")
        sys.exit(1)
    if not isinstance(index, dict) or not isinstance(index.get("libraries", {}), dict):
        eprint("error: Package index has an invalid format (expected {\"libraries\": {...}})")
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
    with open(path, "w", encoding="utf-8") as f:
        f.write("# Leashed package configuration\n")
        for key in ["name", "version", "author", "description", "main", "repo", "dependencies", "imports"]:
            val = config.get(key)
            if val:
                f.write(f'{key}: "{val}"\n')


def run_leash_check(filepath, extra_import_dirs=None):
    try:
        from leash.cli import check_file
        errs, warns = check_file(filepath, verbose=VERBOSE, extra_import_dirs=extra_import_dirs)
        if errs:
            eprint(f"error: {len(errs)} error(s) found in source code. Fix them before publishing.")
            for e in errs:
                eprint(f"  {e}")
            sys.exit(1)
        if warns and VERBOSE:
            print(f"[leashed] {len(warns)} warning(s) found (continuing)")
        return True
    except ImportError:
        cmd = [sys.executable, "-m", "leash.cli", "check", filepath]
        if extra_import_dirs:
            for d in extra_import_dirs:
                cmd.extend(["-oi", d])
        rc = subprocess.run(cmd, capture_output=True, text=True)
        if rc.returncode != 0:
            eprint("error: Source code check failed. Fix errors before publishing.")
            eprint(rc.stderr)
            sys.exit(1)
        return True


def run_leash_compile(filepath, output_stem, extra_import_dirs=None):
    try:
        from leash.cli import compile_file
        compile_file(
            filepath,
            output_name=output_stem,
            output_type="static",
            is_run_mode=False,
            extra_import_dirs=extra_import_dirs,
        )
        return True
    except ImportError:
        cmd = [sys.executable, "-m", "leash.cli", "compile", filepath, "to-static", output_stem]
        if extra_import_dirs:
            for d in extra_import_dirs:
                cmd.extend(["-oi", d])
        rc = subprocess.run(cmd, capture_output=True, text=True)
        if rc.returncode != 0:
            eprint("error: Compilation failed.")
            eprint(rc.stderr)
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


def cmd_publish(args):
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
    # Make relative paths absolute from project_dir
    if extra_import_dirs:
        extra_import_dirs = [os.path.join(project_dir, d) if not os.path.isabs(d) else d for d in extra_import_dirs]

    # Pre-flight registry check: ownership + strictly increasing version.
    # Fails fast before any compiling/pushing so mistakes cost nothing.
    index = fetch_index()
    libs = index.get("libraries", {})
    existing_entry = libs.get(name)
    if existing_entry is not None and not isinstance(existing_entry, dict):
        eprint(f"error: Registry entry for '{name}' is corrupt; cannot publish over it.")
        sys.exit(1)
    if existing_entry:
        reg_owner = existing_entry.get("publisher") or existing_entry.get("author", "")
        if reg_owner not in (publisher, author):
            eprint(f"error: Library '{name}' is already registered by '{reg_owner}'.")
            eprint("  Only the original owner can update a package. Pick another name.")
            sys.exit(1)
        old_version = existing_entry.get("version", "0.0.0")
        if not is_newer_version(version, old_version):
            eprint(f"error: Version {version} is not greater than the published version {old_version}.")
            eprint("  Bump the 'version' field in leash-pkg.lshc and try again.")
            sys.exit(1)

    print(f"[leashed] Publishing '{name}' v{version} by {author}")
    print(f"[leashed] Publisher: {publisher}")

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
    with open(os.path.join(lib_out, PACKAGE_CONFIG), "w", encoding="utf-8") as f:
        json.dump(pkg_config, f, indent=2)
    with open(os.path.join(lib_out, PUBLISHER_FILE), "w", encoding="utf-8") as f:
        f.write(publisher + "\n")

    print("[leashed] Library compiled successfully")

    # Step 3: Determine or create the library repo
    repo_url = config.get("repo", "")
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
        # Save repo URL to config for future publishes
        config["repo"] = repo_url
        write_pkg_config(config_path, config)
        print(f"[leashed] Saved 'repo' to {LEASHED_CONFIG}")

    # Step 4: Push source + compiled library to the library repo
    repo_tmp = tempfile.mkdtemp(prefix="leashed_repo_")
    rc, _, err = run_git(["init"], cwd=repo_tmp)
    if rc != 0:
        eprint(f"error: Git init failed: {err}")
        tmp_cleanup(out_dir)
        tmp_cleanup(repo_tmp)
        sys.exit(1)

    # Copy everything into the temp repo
    for item in os.listdir(project_dir):
        if item == ".git" or item == "__pycache__" or item == "out":
            continue
        sp = os.path.join(project_dir, item)
        dp = os.path.join(repo_tmp, item)
        if os.path.isfile(sp):
            shutil.copy2(sp, dp)
        elif os.path.isdir(sp):
            shutil.copytree(sp, dp)

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

    # Try to fetch default branch to see if repo already has content
    rc, _, _ = run_git(["fetch", "--depth", "1", "origin", "main"], cwd=repo_tmp)
    has_main = rc == 0
    if not has_main:
        rc, _, _ = run_git(["fetch", "--depth", "1", "origin", "master"], cwd=repo_tmp)
        has_master = rc == 0
    else:
        has_master = False

    default_branch = "main"
    if has_main:
        rc, _, err = run_git(["checkout", "-b", "main", "origin/main"], cwd=repo_tmp)
        if rc != 0:
            rc, _, err = run_git(["checkout", "main"], cwd=repo_tmp)
    elif has_master:
        default_branch = "master"
        rc, _, err = run_git(["checkout", "-b", "master", "origin/master"], cwd=repo_tmp)
        if rc != 0:
            rc, _, err = run_git(["checkout", "master"], cwd=repo_tmp)
    else:
        # New repo — use main
        rc, _, err = run_git(["checkout", "-b", "main"], cwd=repo_tmp)

    # Re-add everything (fetch/checkout might have reset)
    shutil.copytree(os.path.join(out_dir, LIBRARY_DIR), os.path.join(repo_tmp, LIBRARY_DIR), dirs_exist_ok=True)
    rc, _, err = run_git(["add", "-A"], cwd=repo_tmp)
    if rc != 0:
        eprint(f"error: Git add failed: {err}")
        tmp_cleanup(out_dir)
        tmp_cleanup(repo_tmp)
        sys.exit(1)

    msg = f"Publish {name} v{version}\n\nPublisher: {publisher}\nDescription: {description}"
    rc, _, err = run_git(["-c", "user.name=leashed", "-c", "user.email=leashed@localhost", "commit", "-m", msg], cwd=repo_tmp)
    if rc != 0:
        eprint(f"error: Git commit failed: {err}")
        tmp_cleanup(out_dir)
        tmp_cleanup(repo_tmp)
        sys.exit(1)

    print(f"[leashed] Pushing to '{repo_url}'...")
    rc, out, err = run_git(["push", "--force", "-u", "origin", default_branch], cwd=repo_tmp)
    if rc != 0:
        eprint(f"error: Failed to push: {err}")
        eprint("  Make sure you have write access to the repository.")
        tmp_cleanup(out_dir)
        tmp_cleanup(repo_tmp)
        sys.exit(1)

    # Tag this release so `leashed install name@version` can fetch it
    tag = f"v{version}"
    run_git(["tag", "-f", tag], cwd=repo_tmp)
    rc, _, err = run_git(["push", "--force", "origin", tag], cwd=repo_tmp)
    if rc != 0:
        eprint(f"warning: Could not push version tag '{tag}': {err}")
        eprint("  The library was published; installing by exact version may not work.")

    print(f"[leashed] Successfully published '{name}' v{version}!")
    print(f"[leashed]   Repo: {repo_url}")

    # Step 5: Register in the central registry
    is_owner = (gh_user == REGISTRY_OWNER)

    print(f"[leashed] Registering '{name}' in the package index...")
    reg_tmp = tempfile.mkdtemp(prefix="leashed_reg_")

    if is_owner:
        # Push directly to the upstream repo (no fork needed for owner)
        rc, _, err = run_git(["clone", "--depth", "1", REGISTRY_GIT, reg_tmp])
        if rc != 0:
            # Repo might be empty (no commits yet)
            rc, _, err = run_git(["init"], cwd=reg_tmp)
            if rc != 0:
                eprint(f"error: Failed to init registry repo: {err}")
                tmp_cleanup(out_dir)
                tmp_cleanup(repo_tmp)
                tmp_cleanup(reg_tmp)
                sys.exit(1)
            rc, _, err = run_git(["remote", "add", "origin", REGISTRY_GIT], cwd=reg_tmp)
            if rc != 0:
                eprint(f"error: Failed to add remote: {err}")
                tmp_cleanup(out_dir)
                tmp_cleanup(repo_tmp)
                tmp_cleanup(reg_tmp)
                sys.exit(1)
            push_branch = "main"
        else:
            rc, _, _ = run_git(["fetch", "--depth", "1", "origin", "main"], cwd=reg_tmp)
            has_main = rc == 0
            if has_main:
                run_git(["checkout", "-b", "main", "origin/main"], cwd=reg_tmp)
                push_branch = "main"
            else:
                rc, _, _ = run_git(["fetch", "--depth", "1", "origin", "master"], cwd=reg_tmp)
                if rc == 0:
                    run_git(["checkout", "-b", "master", "origin/master"], cwd=reg_tmp)
                    push_branch = "master"
                else:
                    push_branch = "main"
    else:
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
        run_gh(["repo", "sync", f"{gh_user}/{REGISTRY_REPO.split('/')[1]}",
                "--source", REGISTRY_REPO], required=False)

        fork_url = f"https://github.com/{gh_user}/leash-packages.git"
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
            run_git(["checkout", "-b", f"register-{name}", "upstream/main"], cwd=reg_tmp)
            push_branch = f"register-{name}"
        else:
            rc, _, _ = run_git(["fetch", "--depth", "1", "upstream", "master"], cwd=reg_tmp)
            if rc == 0:
                run_git(["checkout", "-b", f"register-{name}", "upstream/master"], cwd=reg_tmp)
                push_branch = f"register-{name}"
            else:
                run_git(["checkout", "-b", f"register-{name}"], cwd=reg_tmp)
                push_branch = f"register-{name}"

    # Read current index and add/update entry
    index_path = os.path.join(reg_tmp, "index.json")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            index = json.load(f)
    else:
        index = {"libraries": {}}

    if name in index.get("libraries", {}):
        existing_pub = index["libraries"][name].get("author", "")
        if existing_pub != author:
            eprint(f"error: Library '{name}' is already registered by '{existing_pub}'.")
            eprint("  Only the original author can update the registry entry.")
            tmp_cleanup(out_dir)
            tmp_cleanup(repo_tmp)
            tmp_cleanup(reg_tmp)
            sys.exit(1)

    if "libraries" not in index:
        index["libraries"] = {}
    # Keep all previously published versions so `install name@version` works
    old_versions = {}
    if existing_entry:
        old_versions = existing_entry.get("versions", {}) or {}
    old_versions[version] = {
        "repo": repo_url,
        "tag": tag,
        "published_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    index["libraries"][name] = {
        "repo": repo_url,
        "description": description,
        "author": author,
        "publisher": publisher,
        "version": version,
        "versions": old_versions,
    }

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

    rc, _, err = run_git(["push", "--force", "-u", "origin", push_branch], cwd=reg_tmp)
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
    `packages` maps name -> {"version", "repo"}."""
    out = {"version": LOCKFILE_VERSION, "packages": {}}
    for name in sorted(packages):
        p = packages[name] if isinstance(packages[name], dict) else {}
        version = p.get("version", "")
        if not validate_version(version):
            continue
        out["packages"][name] = {
            "version": version,
            "repo": p.get("repo", ""),
            "tag": p.get("tag") or f"v{version}",
        }
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


def lockfile_pin(project_dir, name):
    """Return the locked version of `name` when the project has an entry."""
    data = read_lockfile(project_dir)
    if not data:
        return None
    entry = data.get("packages", {}).get(name)
    if isinstance(entry, dict) and validate_version(entry.get("version", "")):
        return entry["version"]
    return None


def lockfile_record(project_dir, name, version, repo):
    """Insert or update one entry in the project lockfile."""
    if not validate_version(version):
        eprint(f"warning: not recording {name}@{version} in {LOCKFILE}: invalid version")
        return
    pkgs = {}
    data = read_lockfile(project_dir)
    if data:
        pkgs = dict(data.get("packages", {}))
    pkgs[name] = {"version": version, "repo": repo}
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
    """Shared registry install path. Returns (name, version, repo_url)."""
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
        n, v = _install_from_repo(repo_url, requested_version=req_version, libname=libname)
        return n, v, repo_url
    repo_url = entry.get("repo", "")
    ver = entry.get("version", "?")
    author = entry.get("author", "?")
    desc = entry.get("description", "")
    print(f"[leashed] Found {libname} v{ver} by {author}")
    if desc:
        print(f"  {desc}")
    n, v = _install_from_repo(repo_url, libname=libname)
    return n, v, repo_url


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
            print(f"[leashed] Installing {name}@{version} (locked)...")
            _install_from_repo(repo, requested_version=version, libname=validate_name(name))
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
        _n, v, repo = _install_registry(name, ver)
        resolved[name] = {"version": v, "repo": repo}
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


def _install_from_repo(repo_url, requested_version=None, libname=None):
    """Clone a library repo and install it into ~/.leash/libs.

    Works both for registry-published repos (library/ layout) and any plain
    git repo containing a package.lshc or .lsh sources at its root.

    Security: the URL is validated before cloning (no ext::/fd::/file://
    transports, no git option injection), and packages containing symlinks
    are refused (path traversal via copytree).
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

        version = pkg.get("version", requested_version or "?")
        author = pkg.get("author", "?")
        desc = pkg.get("description", "")

        print(f"[leashed] Installing '{libname}' v{version} from {repo_url}")
        assert_no_symlinks(src)
        os.makedirs(LEASH_LIBS_DIR, exist_ok=True)
        dest_root = os.path.join(LEASH_LIBS_DIR, libname)
        if os.path.exists(dest_root):
            shutil.rmtree(dest_root, onerror=_del_rw)
        shutil.copytree(src, dest_root)

        _write_stub(libname, version, author, desc, dest_root)
        print(f"[leashed] Successfully installed '{libname}' v{version}")
        return libname, version
    finally:
        tmp_cleanup(repo_tmp)


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
    target = args[0].strip()

    # 1) Direct git URL or user/repo shorthand — decentralized, no registry needed
    if _looks_like_git_target(target):
        url = target
        if "://" not in url and not url.endswith(".git"):
            url = f"https://github.com/{url}.git"
        _install_from_repo(url)
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
            if validate_version(pin) and repo:
                print(f"[leashed] Using locked version {libname}@{pin} from {LOCKFILE}")
                _n, v = _install_from_repo(repo, requested_version=pin, libname=libname)
                lockfile_record(project_dir, libname, v, repo)
                return
            # malformed lockfile entry: fall through and re-resolve below
            eprint(f"warning: {LOCKFILE} entry for '{libname}' is incomplete — resolving from registry")
        elif locked_strict:
            eprint(f"error: '{libname}' is not in {LOCKFILE} (--locked)")
            eprint(f"  Run 'leashed add {libname}' or 'leashed lock' first.")
            sys.exit(1)

    print(f"[leashed] Looking up '{libname}'...")
    _n, v, repo = _install_registry(libname, req_version)
    if project_dir is not None:
        lockfile_record(project_dir, libname, v, repo)


def cmd_uninstall(args):
    if len(args) < 1:
        eprint("Usage: leashed uninstall <library_name>")
        sys.exit(1)
    libname = validate_name(args[0])
    dest_root = os.path.join(LEASH_LIBS_DIR, libname)
    stub_path = os.path.join(LEASH_LIBS_DIR, f"{libname}.lsh")
    if not os.path.exists(dest_root) and not os.path.exists(stub_path):
        eprint(f"error: Library '{libname}' is not installed")
        sys.exit(1)
    if os.path.exists(dest_root):
        shutil.rmtree(dest_root, onerror=_del_rw)
    if os.path.exists(stub_path):
        os.remove(stub_path)
    project_dir = _project_dir_or_none()
    if project_dir is not None:
        lockfile_unpin(project_dir, libname)
    print(f"[leashed] Uninstalled '{libname}'")


def cmd_list(args):
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
        print(f"  - {name} v{version}" + (f"  {desc}" if desc else ""))


def cmd_info(args):
    if len(args) < 1:
        eprint("Usage: leashed info <library_name>")
        sys.exit(1)
    libname = validate_name(args[0])
    index = fetch_index()
    libs = index.get("libraries", {})
    if libname not in libs:
        eprint(f"error: Library '{libname}' not found in the package index")
        sys.exit(1)
    e = get_index_entry(index, libname)
    print(f"{libname}")
    print(f"  Latest version: {e.get('version', '?')}")
    print(f"  Author:         {e.get('author', '?')}")
    if e.get("publisher") and e["publisher"] != e.get("author"):
        print(f"  Publisher:      {e['publisher']}")
    if e.get("description"):
        print(f"  Description:    {e['description']}")
    print(f"  Repo:           {e.get('repo', '?')}")
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
    for t in targets:
        if "@" in t:
            t = t.split("@", 1)[0]
        libname = validate_name(t.strip())
        _, version, repo = _install_registry(libname)
        new_versions[libname] = version
        new_repos[libname] = repo

    if update_deps_field:
        config = read_pkg_config(config_path)
        config["dependencies"] = ", ".join(
            f"{n}@{new_versions[n]}" for n in new_versions if new_versions[n] != "?")
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
                lockfile_record(project_dir, n, v, new_repos.get(n, ""))


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
        _n, v, repo = _install_registry(name, ver)
        resolved[name] = {"version": v, "repo": repo}
    write_lockfile(project_dir, resolved)
    print(f"[leashed] Locked {len(resolved)} package(s) in {LOCKFILE}")
    print(f"[leashed] Commit {LOCKFILE} — fresh clones restore it with 'leashed install'")


def cmd_add(args):
    if len(args) < 1:
        eprint("Usage: leashed add <library_name>")
        sys.exit(1)
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
    if not os.path.exists(os.path.join(LEASH_LIBS_DIR, f"{libname}.lsh")):
        print(f"[leashed] Library '{libname}' not installed globally. Installing first...")
        pin = lockfile_pin(project_dir, libname)
        if pin:
            print(f"[leashed] Using locked version {libname}@{pin} from {LOCKFILE}")
            _n, _v, _r = _install_registry(libname, pin)
        else:
            _n, _v, _r = _install_registry(libname)
        installed_version = _v
        installed_repo = _r

    version = "?"
    index = fetch_index()
    libs = index.get("libraries", {})
    if libname in libs and isinstance(libs[libname], dict):
        version = libs[libname].get("version", "?")
    if installed_version:
        version = installed_version

    deps = config.get("dependencies", "")
    deps_list = [d.strip() for d in deps.split(",") if d.strip()]
    existing = {d.split("@", 1)[0] for d in deps_list}
    if libname in existing:
        print(f"[leashed] '{libname}' is already a dependency of this project")
        return
    entry = f"{libname}@{version}"
    deps_list.append(entry)
    config["dependencies"] = ", ".join(deps_list)
    write_pkg_config(config_path, config)

    main_file = config.get("main", "")
    if main_file:
        main_path = os.path.join(project_dir, main_file)
        if os.path.exists(main_path):
            with open(main_path, "r", encoding="utf-8") as f:
                content = f.read()
            line = f"use {libname}::*; // added by leashed\n"
            if line not in content and f"use {libname}::" not in content:
                with open(main_path, "w", encoding="utf-8") as f:
                    f.write(line + content)
                print(f"[leashed] Added 'use {libname}::*;' to {main_file}")

    if version != "?":
        lockfile_record(project_dir, libname, version,
                        installed_repo or (libs[libname].get("repo", "") if libname in libs and isinstance(libs[libname], dict) else ""))

    print(f"[leashed] Added '{libname}' ({version}) as a dependency")


def cmd_search(args):
    if len(args) < 1:
        eprint("Usage: leashed search <query>")
        sys.exit(1)
    query = args[0].lower()
    print(f"[leashed] Searching for '{query}'...")

    index = fetch_index()
    libs = index.get("libraries", {})

    matching = {}
    for k, v in libs.items():
        if not isinstance(v, dict):
            continue  # corrupt entry — never render or use it
        if query in k.lower() or query in v.get("description", "").lower():
            matching[k] = v

    if not matching:
        print(f"[leashed] No libraries found matching '{query}'")
        all_libs = list(libs.keys())[:15]
        if all_libs:
            print(f"  Available: {', '.join(all_libs)}")
            if len(libs) > 15:
                print(f"  ... and {len(libs) - 15} more")
        return

    print(f"[leashed] Found {len(matching)} library(ies):")
    for lib_name, info in sorted(matching.items()):
        ver = info.get("version", "?")
        author = info.get("author", "?")
        desc = info.get("description", "")
        print(f"  - {lib_name} v{ver} by {author}")
        if desc:
            print(f"    {desc}")


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
