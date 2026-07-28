# Portable Parallel Native CPU Backend

## Purpose

`native-parallel` accelerates one strategy-supplied, already-prepared parent
nonce interval with
multiple nonoverlapping calls to the verified portable native backend. It does
not change mining rules, prepare work, communicate with Stratum, progress work,
or submit shares.

Python's standard `ThreadPoolExecutor` is the portable concurrency boundary.
The existing C hashing loop releases the GIL, so independent extension calls
can run concurrently without multiprocessing, serialization, another native
thread library, or packaging changes. The design applies to macOS, Windows,
Linux, and Docker wherever the optional extension builds.

## Public Contract

`NativeParallelBackend` implements the existing operation:

```python
search_nonce_range(
    work: PreparedMiningWork,
    start_nonce: int,
    stop_nonce: int,
) -> NonceSearchResult
```

`partition_nonce_range(start_nonce, stop_nonce, worker_count)` is a pure public
helper returning an immutable tuple of frozen, slotted `NonceRangeAssignment`
values. The parent start is inclusive and stop is exclusive.

## Deterministic Partitioning

For parent length `L` and configured workers `W`, the assignment count is
`min(L, W)`. Integer division produces a base size and remainder. Assignments
are emitted in ascending order, and the first remainder assignments receive
one extra nonce.

This guarantees:

- no empty assignment;
- contiguous half-open bounds;
- no overlap, gap, wrap, or duplicate nonce;
- an exact union equal to the parent range;
- deterministic ascending order;
- assignment sizes differing by at most one.

Every assignment is submitted exactly once with the same immutable
`PreparedMiningWork` to `NativeSequentialBackend.search_nonce_range`. The
parallel layer never duplicates C hashing or bypasses Python candidate
verification.

## Candidate Reduction and Accounting

All running assignments are allowed to finish because the native loop has no
cooperative cancellation boundary. If several assignments report candidates,
the smallest nonce in the complete parent range wins regardless of worker
completion order. This preserves deterministic ascending-range semantics at
the backend boundary.

`hashes_checked` is the exact sum of hashes actually checked by every completed
worker. Candidate-producing assignments contribute their partial counts; fully
exhausted assignments contribute their full sizes. The value is not a fabricated
sequential count.

`elapsed_ns` is one `perf_counter_ns` wall-clock interval around the complete
parent search. Worker elapsed values are neither summed nor averaged. Aggregate
rate remains:

```text
hashes_checked * 1_000_000_000 / elapsed_ns
```

Zero elapsed time continues to report an unavailable rate.

## Pool Lifecycle and Failure Model

One executor is created lazily per selected backend instance and reused across
chunks, pool job replacement, extra-nonce progression, network-time rolling,
and fresh Stratum sessions after reconnect. Prepared work remains call-scoped
and is not retained after search.

The CLI closes the selected backend after success, controlled stop, runtime
failure, and recovery exhaustion. Closure is idempotent, waits for running
workers, and shuts the executor down exactly once. A recoverable Stratum
connection loss does not close or replace the backend.

The selected search strategy controls only the order of parent ranges. Worker
assignments, executor scheduling, completion order, and reduction remain
private backend details and are not returned to the strategy or emitted as
strategy events. Changing worker count cannot change sequential parent-range
order or the bit-reversal order selected by orbiting-bit. Every orbiting-bit
parent range is passed unchanged into the same private balanced partitioning.
The separation is detailed in
[`08-search-strategies.md`](08-search-strategies.md).

A worker, executor, clock, result-validation, or reduction failure becomes a
sanitized `ComputeBackendExecutionError`. Pending futures are cancelled where
possible; running native calls finish during shutdown. The backend is then
terminally broken and cannot silently continue. There is no automatic fallback,
range retry, or compute-triggered Stratum reconnect.

## Configuration and Selection

Select the backend explicitly:

```text
HASHPHERE_COMPUTE_BACKEND=native-parallel
HASHPHERE_COMPUTE_WORKERS=4
```

The worker value is strict unpadded ASCII decimal from 1 through 256 and
defaults to 2. A parent range shorter than the configured count creates only as
many assignments as nonces. The value does not alter `python` or sequential
`native` behavior.

The backend is available only when the optional native extension imports.
Unavailable selection or invalid worker configuration fails before networking.
`auto` and legacy `cpu` continue to select Python. Lite/Auto/Max/Custom profiles
remain separate and may choose worker policy in a future milestone.

## Offline Benchmark

Use the same public synthetic non-pool fixture as the sequential backends:

```bash
uv run python -m hashphere compute-benchmark \
  --backend native-parallel \
  --workers 4 \
  --hash-count 1000000
```

Output reports the stable backend, `c-threadpool` implementation, configured
workers, actual aggregate hashes, parent wall-clock elapsed time, aggregate
rate, and exhausted/candidate status. It contains no fixture bytes, target,
digest, candidate nonce, assignment details, thread identifier, credential, or
event record.

Benchmark results are local evidence for one machine, compiler, worker count,
build, and synthetic range. Automated tests assert parity and accounting rather
than a fixed speedup.

## Limitations and Next Boundary

There is no cooperative mid-range cancellation. A stop request waits for the
current parallel call and prevents the next mining chunk. There is no work
stealing, multiprocessing, SIMD, assembly, GPU code, device selection, or
platform-specific threading.

The explicit sequential and orbiting-bit parent-range strategies are complete,
and the optional CUDA correctness backend is independent of this CPU worker
pool. Other global search orders, CUDA hardware tuning and multi-GPU support,
resource profiles, wheel publishing, distributed workers, and automatic
backend policy remain deferred.
