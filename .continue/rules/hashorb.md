# HashOrb Continue Rules

These rules apply to every Continue Agent task in this repository.

1. Read `AGENTS.md` before planning, editing, testing, or reporting.
2. Read the referenced task specification under `tasks/` completely.
3. Confirm the current branch and working-tree state before editing.
4. Do not work directly on `main`.
5. Do not broaden scope without reporting why it is necessary.
6. Do not add test-specific behavior to production code.
7. Do not weaken tests, suppress failures, or claim completion around failing commands.
8. Keep subprocess commands explicit and never use `shell=True`.
9. Update relevant documentation and `docs/activity.md` when required by `AGENTS.md`.
10. Use the commit-message format defined in `AGENTS.md`.
11. Do not commit or push unless the active task explicitly authorizes it.
12. Before ending, run every validation command required by the task and the baseline in `AGENTS.md`.
13. Produce the exact HashOrb Task Report defined in `AGENTS.md`.
14. Include actual command results, exact test counts, changed files, documentation status, Git status, diff summary, commit status, remaining issues, and one exact final status.
15. Never report `READY TO COMMIT` or `READY FOR REVIEW` when a requirement is incomplete, a validation command failed, or the result was not actually observed.

When instructions conflict, use this priority:

1. Safety and secret protection
2. The active task specification
3. `AGENTS.md`
4. This Continue rule
5. Existing local convention

Stop with `BLOCKED` rather than guessing when required information or access is unavailable.
