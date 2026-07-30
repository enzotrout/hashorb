# Installation and Packaging

## What, Why, and Plain Talk

**What:** Hashsphere has one CPython 3.13 package, an installed `hashsphere`
command, user-local platform scripts, a CPU Docker image, an offline doctor,
archive/privacy verification, and CPU packaging CI definitions.

**Why:** Installation must not require users to reconstruct the development
environment or create separate miners for each operating system.

**Plain talk:** Linux, macOS, Windows, and Docker all consume the same miner.
Their thin edges install or launch it; they do not contain mining code.

## Honest Support and Validation Boundary

| Tier | Implementation | Validation status |
| --- | --- | --- |
| Portable CPU source | Python always; native and native-parallel when the optional C extension builds | Built, archive-scanned, clean-installed, and smoked on Linux ARM64 Spark |
| Linux CUDA source | Explicit local `_cuda` build with an explicit supported architecture | `sm_121` compilation and hardware parity validated on the Spark |
| Docker CPU | Multi-stage CPU wheel, non-root runtime, exec entry point | Built and run on Linux ARM64 Spark with Docker |
| Docker NVIDIA | Deferred design | Host runtime exists, but no clean pinned Python 3.13/CUDA development and runtime image pairing was established |
| macOS CPU | Shared package and Unix installer | Earlier Apple Silicon native source build was exercised; current packaging HEAD awaits its CI runner |
| Windows CPU | Shared package and PowerShell installer | Statically reviewed and CI-configured; current HEAD awaits its Windows runner |
| Windows native/CUDA | Optional or deferred | MSVC native and Windows CUDA have not been validated |

The CI workflow defines hosted Linux, macOS, and Windows CPU jobs. Defining a
job is not the same as executing it. This document does not call the repository
blanket “cross-platform validated.” Linux x86-64, Intel macOS, Windows hardware,
physical two-GPU execution, and portable CUDA wheels still need their own gates.

## Distribution Tiers

### Portable CPU

Normal `uv build`, `uv sync`, `uv tool install`, pip, and Docker CPU builds do
not set `HASHPHERE_BUILD_CUDA=1`. They do not invoke `nvcc`, link a CUDA runtime,
or probe a device. `_cuda.cu` remains source material for an explicit later
build; no `_cuda` shared library appears in a CPU wheel.

The Python backend is the portable correctness baseline. `_native` is declared
optional, so a missing platform compiler leaves Python usable and makes
`native` and `native-parallel` controlled optional-unavailable features.
Profiles resolve through the same shared policy when CUDA and/or native are
absent. Importing the package requires no `.env` and performs no hardware or
network action.

### Linux CUDA

CUDA remains a local source-build tier:

```bash
HASHPHERE_BUILD_CUDA=1 \
HASHPHERE_CUDA_ARCH=121 \
uv sync --locked --reinstall-package hashphere
```

The target must be selected explicitly and must be one of the architectures
accepted by `setup.py`. The existing toolkit discovery is reused. On Linux the
local build embeds discovered toolkit library directories as RUNPATHs. That is
suitable for the validated host, not a portable CUDA wheel or publication
design. The image or wheel must never bundle the NVIDIA host driver.

### Docker CPU

The repository-root `Dockerfile` pins a controlled Python patch version. A full
Debian builder compiles the CPU wheel; the slim final image contains neither a
compiler nor uv. The final user is the unprivileged `hashsphere` account with
numeric UID/GID 10001. `/app/logs` is writable and declared as a volume.

The JSON exec entry point is `hashsphere`, so there is no shell or uv parent
between Docker and Python. The default command is offline doctor, not mining.
The continuous miner already translates SIGINT and SIGTERM into its cooperative
stop controller. On Windows, Ctrl-C maps through Python's available signal
boundary; Windows service-control behavior has not been claimed.

### Docker NVIDIA

No CUDA Dockerfile is included. Although the Spark daemon exposes the NVIDIA
runtime, this milestone did not establish a maintainable base pairing that
simultaneously supplies the validated CUDA toolchain/runtime and CPython 3.13
without cross-distribution copying or an additional custom Python build. A
fragile image would misrepresent CUDA-wheel portability.

A future CUDA image must use separate NVIDIA development and runtime stages,
accept the architecture as an explicit build argument, omit host drivers,
require the host NVIDIA container runtime, and keep device selection explicit.
It must never infer every visible GPU. The CPU Dockerfile remains independent.

## Offline Doctor

Run:

```bash
hashsphere doctor
```

