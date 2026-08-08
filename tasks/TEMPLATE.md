# Task: <short name>

## Objective

Describe one concrete outcome.

## Context

List only the background needed to make correct decisions.

## Starting point

- Base branch: `main`
- Task branch: `local/<short-task-name>`
- Expected clean tree: yes

## Allowed files

- `path/to/file`

## Forbidden areas

- Files and subsystems that must not change
- Secret, wallet, Bitcoin submission, Stratum, or CUDA boundaries that are out of scope

## Required behavior

1. State observable behavior precisely.
2. Define failure behavior and exit codes where relevant.
3. Define platform differences explicitly.

## Required tests

- Test behavior, failure propagation, repeated calls, and platform boundaries.
- Do not write tests that require production hacks.
- Do not modify `sys.modules` unless the task explicitly concerns import machinery.

## Documentation

List exact documentation that must change, including `docs/activity.md` for meaningful work.

## Validation

Run in this exact order:

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy src
uv run pytest -q
git diff --check
git status --short
git diff --stat
```

Add task-specific focused and platform-neutral validation before the full suite.

## Commit

- Authorized: no
- Message: `<type>(<scope>): <imperative description>`

## Stop conditions

Stop and report `BLOCKED` when:

- The working tree contains unrelated changes.
- A required dependency, platform, permission, or source is unavailable.
- A requested change conflicts with a safety boundary.
- A validation failure cannot be resolved within the allowed scope.

## Required final report

Use the exact HashOrb Task Report in `AGENTS.md`.
Do not claim completion without actual command output and exact test counts.
