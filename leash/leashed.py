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

LEASHED_VERSION = "0.1.0"
REGISTRY_REPO = "foksiny/leash-packages"
REGISTRY_OWNER = REGISTRY_REPO.split("/")[0]
REGISTRY_URL = f"https://raw.githubusercontent.com/{REGISTRY_REPO}/main/index.json"
REGISTRY_GIT = f"https://github.com/{REGISTRY_REPO}.git"
LEASH_LIBS_DIR = os.path.expanduser("~/.leash/libs")
LEASHED_CONFIG = "leash-pkg.lshc"
PACKAGE_CONFIG = "package.lshc"
PUBLISHER_FILE = "publisher"
LIBRARY_DIR = "library"
VERBOSE = False


def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


def validate_name(name):
    if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_-]*$', name):
        eprint(f"error: Invalid name '{name}'. Must start with a letter or underscore and contain only letters, digits, hyphens, and underscores.")
        sys.exit(1)
    return name


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
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"libraries": {}}
        eprint(f"error: Failed to fetch package index (HTTP {e.code})")
        sys.exit(1)
    except (urllib.error.URLError, json.JSONDecodeError) as e:
        eprint(f"error: Failed to fetch package index: {e}")
        sys.exit(1)


def read_pkg_config(path):
    config = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            ci = s.find(" #")
            if ci >= 0:
                s = s[:ci].strip()
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
        for key in ["name", "version", "author", "description", "main", "repo", "dependencies"]:
            val = config.get(key)
            if val:
                f.write(f'{key}: "{val}"\n')


def run_leash_check(filepath):
    try:
        from leash.cli import check_file
        errs, warns = check_file(filepath, verbose=VERBOSE)
        if errs:
            eprint(f"error: {len(errs)} error(s) found in source code. Fix them before publishing.")
            for e in errs:
                eprint(f"  {e}")
            sys.exit(1)
        if warns and VERBOSE:
            print(f"[leashed] {len(warns)} warning(s) found (continuing)")
        return True
    except ImportError:
        rc = subprocess.run(
            [sys.executable, "-m", "leash.cli", "check", filepath],
            capture_output=True, text=True
        )
        if rc.returncode != 0:
            eprint("error: Source code check failed. Fix errors before publishing.")
            eprint(rc.stderr)
            sys.exit(1)
        return True


