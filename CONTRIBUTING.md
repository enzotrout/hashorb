# Contributing to HashOrb

Thanks for your interest in improving HashOrb.

HashOrb is an open source Bitcoin mining project focused on learning, experimentation, transparent mining behavior, and reproducible engineering.

## Before you start

For non-trivial changes, open an issue first so the proposed behavior and scope can be discussed before implementation.

Please keep changes focused. Small, reviewable pull requests are preferred over broad refactors.

## Development setup

For a complete contributor workstation and IDE setup, including Visual Studio Code, PyCharm, the repository `.venv`, recommended extensions, validation tasks, and debugging, see the [IDE Development Setup](docs/18-ide-development-setup.md).

For the base toolchain and command-line workflow, see [Development Environment](docs/00-development-environment.md).

Typical development commands include:

```bash
uv sync --locked --no-python-downloads
uv run pytest
uv run ruff check .
uv run mypy src
```

If you modify native or CUDA code, rebuild the relevant extension and run the affected tests before opening a pull request.

## Pull requests

Before submitting a pull request:

- run the relevant test suite
- run formatting and lint checks
- run mypy for typed Python changes
- add or update tests for behavior changes
- update documentation when user-visible behavior changes
- avoid unrelated cleanup in the same pull request
- do not commit secrets, wallet private keys, seed phrases, credentials, or private configuration

Describe what changed, why it changed, and how it was tested.

## Mining and security changes

Changes involving live mining, Stratum behavior, payout addresses, share submission, CUDA/native backends, security policy, or deployment configuration deserve extra review. Include clear testing evidence and avoid changing safety defaults without explanation.

## Reporting security issues

Do not open a public issue for a suspected vulnerability. Follow the repository [Security Policy](SECURITY.md) instead.

## Community discussion

Use [GitHub Discussions](https://github.com/enzotrout/hashorb/discussions) for usage questions, ideas, mining results, and general project discussion that is not a concrete bug report or implementation task.

## Code of conduct

Participation in HashOrb is governed by the repository [Code of Conduct](CODE_OF_CONDUCT.md).
