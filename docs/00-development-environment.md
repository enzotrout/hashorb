# Milestone 0.3 Summary

## Python Development Environment

The Hashphere project uses an isolated Python development environment based on
Python 3.13 and `uv`. The recommended workflow is to let `uv` provision the
project Python locally, then run `uv sync` from the repository root so the
same interpreter is used for tests, linting, and CUDA builds.

### Development Standards

- Python 3.13
- uv
- `.venv`
- `pyproject.toml`
- `uv.lock`

### Development Toolchain

- pytest
- Ruff
- mypy

### Validation

The following commands should succeed on every developer workstation:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest -q
uv lock --check
```

### Resource Strategy

CPU development and packaging should remain reproducible on Linux, macOS, and
Windows CI runners. CUDA development and GPU benchmarking require an explicitly
configured NVIDIA Linux host; they are not part of the portable CPU gate.
