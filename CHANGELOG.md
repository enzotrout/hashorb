# Changelog

All notable changes to HashOrb will be documented in this file.

The project is currently in active pre-release development. Entries are grouped under **Unreleased** until a GitHub release is published.

## Unreleased

### Added

- Interactive Bitcoin payout-address onboarding for live mining.
- Built-in search strategies including Sequential, Orbiting Bit, and Fibonacci Bounce.
- CPU, native parallel, CUDA, and experimental multi-device CUDA compute paths.
- CKPool Stratum mining, Bitcoin Core true-solo tooling, structured JSONL events, and terminal dashboard support.
- Cross-platform packaging and installed-distribution smoke tests.
- Repository contribution, issue, pull-request, security, and development documentation.

### Changed

- Simplified multi-machine operation to one independent HashOrb miner per machine.
- Improved installed CLI `.env` discovery from the current working directory and its parents.

### Security

- Added automated dependency, secret, source, workflow, and container-image scanning.
- Added expiring risk-acceptance records for upstream Bookworm image CVEs when no fixed package is available and the affected capabilities are outside the hardened HashOrb runtime.

## Release policy

HashOrb follows semantic versioning once releases are published. During the current alpha period, breaking changes may still occur between releases and will be called out here and in release notes.
