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

**Phase 0 — Development Environment**

Progress:

- ✅ 0.1 Machine Audit
- ✅ 0.2 Git Setup
- ✅ 0.3 Python Environment
- ⬜ 0.4 VS Code
- ⬜ 0.5 ChatGPT / Codex
- ⬜ 0.6 Docker
- ⬜ 0.7 Bitcoin Core
- ⬜ 0.8 DGX Spark
- ⬜ 0.9 GitHub Project
- ⬜ 0.10 Engineering Standards
- ⬜ 0.11 Validation

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

Planned:

- Block template retrieval
- Coinbase creation
- Merkle root calculation
- Header assembly
- SHA256d engine
- Nonce search
- Multi-process mining
- Block submission
- Persistent best hash

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

**Milestone 0.4 — VS Code Professional Development Environment**

Objective:

Create a professional development environment with:

- Python integration
- Git integration
- Debugging
- Remote SSH
- Terminal integration
- ChatGPT Codex
- GitHub authentication

---

# Next Session

Continue with:

**Milestone 0.4 — VS Code Professional Setup**
