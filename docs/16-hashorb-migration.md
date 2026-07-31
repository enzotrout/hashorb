# HashOrb pre-release naming migration

**HashOrb: Distributed hashing as a coordinated swarm.**

The project was renamed to HashOrb before its first public release. Distributed
workers remain a future milestone; the tagline states the direction rather than
claiming that remote worker coordination is implemented today.

## Canonical identity

| Surface | Identity |
| --- | --- |
| Brand | `HashOrb` |
| Domain | `https://hashorb.com` |
| Repository | `hashorb` |
| Python distribution | `hashorb` |
| Python import | `hashorb` |
| Module command | `python -m hashorb` |
| Console command | `hashorb` |
| Environment prefix | `HASHORB_` |
| CPU image example | `hashorb:cpu` |
| Local checkout example | `~/Development/hashorb` |

No alias package, old console command, or mixed environment-prefix
compatibility layer is provided. This is a clean pre-release migration.

## Private `.env` migration

Do not share or print the file. On Linux, first ensure that it is owned by the
current user and accessible only to that user, then run:

```bash
python scripts/migrate-hashorb-env.py .env
python scripts/migrate-hashorb-env.py --verify .env
```

The migration tool changes only keys beginning with `HASHSPHERE_` or
`HASHPHERE_`, creates `.env.pre-hashorb` without overwriting an existing backup,
preserves all values and unrelated bytes, retains restrictive permissions, and
atomically replaces the original. It refuses ambiguous old-prefix mappings or
an already-present resulting `HASHORB_` key. Verification prints only remaining
legacy key names and exits nonzero when any remain. The repository never runs
this tool automatically.

The three sensitive opt-ins after migration are:

```text
HASHORB_ENABLE_TRUE_SOLO_HASHING
HASHORB_ENABLE_TRUE_SOLO
HASHORB_ENABLE_BLOCK_SUBMISSION
```

Old-prefixed keys cannot grant hashing, proposal, or submission permission.
Runtime configuration loading reports only a generic legacy-configuration
error and never reflects a key's value.

## GitHub repository rename

Only after this branch and its hosted checks have been reviewed, an operator
may open **Repository Settings → General → Repository name** and rename
`enzotrout/hashsphere` to `enzotrout/hashorb`. This milestone does not perform
that remote action.

The current remote uses SSH with a configured host alias. After the operator
rename, preserve that transport and alias:

```bash
git remote set-url origin git@github-hashsphere:enzotrout/hashorb.git
git remote -v
git fetch origin
git status
```

Set the repository homepage to `https://hashorb.com`, then verify that the old
GitHub repository URL redirects to the renamed repository. Do not change SSH to
HTTPS merely for the rename.

## GitHub security settings still requiring operator review

The migration does not claim or modify these settings:

- Dependency graph
- Dependabot alerts and security updates
- Dependabot notification preferences
- SPDX SBOM export
- rules protecting `main` from deletion and force pushes
- required Packaging and Security checks
- an up-to-date branch requirement where appropriate
- a pull-request requirement only if compatible with the maintainer workflow
- signed commits only after signing is verified
- Security page review after hosted workflows complete

Enable only controls that have been reviewed and will not lock out the operator.

## Docker Hub and PyPI

Reserve or create a private Docker Hub repository named `hashorb`, review its
access controls, and do not publish an image during this milestone.

Do not create an empty or placeholder PyPI project. A genuine prerelease may be
published only after the code migration is merged, the GitHub repository is
renamed, trusted publishing is configured, artifacts are reviewed, and
publication is explicitly approved.
