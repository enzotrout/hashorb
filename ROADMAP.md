# Hashphere

**Project:** Bitcoin CPU Miner and Learning Platform

**Status:** Active Development

**Repository Version:** Pre-Alpha

---

# Vision

Hashphere is an educational Bitcoin mining project whose goals are:

- Teach how Bitcoin mining actually works.
- Produce a real Bitcoin miner with no stubbed components.
- Run on macOS, Windows, Linux, and Docker with nearly identical code.
- Eventually support GPUs and additional hardware accelerators.
- Provide an attractive real-time dashboard.
- Remain understandable and well documented for developers learning the Bitcoin protocol.

---

# Design Principles

- Simplicity first
- Correctness before optimization
- Cross-platform by design
- Real mining only
- Minimal platform-specific code
- Reproducible development environment
- Documentation is treated as first-class code

---

# Current Phase

**Phase 2 — CPU Miner and Stratum Integration**

Progress:

- ✅ Runtime configuration and synchronous Stratum transport
- ✅ Subscribe, authorize, notification parsing, and authenticated share submission
- ✅ Immutable mining jobs and deterministic coinbase, Merkle, and header construction
- ✅ Double-SHA256, target calculation, and proof-of-work comparison
- ✅ Prepared mining work and bounded sequential nonce search
- ✅ Opt-in bounded live Stratum mining with at most one submission
- ✅ Timeout-aware Stratum notification polling
- ✅ Sanitized structured JSONL event logging
- ✅ Built-in structured-log summary command
- ✅ Bounded chunked mining and notification handling between chunks
- ✅ Continuous synchronous mining lifecycle and controlled shutdown
- ✅ Search-space expansion with extra-nonce and network-time progression
- ✅ Duplicate-work prevention across local progression and pool reannouncements
- ✅ Single-endpoint reconnect and fresh-session recovery
- ✅ Compute-backend contract, deterministic registry, and Python reference backend
- ✅ Portable optimized native C CPU backend
- ✅ Portable parallel native CPU backend
- ✅ Search-strategy abstraction and sequential reference strategy
- ✅ Deterministic orbiting-bit search strategy
- ✅ GPU/CUDA correctness backend validated on DGX Spark GB10
- ✅ DGX Spark CUDA SHA-256 performance tuning and repeated offline benchmark
- ✅ Deterministic explicit multi-CUDA orchestration architecture
- ✅ Lite / Auto / Max / Custom performance profiles
- ✅ Portable CPU package, console command, and offline doctor
- ✅ User-local Linux/macOS and Windows installation boundaries
- ✅ Non-root Docker CPU image
- ✅ Linux/macOS/Windows CPU packaging CI architecture
- ✅ Strict Bitcoin Core RPC, template, coinbase, SegWit, and complete-block construction
- ✅ Bounded Bitcoin Core true-solo lifecycle, readiness command, proposal, and submission
- ✅ Deterministic fake-RPC suite and opt-in isolated regtest gate
- ⬜ Pool failover

---

# Phase 1 — Architecture

Planned:

- Repository layout
- Package structure
- Configuration system
- Logging
- Environment variables
- Shared mining engine

---

# Phase 2 — CPU Miner

Progress:

- ✅ Bitcoin Core block-template retrieval
- ✅ Stratum mining-job ingestion
- ✅ Coinbase creation
- ✅ Merkle root calculation
- ✅ Header assembly and hashing
- ✅ SHA256d engine
- ✅ Bounded nonce search
- ✅ Stratum share submission
- ✅ One bounded live mining range
- ✅ Timeout-aware notification polling
- ✅ Sanitized structured JSONL event logging
- ✅ Built-in structured-log analysis
- ✅ Bounded chunked mining
- ✅ Continuous mining lifecycle
- ✅ Extra-nonce progression, network-time rolling, and duplicate-work prevention
- ✅ Single-endpoint reconnect and session recovery
- ✅ Compute-backend abstraction and Python sequential reference backend
- ✅ Portable optimized native C CPU mining
- ✅ Portable parallel native CPU mining
- ✅ Search-strategy abstraction and sequential reference strategy
- ✅ Deterministic orbiting-bit search strategy
- ✅ GPU/CUDA correctness backend validated on DGX Spark GB10
- ✅ DGX Spark CUDA SHA-256 performance tuning and repeated offline benchmark
- ✅ Deterministic explicit multi-CUDA orchestration architecture
- ⬜ Pool failover
- ✅ Direct block submission through the separate Bitcoin Core true-solo boundary
- ⬜ Persistent best hash

