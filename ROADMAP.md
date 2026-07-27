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
- ⬜ Search-space expansion with extra-nonce and network-time progression
- ⬜ Continuous worker scheduling and recovery
- ⬜ Multiprocess CPU backend

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

- ⬜ Bitcoin Core block-template retrieval
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
- ⬜ Extra-nonce progression, network-time rolling, and duplicate-work prevention
- ⬜ Multi-process mining
- ⬜ Direct block submission
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

# Phase 4 — Cross Platform

Targets:

- macOS
- Windows
- Linux
- Docker

Goal:

Over 80% of the codebase should be shared across all platforms.

---

# Phase 5 — Advanced Features

Planned:

- Stratum pool mining
- Solo mining
- GPU acceleration
- NVIDIA DGX Spark support
- Benchmark mode
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

**Continuous Live Stratum Mining Lifecycle — Complete**

Objective:

Search sequential bounded nonce chunks until controlled shutdown, an optional
chunk limit, one submission result, or an unrecoverable failure. Maintain
ordered difficulty and job semantics, responsive bounded notification waits,
accurate cumulative metrics, and portable signal restoration.

Bounded chunking, JSONL writing, and native analysis remain complete. Search-
space expansion through `extra_nonce_2` progression, network-time rolling, and
duplicate-work prevention is next. Reconnects and pool failover follow that.
A compute-backend abstraction then precedes native and parallel CPU backends.
Sequential, partitioned, strided, and orbiting-bit strategies precede CUDA,
DGX Spark/GB10, multi-GPU, and host-side verification. Lite/Auto/Max/Custom
operating profiles, macOS/Windows/Linux/Docker packaging, broader Stratum pool
support, Bitcoin Core true solo mining, distributed workers, adaptive tuning,
and Prometheus/Grafana-compatible metrics remain deferred in that dependency
order.

---

# Next Session

Continue with:

**Expand search space safely with extra-nonce progression, network-time rolling,
and duplicate-work prevention.**
