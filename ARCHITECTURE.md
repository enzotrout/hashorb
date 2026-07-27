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

# Chunked Mining Application Boundary

`hashphere.mining.chunks` owns finite chunk-range calculation, invocation-wide
hash accounting, ordered between-chunk notification processing, job
replacement, replacement-work preparation, and stopping after budget
exhaustion or the first candidate. It composes deterministic mining primitives
through small injected preparation, search, polling, submission, and observer
boundaries; it owns no socket, file, settings, or console output.

The CLI owns live opt-ins, configuration, client and event-sink construction,
initial authorized job acquisition, one invocation-scoped extra nonce, human-
readable output, and deterministic cleanup. `StratumClient` retains ownership
of notification queues and nonblocking polling. Coinbase, Merkle, header,
target, and nonce-search primitives remain deterministic and unaware of
orchestration.

Observability passively records callbacks selected by CLI orchestration. It
does not choose ranges, apply difficulty, replace work, submit shares, or
control mining progress. This keeps a future continuous lifecycle above the
same finite chunk primitive without moving application state into protocol or
compute layers.

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
raw records or identifiers.

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
