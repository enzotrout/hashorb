# Git Workflow

## What

HashOrb development uses short task branches, local validation, pull requests, and a clean `main` branch.

## Why

Mining, networking, native code, CUDA, packaging, and security changes can interact in subtle ways. Small reviewed branches make it easier to understand what changed and to keep unrelated work out of a release.

## Plain Talk

Do one bounded piece of work on its own branch, prove it passes locally, review the diff, then merge it through a pull request. Do not use `main` as a scratch branch.

## Typical Flow

Start from a clean, current `main`:

```bash
./dev sync
git switch -c <type>/<short-task-name>
```

Examples:

```text
feat/fibonacci-bounce
fix/share-rejection
chore/security-cleanup
docs/public-docs-cleanup
```

Make the change, then run:

```bash
./dev check
./dev full
./dev review
```

Inspect the working tree and diff before committing:

```bash
git status
git diff --check
git diff
```

Commit with a concise description of the change:

```bash
git add <reviewed-files>
git commit -m "docs: improve quick start"
git push -u origin HEAD
```

Open a pull request to `main`, review the exact changed files and CI result, then merge only when the branch is current and the required gates are green.

## Branch Hygiene

- Keep unrelated changes out of the branch.
- Do not commit `.env`, credentials, wallet secrets, private keys, generated logs, virtual environments, or local binaries.
- Prefer normal forward merges or squash merges through reviewed pull requests.
- Do not force-push `main`.
- Do not use destructive reset commands as a routine synchronization method.
- Re-run the relevant validation when `main` changes underneath a long-lived branch.

## Sensitive Changes

Give extra review to changes involving:

- live Stratum or Bitcoin Core command boundaries
- block/share submission
- target or byte-order logic
- configuration and credentials
- native C or CUDA code
- GitHub Actions
- packaging and Docker
- security scanners or ignore files

The repository-local `./dev review` command helps identify changed filenames that deserve extra attention, but it does not replace reading the diff.

## Related Documentation

- [Development Environment](00-development-environment.md)
- [Development Workflow](development.md)
- [Security Audit](15-security-audit.md)
- repository-root [`SECURITY.md`](../SECURITY.md)