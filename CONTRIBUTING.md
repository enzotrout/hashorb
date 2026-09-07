# Contributing to HashOrb

Thanks for your interest in improving HashOrb.

HashOrb is an open source Bitcoin mining project focused on learning, experimentation, transparent mining behavior, and reproducible engineering.

## Before you start

For non-trivial changes, open an issue first so the proposed behavior and scope can be discussed before implementation.

Please keep changes focused. Small, reviewable pull requests are preferred over broad refactors.

## Development setup

Follow the setup instructions in the README for your platform.

Typical development commands include:

```bash
uv sync --locked
uv run pytest
uv run ruff check .
```

If you modify native or CUDA code, rebuild the relevant extension and run the affected tests before opening a pull request.

## Pull requests

Before submitting a pull request:

- run the relevant test suite
- run formatting and lint checks
- add or update tests for behavior changes
- update documentation when user-visible behavior changes
- avoid unrelated cleanup in the same pull request
- do not commit secrets, wallet private keys, seed phrases, credentials, or private configuration

Describe what changed, why it changed, and how it was tested.

## Mining and security changes

Changes involving live mining, Stratum behavior, payout addresses, share submission, CUDA/native backends, security policy, or deployment configuration deserve extra review. Include clear testing evidence and avoid changing safety defaults without explanation.

## Reporting security issues

Do not open a public issue for a suspected vulnerability. Follow the repository security policy instead.

## Code of conduct

Participation in HashOrb is governed by the repository Code of Conduct.
