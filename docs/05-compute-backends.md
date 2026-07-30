# Compute Backends

## Purpose

The compute boundary separates mining lifecycle and search-order decisions from
nonce-search execution. Mining orchestration constructs and advances Bitcoin
work, the selected strategy assigns one bounded parent interval, and a compute
backend searches only that supplied interval before returning the shared result
model. The backend never chooses the global order of parent intervals.

The existing Python sequential scanner remains the correctness reference. The
optional `native` backend performs the same bounded sequential search through a
self-contained portable C extension. `native-parallel` divides a parent range
among concurrent verified native calls. The optional `cuda` backend evaluates
one parent range on one explicitly selected NVIDIA device and verifies every
reported candidate again in Python. `cuda-multi` partitions that same parent
range across explicitly selected CUDA devices and reduces verified child
results deterministically. Multiprocessing, SIMD, automatic device discovery,
and real multi-GPU hardware validation remain deferred. The CUDA implementation now
specializes Bitcoin header hashing and reuses device-owned resources behind the
unchanged backend contract.

## Public Contract

`MiningComputeBackend` exposes immutable `capabilities` and one operation:

```python
def search_nonce_range(
    work: PreparedMiningWork,
    start_nonce: int,
    stop_nonce: int,
) -> NonceSearchResult: ...
```

`PreparedMiningWork` already contains the fixed 76-byte header prefix and the
validated network and share targets. The range uses an inclusive start and an
exclusive stop. Backends must preserve the assignment exactly: they cannot
wrap, extend, split invisibly, repeat, or fabricate work.

The returned `NonceSearchResult` retains the existing contract: exact input
bounds, actual hashes checked, elapsed nanoseconds, an exhausted or found
outcome, and at most the deterministic first qualifying `NonceSearchMatch`.
Sequential backends count through their first match; a parallel backend sums
the actual work completed by every assignment. A match retains
the exact nonce, raw block-hash bytes, and independent inclusive share-target
and network-target flags. Submission is never a backend responsibility.

## Ownership

A backend owns:

- execution of one supplied prepared-work nonce interval;
- stable identity and capability metadata;
- backend-local invariant checks;
- lifecycle cleanup for backend-owned execution resources;
- translation of implementation failures into controlled compute errors.

A backend does not own:

- Stratum sockets, messages, notifications, queues, or reconnects;
- settings or environment loading;
- job, coinbase, Merkle, header, or target construction;
- extra-nonce or network-time progression;
- parent-range ordering or strategy cursor state;
- share submission or retry decisions;
- signals, console output, event files, client cleanup, or event-sink cleanup.

Prepared work is call-scoped from orchestration's perspective. A backend may
retain an implementation-private derived cache only behind its lifecycle
boundary. Such a cache must compare all relevant work and target bytes, replace
state before searching changed work, remain invisible to strategy and Stratum,
and be discarded on close.

## Capabilities

`ComputeBackendCapabilities` is a frozen, slotted value containing:

- exact backend name and stable display name;
- backend kind and implementation;
- parallel-search, cooperative-cancellation, and device-selection flags;
- deterministic-search-order flag;
- optional preferred batch size;
- availability plus an optional controlled unavailability category.

Identity values are stable lowercase identifiers. Availability metadata never
contains raw exceptions or arbitrary hardware messages. The model deliberately
omits serial numbers, device paths, credentials, work bytes, and dynamic
benchmark claims.

The `python` backend declares kind `cpu`, implementation `python`, deterministic
search order, and no parallel search, cooperative cancellation, device
selection, or preferred batch size.

The `native` backend declares kind `cpu`, implementation `c`, and the same
sequential capability flags. Its availability is true only when the compiled
extension imports and exposes the expected callable. Otherwise it carries one
controlled category such as `ExtensionNotInstalled`, never raw importer text.

The `native-parallel` backend declares kind `cpu`, implementation
`c-threadpool`, deterministic result order, parallel search, no cooperative
cancellation, no device selection, and no preferred batch size. Its safe
immutable backend metadata includes the configured worker count.

