# Hashphere Architecture

# Overview

Hashphere is an open source Bitcoin mining project designed to run across multiple platforms.

The goal is for nearly all mining logic to be shared across:

- macOS
- Windows
- Docker
- Future platforms such as NVIDIA DGX Spark

Platform-specific code should remain minimal and primarily handle installation, packaging, operating system integration, and hardware-specific integration.

---

# Repository Layout

## `src/hashphere/`

Contains the reusable Hashphere Python package.

The project follows the Python `src` layout to clearly separate the reusable package from repository tooling and to prevent accidental imports from the project root.

Major packages:

- `compute`
- `config`
- `core`
- `crypto`
- `mining`
- `network`
- `observability`
- `protocol`
- `rpc`
- `telemetry`
- `utils`

Each package has a single, well-defined responsibility and should remain as independent as practical.

## `platform/`

Contains platform-specific launchers, packaging, installers, and operating system integration.

Examples include:

- Docker
- macOS
- Windows
- NVIDIA DGX Spark

The platform layer should remain thin. It should launch and configure the application without containing mining logic.

## `docs/`

Long-form project documentation.

## `examples/`

Small example programs demonstrating how to use individual Hashphere components.

## `scripts/`

Developer utilities, benchmarking tools, release helpers, and project automation.

---

# Design Principles

Hashphere is built around the following engineering principles:

- Shared code first
- Platform independence
- Small, testable modules
- Clear separation of responsibilities
- Testability by design
- Documentation before implementation

---

# Stratum Transport and Client Boundary

`hashphere.network.stratum.transport` owns the single synchronous TCP socket,
newline-delimited JSON framing, and raw receive buffering. A bounded receive
temporarily changes the socket timeout, restores the prior timeout on every
outcome, and retains incomplete or additional framed data for later calls. A
normal receive timeout is distinct from connection closure, I/O failure, and
malformed protocol data.

`hashphere.network.stratum.client` owns connection state, request identifiers,
request-response routing, notification parsing, and the ordered notification
queue. In the authorized state, bounded polling returns queued notifications
before touching the transport and maps only a normal receive timeout to
`None`. Other transport and protocol failures remain visible to the caller.
The client does not close, retry, or reconnect automatically after a poll.

This split keeps socket mechanics out of mining orchestration and keeps
protocol state out of the transport. Both layers remain synchronous and expose
small injectable boundaries for deterministic tests.

---

# Search Strategy Boundary

`hashphere.mining.strategy` owns the deterministic policy for selecting the
next parent half-open nonce assignment. A strategy definition exposes immutable
low-cardinality capabilities and creates one narrowly controlled cursor for
each effective prepared-work variant. The cursor owns assignment order,
sequence numbers, advancement, and exhaustion detection without retaining
prepared work or an unbounded search history.

The built-in `sequential` strategy emits ascending contiguous ranges beginning
at the configured start nonce. It shortens the final range at the unsigned
32-bit boundary and never wraps, skips, overlaps, or repeats an assignment.
The experimental built-in `orbiting-bit` strategy instead bit-reverses a
fixed-width permutation counter to select physical parent-range indexes. The
public assignment index remains emission order; physical index determines the
range bounds. Each emitted range is contiguous even though their global order
is not.

Orbiting-bit encloses the physical range count in the smallest power-of-two
permutation domain. Reversed indexes outside the valid range count are skipped
internally and never reach a backend, chunk counter, or event. Fixed arithmetic
state tracks permutation counter and emitted count; no visited-range set or
prepared work is retained. The bit-reversal bijection and bounds mapping give
complete, nonoverlapping, nonrepeating coverage.

One strategy definition is selected per mining invocation and survives pool-job
replacement, extra-nonce progression, network-time rolling, and Stratum
recovery. Each legitimate new effective work variant receives a fresh cursor;
difficulty-only notifications and duplicate work do not reset one.

The strategy schedules a parent range, and the selected compute backend hashes
that range. Backend compatibility is validated before live networking. A
parallel backend may privately partition the supplied parent range among its
workers, but worker count and subdivision never enter strategy state. Strategy
failures are terminal and trigger neither reconnect nor backend or strategy
fallback. Strategy objects own no sockets, threads, executors, files, or cleanup
resources. The full strategy contract and orbiting-bit integration are in
[`docs/08-search-strategies.md`](docs/08-search-strategies.md), with the
accessible bit-reversal design in
[`docs/09-orbiting-bit.md`](docs/09-orbiting-bit.md).

