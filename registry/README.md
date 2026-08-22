# leash-packages

The official Leash package registry. The whole registry is a single
[`index.json`](index.json) mapping library names to their GitHub repositories.
Every library lives in its **own repository under the publisher's account** —
this repo only holds the index.

## Publishing is fully self-service

Anyone can publish a package **without any human review**:

```bash
leashed init mylib
cd mylib
leashed publish
```

`leashed publish` pushes your library to your own GitHub repo, then opens a
pull request against `index.json`. A bot (this repo's `Validate registry
changes` workflow) checks the PR automatically and **merges it within a
minute** if every rule passes. No maintainer, no waiting on people.

### Rules the bot enforces

| Rule | Why |
|------|-----|
| Entry `publisher` must be the PR author's GitHub login | You can only publish as yourself |
| Existing entries can only be updated by their original owner | No package hijacking |
| Repo URL must live under `https://github.com/<publisher>/...` | Index entries point at your own repos |
| Version must be valid semver and strictly greater than the last | Reproducible upgrades |
| All previously published versions stay in the entry | Old versions remain installable (`leashed install name@1.2.3`) |
| Package repo must contain a correct `library/` layout and compile | Broken packages never reach users |
| Entries cannot be deleted via PRs | Registry stability |

If a check fails, the bot comments on the PR with the exact reason — fix it
and re-run `leashed publish`.

## Installing without the registry

The registry is optional. Anyone can install straight from any Git URL:

```bash
leashed install https://github.com/someone/somelib.git
leashed install someone/somelib        # shorthand for github.com
```

## One-time setup checklist (registry maintainers)

1. Copy `.github/workflows/validate.yml` and `scripts/validate_index.py`
   into this repository.
2. **Settings → General → Pull Requests**: enable **Allow auto-merge**.
3. **Settings → Actions → General → Workflow permissions**: select
   **Read and write permissions**.
4. Branches → Branch protection rules for `main`:
   - Require status check **"Validate registry changes"**
   - Do **not** require human approvals (the bot approves)
   - Allow forks to pull requests (default)
5. Create the label `validation-failed` (Issues → Labels).
6. Optionally star/watch PRs if you want visibility — nothing needs approval.

## Manual overrides

Maintainers can always close a PR or push a fix to `main` directly; the bot
never merges anything that fails validation. To remove an abusive package,
edit `index.json` directly and open an issue against the offending library's
repo.

## Hosting your own registry

Any clone of this layout works. Point clients at it with environment
variables:

```bash
export LEASHED_REGISTRY_REPO=you/your-packages
# or fully custom URLs:
export LEASHED_REGISTRY_URL=https://example.com/index.json
export LEASHED_REGISTRY_GIT=https://github.com/you/your-packages.git
```

Copy the same workflow into your registry repo to get the same bot.
