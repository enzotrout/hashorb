# HashOrb Development Activity

This log records meaningful implementation, repair, migration, packaging, CI, and workflow work.
Keep entries concise and factual. Do not include secrets, credentials, wallet material, private endpoints, or hidden configuration values.

## Entry template

```markdown
## YYYY-MM-DD — Short task name

Branch: `local/<branch-name>`
Commit: `<sha>` or `pending`

Changed:
- Concise implementation fact

Validation:
- Command and result

Documentation:
- Updated files or approved no-documentation statement

Remaining:
- None, or concise outstanding work
```

## 2026-08-06 — Agent workflow foundation

Branch: `local/agent-workflow-foundation`
Commit: `a27404f`

Changed:
- Added a repository-owned development contract for coding agents.
- Added Continue-specific rules that require the contract and structured reports.
- Added a reusable task specification template.
- Added this development activity log.

Validation:
- Packaging #25 passed on Windows / Python 3.13, Ubuntu / Python 3.13, macOS / Python 3.13, and Docker CPU / Ubuntu.
- PR #4 was reviewed and found mergeable; stale activity status was corrected before merge.

Documentation:
- Added `AGENTS.md`, `.continue/rules/hashorb.md`, `tasks/TEMPLATE.md`, and `docs/activity.md`.

Remaining:
- None.

## 2026-08-08 — Developer workflow helper v2

Branch: `local/dev-workflow-helper-v2`
Commit: `e33d412`

Changed:
- Added the cross-platform `dev` workflow helper with help, status, sync, start, check, doctor, full, and review commands.
- Added behavior-focused helper tests and explicit Packaging CI coverage for the Python wrapper, Unix executable entry point, status, doctor, and focused validation.
- Kept mining, Bitcoin, Stratum, cryptography, and CUDA kernel behavior out of scope.

Validation:
- Packaging #40 passed on Windows / Python 3.13, Ubuntu / Python 3.13, macOS / Python 3.13, and Docker CPU / Ubuntu before the final documentation and diff-hygiene updates.
- Ubuntu focused helper tests: 17 passed in 0.18s.
- Ubuntu full suite: 2190 passed and 20 skipped in 4.82s; skips were the documented Bitcoin Core, CUDA hardware, multi-CUDA hardware, and platform-specific installer gates.
- Ruff check passed, Ruff format reported 114 files already formatted, and mypy reported no issues in 52 source files.

Documentation:
- Added `docs/development.md` and `tasks/dev-workflow-helper-v2.md`.
- Updated `README.md`, `.github/workflows/packaging.yml`, and this activity log.

Remaining:
- Final branch-head Packaging and PR Security checks must pass before merge review.
