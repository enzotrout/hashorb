# Rebalance Auto Compute Profile

## Objective

Make `auto` materially less aggressive than `max` on CUDA hosts while preserving the existing compute, mining, Stratum, candidate-verification, and submission safety boundaries.

On the validated one-GPU DGX Spark, Auto and Max currently resolve to the same CUDA device, 256 threads per block, 500,000,000-hash parent range, and zero pacing, so their effective rates are effectively identical. Auto should instead represent balanced everyday operation between Lite and Max.

## Scope

Allowed files:

- `src/hashorb/config/profile.py`
- `tests/test_compute_profiles.py`
- `docs/12-performance-profiles.md`
- `docs/activity.md`
- `tasks/rebalance-auto-profile.md`

Do not change:

- SHA-256 or hash validity
- nonce-range ordering or uniqueness
- CUDA kernels or launch-size validation
- candidate verification
- Stratum protocol behavior
- share submission behavior
- reconnect or liveness behavior
- Lite or Max policy
- CPU/GPU hybrid execution

## Required behavior

1. Auto on CUDA keeps the existing efficient CUDA backend selection, explicit-device behavior, 256 threads per block, and 500,000,000-hash parent range.
2. Auto CUDA applies a fixed 0.08-second inter-range delay after each complete parent range and before the next.
3. The same Auto CUDA pacing applies to one explicitly selected CUDA device and to an explicitly selected CUDA device list.
4. Max CUDA remains unpaced at 0 seconds.
5. Auto CPU fallback keeps its existing bounded worker-count and chunk-size policy with zero additional inter-range delay.
6. Lite remains unchanged.
7. Auto and Max continue rejecting manual pacing overrides; the preset policy owns the value.
8. Tests must prove Auto CUDA pacing across implicit single-device, explicit single-device, and explicit multi-device resolution, and prove Auto CPU and Max remain unpaced.
9. Documentation must describe Auto as balanced rather than near-Max, distinguish GPU pacing from CPU worker caps, and clearly mark any projected Spark rate as a prediction until measured on the updated code.

## Design evidence

The manually validated one-hour Spark Max run on 2026-08-10 sustained 2,759,853,619.21 H/s over 9,931,190,860,032 hashes with zero command failures, connection losses, reconnects, stale sessions, or duplicate work.

At approximately 2.76 GH/s, a 500,000,000-hash CUDA range takes about 0.181 seconds of compute time. Adding 0.08 seconds between complete ranges gives a projected duty fraction near 0.181 / (0.181 + 0.08), or about 69%. This is a tuning target, not a guaranteed utilization, power, temperature, or hashrate value.

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

After hosted validation, run a human-controlled Spark gate comparing Lite, Auto, and Max. Auto should be materially below Max and above Lite in effective wall-clock rate; do not encode a fragile exact performance threshold into automated tests.

## Authorization

This task is authorized to create its dedicated branch, commit and push the bounded changes, and open a pull request. Merge still requires explicit user authorization after review and validation.