After a completed range, pool notifications remain higher priority than either
strategy's next assignment. The optional CUDA backend receives the same
ordinary parent-range contract and does not reinterpret sequential or orbiting
order. `cuda-multi` privately partitions that parent range without adding a
strategy cursor per device, preserving this ownership split.

Continuous CLI ownership includes one monotonic `StopController`. An optional
positive runtime limit begins only after configuration and compute/strategy
selection, then shares the same cooperative boundary used by SIGINT and
SIGTERM, initial notification waits, reconnect backoff, and mining. The first
observed cause is stable: a signal produces `stopped_by_user`, while deadline
expiry produces `runtime_limit_reached`. Both are successful completions. The
controller creates no timer thread or subprocess. Cleanup restores prior signal
handlers and closes the recovery owner, selected backend, and event sink.
Non-cancellable compute calls finish their current parent range before the stop
is observed; no new range or reconnect attempt begins afterward.

Opt-in Stratum liveness is a separate monotonic boundary. Server activity is
time since any supported complete notification, job age is time since a job
notification, and work activity is time since a completed range. Neither work
completion nor difficulty traffic falsely refreshes job age. Limits are
disabled by default because generic Stratum permits quiet sessions and long-
lived valid jobs. A threshold crossing suppresses a candidate from the stale
session, closes that session, and invokes the same `StratumSessionRecovery`
owner used for connection loss. Fresh subscription state and usable work reset
all session clocks before scheduling resumes. Socket `SO_KEEPALIVE` uses OS
defaults only; portable suspend inference remains deferred.

---

# Compute Backend Boundary

`hashphere.compute` owns nonce-search execution contracts, immutable
low-cardinality capabilities, deterministic built-in registration and
selection, and translation of backend-local failures into controlled compute
errors. A backend receives one validated `PreparedMiningWork` plus an exact
half-open nonce interval and returns the existing immutable
`NonceSearchResult`. It does not construct Bitcoin work or own Stratum,
progression, recovery, submission, settings, signals, console output, event
files, client cleanup, or event-sink cleanup. A resource-owning backend does
own its executor cleanup behind the shared close boundary.

The built-in registry is created per invocation and contains the always
available `python` backend plus `native`, `native-parallel`, `cuda`, and
`cuda-multi`.
Native availability is determined by one controlled optional-extension import.
CUDA remains uninitialized in an ordinary CPU registry and initializes its
explicit device only for CUDA listing or selection. `auto` deterministically
remains `python`; `cpu` is retained as a configuration compatibility alias.
Explicit unavailable backend selection fails before a live mining connection
is opened, as do invalid parallel worker or CUDA device configurations.
The same selected instance survives job changes, local work progression, and
fresh Stratum sessions. Python orchestration treats each prepared work value as
call-scoped. A backend may retain only an implementation-private derived cache
behind its lifecycle boundary and must replace it on any byte-level work or
target change.

`PythonSequentialBackend` delegates to the existing validated
`search_nonce_range` function and is the correctness oracle for future
implementations. It declares deterministic sequential order with no parallel
search, device selection, or cooperative mid-range cancellation. Execution
errors are terminal and never cause automatic fallback or Stratum reconnect.

The portable C extension receives only the 76-byte header prefix, two lossless
32-byte little-endian targets, and exact nonce bounds. Its Python wrapper owns
timing and constructs the public result. It recomputes any reported candidate
digest and both target flags with existing Python primitives before the
candidate can reach submission. The C loop releases the GIL but creates no
thread and uses no assembly or platform library.

`NativeParallelBackend` owns one persistent Python `ThreadPoolExecutor` per
selected backend instance. It divides the strategy-supplied parent interval
into deterministic ascending, contiguous, balanced half-open assignments whose
union is exact and whose intersections are empty. Every assignment calls the
same verified native wrapper exactly once. Completion order cannot affect the
result: reduction selects the lowest qualifying nonce, sums actual worker hash
counts, and records one parent-call wall-clock interval rather than summed
worker time.

