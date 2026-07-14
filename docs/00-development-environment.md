# Milestone 0.3 Summary

## Python Development Environment

The Hashphere project uses an isolated Python development environment based on
Python 3.13 and `uv`.

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
uv run mypy tests
uv run pytest
uv lock --check
```

### Resource Strategy

The MacBook serves as the primary development workstation.

Large AI models, CUDA development, GPU benchmarking, and future model-assisted
tasks should execute on the DGX Spark.
