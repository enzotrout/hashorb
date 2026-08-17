# HashOrb Documentation

This directory is the public technical documentation for HashOrb.

If you want to get HashOrb running quickly, start with the [Quick Start Guide](QUICKSTART.md). If you want to understand how the miner is built, use the topic map below.

HashOrb is an active pre-release learning project. The documentation describes implemented behavior unless a section is explicitly marked experimental, deferred, historical, or pending hardware validation.

## Quick Start

- [Quick Start: Linux, macOS, Windows, and Docker](QUICKSTART.md) — install, configure a public Bitcoin payout address, verify the environment, and start a short bounded Stratum mining run.

## Development and Packaging

- [Development Environment](00-development-environment.md) — **Plain talk:** the tools and checks needed to work on HashOrb without polluting the system Python.
- [Git Workflow](01-git-workflow.md) — **Plain talk:** how changes move from a task branch through local checks and a reviewed pull request.
- [Python Environment](02-python.md) — **Plain talk:** how Python 3.13, `uv`, `.venv`, and the lock file keep local development reproducible.
- [Development Workflow](development.md) — **Plain talk:** how the repository-local `dev` helper runs routine setup, checks, doctor, and review commands.
- [Installation and Packaging](13-installation-and-packaging.md) — **Plain talk:** how the same HashOrb package is installed and packaged across supported operating-system boundaries.

## Mining Architecture

- [Stratum and Compute Design](03-stratum-and-compute-design.md) — **Plain talk:** Stratum supplies pool work, HashOrb prepares it, a strategy chooses where to search, and a backend hashes that range.
- [Bitcoin Core True Solo](14-bitcoin-core-true-solo.md) — **Plain talk:** direct solo mining is a separate, explicitly armed path that can inspect, hash, validate, and submit complete blocks through a local Bitcoin Core node.
- [Observability](04-observability.md) — **Plain talk:** mining can write sanitized local JSONL events so behavior can be inspected without putting secrets into logs.
- [Terminal Dashboard](14-dashboard-tui.md) — **Plain talk:** the dashboard reads those sanitized events and displays mining state without controlling the miner.

## Compute Backends

- [Compute Backends](05-compute-backends.md) — **Plain talk:** backends decide how a supplied nonce range is hashed; they do not decide which range comes next.
- [Native CPU](06-native-cpu.md) — **Plain talk:** the optional C extension speeds up CPU hashing while Python remains the correctness reference.
- [Parallel CPU](07-parallel-cpu.md) — **Plain talk:** several CPU workers split one parent range without changing the mining rules.
- [CUDA Backend](10-cuda-backend.md) — **Plain talk:** one explicitly selected NVIDIA GPU searches a supplied range and Python independently verifies any reported candidate.
- [Multi-GPU](11-multi-gpu.md) — **Plain talk:** selected GPUs can divide one parent range, but broad real multi-GPU hardware validation is still pending.
- [Performance Profiles](12-performance-profiles.md) — **Plain talk:** Lite, Auto, Max, and Custom choose operating intensity without changing Bitcoin validity or probability for a fixed number of unique hashes.

## Search Strategies

- [Search Strategies](08-search-strategies.md) — **Plain talk:** strategies change the order in which ordinary nonce ranges are visited, not SHA-256 or Bitcoin validity.
- [Orbiting Bit](09-orbiting-bit.md) — **Plain talk:** a deterministic bit-reversal order spreads range visits across the nonce map while still covering every range once.
- [Fibonacci Bounce](17-fibonacci-bounce.md) — **Plain talk:** a deterministic Fibonacci-derived permutation changes range order while remaining exhaustive and duplicate-free.

## Security

- [Security Audit and Threat Model](15-security-audit.md) — historical deep-dive audit evidence and threat-model material. For current reporting and support policy, see the repository-root [`SECURITY.md`](../SECURITY.md).

## Documentation Status

The public docs intentionally exclude temporary handoff notes, development activity logs, and completed naming-migration instructions. Those records remain available in Git history but are not maintained as current user documentation.

When implementation and documentation disagree, treat the current code and tests as authoritative and open an issue or pull request to correct the docs.