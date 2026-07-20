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
