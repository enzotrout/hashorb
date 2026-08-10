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
Commit: `87caadf`

Changed:
- Added the cross-platform `dev` workflow helper with help, status, sync, start, check, doctor, full, and review commands.
- Added behavior-focused helper tests and explicit Packaging CI coverage for the Python wrapper, Unix executable entry point, status, doctor, focused validation, and branch diff hygiene.
- Added explicit detached-HEAD reporting for CI checkouts without weakening normal repository validation.
- Kept mining, Bitcoin, Stratum, cryptography, and CUDA kernel behavior out of scope.

Validation:
- PR Packaging #48 passed on Windows / Python 3.13, Ubuntu / Python 3.13, macOS / Python 3.13, and Docker CPU / Ubuntu.
- Ubuntu focused helper tests: 18 passed in 0.22s.
- Ubuntu full suite: 2191 passed and 20 skipped in 5.29s; skips were the documented Bitcoin Core, CUDA hardware, multi-CUDA hardware, and platform-specific installer gates.
- Ruff check passed, Ruff format reported 114 files already formatted, and mypy reported no issues in 52 source files.
- `git diff --check origin/main...HEAD`, `git status --short`, and `git diff --stat origin/main...HEAD` passed in Packaging; the checkout was clean.
- PR Security #14 passed source/dependency/workflow/artifact scanning and hardened CPU-container security scanning.

Documentation:
- Added `docs/development.md` and `tasks/dev-workflow-helper-v2.md`.
- Updated `README.md`, `.github/workflows/packaging.yml`, and this activity log.

Remaining:
- None in the implementation. GitHub PR checks remain required on each updated head before merge.

## 2026-08-10 — Rebalance Auto compute profile

Branch: `local/rebalance-auto-profile`
Commit: `3ac4220`

Changed:
- Kept Auto CUDA on the validated 500,000,000-hash range and 256-thread launch while adding an 80 ms rest between complete ranges.
- Kept Auto CPU fallback on its existing bounded worker and chunk-size policy without additional pacing.
- Left Lite and Max behavior unchanged and deferred CPU/GPU hybrid allocation.
- Used the manually validated one-hour Spark Max run at 2.759853619 GH/s as the tuning baseline.

Validation:
- Packaging #68 passed Windows / Python 3.13, Ubuntu / Python 3.13, macOS / Python 3.13, and Docker CPU / Ubuntu on implementation head `3ac4220`.
- Security #22 passed source/dependency/workflow/artifact scanning and hardened CPU-container security scanning on implementation head `3ac4220`.
- Hosted full suite passed with 2,193 tests and 20 documented skips; Ruff, formatting, mypy, lock, package, and diff-hygiene gates passed.
- Five-minute Spark live gate measured Lite at 1.1486 GH/s effective, Auto at 1.8854 GH/s, and Max at 2.7550 GH/s. Auto was 68.4% of Max and 1.64 times Lite.
- All three Spark runs reached `runtime_limit_reached` with zero duplicate work, connection losses, reconnect attempts, or failed reconnects.
- Packaging #74 and Security #25 passed on the documentation head that recorded the measured Spark results.

Documentation:
- Updated `docs/12-performance-profiles.md`, added `tasks/rebalance-auto-profile.md`, and updated this activity log with measured hardware evidence.

Remaining:
- None in implementation or hardware acceptance. Exact-head Packaging and Security validation remains required before merge.

## 2026-08-10 — Dashboard TUI foundation

Branch: `local/dashboard-tui-foundation`
Commit: `pending`

Changed:
- Added a read-only terminal dashboard state model and ANSI renderer driven only by existing sanitized JSONL mining events.
- Added live raw/effective rate presentation, nonce-space bucket visualization, observed strategy path, recent events, run/network counters, and optional safe `nvidia-smi` telemetry.
- Integrated `hashorb dashboard` directly into the canonical `hashorb.__main__:main` command dispatch after CI correctly rejected an alternate console-entry wrapper.
- Kept mining control, profile changes, backend changes, strategy execution, and share submission outside this slice.
- Corrected archived snapshots so completed runs freeze terminal effective rate and elapsed clocks and never present current GPU telemetry as historical telemetry.

Validation:
- Exact implementation head `998d21411aeb7646a2145c13fe578f7c136803b9` passed Packaging #128 on Windows / Python 3.13, Ubuntu / Python 3.13, macOS / Python 3.13, and Docker CPU / Ubuntu.
- Security #51 passed source/dependency/workflow/artifact scanning and hardened CPU-container security scanning on that implementation head.
- Ubuntu full suite: 2,213 passed and 20 documented hardware/platform skips. Ruff check, Ruff format, mypy, lock, branch-diff hygiene, package build, distribution verification, and installed-distribution smoke all passed.
- Spark archived-log gate rendered the real one-hour Max run correctly: 9.93 T hashes, approximately 2.759 GH/s effective, frozen 01:00:00 uptime, terminal outcome, coherent sequential nonce visualization, and historical GPU telemetry omitted.
- Spark live Auto gate rendered in place with approximately 1.879 GH/s effective versus 2.758 GH/s raw, updating GPU telemetry, nonce-space progression/reset, recent events, and run counters.
- The corresponding three-minute Auto mining run completed with `runtime_limit_reached`, 339,212,481,792 hashes, 710 chunks, zero duplicate work, zero reconnect attempts, approximately 2.758 GH/s raw, and approximately 1.884 GH/s effective.
- Final exact-head Packaging #132 and Security #53 passed after recording the human acceptance evidence.

Documentation:
- Added `docs/14-dashboard-tui.md` and `tasks/dashboard-tui-foundation.md` and recorded the completed Spark human acceptance here.

Remaining:
- None in implementation, hosted validation, semantic review, or Spark acceptance. Merge requires explicit user authorization.
