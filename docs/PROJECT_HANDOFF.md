# HashOrb Project Handoff

This file is the authoritative short-form checkpoint for continuing HashOrb work across chats, machines, or interrupted sessions.

Update it at major milestones, especially before changing branches, merging a PR, starting a long hardware test, or ending a long development session.

## Current checkpoint

Date: 2026-08-13

Repository: `enzotrout/hashorb`

Primary development host: DGX Spark `spark-2b09`

Local repository path: `~/Development/hashorb`

Active integration branch: `local/dashboard-hash-quality`

Open PR: #10, `Restore exact Best Hash dashboard on current main`

PR state: draft and mergeable. The one-hour combined mining gate is complete and clean. Do not merge until the post-run dashboard-polish checks below are also clean.

Base branch: `main`

Current `main` at integration time: `b2516426f1d35d33d02d972870755d4b67195a34`

## What is currently integrated

The active branch contains these lines of work:

1. PR #9 continuous Stratum share/CKPool resilience
   - structured pool share rejection is nonfatal
   - a normal full-range accepted/rejected share does not terminate continuous mining
   - partial-range matches, network-target matches, explicit stop boundaries, and ambiguous failures remain terminal where required for correctness
   - CKPool informational methods such as `mining.ping` and `client.show_message` are ignored safely
   - transport failure during share submission is not solved by PR #9 and must not be described as solved

2. Exact Best Hash / hash-quality dashboard
   - every successful search result can expose the exact lowest Bitcoin hash among the hashes actually checked
   - CUDA uses a bounded reduction strategy rather than a global per-hash atomic update
   - Python defensively reconstructs and verifies the hash for the best nonce returned by native/CUDA extensions
   - run-wide `best_hash_improved` events survive job and work-variant changes and reset only for a new mining command
   - dashboard renders Best Hash, Best Difficulty, Network Target, Share Target, target-hit indicators, and share counts
   - Best Hash is the true minimum observed over searched nonces, not merely the best share candidate

3. Dashboard presentation polish staged after the one-hour miner had already started
   - `DEVICE / RATE` now labels the weighted run-average raw compute rate as `Average` instead of showing the noisy latest range sample
   - the blocky nonce-space bucket bar is replaced by `SEARCH ACTIVITY`
   - `SEARCH ACTIVITY` keeps the most recent range endpoints and renders a smooth two-marker dotted animation; marker position is display-only and does not claim to be an exact GPU nonce location
   - orbiting-bit keeps a recent bucket path and the work-variant number
   - Best Hash and Best Difficulty turn bright magenta for 60 seconds after a new run-wide best
   - pool Difficulty and its derived Share Target turn bright magenta for 60 seconds when difficulty changes
   - share status/counts turn bright magenta for 60 seconds after candidate/submission activity
   - reconnect counters turn bright magenta for 60 seconds after connection-loss/reconnect activity
   - a Network Target HIT remains persistently emphasized for the rest of the run

Important validation boundary: the one-hour miner was started before the dashboard-presentation commits. Those later commits change only `src/hashorb/dashboard.py`, dashboard tests, and this documentation. They do not alter mining, CUDA, search strategy, Stratum, share submission, or work-allocation behavior. Therefore the completed one-hour result is the mining-behavior gate; validate the newer dashboard code separately by tests and by replaying the completed JSONL.

## Critical build detail

The exact Best Hash CUDA branch uses the newer CUDA extension result interface. A stale `_cuda.so` can cause immediate runtime failures or tuple-shape mismatches.

After switching between `main` and this branch, rebuild CUDA explicitly on the DGX Spark:

```bash
cd ~/Development/hashorb
rm -f src/hashorb/compute/_cuda*.so
rm -rf build

HASHORB_BUILD_CUDA=1 \
HASHORB_CUDA_ARCH=121 \
uv run python setup.py build_ext --inplace
```

The DGX Spark target is `sm_121`.

Dashboard-only commits do not require a CUDA rebuild.

## Validation completed on the combined branch

After merging current `main` into `local/dashboard-hash-quality` and rebuilding CUDA for `sm_121`:

```text
Full pytest suite: 2249 passed, 22 skipped
CUDA hardware suite: 15 passed
Ruff lint: passed
Ruff format: formatting correction applied to merged PR #9 files
mypy: passed
```

Expected skips include isolated regtest, opt-in CUDA tests during the normal suite, multi-CUDA tests requiring two devices, and the Windows-only installer test.

Previous exact Best Hash hardware validation recorded a CUDA median throughput regression of approximately 1.34%, within the accepted <=3% gate.

## Five-minute combined live gate

A fresh 5-minute live test on `local/dashboard-hash-quality` used Auto + CUDA device 0 + Orbiting Bit against `stratum.ckpool.org:3333` at assigned difficulty 10000.

Result:

```text
Result: runtime_limit_reached
Chunks completed: 1175
Jobs used: 13
Job replacements: 12
Work variants used: 137
Extra nonce 2 advances: 124
Reconnect attempts: 0
Candidates found: 0
Submissions performed: 0
Hashes checked: 560025617664
Compute-only hashes per second: 2728647504.41
Effective wall-clock hashes per second: 1866558852.36
```

The live dashboard rendered correctly with the restored `HASH QUALITY / TARGET` panel. During the run it showed a run-wide Best Hash and Best Difficulty even with zero share candidates, confirming exact Best Hash telemetry was active.

One observed live snapshot showed:

