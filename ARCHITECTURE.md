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

# Compute Backend Boundary

`hashphere.compute` owns nonce-search execution contracts, immutable
low-cardinality capabilities, deterministic built-in registration and
selection, and translation of backend-local failures into controlled compute
errors. A backend receives one validated `PreparedMiningWork` plus an exact
half-open nonce interval and returns the existing immutable
`NonceSearchResult`. It does not construct Bitcoin work or own Stratum,
progression, recovery, submission, settings, signals, console output, event
files, or cleanup.

The built-in registry is created per invocation and contains the always
available `python` backend plus `native`, whose availability is determined by a
controlled optional-extension import. `auto` deterministically remains
`python`; `cpu` is retained as a configuration compatibility alias. Explicit
unavailable `native` selection fails before a live mining connection is opened.
The same selected instance survives job changes, local work progression, and
fresh Stratum sessions, while each prepared work value remains call-scoped and
is not retained after search.

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

The native extension is optional at build and import time, preserving
Python-only installs. Future parallel CPU and GPU implementations must preserve
the same range and result semantics and remain unaware of network and
logging-file ownership. Detailed contracts are in
[`docs/05-compute-backends.md`](docs/05-compute-backends.md) and
[`docs/06-native-cpu.md`](docs/06-native-cpu.md).

---

# Chunked Mining Application Boundary

`hashphere.mining.chunks` owns finite chunk-range calculation, invocation-wide
hash accounting, ordered between-chunk notification processing, job
replacement, replacement-work preparation, and stopping after budget
exhaustion or the first candidate. It composes deterministic mining primitives
through small injected preparation, search, polling, submission, and observer
boundaries; it owns no socket, file, settings, or console output.

The CLI owns live opt-ins, configuration, client and event-sink construction,
initial authorized job acquisition, one invocation-scoped extra nonce, human-
readable output, compute-backend selection, and deterministic cleanup.
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