Doctor reports version, Python compatibility, OS family, sanitized architecture,
installation kind, backend availability, optional extension presence, profile
readiness, writable log-directory status, and configuration presence. Output
uses only `ready`, `optional unavailable`, `configuration needed`, and `error`.
Missing optional backends or live configuration do not make a CPU installation
fail. Failed required checks, such as incompatible Python or an unwritable log
directory, return nonzero.

Doctor does not call `Settings.from_env`, open a socket, handshake, mine, dump
the environment, print a configuration value, show a path, or include raw
exception text. It checks extension presence without initializing CUDA. Probe
one ordinal only when intentionally requested:

```bash
hashsphere doctor --probe-cuda-device 0
```

The probe reports only a usable-ordinal count of zero or one. Selecting a
profile may perform the capability probes required by that profile policy.

## Linux User-Local Installation

Prerequisites are uv on `PATH` and an existing CPython 3.13. The installer does
not download Python, use sudo, modify a shell profile, or start mining.

```bash
scripts/install-unix.sh --dry-run
scripts/install-unix.sh install
hashsphere --help
hashsphere doctor
```

The command calls `uv tool install --python 3.13 --force` on the reviewed
checkout. uv owns the user-local tool environment. If its executable directory
is not already on `PATH`, inspect `uv tool dir --bin` and choose how to expose
it; the script does not edit shell configuration.

Upgrade and uninstall are explicit:

```bash
scripts/install-unix.sh upgrade
scripts/install-unix.sh uninstall
```

Run from a chosen configuration directory containing `.env`. Relative log
paths are relative to that working directory. A separate explicit CUDA source
build is a developer/host operation and is never enabled by this installer.

## macOS CPU Installation

The same Unix script handles Darwin. It does not install Homebrew, uv, Python,
or Xcode tools. Python works without a compiler. Apple Silicon native C was
previously exercised; Intel native remains a separate compile gate. macOS has
no CUDA tier.

```bash
./scripts/install-unix.sh install
hashsphere doctor
hashsphere profile-info --profile auto
hashsphere compute-benchmark --backend python --hash-count 100000
```

Ctrl-C is handled by the continuous miner's shared stop controller. The current
packaging changes must pass the macOS CI job before this HEAD is described as
macOS validated.

## Windows CPU Installation

Use a normal, non-administrator PowerShell session with uv and CPython 3.13
already on `PATH`:

```powershell
& .\scripts\install-windows.ps1 -Action install -DryRun
& .\scripts\install-windows.ps1 -Action install
hashsphere --help
hashsphere doctor
hashsphere profile-info --profile auto
hashsphere compute-benchmark --backend python --hash-count 100000
```

The script enables terminating errors and UTF-8 output for its own process. It
does not weaken execution policy, request elevation, mutate global `PATH`, or
download executables. If local policy blocks a reviewed script, follow the
machine owner's policy rather than changing policy globally.

Upgrade with `-Action upgrade` and uninstall with `-Action uninstall`.
Hashsphere uses `pathlib`, explicit UTF-8 JSON/JSONL I/O, and the `.exe` console
launcher generated by packaging. Spaces and backslashes remain single argument
values when quoted in PowerShell. Python is the required backend. MSVC native,
Ctrl-C behavior on a real Windows console, and CUDA require executed Windows
gates before stronger claims.

## Configuration and Logs

All tiers use the same environment names and `.env.example`. The template is
grouped into required live Stratum settings, optional profile, search strategy,
and Custom controls. Lifecycle, liveness, reconnect, and log paths remain CLI
options, so the template does not invent incompatible environment variables.

Hashsphere discovers `.env` from the current working directory through
python-dotenv. Keep one configuration directory outside an image and run the
command from there. Do not put a seed, private key, or wallet password in it.
The repository and Docker build context ignore `.env`, credentials, secrets,
logs, virtual environments, caches, local binaries, and profiling traces.

JSONL parents are created by the runtime when needed. Use explicit paths such
as `--log-file logs/events.jsonl`; Windows may use a quoted path such as
`--log-file '.\Hashsphere Logs\events.jsonl'`.

## Docker Use

Build and run only offline commands during packaging validation:

```bash
docker build -t hashphere:cpu .
docker run --rm hashphere:cpu
docker run --rm hashphere:cpu --help
docker run --rm hashphere:cpu profile-info --profile auto
docker run --rm hashphere:cpu \
  compute-benchmark --backend python --hash-count 100000
```

For persistent logs, use a Docker-managed volume, which avoids host UID
assumptions:

```bash
docker volume create hashphere-logs
docker run --rm -v hashphere-logs:/app/logs hashphere:cpu \
  doctor --log-dir /app/logs
```

Live operation remains explicit and was not executed by packaging validation.
After reviewing `.env`, a bounded example is:

