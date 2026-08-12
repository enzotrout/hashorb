# Dashboard Hash Quality

## Objective

Extend the terminal dashboard with mining-quality information that answers three operator questions: how good is the best hash seen so far, have any pool-share targets been hit/submitted, and has the Bitcoin network target been found.

The implementation must preserve mining correctness and keep the dashboard read-only. A true Best Hash means the exact lowest numerical Bitcoin double-SHA256 value among every nonce actually checked during the run. Sampling or estimated best-hash values are not acceptable.

## Scope

Allowed implementation areas:

- `src/hashorb/mining/search.py`
- `src/hashorb/compute/native.py`
- `src/hashorb/compute/parallel.py`
- `src/hashorb/compute/cuda.py`
- `src/hashorb/compute/cuda_multi.py`
- `src/hashorb/compute/_native.c`
- `src/hashorb/compute/_cuda.cu`
- `src/hashorb/__main__.py` only for safe event projection from completed search results
- `src/hashorb/dashboard.py`
- observability validation/documentation required for the new safe event field/event
- focused existing tests and new tests required for the behavior
- dashboard/compute documentation, `docs/activity.md`, and this task file

Do not change:

- SHA-256 algorithm semantics
- nonce allocation or search-strategy order
- share/network target comparison semantics
- Stratum protocol behavior or credentials
- candidate verification or submission policy
- reconnect or liveness policy
- Lite/Auto/Max profile selection or pacing
- dashboard mining controls

## Required behavior

1. Every successful nonce-search result must expose the exact lowest hash among the hashes it actually checked, including matched ranges that terminate early.
2. Python, native C, native-parallel, CUDA, and multi-CUDA backends must agree on the same best hash for identical work/ranges.
3. Native and CUDA extension results must be defensively verified at the Python boundary. The extension may return the nonce associated with its minimum and Python may recompute that hash once for validation.
4. Parallel and multi-device reducers must choose the numerically lowest Bitcoin hash from their child results independently of nonce order.
5. CUDA Best Hash tracking must use bounded reduction rather than a global atomic/update for every tested hash. It must not change target-match/candidate selection behavior.
6. Observability must emit only sanitized Best Hash telemetry. The full hash is allowed because it is derived proof-of-work output and not credential/raw-work material; no header, coinbase, extra nonce, payout address, or secret may accompany it.
7. Prefer emitting a `best_hash_improved` event only when the run-wide minimum improves, avoiding one full hash in every range-completion record.
8. Best Hash is run-wide and must survive job/work-variant changes; it resets only when a new mining command invocation starts.
9. The dashboard must show a dedicated hash-quality/target section containing at least:
   - Best Hash in canonical Bitcoin display byte order
   - Best Difficulty or equivalent quality metric derived from the best hash
   - network target derived locally from `network_bits`
   - share target derived locally from Stratum difficulty
   - share-target hit indicator
   - Bitcoin block/network-target found indicator
   - submitted / accepted / rejected share counts
10. Share-target and network-target indicators must latch for the run once a `share_candidate_found` event reports the corresponding target match.
11. A network-target hit must be visually unmistakable in the terminal while remaining a display-only state.
12. Existing logs without Best Hash events must remain readable and show Best Hash as unavailable rather than failing.
13. Existing dashboard, mining, packaging, and security behavior must remain compatible.

## Pre-change CUDA baseline

Captured on DGX Spark before Best Hash tracking with the current `cuda` backend, device 0, a 500,000,000-hash deterministic range, 2 warmups, and 10 measured repetitions.

```text
Hashes per run: 500000000
Initialization: 302132824 ns
First launch: 190012094 ns
Median elapsed time: 179333825 ns
Minimum elapsed time: 176874788 ns
Maximum elapsed time: 181232770 ns
Median hashes per second: 2788097132.82
Minimum hashes per second: 2758882954.78
Maximum hashes per second: 2826858511.91
Total backend-call wall time: 2337650986 ns
Cleanup: 212142 ns
Result: range exhausted
```

Post-change A/B validation must rerun the same command and compare the median and measured spread. A material throughput regression requires redesign rather than being accepted by default.

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

Hosted Packaging and Security workflows must pass for the exact PR head before merge when hosted Actions are available. While the repository owner's Actions billing/quota is unavailable, record hosted validation as unavailable and use the required local/hardware gates without claiming hosted CI passed.

Hardware gates before merge:

1. macOS/native-parallel live mining confirms Best Hash changes over time and share/target panel renders correctly.
2. DGX Spark CUDA parity confirms the reported Best Hash against a CPU reference on bounded deterministic work.
3. DGX Spark A/B benchmark compares Max CUDA throughput before and after Best Hash tracking. Any material regression requires redesign rather than acceptance by default.
4. DGX Spark live dashboard confirms Best Hash, targets, share counters, and target indicators without display corruption.

## Authorization

This task is authorized to use `local/dashboard-hash-quality`, commit and push the bounded implementation, update tests/documentation, and open a pull request. Merge still requires explicit user authorization after semantic review and the required available validation/hardware performance/correctness gates.
