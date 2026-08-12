# Task: Reduce GitHub Actions usage

## Objective

Reduce hosted GitHub Actions minute consumption during active HashOrb development while preserving meaningful cross-platform and security validation at the pull-request gate.

## Scope

- Stop Packaging from running on ordinary branch pushes.
- Run Packaging only for non-draft pull requests whose changes are not documentation-only, plus explicit manual dispatches.
- Keep exact-head reruns for non-draft pull-request updates through the `synchronize` event.
- Stop Security from running again on pushes to `main` after the same change was already validated in its pull request.
- Reduce scheduled Security scans from weekly to monthly.
- Skip hosted Packaging and Security jobs for draft pull requests and documentation/task-only pull requests.
- Preserve manual `workflow_dispatch` for both workflows.
- Keep `cancel-in-progress: true` so obsolete PR runs do not waste minutes.
- Record a repository development rule that GitHub Actions is a validation gate, not a development or patch-execution environment.

## Non-goals

- Do not weaken any Packaging or Security job contents once those jobs run.
- Do not remove Linux, macOS, Windows, Docker, source-security, dependency, artifact, or container-security coverage.
- Do not change mining, Stratum, Bitcoin, compute, CUDA, dashboard, packaging product behavior, or dependencies.
- Do not use GitHub Actions to implement or validate this task while Actions billing/quota is unavailable.

## Hosted CI policy

Normal development uses local validation on the Mac and/or DGX Spark. Hosted GitHub Actions should consume runner time only when:

1. a non-draft pull request is opened or updated and contains code/configuration/workflow changes,
2. a pull request transitions from draft to ready for review,
3. a maintainer explicitly requests a manual workflow run, or
4. the monthly Security schedule runs.

Until the repository owner's GitHub Actions billing/quota is available again, hosted workflow failures caused solely by billing/quota are recorded as unavailable rather than treated as HashOrb product failures.

## Acceptance criteria

- Packaging has no generic `push` trigger.
- Packaging PR jobs are skipped while the PR is draft.
- Packaging ignores documentation/task-only PRs.
- Packaging preserves Ubuntu, macOS, Windows, Docker and manual-dispatch coverage.
- Security has no `push` trigger.
- Security PR jobs are skipped while the PR is draft.
- Security ignores documentation/task-only PRs.
- Security schedule is monthly rather than weekly.
- Security preserves source/artifact and container jobs plus manual dispatch.
- Repository agent instructions prohibit using hosted Actions as a development shell or patch runner.
- The diff contains only workflow/development-policy documentation changes.

## Validation

Because hosted Actions are currently unavailable due to account billing/quota, validate this slice locally/manual-review only:

```bash
python - <<'PY'
from pathlib import Path
import yaml
for path in (Path('.github/workflows/packaging.yml'), Path('.github/workflows/security.yml')):
    with path.open() as stream:
        yaml.safe_load(stream)
    print(f'parsed: {path}')
PY

git diff --check origin/main...HEAD
git status --short
git diff --stat origin/main...HEAD
```

Also manually review the `on:` triggers and job-level draft guards. Do not claim hosted CI passed while Actions billing/quota is unavailable.

## Final status target

`READY FOR REVIEW`
