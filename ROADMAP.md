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
- ⬜ Built-in structured-log summary command
- ⬜ Chunked mining orchestration and notification handling between chunks
- ⬜ Continuous CPU mining and worker scheduling
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
- ⬜ Built-in structured-log analysis
- ⬜ Chunked and continuous mining
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

**Sanitized Structured JSONL Event Logging — Complete**

Objective:

Preserve existing human-readable console output while optionally appending
sanitized, versioned, machine-readable events for each live Stratum command.
The logging boundary must be synchronous, injectable, immediately flushed, and
unable to accept secret-bearing fields or raw protocol payloads.

Timeout-aware notification polling and structured logging are complete. A
small built-in log-summary command is the next slice; chunked orchestration
remains deferred until basic analysis is available. This milestone does not
introduce chunk scheduling, retries, reconnects, or continuous mining.

---

# Next Session

Continue with:

**Complete structured event logging, then add a small built-in log-summary
command before designing bounded nonce-chunk orchestration.**