The pool is created lazily, survives chunks, work changes, local progression,
and recovered Stratum sessions, and is closed once by caller-owned backend
cleanup. It never owns settings loading, job state, reconnect, submission,
events, signals, or client cleanup. Running native calls cannot yet be
cooperatively interrupted; a stop prevents the next chunk after the current
parallel call finishes. `HASHPHERE_COMPUTE_WORKERS` is validated by
configuration and passed into backend construction without being interpreted
by mining orchestration.

`CudaBackend` is a verified Python wrapper around the optional `_cuda`
extension. The extension owns one correctness-first grid-stride kernel for an
exact supplied parent range, persistent device buffers, and deterministic
atomic-minimum candidate reduction. Host preparation computes the SHA-256 state
after the fixed first 64-byte header block. The device specializes the remaining
16 header bytes and fixed 32-byte second SHA-256 pass, eliminating per-thread
header reconstruction and the former stack-resident generic schedule. Derived
work is uploaded only when the prefix or either target changes; every range
still resets its candidate result. The complete kernel evaluates the full range,
so its hash count is exact. It neither sees strategy state nor owns Stratum,
work progression, submission, settings loading, output, events, or client
cleanup.

The wrapper reconstructs the exact 80-byte header for every device candidate,
rehashes it through the existing Python `hash_block_header`, and independently
checks both targets through `hash_meets_target`. A nonce, count, flag, range, or
verification mismatch is terminal; no unverified device result can reach
submission. One selected CUDA instance and device ordinal survive chunks,
work changes, and recovered Stratum sessions and are closed once by the same
caller-owned backend cleanup as CPU resources. Cleanup synchronizes owned work
without resetting unrelated global CUDA state.

`CudaMultiBackend` owns one isolated `CudaBackend` context per explicit ordinal
and one persistent host thread pool. It reuses the native-parallel partitioning
primitive, pairs ascending nonempty child ranges with canonical ascending
ordinals, and waits for all full-range CUDA results. Reduction requires exact
aggregate accounting and selects the global lowest Python-verified candidate.
Native CUDA operations release the GIL, so different device contexts can run
concurrently. Any child failure cancels pending work, waits for active kernels,
closes every context, and permanently fails the logical backend without
fallback or Stratum reconnect.

CUDA compilation is deliberately gated by `HASHPHERE_BUILD_CUDA=1` and requires
one narrowly validated numeric `HASHPHERE_CUDA_ARCH`; it neither guesses from
the host nor accepts raw compiler flags. Normal source and wheel builds do not
invoke `nvcc`, while source distributions retain the CUDA source. The default
test suite uses an injected extension-shaped fake and never initializes CUDA.
The gated real-device suite passes on a CUDA 13.0 NVIDIA GB10 build containing
an `sm_121` cubin.
Sequential and orbiting-bit remain backend-independent and compatible.
Multi-GPU ownership, deterministic reduction, and external performance-profile
policy are implemented, with physical two-device validation still pending.
Windows CUDA builds and portable published CUDA wheels remain future boundaries.

The native extension is optional at build and import time, preserving
Python-only installs; both native modes then report controlled unavailability.
The CUDA extension is even more explicit and never participates in a normal
CPU build. Every backend preserves the same range and result semantics and
remains unaware of network and logging-file ownership.
Detailed contracts are in
[`docs/05-compute-backends.md`](docs/05-compute-backends.md) and
[`docs/06-native-cpu.md`](docs/06-native-cpu.md), with parallel lifecycle detail
in [`docs/07-parallel-cpu.md`](docs/07-parallel-cpu.md) and CUDA design in
[`docs/10-cuda-backend.md`](docs/10-cuda-backend.md), with multi-device
orchestration in [`docs/11-multi-gpu.md`](docs/11-multi-gpu.md).

---

# Chunked Mining Application Boundary

`hashphere.mining.chunks` owns finite chunk budgeting, invocation-wide hash
accounting, ordered between-chunk notification processing, job
replacement, replacement-work preparation, and stopping after budget
exhaustion or the first candidate. Its per-work strategy cursor supplies each
parent range within the finite budget. It composes deterministic mining
primitives through small injected preparation, search, polling, submission,
strategy, and observer boundaries; it owns no socket, file, settings, or
console output.

