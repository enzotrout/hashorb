# Compute Backends

## Purpose

The compute boundary separates mining lifecycle decisions from nonce-search
execution. Mining orchestration constructs and advances Bitcoin work, assigns
one bounded interval, and handles any candidate. A compute backend searches
only that supplied interval and returns the shared result model.

The existing Python sequential scanner remains the correctness reference. The
optional `native` backend performs the same bounded sequential search through a
self-contained portable C extension. Multiprocessing, SIMD, accelerators, and
device discovery remain deferred.

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
bounds, exact hashes checked, elapsed nanoseconds, an exhausted or found
outcome, and at most the first qualifying `NonceSearchMatch`. A match retains
the exact nonce, raw block-hash bytes, and independent inclusive share-target
and network-target flags. Submission is never a backend responsibility.

## Ownership

A backend owns:

- execution of one supplied prepared-work nonce interval;
- stable identity and capability metadata;
- backend-local invariant checks;
- translation of implementation failures into controlled compute errors.

A backend does not own:

- Stratum sockets, messages, notifications, queues, or reconnects;
- settings or environment loading;
- job, coinbase, Merkle, header, or target construction;
- extra-nonce or network-time progression;
- share submission or retry decisions;
- signals, console output, event files, or cleanup.

Prepared work is call-scoped. Neither sequential backend retains it after the
search returns, and no cache contract exists.

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

## Registry and Selection

`ComputeBackendRegistry` snapshots an explicit iterable into isolated immutable
per-instance state. It rejects duplicates, lists capabilities in sorted exact
backend-name order, and performs no plugin loading, entry-point discovery, dynamic
import, or hardware probing.

The built-in registry always contains `python` and also describes `native`
whether available or unavailable. Configuration accepts:

- `python`, selecting the reference backend directly;
- `auto`, deterministically selecting `python` in this milestone;
- `cpu`, a compatibility alias selecting `python`.
- `native`, selecting the extension only when available.

Unknown and unavailable selectors are controlled configuration errors detected
before a mining command constructs a live client. `auto` does not currently
benchmark or optimize for hardware. `HASHPHERE_COMPUTE_PROFILE` remains a
separate deferred resource-policy setting.

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

## Observability and Privacy

Mining commands emit one `compute_backend_selected` event after selection and
before authorization. Safe fields are the backend name, kind, implementation,
parallel flag, and cooperative-cancellation flag. Existing nonce-range events
continue to describe searches; there is no event per hash.

The read-only log summary counts selections by stable backend name. It does not
display hardware identifiers or availability errors, and backend aggregation
does not change weighted hash-rate calculation. Console output reports only
the selected stable name.

Native selection emits the same low-cardinality fields with name `native`, kind
`cpu`, and implementation `c`. Compiler paths, CPU identity, work bytes, native
tracebacks, and raw import errors are excluded.

## Offline Benchmark

`compute-benchmark` selects an explicit `python` or `native` backend without
loading runtime settings, credentials, event sinks, or network code. It uses
fixed public synthetic `PreparedMiningWork`, accepts one strict half-open range,
and prints only identity, hashes checked, elapsed nanoseconds, calculated rate,
and exhausted/candidate status. Fixture bytes, targets, digest, and nonce are
not printed.

The rate is calculated from unrounded totals:

```text
hashes_checked * 1_000_000_000 / elapsed_ns
```

Zero elapsed time reports unavailable. Measurements are local evidence for one
build and machine, not a promised speedup or pool-performance claim.

## Future Extension

A future parallel CPU backend must accept the same prepared work and assigned
half-open range and return a semantically identical result. Parallel
workers must receive nonoverlapping assignments; their scheduling and
first-candidate rules need an explicit deterministic contract before they can
declare deterministic search order.

Cooperative mid-range cancellation is deferred. The Python backend truthfully
declares that it cannot cancel a running range. A future cancellation input can
be added to the execution boundary only with lifecycle and accounting tests.

A future CUDA backend may search one supplied interval on a selected device,
but device probing, memory management, and host-side result verification remain
backend-local. It must remain unaware of Stratum, progression, logging files,
and submission. CUDA, Metal, Vulkan, DGX Spark/GB10 optimization, multi-GPU
coordination, multiprocessing, SIMD, search strategies, resource profiles, and
wheel publishing are all deferred.
