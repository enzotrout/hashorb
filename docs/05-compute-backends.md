# Compute Backends

## Purpose

The compute boundary separates mining lifecycle decisions from nonce-search
execution. Mining orchestration constructs and advances Bitcoin work, assigns
one bounded interval, and handles any candidate. A compute backend searches
only that supplied interval and returns the shared result model.

The existing Python sequential scanner remains the correctness reference. No
native, multiprocessing, accelerator, or device-discovery dependency is part
of this milestone.

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

Prepared work is call-scoped. The Python backend does not retain it after the
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

## Python Reference Backend

`PythonSequentialBackend` delegates exactly once to the existing validated
`hashphere.mining.search.search_nonce_range` function. It does not duplicate
header construction, hashing, hash-to-integer conversion, target comparison,
or range validation. The exact supplied bounds pass through unchanged and the
validated result is returned unchanged.

This makes direct `search_nonce_range` behavior the oracle for future backends.
Parity tests cover exhausted, share-target, and network-target outcomes,
including nonce, digest, flags, counts, bounds, and controlled timing.

## Registry and Selection

`ComputeBackendRegistry` snapshots an explicit iterable into isolated immutable
per-instance state. It rejects duplicates, lists capabilities in sorted exact
backend-name order, and performs no plugin loading, entry-point discovery, dynamic
import, or hardware probing.

The built-in registry contains `python`. Configuration accepts:

- `python`, selecting the reference backend directly;
- `auto`, deterministically selecting `python` in this milestone;
- `cpu`, a compatibility alias selecting `python`.

Unknown and unavailable selectors are controlled configuration errors detected
before a mining command constructs a live client. `auto` does not currently
benchmark or optimize for hardware. `HASHPHERE_COMPUTE_PROFILE` remains a
separate deferred resource-policy setting.

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

## Future Extension

A future native or parallel CPU backend must accept the same prepared work and
assigned half-open range and return a semantically identical result. Parallel
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
coordination, multiprocessing, search strategies, and resource profiles are
all deferred.