The `cuda` backend declares kind `gpu`, implementation `cuda`, deterministic
result order, parallel search, no cooperative cancellation, device selection,
and no preferred batch size. Its only runtime identifier is the configured
nonnegative device ordinal. Availability requires the optional extension,
successful runtime initialization, and the requested device; failures use a
controlled category rather than a driver or compiler message.

The `cuda-multi` backend declares kind `gpu`, implementation `cuda-multi`,
parallel search, deterministic result order, explicit device selection, and no
cooperative cancellation. Its only device metadata is a canonical ascending
tuple of configured ordinals. Full ownership and proof details are in
[`11-multi-gpu.md`](11-multi-gpu.md).

## Python Reference Backend

`PythonSequentialBackend` delegates exactly once to the existing validated
`hashphere.mining.search.search_nonce_range` function. It does not duplicate
header construction, hashing, hash-to-integer conversion, target comparison,
or range validation. The exact supplied bounds pass through unchanged and the
validated result is returned unchanged.

This makes direct `search_nonce_range` behavior the oracle for future backends.
Parity tests cover exhausted, share-target, and network-target outcomes,
including nonce, digest, flags, counts, bounds, and controlled timing.

## Portable Native Backend

`NativeSequentialBackend` validates the public work and range, converts each
positive target losslessly to exactly 32 little-endian bytes, and calls the
extension with only:

- the validated 76-byte header prefix;
- share and network target bytes;
- inclusive start nonce;
- exclusive stop nonce.

The C loop appends four explicit unsigned little-endian nonce bytes, performs
Bitcoin double-SHA256, compares the raw digest independently and inclusively to
both targets using Hashphere's little-endian proof-of-work interpretation, and
stops at the first match. It preserves the exact range and count and never
constructs coinbase, Merkle, target, job, or Stratum data. The loop releases
the GIL and creates no threads.

The extension returns only optional nonce and raw digest values, two Boolean
target flags, and the exact count. The Python wrapper measures only that call
with `perf_counter_ns`, clamps a negative measured delta to zero, and constructs
the public immutable result.

Every native candidate is defense-in-depth verified before return. Python
reconstructs the exact 80-byte header, recomputes its digest with the existing
`hash_block_header`, and checks both flags with `hash_meets_target`. A nonce,
count, digest, or flag disagreement is a terminal
`ComputeBackendExecutionError`; an unverified candidate can never reach share
submission. Exhausted ranges avoid this rare verification work.

## Portable Native Parallel Backend

`NativeParallelBackend` receives the unchanged parent range and calls
`partition_nonce_range`. For range length `L` and configured worker count `W`,
it creates `min(L, W)` ascending contiguous half-open assignments. Division by
that assignment count gives a base size and remainder; the first remainder
assignments receive one additional nonce. No assignment is empty, sizes differ
by at most one, and the exact union equals the parent range without overlap.

One persistent standard-library `ThreadPoolExecutor` is created lazily per
selected backend instance. Python threads are appropriate because the existing
C search loop releases the GIL. Each assignment is submitted exactly once with
the same immutable `PreparedMiningWork` to the existing
`NativeSequentialBackend.search_nonce_range`, so native result validation and
Python candidate verification cannot be bypassed.

All running assignments finish before reduction. Worker completion order is
irrelevant: the lowest reported qualifying nonce wins. `hashes_checked` is the
sum of actual worker counts, including partial assignment searches that found
candidates. Parent `elapsed_ns` is one monotonic wall-clock interval around the
complete executor operation and never the sum of worker elapsed values.

The executor survives chunks, job replacement, extra-nonce progression,
network-time rolling, and Stratum reconnect. `close_compute_backend` shuts it
down once on success, stop, runtime failure, or recovery exhaustion; sequential
backends are no-ops. Running C calls do not support cooperative interruption.
After a worker or reduction failure, pending futures are cancelled where
possible, running work finishes safely during shutdown, and the broken backend
cannot be reused.

