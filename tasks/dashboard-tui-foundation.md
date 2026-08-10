# Dashboard TUI Foundation

## Objective

Build the first real HashOrb terminal dashboard from the approved TUI mockup. The dashboard must be useful during live mining, visually represent nonce-space traversal, and remain read-only so presentation work cannot alter mining, Stratum, compute, profile, or submission behavior.

## Scope

Allowed files:

- `src/hashorb/dashboard.py`
- `src/hashorb/__main__.py`
- `tests/test_dashboard.py`
- existing CLI tests only when required for the new dashboard command
- `README.md`
- `docs/14-dashboard-tui.md`
- `docs/activity.md`
- this task file

Keep the canonical installed console entry point `hashorb.__main__:main`. Integrate `dashboard` as a small command-time dispatch in the existing CLI without changing existing command behavior or introducing a second console wrapper.

Do not change:

- SHA-256 or candidate validity
- nonce allocation or search-strategy execution
- CUDA/native kernels or backend scheduling
- Stratum protocol behavior
- profile selection or pacing
- candidate verification or share submission
- reconnect or liveness policy
- event-log schema or event emission
- live mining start/stop/profile/backend/device controls

## Required behavior

1. Add a `hashorb dashboard --log-file PATH` command that follows an existing HashOrb JSONL log in read-only mode and redraws a terminal dashboard until interrupted.
2. Add `--once` for one deterministic snapshot suitable for tests, CI, terminals that do not support live redraw, and archived logs.
3. Add `--refresh-seconds VALUE` with a bounded finite refresh interval and a sensible default.
4. Do not require a new third-party runtime dependency. Use the Python standard library and ANSI output only when live redraw is active on a terminal.
5. The dashboard must select the latest mining run in an append-only log and ignore events belonging to older/interleaved runs once a newer mining run becomes active.
6. Show at least: command/run state, profile, backend, CUDA device when known, search strategy, endpoint, difficulty, uptime, jobs, work variants, ranges completed, hashes checked, raw weighted compute rate, recent effective wall-clock rate, reconnects, duplicate work, candidates, submissions, and recent events.
7. Visualize the 32-bit nonce space using only existing `nonce_range_started` / `nonce_range_completed` events. Sequential traversal should appear contiguous; non-linear strategies such as orbiting-bit should naturally appear distributed according to observed ranges. Do not inspect strategy cursor internals.
8. Reset per-work-variant nonce visualization when selected work changes via `mining_job_replaced` or `mining_work_advanced`.
9. Keep a recent raw-rate sparkline based on completed ranges and compute a recent effective rate from event timestamps plus completed hashes so Auto pacing is visible as lower effective throughput while raw CUDA rate remains high.
10. Optionally show safe local NVIDIA telemetry when `nvidia-smi` is available. Query only temperature, power draw, utilization, total memory, and used memory for the already selected ordinal. Never query or display UUID, serial number, PCI address, device path, driver path, or arbitrary command errors. Missing telemetry is informational only.
11. Live file following must preserve a partial final JSON line until it is completed. Malformed complete records must fail visibly rather than being silently skipped.
12. The dashboard must never create, truncate, append, repair, rotate, or delete the source log.
13. No credential, wallet, username, password, raw work, extra-nonce value, or arbitrary exception text may be displayed.
14. Ctrl-C must exit the live dashboard cleanly with status 0.
15. The first slice is display-only. Keyboard mining controls from the visual mockup are explicitly deferred.
16. Existing installed commands and their status/output behavior must remain unchanged.

## Validation

Run the repository baseline:

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy src
uv run pytest -q
git diff --check
git status --short
git diff --stat
```

Hosted Packaging and Security workflows must pass for the exact PR head before merge.

A human Spark gate must also render `--once` from a real mining log and run the live dashboard against an actively appended mining log before merge.

Spark human acceptance completed on 2026-08-10:

- Archived one-hour Max log rendered correctly with 9.93 T hashes, approximately 2.759 GH/s effective, frozen 01:00:00 uptime, terminal outcome, historical NVIDIA telemetry omitted, and a coherent final sequential nonce-space projection.
- A three-minute live Auto run rendered in-place while mining. The dashboard showed approximately 1.879 GH/s effective versus 2.758 GH/s raw, live GPU temperature/power/utilization, advancing/resetting nonce-space buckets as work variants changed, recent events, and live run counters.
- The corresponding Auto mining run completed with `runtime_limit_reached`, 339,212,481,792 hashes, 710 chunks, zero duplicate work, zero reconnect attempts, approximately 2.758 GH/s raw, and approximately 1.884 GH/s effective.

## Authorization

This task is authorized to create and use `local/dashboard-tui-foundation`, commit and push the bounded implementation, update documentation and tests, and open a pull request. Merge still requires explicit user authorization after review, hosted validation, and the Spark human gate.

## Task Report

Status: READY FOR REVIEW

Implementation, semantic review, archived-log acceptance, and live Spark acceptance are complete. Merge still requires explicit user authorization after the final exact-head hosted Packaging and Security workflows pass.
