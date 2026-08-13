# HashOrb Project Handoff

This file is the authoritative short-form checkpoint for continuing HashOrb work across chats, machines, or interrupted sessions.

Update it at major milestones, especially before changing branches, merging a PR, starting a long hardware test, or ending a long development session.

## Current checkpoint

Date: 2026-08-13

Repository: `enzotrout/hashorb`

Primary development host: DGX Spark `spark-2b09`

Local repository path: `/home/ltrout/Development/hashorb`

Active integration branch: `local/dashboard-hash-quality`

Open PR: #10, `Restore exact Best Hash dashboard on current main`

PR state: draft and mergeable. Do not merge until the one-hour combined live gate and the post-run dashboard-polish checks below are clean.

Base branch: `main`

Current `main` at integration time: `b2516426f1d35d33d02d972870755d4b67195a34`

## What is currently integrated

The active branch contains both of these previously separate lines of work:

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

3. Dashboard presentation polish staged while the one-hour mining process was already running
   - `DEVICE / RATE` now labels the weighted run-average raw compute rate as `Average` instead of showing the noisy latest range sample
   - the blocky nonce-space bucket bar is replaced by `SEARCH ACTIVITY`
   - `SEARCH ACTIVITY` keeps the most recent range endpoints and renders a smooth two-marker dotted animation; marker position is display-only and does not claim to be an exact GPU nonce location
   - orbiting-bit keeps a recent bucket path and the work-variant number
   - Best Hash and Best Difficulty turn bright magenta for 60 seconds after a new run-wide best
   - pool Difficulty and its derived Share Target turn bright magenta for 60 seconds when difficulty changes
   - share status/counts turn bright magenta for 60 seconds after candidate/submission activity
   - reconnect counters turn bright magenta for 60 seconds after connection-loss/reconnect activity
   - a Network Target HIT remains persistently emphasized for the rest of the run

Important validation boundary: the currently running one-hour miner was started before the dashboard-presentation commits. Those later commits change only `src/hashorb/dashboard.py`, dashboard tests, and this documentation. They do not alter mining, CUDA, search strategy, Stratum, share submission, or work-allocation behavior. Therefore the one-hour run remains the mining-behavior gate; after it ends, validate the new dashboard code separately by tests and by replaying the completed JSONL.

## Critical build detail

The exact Best Hash CUDA branch uses the newer CUDA extension result interface. A stale `_cuda.so` can cause immediate runtime failures or tuple-shape mismatches.

After switching between `main` and this branch, rebuild CUDA explicitly on the DGX Spark:

```bash
cd /home/ltrout/Development/hashorb
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

## Most recent live combined gate

A fresh 5-minute live test on `local/dashboard-hash-quality` used:

- profile: `auto`
- backend: `cuda`
- CUDA device: `0`
- strategy: `orbiting-bit`
- endpoint: `stratum.ckpool.org:3333`
- assigned difficulty: `10000`
- runtime limit: `300` seconds
- reconnect limit: `5`

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

## Previous failure that must remain understood

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

PR #9 was created to make structured share rejection nonfatal and to skip known CKPool informational messages. The next long run is specifically intended to exercise this combined code in real conditions and ideally encounter another share candidate.

## Current gate: one-hour live run

Remain on the local checkout that launched the run until it completes. Do not pull the newer dashboard-only commits into that checkout during the active mining validation.

The running command is equivalent to:

```bash
cd /home/ltrout/Development/hashorb

HASHORB_SEARCH_STRATEGY=orbiting-bit \
HASHORB_ENABLE_LIVE_STRATUM=1 \
HASHORB_ENABLE_LIVE_MINING=1 \
uv run python -m hashorb stratum-mine \
  --profile auto \
  --device 0 \
  --start-nonce 0 \
  --max-runtime-seconds 3600 \
  --max-reconnect-attempts 5 \
  --log-file logs/hashorb-orbiting-bit-auto-besthash-1h.jsonl
```

Acceptance criteria:

- finishes cleanly as `runtime_limit_reached`, unless a true network-target hit creates an intentional terminal outcome
- no `Continuous Stratum mining failed`
- no unhandled `StratumClientError`
- Best Hash may improve across job replacements and work-variant changes
- zero reconnects is desirable but reconnect recovery may occur if the connection genuinely drops
- if a share candidate appears, submission result is recorded and normal full-range mining continues after an accepted or structured rejected share
- exact pool rejection reason must not be inferred unless explicitly logged

## Post-run dashboard polish validation

After the one-hour miner finishes:

1. pull the latest `local/dashboard-hash-quality` branch
2. no CUDA rebuild is required because the post-start changes are dashboard/tests/docs only
3. run Ruff format/check, mypy, and focused dashboard/hash-quality tests
4. replay the completed one-hour log through the updated dashboard
5. verify:
   - `Raw range-rate history ... Average <rate>`
   - `SEARCH ACTIVITY — orbiting-bit`
   - dotted two-marker activity animation is smooth and endpoints remain readable
   - recent orbit path and variant are readable without the old block bucket map
   - recent rare changes turn bright magenta for about 60 seconds when replayed/live
   - Network Target HIT, if ever true, remains persistently emphasized

Recommended focused checks:

```bash
uv run ruff format --check src/hashorb/dashboard.py tests/test_dashboard.py
uv run ruff check src/hashorb/dashboard.py tests/test_dashboard.py
uv run mypy src
uv run pytest -q tests/test_dashboard.py tests/test_hash_quality_dashboard.py
```

Replay the completed log:

```bash
uv run python -m hashorb dashboard \
  --log-file logs/hashorb-orbiting-bit-auto-besthash-1h.jsonl
```

## After all gates

If the one-hour mining gate and post-run dashboard polish validation are clean:

1. record the final one-hour metrics and any share behavior in this file
2. update PR #10 with the completed combined validation
3. mark PR #10 ready for review
4. merge PR #10 only after confirming the branch head has not changed unexpectedly
5. switch the Spark back to `main`
6. `git pull --ff-only`
7. rebuild the CUDA extension on `main` if the merged result changes the extension relative to the currently loaded `.so`

## New-session recovery procedure

When starting a new ChatGPT conversation or resuming after a long interruption, read this file first and verify actual repository state before making changes:

```bash
cd /home/ltrout/Development/hashorb
git status --short
git branch --show-current
git log -1 --oneline --decorate
git fetch origin
```

Then compare the observed branch/commit with this checkpoint and the open PR before changing code.

Do not reconstruct project state from conversational memory alone when this file and Git history are available.