The complete lifecycle and benchmark boundary is also summarized in
[`07-parallel-cpu.md`](07-parallel-cpu.md).

## Optional CUDA Correctness Backend

`CudaBackend` receives the unchanged 76-byte header prefix, exact little-endian
share and network targets, and inclusive/exclusive nonce bounds. The optional
extension assigns logical offsets with a deterministic grid stride:

```text
lane = block_index * threads_per_block + thread_index
stride = grid_size * threads_per_block
offsets for one lane = lane, lane + stride, lane + 2*stride, ...
```

Only offsets below `stop_nonce - start_nonce` are evaluated. This partitions
the logical range exactly once without gaps, duplication, wraparound, or
out-of-range work. Each nonce is appended as four explicit unsigned
little-endian bytes. Host preparation computes the SHA-256 state after the
fixed 64-byte first header block once per changed work value. Each nonce starts
from that midstate, hashes the specialized 16-byte tail/padding block, and then
uses a fixed 32-byte second-pass block. Digest words are compared inclusively
and independently with the two little-endian targets.

Parallel execution order is not observable. Every qualifying lane applies an
atomic minimum to the absolute nonce, so the public candidate is always the
smallest qualifying nonce in the supplied parent range. A synchronized
correctness kernel evaluates the full range even after a candidate appears;
therefore `hashes_checked` is exactly `stop_nonce - start_nonce`. One monotonic
host interval covers input transfer, launch, synchronization, candidate flag
retrieval, and result return. No lane or kernel timing is summed.

The extension returns only optional candidate nonce, two target flags, and the
full hash count. Python rejects malformed counts, flags, or out-of-range
nonces, reconstructs `header_prefix || nonce_little_endian`, calculates the
digest through the established `hash_block_header`, and checks both flags with
`hash_meets_target`. A disagreement raises `ComputeBackendExecutionError`.
Neither a fabricated nor an unverified candidate can reach submission.

One CUDA backend instance owns one device selection, one prepared-work buffer,
one candidate buffer, and one flag buffer for the mining invocation. Stable
work avoids repeat uploads; a prefix or target change replaces the complete
derived device work before the next kernel, and every call resets candidate
state.
It survives chunks, job replacement, extra-nonce progression, network-time
rolling, and Stratum recovery. Caller-owned cleanup synchronizes backend work
once, is idempotent, and does not reset unrelated CUDA global state. The backend
owns no settings loading, strategy cursor, networking, submission, signal,
console, or event-file behavior. Full source and validation details are in
[`10-cuda-backend.md`](10-cuda-backend.md).

## Registry and Selection

`ComputeBackendRegistry` snapshots an explicit iterable into isolated immutable
per-instance state. It rejects duplicates, lists capabilities in sorted exact
backend-name order, and performs no plugin loading, entry-point discovery, dynamic
import, or hardware probing.

The built-in registry always contains `python` and also describes `cuda`,
`cuda-multi`, `native`, and `native-parallel` whether available or unavailable. An ordinary
CPU registry leaves CUDA uninitialized, so importing Hashphere or selecting a
CPU backend does not probe a GPU. CUDA listing or explicit selection performs
the controlled extension/runtime/device initialization. Configuration accepts:

- `python`, selecting the reference backend directly;
- `auto`, deterministically selecting `python` in this milestone;
- `cpu`, a compatibility alias selecting `python`;
- `native`, selecting the extension only when available;
- `native-parallel`, selecting the thread pool only when the extension and
  worker configuration are available;
- `cuda`, selecting the optional extension only when the configured device
  initializes successfully.
- `cuda-multi`, selecting only the explicitly configured device set when every
  device initializes successfully.

Unknown and unavailable selectors are controlled configuration errors detected
before a mining command constructs a live client. The legacy backend selector
`auto` remains a static alias for Python. The separate optional compute profile
`auto` performs narrow command-time capability resolution; the two settings do
not share semantics. See
[`12-performance-profiles.md`](12-performance-profiles.md).

