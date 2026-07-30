# Explicit Multi-CUDA Orchestration

## What, Why, and Plain Talk

**What:** `cuda-multi` is one logical compute backend that owns an explicitly
configured set of CUDA devices, partitions each strategy-supplied parent range,
runs one child range on each useful device concurrently, and returns one
`NonceSearchResult`.

**Why:** A future multi-GPU host must contribute all selected devices without
creating gaps, overlaps, separate Stratum sessions, separate strategy cursors,
or completion-order-dependent answers.

**Plain talk:** Several GPUs can divide one search area and return one correct
answer as though they were one larger device.

This milestone establishes and tests the architecture. The DGX Spark exposes
one GB10 GPU, so real multi-GPU hardware validation remains pending. No live
Stratum command was run while implementing or validating this milestone.

## Public Configuration

Single-device CUDA remains unchanged:

```bash
HASHPHERE_COMPUTE_BACKEND=cuda
HASHPHERE_CUDA_DEVICE=0
```

Multi-device CUDA is a separate explicit selector and requires a list:

```bash
HASHPHERE_COMPUTE_BACKEND=cuda-multi
HASHPHERE_CUDA_DEVICES=0,1
```

The list contains one to 256 unique, unpadded ASCII decimal ordinals from `0`
through `2147483647`. Surrounding whitespace on list elements is accepted.
Empty elements, duplicates, signs, decimal padding, non-ASCII digits, excessive
ordinals, and an omitted list are rejected before networking. Ordinals are
canonicalized into ascending order, making device-to-range assignment stable.
Hashsphere never discovers or selects every visible GPU implicitly.

A one-device list is allowed. It validates real extension, context, executor,
partition, reduction, and cleanup integration on a single-GPU host. It is not
evidence of multi-GPU execution or scaling.

`auto` and legacy `cpu` remain aliases for `python`. `python`, `native`,
`native-parallel`, and `cuda` retain their existing behavior. There is no
fallback from `cuda-multi` to fewer devices or to another backend.

## Parent and Child Range Contract

The search strategy still owns global parent-range ordering. `cuda-multi`
reuses the exact `partition_nonce_range` primitive already proven by
`native-parallel`; it does not duplicate the arithmetic. For a parent
`[start, stop)` and `D` configured devices:

```text
N = stop - start
A = min(N, D)
base, remainder = divmod(N, A)
child_size(i) = base + 1 when i < remainder, otherwise base
```

Children are emitted in ascending contiguous order and paired with ascending
ordinals. This construction proves:

- every child is nonempty and inside the parent;
- adjacent children meet at one boundary;
- the first starts at `start` and the last stops at `stop`;
- the sizes sum to `N` and differ by at most one;
- intersections are empty, so no nonce is duplicated;
- devices beyond `A` are skipped for a small range;
- the exact unsigned stop boundary `2**32` is preserved.

No visited-range set is stored. Sequential and orbiting-bit continue to choose
the same parent ranges in the same global order as before.

## Ownership and Concurrency

One `CudaMultiBackend` owns:

- one independently initialized `CudaBackend` per canonical ordinal;
- one persistent `ThreadPoolExecutor`, created lazily;
- one serialized logical-call boundary;
- child-future collection and deterministic reduction;
- executor and device cleanup.

Each `CudaBackend` owns an opaque native `CudaContext` capsule containing its
ordinal, prepared-work cache, work buffer, candidate buffer, and flag buffer.
The extension selects the context's device for every operation. No buffer or
prepared-work cache is shared across ordinals. Stable work is reused on every
device; a changed prefix or either changed target replaces cached work on every
participating device during that device's next call.

CUDA initialization, kernel execution, synchronization, transfer, and cleanup
release the Python GIL around the native operation. Python threads can therefore
enter different device contexts concurrently. The optimized hashing kernel and
its launch geometry are unchanged.

There is still one Stratum client, one authorized session, one selected search
strategy, and one per-work strategy cursor. Individual devices never see
Stratum state or create nested executors or child processes.

## Result Reduction and Candidate Safety

The parent call owns one wall-clock interval. Every child result must report its
exact assigned bounds and full CUDA hash count. The aggregate count must equal
`stop - start`; inconsistent or partial results are failures, not exhaustion.

Every child is an ordinary `CudaBackend`, so any reported candidate is first
reconstructed, double-hashed, and checked against both targets in Python. Only
verified child results reach multi-device reduction. Reduction waits for all
children and chooses the numerically lowest verified nonce, independent of
future completion order. Share-only, network-only, and both-target flags remain
attached to that verified match.

No candidate event, submission, retry, or reconnect occurs inside either CUDA
backend. Existing lifecycle checks suppress a completed candidate after job
replacement, stale-session declaration, runtime expiry, or observed stop.

