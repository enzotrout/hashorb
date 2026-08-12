# HashOrb Development Contract

This file is the permanent operating contract for coding agents working in this repository.
Read it before planning or editing any task.

## Working rules

1. Start from a clean working tree on an explicit task branch.
2. Never commit directly to `main`.
3. Use branch names in the form `local/<short-task-name>` unless the task says otherwise.
4. Read the task specification completely before changing files.
5. Inspect existing code, tests, documentation, and CI before implementing.
6. Change only files required by the task. Report any necessary scope expansion before making it.
7. Do not add production behavior solely to satisfy a test.
8. Do not weaken, skip, delete, or rewrite a valid test to hide a defect.
9. Do not suppress exceptions or command failures unless the documented product behavior requires it.
10. Never use placeholders while claiming a task is complete.
11. Do not use `shell=True` for subprocess execution.
12. Never expose secrets, wallet material, credentials, private keys, seed phrases, RPC cookies, or hidden configuration values.
13. Preserve Bitcoin, Stratum, compute-backend, CUDA, and submission safety boundaries unless the task explicitly and narrowly changes them.
14. Stop and report `BLOCKED` when a required fact, dependency, platform, or permission is unavailable.

## Task execution

For each task:

1. Read `AGENTS.md`.
2. Read the referenced file under `tasks/`.
3. Confirm the branch and clean-tree state.
4. State a concise implementation plan.
5. Implement the smallest complete change.
6. Add or update tests that prove behavior rather than implementation trivia.
7. Update documentation when behavior, architecture, workflow, configuration, or operator expectations change.
8. Add a concise entry to `docs/activity.md` for meaningful work.
9. Run every validation command named by the task.
10. Run repository-wide formatting, linting, typing, and tests before declaring success.
11. Review `git diff --check`, `git status --short`, and `git diff --stat`.
12. Produce the required HashOrb Task Report.
13. Do not commit or push unless the task explicitly authorizes it.

## Documentation policy

Update user-facing documentation when user behavior changes.
Update architecture documentation when system boundaries or design decisions change.
Update `docs/activity.md` for meaningful implementation, repair, migration, packaging, CI, or workflow work.

When no documentation change is appropriate, the final report must say:

`Documentation: not required because no user-facing, workflow, configuration, or architectural behavior changed.`

## Commit messages

Use this format:

`<type>(<scope>): <imperative description>`

Allowed types:

- `feat`
- `fix`
- `test`
- `docs`
- `refactor`
- `perf`
- `chore`
- `ci`

Examples:

- `feat(compute): add bounded native worker selection`
- `fix(dev): propagate synchronization failures`
- `test(dev): cover cross-platform wrapper behavior`
- `docs(agent): document local development workflow`

Keep the subject concise, imperative, and specific. Do not use vague messages such as `updates`, `fix stuff`, or `changes`.

## Required validation baseline

Unless a task defines a stricter sequence, run:

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy src
uv run pytest -q
git diff --check
git status --short
git diff --stat
```

A task is not complete when any required command fails.
Skipped tests must be listed in the report with their stated reason.

## Hosted CI usage

GitHub Actions is a validation gate, not a development shell or patch-execution environment.

1. Perform normal implementation, formatting, linting, typing, tests, and hardware validation locally on the appropriate Mac, Linux, Docker, or DGX Spark environment.
2. Do not create temporary GitHub Actions workflows merely to edit files, apply patches, inspect source, or execute ordinary development commands.
3. Hosted Packaging and Security jobs are intended for non-draft pull-request validation, explicit manual dispatches, and the scheduled Security sweep defined by the workflows.
4. Draft pull requests and ordinary branch pushes should not consume hosted runner minutes.
5. Documentation/task-only pull requests should not run the heavy Packaging or Security matrices.
6. Preserve exact-head pull-request validation after substantive corrective commits by allowing non-draft PR synchronization events to rerun the gates.
7. If hosted Actions are unavailable because of billing, quota, or account limits, record that fact accurately and continue local development when the task can be validated locally. Never claim hosted CI passed when it did not run.
8. A task that explicitly requires hosted cross-platform validation remains incomplete until that gate is available or the task is explicitly re-scoped.

## Required HashOrb Task Report

Every implementation task must end with this structure:

```markdown
# HashOrb Task Report

## Summary

One or two sentences describing the completed work.

## Branch

`local/<branch-name>`

## Objective

The task objective in plain language.

## What changed

- `path/to/file`: concise description

## Behavior now

Describe the resulting user-visible or developer-visible behavior.

## Tests added or changed

- Test file or test name: what it proves

## Validation

```text
<actual command>
<actual result>
```

## Documentation

- Updated: `path`

Or the approved no-documentation statement.

## Git status

```text
<actual git status --short output>
```

## Diff summary

```text
<actual git diff --stat output>
```

## Commit

`<actual commit message>`

Or:

`Not committed.`

## Remaining issues

- None

Or list every incomplete, uncertain, skipped, or failing item.

## Final status

`READY TO COMMIT`
```

Use exactly one final status:

- `BLOCKED`
- `INCOMPLETE`
- `READY TO COMMIT`
- `READY FOR REVIEW`

Do not report `READY TO COMMIT` or `READY FOR REVIEW` unless every task requirement and validation command succeeded.
Use actual command results and test counts. Never invent, summarize from memory, or claim a command ran when it did not.