```text
Best Hash       000000000b4bb0d0ef478110c68f936bde5fd01b5baa4fcc7a3e87d69c01f95a
Best Difficulty 22.6632
Share Target    NOT HIT
Network Target  NOT FOUND
```

## One-hour combined live gate: PASSED

The one-hour Auto + CUDA device 0 + Orbiting Bit run completed cleanly with no reconnects and no mining failure.

```text
Result: runtime_limit_reached
Final difficulty: 10000
Chunks completed: 13945
Jobs used: 129
Job replacements: 128
Work variants used: 1626
Extra nonce 2 advances: 1497
Extra nonce 2 cycles: 0
Network-time rolls: 0
Duplicate work ignored: 0
Reconnect attempts: 0
Successful reconnects: 0
Failed reconnect attempts: 0
Sessions established: 1
Candidates found: 0
Submissions performed: 0
Hashes checked: 6645267804416
Compute elapsed: 2482223511122 ns
Compute-only hashes per second: 2677143204.32
Profile wall-clock elapsed: 3600018555528 ns
Effective wall-clock hashes per second: 1845898209.11
```

Acceptance result:

- full 3600-second runtime completed normally
- no `Continuous Stratum mining failed`
- no unhandled `StratumClientError`
- no reconnects were needed
- exact Best Hash telemetry remained active during the run
- pool/job churn was handled across 128 replacements and 1626 work variants

Important limitation: this run found zero share candidates, so it did **not** exercise the live share-submission path. PR #9's structured share-rejection/continue behavior is covered by tests, but a real pool candidate/submission has not yet been observed on the combined code. Do not claim that live path has been proven until a candidate actually occurs.

## Previous share failure that must remain understood

A prior one-hour Auto + Orbiting Bit run reached a valid local share candidate and then failed with `StratumClientError` before a submission result was recorded.

Observed at failure:

```text
Candidates found: 1
Submissions performed: 0
Best Difficulty: 18,945.8
Assigned pool difficulty: 10,000
Reconnects: 0
```

The exact live pool error was not captured, so do not claim the pool definitely rejected the share as low difficulty.

PR #9 was created to make structured share rejection nonfatal and to skip known CKPool informational messages.

## Next required gate: dashboard polish validation

The one-hour mining gate is complete. Now update the local branch to the latest dashboard-only commits. No CUDA rebuild is required for these commits.

```bash
cd ~/Development/hashorb
git pull --ff-only
```

Run:

```bash
uv run ruff format --check src/hashorb/dashboard.py tests/test_dashboard.py
uv run ruff check src/hashorb/dashboard.py tests/test_dashboard.py
uv run mypy src
uv run pytest -q tests/test_dashboard.py tests/test_hash_quality_dashboard.py
```

Replay the completed one-hour log:

```bash
uv run python -m hashorb dashboard \
  --log-file logs/hashorb-orbiting-bit-auto-besthash-1h.jsonl
```

Verify:

- `Raw range-rate history ... Average <rate>`
- `SEARCH ACTIVITY — orbiting-bit`
- dotted two-marker activity animation is smooth and endpoints remain readable
- recent orbit path and variant are readable without the old block bucket map
- rare changes turn bright magenta for about 60 seconds when live/recent
- Network Target HIT, if ever true, remains persistently emphasized

Because replaying a completed historical log freezes the metric reference at the terminal event, the animation/highlight appearance may be best checked by a short synthetic/live dashboard run after the focused tests if needed.

## After all gates

If the post-run dashboard polish validation is clean:

1. update this file and PR #10 with the final dashboard validation
2. mark PR #10 ready for review
3. merge PR #10 only after confirming the branch head has not changed unexpectedly
4. switch the Spark back to `main`
5. `git pull --ff-only`
6. rebuild the CUDA extension on `main` because the merged Best Hash implementation changes the extension interface relative to old `main`
7. run a short CUDA smoke test after the rebuild

## New-session recovery procedure

When starting a new ChatGPT conversation or resuming after a long interruption, read this file first and verify actual repository state before making changes:

```bash
cd ~/Development/hashorb
git status --short
git branch --show-current
git log -1 --oneline --decorate
git fetch origin
```

Then compare the observed branch/commit with this checkpoint and the open PR before changing code.

Latest validation:
- Full pytest suite: 2254 passed, 22 skipped
- CUDA hardware suite: 15 passed
- Ruff: passed
- mypy: passed
- git diff --check: clean

Live share validation:
- A real CKPool share candidate was found at assigned difficulty 10,000.
- HashOrb calculated Best Difficulty approximately 15,426.9.
- The share was submitted and rejected by CKPool.
- Mining continued normally after the rejection.
- This live-proves the nonfatal structured share-rejection path.
- The old run did not preserve CKPool's rejection code/reason.

New diagnostics:
- Each normal JSONL log now automatically gets a sibling
  <name>.warnings.jsonl containing WARNING and ERROR events.
- Structured Stratum share rejections retain a safe rejection_code
  and normalized rejection_category.
- Continuous mining now preserves run-wide candidate, submission,
  accepted-share, and rejected-share totals in the final summary.

Dashboard:
- Average is effective wall-clock hashrate.
- Hashrate (5m) is rolling 5-minute effective hashrate.
- Hashrate (1hr) is rolling 1-hour effective hashrate.
- Search Activity x markers move through the actual Recent orbit path
  bucket positions rather than remaining stationary.

Do not reconstruct project state from conversational memory alone when this file and Git history are available.
