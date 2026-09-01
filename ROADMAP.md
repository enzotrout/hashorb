# HashOrb

**HashOrb: Bitcoin mining and hashing, one independent miner at a time.**

HashOrb deliberately favors a simple deployment model: one HashOrb instance per machine. Multiple machines can mine independently through a Stratum service such as CKPool using the same Bitcoin payout address, so HashOrb does not need its own distributed-worker coordinator or swarm layer.

**Project:** Bitcoin CPU/GPU Miner and Learning Platform

**Status:** Active Development

**Repository Version:** Pre-Alpha

---

# Vision

HashOrb is an educational Bitcoin mining project whose goals are:

- Teach how Bitcoin mining actually works.
- Produce a real Bitcoin miner with no stubbed components.
- Make it straightforward to install and mine on one or several independent machines.
- Run on macOS, Windows, Linux, and Docker with nearly identical code.
- Support CPU and NVIDIA CUDA compute where validated.
- Provide an attractive real-time dashboard.
- Remain understandable and well documented for developers learning the Bitcoin protocol.

---

# Design Principles

- Simplicity first
- Correctness before optimization
- One machine, one independent HashOrb miner
- Cross-platform by design
- Real mining only
- Minimal platform-specific code
- Reproducible development environment
- Documentation is treated as first-class code

---

# Multi-Machine Mining

HashOrb does not plan to build a distributed swarm or DAG coordinator.

For Stratum mining, scale out by installing HashOrb independently on each machine and configuring each instance with the same Bitcoin payout address. CKPool accepts a Bitcoin address as the username with an optional worker extension, so machines can remain independent while mining toward the same payout identity.

This keeps scheduling, session management, and work distribution at the Stratum service boundary instead of introducing a HashOrb control plane. HashOrb remains responsible for mining correctly and observably on the machine where it runs.

---

# Current Phase

**Phase 2 — Miner and Stratum Integration**

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
- ✅ Deterministic Fibonacci Bounce search strategy
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
- ⬜ Persistent best hash

---

# Phase 1 — Architecture

Completed foundations:

- Repository layout
- Package structure
- Configuration system
- Logging
- Environment variables
- Shared mining engine

---

# Phase 2 — Miner

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
- ✅ Deterministic Fibonacci Bounce search strategy
- ✅ GPU/CUDA correctness backend validated on DGX Spark GB10
- ✅ DGX Spark CUDA SHA-256 performance tuning and repeated offline benchmark
- ✅ Deterministic explicit multi-CUDA orchestration architecture
- ⬜ Pool failover
- ✅ Direct block submission through the separate Bitcoin Core true-solo boundary
- ⬜ Persistent best hash

---

# Phase 3 — Dashboard and Observability

Current and planned:

- ✅ Terminal mining dashboard
- ✅ Hash rate and best-difficulty telemetry
- ✅ Search activity and runtime metrics
- ⬜ Persistent best hash across runs
- ⬜ Historical statistics
- ⬜ Mining efficiency history
- ⬜ Prometheus/Grafana-compatible metrics

---

# Phase 4 — Cross Platform Packaging

Current status:

- ✅ One shared CPU package and console entry point
- ✅ Linux ARM64 package and Docker CPU validation on the Spark
- ✅ macOS CPU CI architecture and user-local guidance
- ✅ Windows CPU CI architecture and PowerShell guidance
- ⬜ Complete executed macOS and Windows runner validation for the current release path
- ⬜ Published platform wheels and releases
- ⬜ Docker NVIDIA image with a maintainable Python 3.13/CUDA base pairing

Goal:

Over 80% of the codebase should be shared across all platforms.

---

# Phase 5 — Advanced Features

Current and planned:

- ✅ Stratum pool mining
- ✅ Bitcoin Core true-solo mining
- ✅ Structurally submission-free Bitcoin Core hash-only operation
- ✅ GPU acceleration
- ✅ NVIDIA DGX Spark support
- ✅ Benchmark mode
- ⬜ Pool failover
- ⬜ REST API
- ⬜ Web dashboard
- ⬜ Remote monitoring

Distributed-worker coordination is intentionally not planned. Multi-machine Stratum mining uses independent HashOrb installations rather than a HashOrb swarm.

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

**Simple, independent Bitcoin mining across supported machines**

HashOrb has working Stratum mining, Bitcoin Core true-solo mining, CPU backends, NVIDIA CUDA support on validated Linux hardware, deterministic search strategies, performance profiles, packaging architecture, security gates, and terminal observability.

The deployment model is intentionally simple. Each computer runs its own HashOrb process and maintains its own mining session. For CKPool, multiple installations can use the same Bitcoin payout address, with optional worker extensions where useful for identification. No HashOrb coordinator is required.

The Spark's one physical GPU has passed the expanded real parity suite, including validated launch sizes and one-device `cuda-multi`. Real two-device validation remains pending and must be performed only on a host with at least two physical CUDA devices.

The isolated wallet-free Bitcoin Core regtest gate has accepted a HashOrb-constructed block. The read-only synchronized-mainnet readiness gate and submission-free `solo-hash` path have also been validated against live Bitcoin Core.

Pool failover and persistent best-hash state remain the main unfinished mining/runtime items. Packaging validation, release publication, broader observability, and optional remote interfaces follow without introducing distributed-worker coordination.

---

# Next Session

Continue with:

**Finish the remaining single-miner reliability and release path: pool failover, persistent best-hash state, executed macOS/Windows packaging validation, and release packaging. Keep multi-machine operation simple by running independent HashOrb instances against the same Stratum payout identity. Keep Windows CUDA, portable CUDA wheels, Docker NVIDIA, long polling, thermal feedback, and runtime profile switching deferred until their prerequisites are cleanly validated.**