`HASHPHERE_COMPUTE_WORKERS` is a strict unpadded ASCII decimal value from 1
through 256, defaults to 2, and configures only `native-parallel`. It does not
alter Python or native sequential search. Profiles may own smarter resource
policy later but do not interpret this value today.

`HASHPHERE_CUDA_DEVICE` is a strict unpadded ASCII decimal device ordinal,
defaults to zero, and is parsed only for explicit CUDA selection. Invalid
syntax fails before networking. CPU backends ignore it. No UUID, serial number,
PCI address, compiler path, driver path, or automatic multi-device selection is
part of the contract.

`HASHPHERE_CUDA_DEVICES` is required for explicit `cuda-multi` selection. It
contains one to 256 unique comma-separated ordinals, accepts surrounding
element whitespace, and canonicalizes them into ascending order. It never
discovers visible devices. A one-device list is a supported integration mode,
not a scaling claim.

There is no native-to-Python execution fallback. `auto` intentionally remains
`python` until parity, packaging, and live benchmark evidence justify a separate
selection decision.

## Invocation Lifecycle

Each mining command creates a fresh registry and selects exactly one backend.
The same instance is used for one-shot mining, every finite chunk, and every
continuous chunk. It also survives pool job replacement, deterministic
extra-nonce and network-time progression, and Stratum session recovery.

A reconnect replaces only session-local Stratum state and work. It does not
reselect the compute backend or reset cumulative chunk, hash, elapsed-time,
candidate, submission, or recovery totals. Newly prepared work is passed to
the same backend as a new call.

Strategy selection is separate and has the same invocation lifetime. The
strategy chooses parent assignments and creates fresh per-work cursor state;
the backend executes each assignment unchanged. Compatibility is validated
before networking. In particular, `native-parallel` owns only the private
subdivision of one strategy-supplied parent range, so worker count never changes
global assignment order. See
[`08-search-strategies.md`](08-search-strategies.md).

Both sequential and orbiting-bit supply ordinary contiguous half-open parent
ranges. Orbiting-bit changes only the order in which those ranges arrive. A
backend receives no permutation counter or physical-range index and performs no
strategy-specific hashing, partitioning, accounting, or candidate handling.
CUDA is compatible with both strategies and sees exactly the same bounds as the
Python and native backends.

Caller-owned command cleanup closes the selected backend only after its final
search and never during a recoverable reconnect. Cleanup is idempotent, waits
for worker termination, and follows existing error precedence so it cannot
obscure an earlier runtime failure.

## Failure Model

Contract and input problems raise `ComputeBackendValidationError`. Unknown or
unavailable selection raises `ComputeBackendSelectionError`. A selected
implementation's failure, invalid result type, or mismatched result bounds
raises `ComputeBackendExecutionError` without exposing its raw exception.

Selection failures return the CLI configuration status before network access.
Validation and execution failures during mining are terminal runtime failures.
They are not converted to exhausted results, do not trigger Stratum recovery,
and do not cause fallback or a duplicate search. Existing caller-owned cleanup
and signal-restoration precedence remains unchanged.

A parallel worker failure is sanitized, cancels futures that have not started
where possible, waits for running calls, and permanently marks that backend
instance unusable. It does not trigger Stratum reconnect or fallback.

CUDA import, runtime, initialization, and missing-device failures make explicit
selection unavailable and return configuration status 2 before networking.
Kernel, transfer, synchronization, result, candidate-verification, and cleanup
failures are sanitized runtime errors with status 1. They never trigger CPU
fallback, repeat a range, or cause Stratum reconnect.

## Observability and Privacy

Mining commands emit one `compute_backend_selected` event after selection and
before authorization. Safe fields are the backend name, kind, implementation,
parallel flag, cooperative-cancellation flag, device-selection flag, optional
worker count, and optional CUDA device ordinal.
`cuda-multi` instead adds a safe device count and ascending ordinal list; it
does not add per-device range events.
Existing nonce-range events describe parent searches; there is no event per
worker, assignment, or hash.