def run_leash_compile(filepath, output_stem):
    try:
        from leash.cli import compile_file
        compile_file(
            filepath,
            output_name=output_stem,
            output_type="static",
            is_run_mode=False,
        )
        return True
    except ImportError:
        rc = subprocess.run(
            [sys.executable, "-m", "leash.cli", "compile", filepath, "to-static", output_stem],
            capture_output=True, text=True
        )
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
    version = config["version"]
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
    print(f"[leashed] Publishing '{name}' v{version} by {author}")
    print(f"[leashed] Publisher: {publisher}")

    # Step 1: Verify source code
    print("[leashed] Verifying source code...")
    run_leash_check(main_path)

    # Step 2: Compile to static library
    print("[leashed] Compiling library...")
    out_dir = tempfile.mkdtemp(prefix="leashed_bld_")
    lib_out = os.path.join(out_dir, LIBRARY_DIR)
    os.makedirs(lib_out, exist_ok=True)
    output_stem = os.path.join(lib_out, name)
    try:
        run_leash_compile(main_path, output_stem)
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
        # Non-owner: fork + PR flow
        rc, _, err = run_gh(["repo", "fork", REGISTRY_REPO, "--clone=false"])
        if rc != 0:
            eprint(f"error: Failed to fork registry: {err}")
            eprint("  Your library was published, but not registered in the index.")
            eprint(f"  Submit a PR to {REGISTRY_REPO} adding to index.json manually.")
            tmp_cleanup(out_dir)
            tmp_cleanup(repo_tmp)
            tmp_cleanup(reg_tmp)
            sys.exit(1)

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
    index["libraries"][name] = {
        "repo": repo_url,
        "description": description,
        "author": author,
        "version": version
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
            print("[leashed] A maintainer will review and merge it.")
        else:
            eprint(f"warning: Failed to create PR: {err}")
            eprint(f"  Submit a PR manually to {REGISTRY_REPO} updating index.json.")

    tmp_cleanup(out_dir)
    tmp_cleanup(repo_tmp)
    tmp_cleanup(reg_tmp)


def cmd_install(args):
    if len(args) < 1:
        eprint("Usage: leashed install <library_name>")
        sys.exit(1)
    libname = validate_name(args[0])
    print(f"[leashed] Installing '{libname}'...")

    index = fetch_index()
    libs = index.get("libraries", {})
    if libname not in libs:
        eprint(f"error: Library '{libname}' not found in the package index")
        eprint("  Run 'leashed search' to find available libraries.")
        sys.exit(1)

    entry = libs[libname]
    repo_url = entry.get("repo", "")
    if not repo_url:
        eprint(f"error: Library '{libname}' has no repo URL in the index")
        sys.exit(1)

    ver = entry.get("version", "?")
    desc = entry.get("description", "")
    author = entry.get("author", "?")
    print(f"[leashed] {libname} v{ver} by {author}")
    if desc:
        print(f"  {desc}")

    os.makedirs(LEASH_LIBS_DIR, exist_ok=True)
    repo_tmp = tempfile.mkdtemp(prefix="leashed_repo_")

    rc, _, err = run_git(["clone", "--depth", "1", repo_url, repo_tmp])
    if rc != 0:
        eprint(f"error: Failed to clone library repository: {err}")
        tmp_cleanup(repo_tmp)
        sys.exit(1)

    src = os.path.join(repo_tmp, LIBRARY_DIR)
    if not os.path.exists(src):
        eprint(f"error: Library '{libname}' has no '{LIBRARY_DIR}/' directory")
        tmp_cleanup(repo_tmp)
        sys.exit(1)

    dest_root = os.path.join(LEASH_LIBS_DIR, libname)
    if os.path.exists(dest_root):
        shutil.rmtree(dest_root)
    shutil.copytree(src, dest_root)

    stub_path = os.path.join(LEASH_LIBS_DIR, f"{libname}.lsh")
    entry_main = "src/main.lsh"
    entry_module = os.path.splitext(entry_main)[0].replace("\\", "::").replace("/", "::")
    with open(stub_path, "w", encoding="utf-8") as f:
        f.write(f"// {libname} {ver} by {author}\n")
        f.write(f"// {desc}\n")
        f.write(f"use {libname}::{entry_module}::*;\n")

    print(f"[leashed] Successfully installed '{libname}'")
    tmp_cleanup(repo_tmp)


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

    if not os.path.exists(os.path.join(LEASH_LIBS_DIR, f"{libname}.lsh")):
        print(f"[leashed] Library '{libname}' not installed globally. Installing first...")
        cmd_install([libname])
    else:
        print(f"[leashed] Library '{libname}' is already installed globally")

    version = "?"
    index = fetch_index()
    libs = index.get("libraries", {})
    if libname in libs:
        version = libs[libname].get("version", "?")

    deps = config.get("dependencies", "")
    deps_list = [d.strip() for d in deps.split(",") if d.strip()]
    entry = f"{libname}@{version}"
    if entry in deps_list:
        print(f"[leashed] '{libname}' is already a dependency of this project")
        return
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

    print(f"[leashed] Added '{libname}' ({version}) as a dependency")


def cmd_search(args):
    if len(args) < 1:
        eprint("Usage: leashed search <query>")
        sys.exit(1)
    query = args[0].lower()
    print(f"[leashed] Searching for '{query}'...")

    index = fetch_index()
    libs = index.get("libraries", {})

    matching = {k: v for k, v in libs.items() if query in k.lower() or query in v.get("description", "").lower()}

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
    print("  install <name>    Install a library globally (~/.leash/libs)")
    print("  add <name>        Add a library to the current project")
    print("  search <query>    Search for libraries")
    print()
    print("Global Options:")
    print("  --verbose/-vb     Enable verbose output")


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
        "add": cmd_add,
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
