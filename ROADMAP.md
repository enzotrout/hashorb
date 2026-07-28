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
- 🟨 GPU/CUDA correctness backend implemented; hardware validation gated
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
- ✅ Extra-nonce progression, network-time rolling, and duplicate-work prevention
- ✅ Single-endpoint reconnect and session recovery
- ✅ Compute-backend abstraction and Python sequential reference backend
- ✅ Portable optimized native C CPU mining
- ✅ Portable parallel native CPU mining
- ✅ Search-strategy abstraction and sequential reference strategy
- ✅ Deterministic orbiting-bit search strategy
- 🟨 GPU/CUDA correctness backend implemented; hardware validation gated
- ⬜ Pool failover
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

**Optional CUDA Correctness Backend — Implemented, Hardware Gate Pending**

Objective:

Add an explicitly built and selected CUDA backend that searches exact parent
ranges, reduces to the smallest qualifying nonce, and verifies every device
candidate through the existing Python correctness primitives. CPU-only builds,
Python defaults, and both search strategies remain operational. CUDA source,
host integration, mocked tests, and build tooling are complete; `nvcc`, an
NVIDIA runtime, and a CUDA device are unavailable on the current validation
host, so device parity and performance remain unclaimed.

Bounded chunking, continuous lifecycle management, JSONL writing, native
analysis, search-space expansion, single-endpoint session recovery, the
compute-backend boundary, portable native sequential execution, and portable
parallel execution, the strategy abstraction, and both sequential and
orbiting-bit orders remain complete. CUDA hardware parity is the current gate,
followed by DGX Spark/GB10 correctness tuning, multi-GPU execution,
Lite/Auto/Max/Custom operating profiles, macOS/Windows/Linux/Docker packaging,
broader pool support and Bitcoin Core true solo mining, distributed workers and
adaptive tuning, then Prometheus/Grafana-compatible metrics. Pool failover
remains a separate recovery milestone. Fibonacci-bounce, random, strided,
partitioned-global, and probabilistic search orders remain a later experimental
strategy-expansion point.

---

# Next Session

Continue with:

**Compile and exercise the optional CUDA backend on NVIDIA hardware, then begin
DGX Spark/GB10 correctness tuning without making unsupported speed claims.**
