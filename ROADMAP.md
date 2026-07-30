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
- ✅ GPU/CUDA correctness backend validated on DGX Spark GB10
- ✅ DGX Spark CUDA SHA-256 performance tuning and repeated offline benchmark
- ✅ Deterministic explicit multi-CUDA orchestration architecture
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

**Lite / Auto / Max / Custom Performance Profiles — Implemented**

Objective:

An optional invocation-fixed profile now resolves user intent into the existing
backend, bounded worker, explicit-device, validated launch-size, parent-chunk,
and interruptible pacing controls. Omission preserves legacy behavior. Presets
reject ambiguous overrides, and actual multiple-GPU use still requires an
explicit list.

The post-tuning human gates sustained approximately 2.462 GH/s for 60 seconds
and 2.461 GH/s for five minutes. The longer run checked 737,414,244,096 hashes
over 1,547 parent ranges and ended with `runtime_limit_reached`, with no
duplicate work, connection loss, reconnect, stale session, or command failure.

The Spark's one physical GPU passes the expanded real parity suite, including
all four validated launch sizes and one-device `cuda-multi`. New paired
500-million-hash measurements put normal `cuda` and one-device `cuda-multi`
within about 0.19%. Lite pacing reduced sampled utilization and approximate
power while lowering effective wall-clock throughput as intended. This does not
validate physical multi-GPU execution or scaling. No live pool command was run
during this milestone.

Bounded chunking, continuous lifecycle management, JSONL writing, native
analysis, search-space expansion, single-endpoint session recovery, the
compute-backend boundary, portable native sequential execution, and portable
parallel execution, the strategy abstraction, and both sequential and
orbiting-bit orders remain complete. Conservative suspend-gap inference remains
deferred because platform clocks differ and scheduler delay is not proof of
suspend. Real two-device validation remains next, followed by
macOS/Windows/Linux/Docker packaging,
broader pool support and Bitcoin Core true solo mining, distributed workers and
adaptive tuning, then Prometheus/Grafana-compatible metrics. Pool failover
remains a separate recovery milestone. Fibonacci-bounce, random, strided,
partitioned-global, and probabilistic search orders remain a later experimental
strategy-expansion point.

---

# Next Session

Continue with:

**Run the explicit two-device offline and hardware gates on a host with at least
two real CUDA devices. Do not claim scaling until that gate passes. Keep
Windows CUDA builds, portable CUDA wheels, thermal feedback, and runtime profile
switching deferred.**