The CLI owns live opt-ins, configuration, client and event-sink construction,
initial authorized job acquisition, one invocation-scoped extra nonce, human-
readable output, compute-backend selection, and deterministic cleanup.
It also selects and compatibility-checks one search strategy before networking;
the same strategy definition is reused throughout the invocation.
`StratumClient` retains ownership of notification queues and nonblocking
polling. Coinbase, Merkle, header, target, and nonce-search primitives remain
deterministic and unaware of orchestration.

Observability passively records callbacks selected by CLI orchestration. It
does not choose ranges, apply difficulty, replace work, submit shares, or
control mining progress.

---

# Continuous Mining Application Boundary

`hashphere.mining.continuous` owns repeated half-open chunk scheduling, the
current per-job nonce position, cumulative session accounting, ordered
notification draining, newest-job replacement, deterministic work-space
advancement, terminal exhaustion waits, cooperative stop checks, and the one
terminal submission. It composes the same deterministic preparation,
progression, and range-search primitives through injected callbacks. It owns
no sockets, settings, signal registration, console output, event files, or
cleanup.

The read-only `StopToken` protocol is the orchestration boundary for graceful
shutdown; `StopController` supplies an idempotent implementation. The CLI owns
installation and restoration of portable Ctrl-C and termination handlers.
Those handlers only request stop. A current synchronous Python range may
finish, while the orchestrator prevents another search or replacement poll.

The session-recovery owner acquires initial authorized work through bounded
0.25-second client polls so shutdown remains responsive before mining begins.
`StratumClient` continues to own protocol state and queues. At each completed
nonce boundary, the orchestrator drains queued pool notifications before
advancing local work. Only final terminal progression exhaustion uses bounded
waits for a newer job.

## Stratum Session-Recovery Boundary

`hashphere.mining.recovery` owns the immutable reconnect policy, deterministic
delay calculation, interruptible backoff, injectable client factory, and the
currently usable `StratumMiningSession`. A session groups exactly one fresh
client, subscription, assembler, usable difficulty-snapshotted job, negotiated
extra-nonce seed, and session index. Recovery closes an unusable client
best-effort before another attempt and never reuses its notification queue,
request IDs, assembler, prepared work, progression cursor, or nonce seed.

Only `StratumConnectionError` is recoverable. Normal bounded receive timeout is
control flow, while protocol, authorization, mining, observability, and
programming errors remain terminal. Recovery requires a fresh authorization,
then a new-session difficulty followed by a usable job before publishing the
session. Each successfully authorized session receives exactly one random seed
at its newly negotiated width; progression within that session is
deterministic. Invocation-wide chunk, hash, elapsed-time, candidate,
submission, and recovery totals remain owned by continuous orchestration and
survive session replacement.

The CLI owns environment loading, configured-endpoint selection, signal
registration, final session closure, event-sink lifecycle, console output, and
exit codes. Its stop token interrupts backoff and prevents later client
creation or search. `StratumClient` remains unaware of retry policy, and the
transport remains unaware of client/session state. A submission failure is
terminal and bypasses recovery because retrying an uncertain request could
duplicate `mining.submit`. Pool failover is not part of this owner and remains
deferred.

## Mining Work-Space Progression Boundary

`hashphere.mining.progression` owns a compact immutable cursor over the search
hierarchy: pool job, effective network time, fixed-width `extra_nonce_2`, then
the nonce range scheduled by continuous orchestration. One caller-generated
extra-nonce seed initializes the cursor. Successors are arithmetic modulo the
negotiated space; a complete cycle rolls network time by one second and resets
to that same seed. The cursor neither generates randomness nor owns sockets,
settings, stop signals, output, persistence, or submission.

`MiningJobContextIdentity` identifies pool work and its acceptance context,
while `MiningWorkIdentity` uses the prepared 76-byte header prefix, job ID, and
both targets. The first prevents an identical notification from restarting
work; the second prevents an effectively identical prepared variant from
reusing the configured nonce start. A changed share target is deliberately a
new acceptance context. These identities and arithmetic counters remain
bounded in size and do not retain a set of searched nonces or extra-nonce
values.

