# IDE Development Setup

## What

This guide prepares a contributor to work on HashOrb in an IDE with the repository's Python 3.13 environment, locked dependencies, linting, typing, tests, and safe debug profiles.

The repository includes first-class Visual Studio Code configuration. Other IDEs can use the same `.venv`, commands, and project files.

## Prerequisites

Install these before opening the project:

- Git
- CPython 3.13
- `uv`
- an IDE such as Visual Studio Code or PyCharm

Optional components:

- a C compiler for the native CPU extension
- NVIDIA drivers and CUDA toolkit for CUDA development on a supported NVIDIA Linux system

CUDA is not required for normal Python, documentation, test, or portable packaging contributions.

## Get the code

Fork the repository on GitHub if you plan to contribute through your own account, then clone your fork:

```bash
git clone https://github.com/YOUR-USERNAME/hashorb.git
cd hashorb
```

Add the upstream repository so you can keep your fork current:

```bash
git remote add upstream https://github.com/enzotrout/hashorb.git
git fetch upstream
```

Create a focused branch for each change:

```bash
git switch -c feature/short-description
```

## Prepare the development environment

From the repository root:

```bash
uv sync --locked --no-python-downloads
```

This creates or updates the repository-local `.venv` using the locked dependency set.

Verify the environment:

```bash
uv run python --version
uv run hashorb doctor
```

HashOrb expects Python 3.13.

## Visual Studio Code

Open the repository folder itself, not only an individual source file:

```bash
code .
```

When VS Code offers recommended extensions, install them. The repository recommends:

- Python
- Python Debugger
- Ruff
- Mypy Type Checker

The repository also supplies workspace settings, validation tasks, and safe offline debug configurations under `.vscode/`.

### Select the interpreter

VS Code should discover the repository `.venv`. If it does not:

1. Open the Command Palette.
2. Choose **Python: Select Interpreter**.
3. Select the interpreter inside the repository `.venv`.

Typical interpreter locations are:

```text
macOS/Linux: .venv/bin/python
Windows:     .venv\Scripts\python.exe
```

Do not point the workspace at a global Python installation when working on HashOrb.

### Run repository tasks

Use **Terminal > Run Task** to run the supplied tasks:

- `HashOrb: Sync environment`
- `HashOrb: Ruff format check`
- `HashOrb: Ruff lint`
- `HashOrb: mypy`
- `HashOrb: pytest`
- `HashOrb: Full validation`

The full validation task runs the normal formatting, lint, typing, and test checks in sequence.

Equivalent terminal commands are:

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy src
uv run pytest -q
```

For the repository's complete pre-review helper, you can also run:

```bash
./dev full
```

On Windows:

```powershell
python .\dev full
```

### Debug safely

The repository includes VS Code debug profiles for:

- `HashOrb: doctor`
- `HashOrb: CPU benchmark`

These profiles are intentionally offline or diagnostic. They do not enable live Stratum mining.

To debug a different command, copy one of the launch profiles locally and change its `args`. Do not commit private addresses, credentials, wallet secrets, seed phrases, or machine-specific paths.

## Environment configuration

For configuration that should be loaded by HashOrb, start from the example file:

```bash
cp .env.example .env
```

On Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

A Bitcoin receive address is public information, but HashOrb never needs a wallet seed phrase, private key, or wallet password for normal CKPool mining.

Keep `.env` local. Do not commit personal configuration.

Live mining remains explicitly opt-in. Development, tests, `doctor`, and offline benchmarks should be used by default while changing code.

## PyCharm and other IDEs

The same repository environment works outside VS Code.

For PyCharm:

1. Open the HashOrb repository as the project.
2. Configure the project interpreter to use `.venv/bin/python` on macOS/Linux or `.venv\Scripts\python.exe` on Windows.
3. Keep the project root as the working directory.
4. Configure test execution with pytest.
5. Configure external tools or run configurations for the same `uv run` commands used by CI.

Do not create a separate IDE-managed dependency environment unless you have a specific reason. Reusing the repository `.venv` keeps local behavior aligned with the lock file and CI.

## Native CPU development

The native C extension is optional for normal contribution work. If your change affects native compute, rebuild the extension using the repository's documented native build path and run the native-specific tests before opening a pull request.

See [Compute Backends](05-compute-backends.md) and [Native CPU](06-native-cpu.md) for the implementation boundary.

## CUDA development

CUDA work should be done only on a supported NVIDIA Linux system with the CUDA toolkit and an explicitly selected target architecture.

Portable contributors do not need CUDA to run the normal Python test suite or work on most of the project.

See [CUDA Backend](10-cuda-backend.md), [Multi-GPU](11-multi-gpu.md), and [Performance Profiles](12-performance-profiles.md) before changing CUDA behavior.

## Before opening a pull request

Update your branch from upstream as needed, then run the full local checks:

```bash
uv sync --locked --no-python-downloads
uv run ruff format --check .
uv run ruff check .
uv run mypy src
uv run pytest -q
uv lock --check
```

Then review your diff:

```bash
git status
git diff --check
git diff upstream/main...HEAD
```

Your pull request should explain:

- what changed
- why it changed
- how it was tested
- any platform, native, CUDA, mining, or security implications

For the broader contribution process, see [Contributing](../CONTRIBUTING.md) and [Git Workflow](01-git-workflow.md).
