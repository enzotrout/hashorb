# Migrating an Existing Pre-Rename Checkout to HashOrb

## What

This guide is for a machine that already had the project checked out before the project was renamed to **HashOrb**.

Typical signs are:

- the local outer directory is still named `hashsphere`
- `git remote -v` still shows the old repository name
- a private `.env` still contains keys beginning with `HASHSPHERE_` or `HASHPHERE_`
- a repository-local `.venv` was created while the checkout lived under the old absolute path
- native or CUDA extensions were compiled from the old path

Fresh clones of `https://github.com/enzotrout/hashorb.git` do not need this procedure.

## Why

The Git repository can survive a remote rename, but local development state may contain the old name or an old absolute filesystem path. Python virtual environments, IDE interpreter selections, compiled extension build metadata, shell working directories, and VS Code remote workspaces are examples.

The safe approach is therefore to update Git first, migrate private configuration without displaying values, rename the outer checkout directory, and then recreate generated environments and compiled artifacts from the new path.

## Plain Talk

Do not delete a working checkout just because its folder still says `hashsphere`. Update it in place, rename the folder once Git is on the renamed `main`, then rebuild the generated pieces.

## Before You Change Anything

Work from a clean checkout. These commands do not print private configuration values:

```bash
git status --short
git branch --show-current
git remote -v
```

Do not continue if there are uncommitted changes you still need. Commit, stash, or otherwise preserve them first.

The repository rename does not require changing SSH to HTTPS or HTTPS to SSH. Preserve the remote style already used on that machine.

## 1. Update the Git Remote

From the existing checkout, inspect the current URL:

```bash
git remote get-url origin
```

For an HTTPS checkout:

```bash
git remote set-url origin https://github.com/enzotrout/hashorb.git
```

For the normal GitHub SSH form:

```bash
git remote set-url origin git@github.com:enzotrout/hashorb.git
```

If the machine uses a custom SSH host alias, keep that alias and change only the repository path. For example, an older alias such as `github-hashsphere` can continue to work until you deliberately rename the SSH config entry:

```bash
git remote set-url origin git@github-hashsphere:enzotrout/hashorb.git
```

Then fetch and fast-forward the renamed main branch:

```bash
git fetch --prune origin
git switch main
git pull --ff-only origin main
```

Verify:

```bash
git status
git remote -v
```

## 2. Migrate a Private `.env` on macOS or Linux

HashOrb includes a POSIX-only helper for the pre-release environment-prefix rename:

```bash
python3 scripts/migrate-hashorb-env.py .env
```

The helper:

- changes only `HASHSPHERE_` and `HASHPHERE_` key prefixes to `HASHORB_`
- preserves values without displaying them
- creates `.env.pre-hashorb` as a backup
- refuses collisions instead of guessing
- uses a temporary file and atomic replacement
- preserves private file permissions

Verify that no legacy key names remain:

```bash
python3 scripts/migrate-hashorb-env.py --verify .env
```

A successful verification produces no legacy key names and exits successfully.

Do not paste the contents of `.env` into an issue, chat, terminal transcript, or documentation. The project does not need seed phrases, private keys, or wallet passwords.

Keep `.env.pre-hashorb` until you have confirmed that the migrated configuration works. Delete the backup manually later when you no longer need rollback.

If the checkout has no `.env`, skip this section.

### Windows note

The migration helper intentionally rejects Windows because its security checks use POSIX ownership and mode semantics. A new Windows setup should use the current `HASHORB_` names from `.env.example`. Do not weaken Windows security settings merely to make the POSIX helper run.

## 3. Rename the Outer Checkout Directory

After Git is updated and the private `.env` is migrated, leave the repository and rename its outer directory.

For the common layout on macOS or Linux:

```bash
cd ~/Development
mv hashsphere hashorb
cd hashorb
```

If your checkout lives somewhere else, rename that actual directory instead. Do not copy personal absolute paths into committed documentation or bug reports.

Confirm the repository still resolves correctly:

```bash
git status
git remote -v
```

## 4. Recreate Generated Python and Build State

A repository-local virtual environment can contain the old absolute path. Recreate it after the folder rename.

From the renamed repository root:

```bash
rm -rf .venv build dist
find . -maxdepth 3 -type d -name '*.egg-info' -prune -exec rm -rf {} +
uv sync --locked --reinstall-package hashorb
```

For an ordinary CPU development host, verify:

```bash
uv run python -c 'import hashorb; import hashorb.compute._native; print("HashOrb CPU imports passed")'
uv run hashorb --help
uv run hashorb doctor
```

The native extension remains optional on platforms where a suitable compiler is unavailable.

## 5. DGX Spark: Rebuild CUDA from the New Path

On a DGX Spark used for HashOrb CUDA development, rebuild the package after the directory rename so the native and CUDA extensions come from the new `hashorb` path.

For the validated GB10 / compute capability 12.1 host:

```bash
rm -rf .venv build dist
find . -maxdepth 3 -type d -name '*.egg-info' -prune -exec rm -rf {} +
HASHORB_BUILD_CUDA=1 \
HASHORB_CUDA_ARCH=121 \
uv sync --locked --reinstall-package hashorb
```

Then run safe local checks:

```bash
uv run python -c 'import hashorb; import hashorb.compute._native; import hashorb.compute._cuda; print("HashOrb native and CUDA imports passed")'
uv run hashorb doctor --probe-cuda-device 0
uv run hashorb compute-benchmark --backend cuda --device 0 --hash-count 1000000
```

Do not copy architecture `121` to another NVIDIA GPU unless that is actually the intended supported architecture for that host.

These checks do not require Stratum mining or Bitcoin Core block submission.

## 6. Reopen IDE and Remote Workspaces

After the folder rename:

- reopen the new `hashorb` directory in VS Code or another IDE
- on a VS Code Remote SSH session to the Spark, reconnect to the new directory
- select the newly created repository `.venv` interpreter
- close terminals whose working directory points at the removed `hashsphere` path
- update personal shell aliases, bookmarks, launchers, or scripts that contain the old local path

Do not commit machine-specific workspace paths.

## 7. Final Verification

On either Mac or Spark:

```bash
pwd
git status
git remote -v
uv run hashorb --help
uv run hashorb doctor
```

The expected state is:

- the outer directory is named `hashorb`
- `origin` points to `enzotrout/hashorb`
- the current package and CLI are named `hashorb`
- current environment variables use the `HASHORB_` prefix
- the recreated `.venv` lives under the new checkout path
- native extensions, and CUDA extensions on the Spark, were rebuilt from the new path

## What Not to Rename

Do not rewrite Git history merely to remove old project names. Historical commits and audit records legitimately describe the project before the rename.

The historical security finding identifiers `HS-01` through `HS-11` are stable identifiers and remain unchanged.

Do not rename Bitcoin protocol terminology, generic uses of the word "hash", or unrelated local files merely because they resemble the project name.

## Troubleshooting

If both `hashsphere` and `hashorb` directories exist, stop and determine which checkout contains your current work before deleting anything.

If `git pull --ff-only` refuses to update, inspect the local branch and commits rather than forcing the branch.

If the environment migration helper refuses a collision, do not edit values blindly. Inspect only the key names involved and decide which intended `HASHORB_` key should remain.

If imports fail after the directory rename, remove and recreate `.venv` and generated build artifacts. Do not try to repair absolute paths inside an old virtual environment by hand.
