# Development Environment

## What

HashOrb uses Python 3.13, `uv`, a repository-local virtual environment, and locked dependencies for development and validation.

## Why

The project contains Python, an optional native C extension, and an optional CUDA extension. A reproducible environment keeps tests, linting, typing, packaging, and native builds tied to the same interpreter and dependency set.

## Plain Talk

Work inside the repository environment instead of installing development dependencies into the operating system's Python. `uv` keeps the environment repeatable, and the repository's `dev` helper runs the checks expected before review.

## Standard Toolchain

- CPython 3.13
- `uv`
- `.venv`
- `pyproject.toml`
- `uv.lock`
- pytest
- Ruff
- mypy

## Start a Development Checkout

From the repository root:

```bash
./dev start
```

On Windows:

```powershell
python .\dev start
```

The helper prepares the locked environment without downloading a different Python interpreter behind your back.

You can also use `uv` directly:

```bash
uv sync --locked --no-python-downloads
```

## Routine Validation

Fast local checks:

```bash
./dev check
```

Full pre-review gate:

```bash
./dev full
```

Equivalent core commands include:

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy src
uv run pytest -q
uv lock --check
```

## CPU and CUDA Boundary

Portable development and packaging should work without CUDA. The Python backend is always the correctness baseline, and the optional native C extension is used when it builds successfully.

CUDA development requires an explicitly configured NVIDIA Linux host, CUDA toolkit, target architecture, and device selection. CUDA is not part of the portable CPU installation contract.

For installation rather than development, use the [Quick Start Guide](QUICKSTART.md). For the repository workflow, see [Development Workflow](development.md) and [Git Workflow](01-git-workflow.md).