---

# Phase 3 — Dashboard

Planned:

- Node status
- Worker status
- Hash rate
- Best hash
- Bits away
- Historical statistics
- Mining efficiency

---

# Phase 4 — Cross Platform Packaging

Current status:

- ✅ One shared CPU package and console entry point
- ✅ Linux ARM64 package and Docker CPU validation on the Spark
- ✅ macOS CPU CI job and user-local guidance; current HEAD not yet runner-validated
- ✅ Windows CPU CI job and PowerShell guidance; current HEAD not yet runner-validated
- ⬜ Published platform wheels and releases, blocked on license selection
- ⬜ Docker NVIDIA image with a maintainable Python 3.13/CUDA base pairing

Goal:

Over 80% of the codebase should be shared across all platforms.

---

# Phase 5 — Advanced Features

Planned:

- ✅ Stratum pool mining
- ✅ Bitcoin Core true-solo mining
- ✅ Structurally submission-free Bitcoin Core hash-only operation
- ✅ GPU acceleration
- ✅ NVIDIA DGX Spark support
- ✅ Benchmark mode
- REST API
- Web dashboard
- Remote monitoring

---

# Development Standards

- Python 3.13
- uv
- Ruff
- mypy
- pytest
- Git
- GitHub
- VS Code

---

# Current Milestone

**Bitcoin Core True-Solo Architecture — Implemented and Accepted by Core Regtest**

Objective:

One versioned shared package now supplies the `hashsphere` console command,
offline doctor, CPU source and wheel builds, user-local installers, a non-root
Docker CPU image, archive privacy checks, clean-installed smokes, and a
Linux/macOS/Windows CPU CI matrix. The Linux CUDA build stays explicit and
local; no mining core was copied into platform directories.

The post-tuning human gates sustained approximately 2.462 GH/s for 60 seconds
and 2.461 GH/s for five minutes. The longer run checked 737,414,244,096 hashes
over 1,547 parent ranges and ended with `runtime_limit_reached`, with no
duplicate work, connection loss, reconnect, stale session, or command failure.

The Spark's one physical GPU passes the expanded real parity suite, including
all four validated launch sizes and one-device `cuda-multi`. New paired
500-million-hash measurements put normal `cuda` and one-device `cuda-multi`
within about 0.19%. Lite pacing reduced sampled utilization and approximate
power while lowering effective wall-clock throughput as intended. The
four-profile live human gate then measured about 1.145 GH/s effective for Lite
and 2.754–2.756 GH/s for Auto, Max, and Custom; all four ended at the runtime
limit with no failure, duplicate work, reconnect, or stale session. This does
not validate physical multi-GPU execution or scaling. No live pool command was
run during packaging validation.

Bounded chunking, continuous lifecycle management, JSONL writing, native
analysis, search-space expansion, single-endpoint session recovery, the
compute-backend boundary, portable native sequential execution, and portable
parallel execution, the strategy abstraction, and both sequential and
orbiting-bit orders remain complete. Conservative suspend-gap inference remains
deferred because platform clocks differ and scheduler delay is not proof of
suspend. Real two-device validation remains pending, followed by executed
macOS and Windows packaging runners,
broader pool support, distributed workers and
adaptive tuning, then Prometheus/Grafana-compatible metrics. Pool failover
remains a separate recovery milestone. Fibonacci-bounce, random, strided,
partitioned-global, and probabilistic search orders remain a later experimental
strategy-expansion point.

The isolated wallet-free Bitcoin Core v31.1 regtest gate now accepts one
Hashsphere-constructed block and advances the private chain from height 0 to 1.
The gate exposed and corrected Core's consensus `CScript` integer encoding for
BIP34 heights 1 through 16; proposal rejection categories remain sanitized and
proposal-rejected blocks are never submitted.

The read-only synchronized-mainnet readiness gate now also passes through
loopback cookie RPC. Live Core v31.1 compatibility preserves repeated per-input
dependency indices and optional fee/sigops metadata while retaining strict
transaction identities, weight, SegWit, target, and mandatory-rule checks.
Readiness parser diagnostics expose only fixed categories and field paths.

---

# Next Session

Continue with:

**Run the new CPU packaging workflow on macOS and Windows runners, select a
project license before publication, and run the explicit two-device hardware
gate only on a host with at least two real CUDA devices. Keep Windows CUDA,
portable CUDA wheels, Docker NVIDIA, long polling, thermal feedback, and
runtime profile switching deferred until their prerequisites are cleanly
validated.**
