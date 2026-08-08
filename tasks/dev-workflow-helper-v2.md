# Task: Developer workflow helper v2

## Objective

Provide one trustworthy, cross-platform development entry point for routine HashOrb setup, validation, diagnostics, and pre-review checks.

## Context

The previous `local/dev-workflow-helper` branch is abandoned. This implementation starts fresh from `main` and must not copy test-specific workarounds or silent failure behavior from that branch.

The helper is development tooling only. It must not change mining, Bitcoin, Stratum, hashing, nonce, share submission, wallet, or CUDA kernel behavior.

## Starting point

- Base branch: `main`
- Task branch: `local/dev-workflow-helper-v2`
- Expected clean tree: yes

## Allowed files

- `dev`
- `scripts/dev_helper.py`
- `scripts/__init__.py` if needed for a clean wrapper import
- `tests/test_dev_helper.py`
- `README.md`
- `docs/development.md`
- `docs/activity.md`
- `.github/workflows/packaging.yml` only if needed to make the helper part of the cross-platform validation gate
- this task specification

## Forbidden areas

- `src/hashorb/bitcoin/`
- `src/hashorb/mining/`
- `src/hashorb/network/`
- `src/hashorb/crypto/`
- `src/hashorb/compute/_native.c`
- `src/hashorb/compute/_cuda.cu`
- Stratum, RPC submission, wallet/key material, block construction, nonce search semantics, or mining algorithms
- security scanning behavior, except invoking existing validation from the helper

## Required behavior

1. Provide `./dev help`, `./dev --help`, `./dev status`, `./dev sync`, `./dev start`, `./dev check`, `./dev doctor`, `./dev full`, and `./dev review`.
2. `status` is read-only and reports repository root, branch, working-tree state, Python, uv, and HashOrb compute-backend availability without hiding command failures.
3. `sync` refuses a dirty tree, fetches `origin`, switches to `main`, and fast-forward pulls `origin/main`; every Git failure must propagate as a nonzero result.
4. `start` verifies uv and performs a locked development-environment sync through uv.
5. `check` runs the fast development gate: formatting check, lint, mypy, focused helper tests, and a uv-environment backend/import probe.
6. `doctor` verifies repository/Git state, supported Python, uv, locked environment/import health, and backend availability. Missing CUDA is informational on machines where CUDA is unavailable; Python must be available and the native backend must be reported explicitly. Repeated calls must be independent and deterministic for the same environment.
7. `full` runs the repository baseline in order: formatting check, lint, mypy, full pytest suite, backend/import probe, then doctor.
8. `review` is read-only and reports branch, working tree, commits and diff summary against `main`, changed files, and suspicious secret-bearing filenames without displaying file contents.
9. All subprocesses use explicit argument sequences and `shell=False` behavior. No command failure is silently ignored.
10. HashOrb import/backend verification must execute through the uv environment rather than relying on the interpreter that launched the wrapper.
11. Output must be concise, deterministic enough for humans and agents to inspect, and errors must identify the failed operation without exposing secrets.
12. The helper must work from paths containing spaces and must use semantic path handling rather than slash-format assumptions.

## Required tests

- Help and `--help` both succeed.
- Wrapper invocation uses the current Python interpreter in cross-platform tests rather than relying on direct shebang execution.
- Status reports clean/dirty state and propagates Git failures.
- Sync rejects dirty trees.
- Fetch, switch, and fast-forward pull failures each make sync fail.
- Missing uv produces a clear nonzero failure for commands that require it.
- Start invokes locked uv synchronization.
- Check runs the expected fast gate in order and stops at failure.
- Doctor succeeds with healthy required components, reports optional unavailable CUDA without failing, and produces equivalent results across repeated calls.
- Full runs the required validation sequence and stops on the first failure.
- Review normalizes paths, handles repository paths containing spaces, and reports suspicious filenames without reading or printing their contents.
- No tests inject fake `hashorb` modules into `sys.modules`.
- Every behavior test contains meaningful assertions.

## Documentation

- Add `docs/development.md` documenting the helper commands, platform invocation, failure semantics, and recommended agent/human workflow.
- Add a concise README development entry linking to the detailed guide.
- Update `docs/activity.md` with this slice and actual validation results before review.

## Validation

Run in this order:

```bash
uv run pytest -q tests/test_dev_helper.py
uv run ruff format --check .
uv run ruff check .
uv run mypy src
uv run pytest -q
git diff --check
git status --short
git diff --stat
```

In addition, Packaging and Security GitHub Actions must pass for the final branch head before the PR is considered ready.

## Commit

- Authorized: yes
- Message family: `<type>(dev): <imperative description>`

## Stop conditions

Stop and report `BLOCKED` when:

- the base branch moves in a way that creates a meaningful conflict;
- a required CI platform is unavailable;
- implementation would require changing a forbidden mining/security boundary;
- a validation failure cannot be resolved within the allowed scope.

## Required final report

Use the exact HashOrb Task Report in `AGENTS.md`.
Do not claim completion without observed CI results and exact test counts from GitHub Actions logs.