Continuous orchestration owns the priority decision: after a completed chunk
and stop check, queued pool work supersedes local progression. Only the final
newest job from one drain is prepared. A replacement abandons local time and
extra-nonce cursor state, restarts from the current session seed and the pool's
network time, and preserves session totals.

Continuous observers remain passive adapters into `EventSink`. The CLI still
owns opt-ins, configuration, recovery-owner and sink construction, sanitized
output, final session closure, sink closure, and signal restoration. Reconnect
and session recovery remain outside transport, progression, and hashing
primitives.

---

# Observability Boundary

`hashphere.observability` owns structured event validation, persistent JSON
Lines storage, and read-only log analysis. An `EventSink` abstraction lets CLI
orchestration emit the same sanitized event catalog whether persistence is
enabled or disabled. The no-op sink avoids conditional logging branches
throughout command and mining flows; the JSONL sink owns directory creation,
append mode, UTF-8 encoding, event envelopes, sequencing, flushing, and file
closure. The analyzer separately opens existing files read-only, validates
schema and per-run integrity, and returns immutable aggregate results.

Networking, cryptographic, and mining-domain components remain unaware of log
paths. The CLI observes their typed results and emits explicitly selected safe
fields through an injected sink; event writers append validated records, and
analyzers read and aggregate them. Raw protocol messages, credentials, extra
nonces, complete coinbase data, and arbitrary exception messages do not cross
the observability boundary. The CLI formats analyzer results without exposing
raw records or identifiers. Recovery observers emit controlled lifecycle
events, and the analyzer counts connection losses, reconnect attempts,
successes, failures, and exhaustion without owning retry decisions.

JSONL is the first local persistence format. Rotation, retention,
machine-readable summary output, and external telemetry exporters remain
separate future components.

---

# Compute Profile Boundary

`hashphere.config.profile` owns immutable policy inputs and deterministic
resolution. It depends only on a narrow capability protocol and produces a
sanitized `ResolvedComputeProfile`; it does not construct a selected backend,
open a socket, read credentials, build Bitcoin work, or verify candidates.
Tests supply fake capabilities. The command-time local provider in
`hashphere.compute.profile` may check native availability and explicitly
permitted CUDA ordinals, closing every probe before final backend construction.

The CLI owns precedence, resolves the profile once, applies the effective
backend/worker/device/launch/chunk/pacing values, emits one profile event, and
then uses the existing backend registry and mining orchestration. Search
strategies and Stratum do not branch on profile names. Lite pacing is part of
the continuous parent-range boundary: bounded notification waits remain
interruptible by stop, runtime, replacement, liveness, and recovery, and exact
range/hash accounting stays unchanged. Raw compute elapsed remains backward
compatible; profiled commands add effective wall-clock accounting.

Profiles never inventory all devices implicitly. Auto and Max check device 0
unless an exact ordinal or list was supplied; multiple devices require the
list. A backend failure after resolution is terminal and cannot trigger an
execution-time fallback. See
[`docs/12-performance-profiles.md`](docs/12-performance-profiles.md).

---

# Architectural Goals

Hashphere is designed around a simple principle:

> **Write the mining engine once and run it everywhere.**

The architecture should allow the vast majority of the codebase to remain platform independent. Operating system differences should be isolated to small platform-specific adapters.

Future enhancements such as GPU acceleration, Stratum V2 support, new hashing backends, telemetry systems, and additional hardware platforms should be implemented by extending existing modules rather than restructuring the project.

Success is measured by:

- High code reuse across platforms
- Clear module boundaries
- Easy testing
- Easy maintenance
- Incremental extensibility

---

# Long-Term Vision

The long-term objective is to build a professional, well-engineered Bitcoin mining application that can run consistently across multiple operating systems while maintaining a single shared codebase.

The architecture should support future capabilities including:

- Solo mining
- Stratum pool mining
- Multiple Bitcoin RPC providers
- GPU acceleration
- ASIC benchmarking
- Performance profiling
- Telemetry and metrics
- Plugin-based extensions
- Additional hardware backends

New functionality should be added by extending existing modules rather than introducing unnecessary complexity or restructuring the project.

As the project grows, maintaining simplicity, readability, and modularity will remain higher priorities than adding features quickly.