## Failure, Stop, and Cleanup Policy

Any initialization failure leaves `cuda-multi` unavailable and closes every
context that was already created. Any child execution, validation, aggregation,
clock, executor, or cleanup failure is terminal. The backend:

1. cancels futures that have not started where possible;
2. waits for already running non-cancellable CUDA calls during executor shutdown;
3. closes every device backend;
4. becomes permanently closed;
5. returns one sanitized compute error.

It never silently drops a device, reruns a range, falls back, or asks Stratum
recovery to reconnect. Repeated close is safe. The command lifecycle continues
to own client, signal-handler, and event-sink cleanup.

SIGINT, SIGTERM, runtime limits, job replacement, and stale-session recovery are
observed only at the established parent-call boundary. No new parent or child
assignment begins after a stop is observed. Because current CUDA kernels cannot
be safely cancelled, responsiveness is bounded by the slowest active child
assignment.

## Offline Benchmark and Spark Evidence

The offline synthetic benchmark accepts explicit devices:

```bash
uv run python -m hashphere compute-benchmark \
  --backend cuda-multi \
  --devices 0,1 \
  --hash-count 500000000
```

It reports only stable backend identity, aggregate count, parent wall time,
aggregate rate, device count, and sanitized ordinals. It loads no `.env`, opens
no socket, and emits no JSONL.

On the one-GPU Spark, two paired 500-million-hash measurements used two warmups
and seven measured repetitions for each backend. The single `cuda` medians were
2.443 and 2.457 GH/s; one-device `cuda-multi` medians were 2.413 and 2.411 GH/s.
Average median elapsed time increased from about 204.08 ms to 207.27 ms, about
1.56% orchestration overhead. Observed repetition ranges were 2.344–2.545 GH/s
for `cuda` and 2.351–2.530 GH/s for one-device `cuda-multi`. These results show
small one-device overhead only; they are not a multi-GPU scaling claim.

A later post-profile paired check used one warmup and five measured repetitions
per backend. At 100 million hashes, `cuda` measured 2.7542 GH/s and one-device
`cuda-multi` 2.6821 GH/s (2.62% lower). At 500 million hashes, the medians were
2.7575 and 2.7522 GH/s (0.19% lower). This supports the 500-million profile
chunk without replacing the established approximately 2.46 GH/s live baseline
or turning one-device integration into a scaling claim.

The gated device-0 suite passes through the isolated native context API and
includes one-device `cuda-multi` parity and cleanup. The extension rebuilds for
`sm_121`, imports on the Spark, and retains an `sm_121` cubin. Actual execution
with two physical CUDA devices is still unvalidated.

## Observability and Privacy

`compute_backend_selected` identifies `cuda-multi` and may add `device_count`
plus the ascending ordinal list. Existing `nonce_range_started` and
`nonce_range_completed` events remain the only per-parent records. There is no
per-device or per-child event stream. Old logs without device-list fields remain
readable.

JSONL, console failures, tests, and documentation exclude UUIDs, serials, PCI
addresses, topology, raw CUDA errors, raw futures, thread identifiers, compiler
paths, credentials, work bytes, targets, hashes, candidates, and session nonce
material.

## Packaging and Profile Boundary

Orchestration is Python-level and uses the same optional `_cuda` extension for
every device. Normal wheel and source builds remain CPU-capable without `nvcc`;
the sdist retains CUDA source, while a CUDA extension is built only with
`HASHPHERE_BUILD_CUDA=1` and an explicit supported architecture. Linux ARM64
local CUDA development builds retain the existing runtime-path policy and
private-prefix remapping. macOS and Windows remain CPU-only; Windows CUDA and
portable CUDA wheel publication remain deferred.

Lite / Auto / Max / Custom now resolve outside this backend before construction.
Auto and Max use `cuda-multi` only for an exact explicit device list; they never
inventory all visible GPUs. Custom requires the list and validated threads per
block explicitly. Profile names still do not enter the orchestrator or native
extension. See [`12-performance-profiles.md`](12-performance-profiles.md).

## Future Two-Device Hardware Gate

A host with at least two real CUDA devices must run the explicitly gated
`tests/test_cuda_multi_hardware.py` suite and offline benchmarks for each device
alone and together. The gate must demonstrate distinct child ranges, exact
Python parity, the global smallest candidate, complete cleanup, terminal device
failure behavior, no duplicate work or stale candidate, and no hardware-
identifier leakage. Aggregate throughput should exceed the fastest individual
device when the hardware and workload allow it. Until that gate passes,
Hashsphere claims architecture validation, not real multi-GPU validation.