The read-only log summary counts selections by stable backend name. It does not
display hardware identifiers or availability errors, and backend aggregation
does not change weighted hash-rate calculation. Console output reports only
the selected stable name.

Native selection emits the same low-cardinality fields with name `native`, kind
`cpu`, and implementation `c`. Compiler paths, CPU identity, work bytes, native
tracebacks, and raw import errors are excluded.

Parallel selection uses stable name `native-parallel`, implementation
`c-threadpool`, and a safe worker count. Thread identifiers, assignment bounds,
processor identity, and raw worker failures remain excluded. Log-summary
backend aggregation naturally includes the stable name and does not aggregate
worker counts.

CUDA selection uses stable name `cuda`, kind `gpu`, implementation `cuda`, and
one ordinal. JSONL excludes UUIDs, serial numbers, PCI addresses, driver and
compiler paths, raw CUDA errors, targets, headers, digests, candidates, and
per-thread or per-block data. Backend summary aggregation naturally counts
`cuda` without creating a device-specific high-cardinality section.

Multi-CUDA selection uses stable name and implementation `cuda-multi`, a device
count, and sanitized ordinals. The summary continues to aggregate only by
stable backend name and remains compatible with old records.

## Offline Benchmark

`compute-benchmark` selects an explicit `cuda`, `cuda-multi`, `python`, `native`, or
`native-parallel` backend without loading runtime settings, credentials, event
sinks, or network code. It uses
fixed public synthetic `PreparedMiningWork`, accepts one strict half-open range,
and prints only identity, hashes checked, elapsed nanoseconds, calculated rate,
and exhausted/candidate status. Fixture bytes, targets, digest, and nonce are
not printed.

Parallel benchmarking accepts `--workers` only with `native-parallel` and
reports that count. Its rate uses summed actual hashes over the parent-call
wall-clock interval. It does not emit per-worker timing or events.

CUDA benchmarking accepts `--device` only with `cuda`, defaults to ordinal
zero, and reports that ordinal. It exercises the same deterministic synthetic
work and host verification without Stratum or live opt-ins. Availability and
syntax failures return status 2; execution, verification, or cleanup failures
return status 1. No speed threshold or comparison is asserted.

Multi-CUDA benchmarking requires `--devices`, reports only count and ordinals,
and divides aggregate exact hashes by parent-call wall time. It creates no
socket, event sink, credential, or per-device timing stream.

Optional `--warmup-runs` and `--repetitions` provide an explicit repeated mode
without changing one-shot defaults. It separates initialization, the first
launch, warmups, measured median/minimum/maximum, total backend-call wall time,
and cleanup. Repetition counts are bounded at 100 and output remains aggregate
and synthetic.

The rate is calculated from unrounded totals:

```text
hashes_checked * 1_000_000_000 / elapsed_ns
```

Zero elapsed time reports unavailable. Measurements are local evidence for one
build and machine, not a promised speedup or pool-performance claim.

## Future Extension

The sequential strategy boundary and deterministic orbiting-bit order are
complete. Any future partitioned, strided, random, or probabilistic global
order requires explicit strategy contracts and parity tests; none is
implemented here. Cooperative
mid-range cancellation is deferred; all current backends truthfully declare
that they cannot cancel a running range. A future cancellation input can be
added only with lifecycle and actual hash-accounting tests.

The CUDA correctness boundary is validated on real NVIDIA GB10 hardware with
CUDA 13.0: the `sm_121` extension passes the gated device-parity and host/build
tests. Both sequential and orbiting-bit continue to pass exact
ordinary parent ranges through the same backend contract. Later offline,
controlled CKPool, and endurance measurements are recorded in the CUDA
documentation as local evidence only. Multi-GPU coordination architecture is
implemented and deterministically tested; a two-device physical hardware gate
remains pending. Metal, Vulkan, Windows CUDA builds, multiprocessing,
SIMD, additional strategies, thermal-aware selection, and portable CUDA wheel
publishing remain deferred.