```bash
docker run --rm \
  --env-file .env \
  -e HASHPHERE_ENABLE_LIVE_STRATUM=1 \
  -e HASHPHERE_ENABLE_LIVE_MINING=1 \
  -v hashphere-logs:/app/logs \
  hashphere:cpu stratum-mine \
  --profile auto \
  --max-runtime-seconds 60 \
  --log-file /app/logs/events.jsonl
```

No address or pool credential is present in the Dockerfile, build context, or
image history. They enter only at runtime through the operator's environment.

## Release Metadata, Artifacts, and Privacy

`pyproject.toml` is the sole version source and defines version 0.1.0, the
Python 3.13 range, README metadata, and `hashsphere` console entry point. No OS
classifier is asserted because current validation breadth does not justify one.
The sdist contains shared source, documentation, templates, platform guidance,
install/verification scripts, and native/CUDA source. Tests, journal history,
local binaries, `.env`, and runtime data are pruned. The CPU wheel contains the
optional native binary when compilation succeeds but no CUDA binary or CUDA
runtime dependency.

`scripts/verify-distributions.py` checks safe archive paths, forbidden local
data, CPU/CUDA separation, metadata, console entry points, and required source
contents without extracting or executing an archive. Native compiler prefix
mapping removes local source and Python roots from binary debug metadata.

There is intentionally no license field or license file: the owner has not yet
selected a license. Package/container publication and open-source licensing
claims remain blocked until that legal choice is made. Hashsphere does not
invent a license.

## CI Architecture

`.github/workflows/packaging.yml` defines one Python 3.13 CPU matrix across
Ubuntu, macOS, and Windows. Every job syncs the lock, runs pytest, Ruff lint and
format checks, mypy, builds sdist/wheel, scans both archives, then clean-installs
and smokes help, doctor, profiles, Python benchmark, and JSONL summary. Native
is optional according to compiler availability. No CUDA or live Stratum runs
on hosted CPU jobs.

An Ubuntu Docker job builds the CPU image and checks doctor, argument
forwarding, profile resolution, Python benchmark, non-root UID, and image
history privacy. CUDA CI remains hardware-dependent and explicitly gated.

## Paths, Encoding, and Signals

The shared core already uses `pathlib` and explicit UTF-8 for configuration,
event logs, and summaries. JSONL writes use a platform-neutral newline and
create parent directories. CLI subprocess smokes use argument arrays and choose
`Scripts/hashsphere.exe` on Windows versus `bin/hashsphere` on POSIX; no shell
string is constructed. Windows-style paths with spaces are covered as one CLI
value.

No application subprocess, file lock, ANSI terminal protocol, or terminal
detection was found. SIGTERM is installed only where available, and SIGINT is
always included in the continuous miner's portable signal scope. Docker uses an
exec entry point, so there is no signal-forwarding wrapper. Non-cancellable
native/CUDA ranges still finish their current call before cooperative stop.

## Shared Core and Future Thin Repositories

The package under `src/hashphere` remains the only miner. Platform directories
contain documentation only; scripts, packaging metadata, containers, and CI
consume the core. A future thin repository can depend on an immutable core tag
or verified release artifact and add only launcher/installer metadata.

Separate repositories may help platform release ownership later, but they are
not currently necessary: one repository plus the CI matrix keeps versions and
correctness gates aligned. If created, a thin repository must never vendor or
copy the miner source.

## Documentation Review Impact

| Material | Classification | Reason |
| --- | --- | --- |
| README, ROADMAP, ARCHITECTURE | Changed | Added installation, distribution, validation, and shared-core boundaries; corrected stale layout/licensing claims |
| docs/00 and docs/02 | Changed | Corrected development commands, paths, version, and platform claims |
| docs/03 | Changed | Removed host-flavored worker examples |
| docs/04, docs/05, docs/07, docs/08, docs/09 | Reviewed; no change needed | Existing logging, backend, parallel, strategy, and privacy contracts remain accurate |
| docs/06 | Changed | Recorded current Docker/CI native build boundary |
| docs/10 and docs/11 | Reviewed; no change needed | Existing local RUNPATH, CUDA-wheel, Windows CUDA, and physical multi-GPU limits remain accurate |
| docs/12 | Changed | Added the four-profile live human-gate results |
| docs/13 | New | Central installation and packaging contract |
| journal | Reviewed; no change needed | Historical development record is not release guidance and remains out of the sdist |
| `.env.example`, Dockerfile, platform and install scripts | Changed/new | One configuration vocabulary and thin platform distribution edges |

Dashboard, Bitcoin Core true solo, distributed workers, adaptive strategies,
physical multi-GPU validation, Windows CUDA, portable CUDA wheels, release
signing, and publication remain deferred.
