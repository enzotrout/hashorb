# Python Development Environment

## Purpose

Hashphere uses an isolated and reproducible Python environment.

The project does **not** install dependencies into the macOS system Python or into the older miner virtual environment.

---

## Standard Versions

- Python: 3.13
- Environment manager: uv
- Virtual environment: `.venv`
- Project metadata: `pyproject.toml`
- Dependency lock file: `uv.lock`

---

## Installation

Install uv (macOS):

```bash
brew install uv
```

Create the virtual environment:

```bash
uv venv --python 3.13
```

Activate it:

```bash
source .venv/bin/activate
```

Install project dependencies:

```bash
uv sync --locked
```

This also attempts to build Hashphere's optional portable native C extension
when a platform compiler is available. The extension is not required for the
default Python backend or a Python-only installation. Native development and
clean-build instructions are documented in
[`06-native-cpu.md`](06-native-cpu.md).

---

## Verification

Verify the Python version:

```bash
uv run python --version
```

Run Ruff:

```bash
uv run ruff check .
```

Run mypy:

```bash
uv run mypy tests
```

Run pytest:

```bash
uv run pytest
```

Verify the lock file:

```bash
uv lock --check
```

---

## Development Tools

| Tool | Purpose |
|------|---------|
| pytest | Unit testing |
| Ruff | Linting, formatting, import sorting |
| mypy | Static type checking |
| uv | Python environment and dependency management |

---

## Environment Separation

Legacy experimental miner:

```text
~/venvs/miner
```

Hashphere environment:

```text
~/Development/Hashphere/.venv
```

The virtual environment is **never committed to Git**.

Only these files are version controlled:

- `pyproject.toml`
- `uv.lock`

These files allow any machine to recreate the exact development environment.

---

## Current Validation

- Python 3.13.7
- Ruff ✔
- mypy ✔
- pytest ✔ (3 tests passed)
- uv lock ✔
