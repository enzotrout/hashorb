# HashOrb development workflow

HashOrb uses one repository-local helper for routine development setup, checks,
diagnostics, and pre-review inspection. It does not own mining behavior,
Bitcoin or Stratum behavior, compute algorithms, or release policy.

## Invocation

Linux and macOS:

```bash
./dev help
```

Windows:

```powershell
python .\dev help
```

Cross-platform tests invoke the wrapper with the current Python interpreter
rather than relying on Windows to execute a Unix shebang directly.

## Commands

`dev status` is read-only. It reports the repository root, current branch,
working-tree state, launcher Python, uv version, uv-environment Python, and
sanitized HashOrb backend availability. The backend probe uses
`uv run --no-sync`, so status does not synchronize the uv environment.

`dev sync` requires a clean working tree, then runs `git fetch --prune origin`,
`git switch main`, and `git pull --ff-only origin main`. Every Git failure
stops the sequence. It never performs a destructive reset.

`dev start` prepares the locked environment with:

```text
uv sync --locked --no-python-downloads
```

`dev check` runs the fast gate in order: format check, Ruff lint, mypy, focused
helper tests, then a HashOrb import/backend probe through uv. It stops on the
first failure.

`dev doctor` verifies repository state, uv, the lock file, Python 3.13 inside
the existing uv environment, the required Python backend, and explicit native
backend status. CUDA is optional; CPU-only machines may report CUDA unavailable
without failing doctor. Calls do not share hidden helper state.

`dev full` runs format check, lint, mypy, the complete pytest suite, the
uv-environment backend/import probe, and doctor. Use it before review.

`dev review` is read-only. It reports repository and branch state, commits and
diff statistics against `main`, changed filenames, and filenames that deserve
extra review for sensitive material. It examines filenames only, not changed
file contents, and normalizes path separators for display.

## Failure behavior

Every subprocess uses an explicit argument sequence with `shell=False`. Return
codes are checked. The helper does not ignore Git, uv, test, format, lint,
typing, import, or backend-probe failures. Error messages identify the failed
operation and exit code without echoing arbitrary subprocess output.

## Human workflow

```text
./dev sync
create or switch task branch
./dev start
make the bounded change
./dev check
./dev full
./dev review
open and review PR
```

`dev sync` intentionally refuses a dirty working tree.

## Coding-agent workflow

```text
Read AGENTS.md
        ↓
Read tasks/<task>.md
        ↓
Implement only the allowed scope
        ↓
./dev check
        ↓
./dev full
        ↓
./dev review
        ↓
HashOrb Task Report
```

`AGENTS.md` and the active task define the development contract. The helper is
the machine-executed validation side of that contract; it does not replace code
review or the pull-request CI gate.

## CI relationship

Hosted GitHub Actions is intentionally reserved for review gates rather than
routine development. Ordinary branch pushes do not run Packaging or Security,
and draft pull requests do not start their hosted jobs. Development validation
runs locally with `dev check`, `dev full`, platform-specific tests, and DGX Spark
hardware checks when CUDA behavior is involved.

Packaging runs for a non-draft pull request when it is opened, updated,
reopened, or marked ready for review. It still runs the full pytest suite and
source-quality checks on Windows, Ubuntu, and macOS, plus the Docker CPU gate.
Documentation/task-only pull requests skip this heavy matrix. Maintainers can
also run Packaging explicitly with `workflow_dispatch`.

Security follows the same non-draft pull-request policy and keeps its source,
dependency, workflow, artifact, and CPU-container security gates. It no longer
runs a duplicate validation after merge to `main`; instead, a scheduled sweep
runs monthly and manual dispatch remains available.

Both workflows keep `cancel-in-progress: true`, so a newer pull-request head
cancels obsolete hosted work. GitHub Actions must not be used as a remote shell,
patch runner, or ordinary implementation environment.

When hosted Actions cannot start because of account billing, quota, or spending
limits, record that hosted validation is unavailable. Continue local development
when the active task can be validated locally, but never report hosted CI as
passed when it did not run.
