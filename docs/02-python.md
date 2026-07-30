# Python Development Environment

## Purpose

Hashphere uses an isolated and reproducible Python environment.

The project does **not** install dependencies into a platform system Python.

---

## Standard Versions

- Python: 3.13
- Environment manager: uv
- Virtual environment: `.venv`
- Project metadata: `pyproject.toml`
- Dependency lock file: `uv.lock`

---

## Installation

Install uv separately using a method you have reviewed. For example, on macOS
with an existing Homebrew installation:

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
uv run mypy src
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

Hashphere environment:

```text
<repository>/.venv
```

The virtual environment is **never committed to Git**.

Only these files are version controlled:

- `pyproject.toml`
- `uv.lock`

These files allow any machine to recreate the exact development environment.

---

## Current Validation

- Python 3.13 is the only declared interpreter line.
- Linux ARM64 Spark development and clean-install gates are current.
- Apple Silicon native builds were exercised previously, but the packaging
  changes on the current HEAD still require the macOS CI runner.
- Windows remains CI-configured and statically reviewed until its runner gate
  executes.
