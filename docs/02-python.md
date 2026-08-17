# Python Environment

## What

HashOrb targets CPython 3.13 and uses `uv` to create and synchronize the repository-local virtual environment from `pyproject.toml` and `uv.lock`.

## Why

The miner needs a predictable interpreter and dependency set across development, tests, packaging, and optional native builds. Keeping those dependencies out of the platform system Python also makes cleanup and reproduction much easier.

## Plain Talk

Install Python 3.13 and `uv`, then let the repository create its own environment. Use `uv run ...` or the `dev` helper instead of mixing HashOrb dependencies into whatever Python your operating system happens to provide.

## Standard Versions

- Python: 3.13
- Environment manager: `uv`
- Virtual environment: `.venv`
- Project metadata: `pyproject.toml`
- Dependency lock file: `uv.lock`

## Prepare the Environment

If you already have Python 3.13 and `uv` available:

```bash
uv sync --locked --no-python-downloads
```

The repository helper wraps the normal development setup:

```bash
./dev start
```

Windows:

```powershell
python .\dev start
```

The sync may build HashOrb's optional portable native C extension when a suitable compiler is available. That extension is not required for the Python backend.

## Run Commands Inside the Environment

Examples:

```bash
uv run python --version
uv run hashorb doctor
uv run hashorb compute-benchmark --backend python --hash-count 100000
```

## Verification

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy src
uv run pytest -q
uv lock --check
```

For the normal repository workflow, prefer:

```bash
./dev check
./dev full
```

## Native and CUDA Builds

The Python backend is the portable correctness baseline.

The native C backend is optional and documented in [Native CPU](06-native-cpu.md). CUDA is a separate explicit Linux/NVIDIA source-build tier documented in [CUDA Backend](10-cuda-backend.md) and [Installation and Packaging](13-installation-and-packaging.md).

## User Installation

This page describes the development environment. To install the `hashorb` command and begin mining, use the [Quick Start Guide](QUICKSTART.md).