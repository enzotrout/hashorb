# Hashphere

Hashphere is an experimental Python Bitcoin mining project. Live operations are
explicitly opt-in and include Stratum inspection, bounded mining, and a
synchronous continuous lifecycle that searches one nonce chunk at a time.
It also has a separate Bitcoin Core true-solo path that builds, independently
checks, proposes, and submits a complete block through a user-operated node.
Continuous mining now expands deterministic Bitcoin work space after a
nonce boundary and recovers fresh authorized work after single-endpoint
Stratum connection loss. Mining commands select one compute backend per
invocation. The Python sequential implementation is the correctness reference,
and an explicitly selected portable native C backend provides optimized
sequential execution. The explicit `native-parallel` backend divides each
parent nonce interval across portable native worker threads. Independently,
one search strategy decides which parent interval comes next. `sequential`
preserves ascending contiguous range order, while experimental `orbiting-bit`
permutes the same parent-range indexes deterministically. An explicitly built
and selected `cuda` correctness backend can search either strategy's ordinary
parent ranges on one NVIDIA device, with every reported candidate verified
again by the Python correctness primitives.

## Install and diagnose

Hashphere currently requires CPython 3.13. Normal installation is CPU-only and
never invokes `nvcc`. From a reviewed checkout, Linux and macOS can use the
shared user-local installer:

```bash
scripts/install-unix.sh install
hashsphere doctor
```

Windows uses the PowerShell boundary without administrator access, PATH edits,
or execution-policy changes:

```powershell
& .\scripts\install-windows.ps1 install
hashsphere doctor
```

Both scripts require uv and an existing Python 3.13 installation; automatic
Python downloads are disabled. Use `upgrade` in place of `install`, or
`uninstall` to remove the uv tool. Run Hashsphere from the directory containing
your `.env`; relative log paths are resolved from that working directory.

`hashsphere doctor` is offline by default. It reports sanitized package,
platform, backend, profile, configuration-presence, and log-directory readiness
without loading Stratum settings, connecting to a pool, showing paths, or
probing CUDA hardware unless explicitly requested.

Build and inspect the CPU container with:

```bash
docker build -t hashphere:cpu .
docker run --rm hashphere:cpu
docker run --rm hashphere:cpu profile-info --profile auto
docker run --rm hashphere:cpu compute-benchmark --backend python --hash-count 100000
```

The image starts with doctor rather than mining, runs as a non-root user, and
expects JSONL logs under `/app/logs`. NVIDIA-container packaging is deferred;
the host-local Linux CUDA build remains the validated CUDA tier. macOS and
Windows CPU gates are defined in CI, but this Linux ARM64 milestone did not
execute those remote runners. See
[`docs/13-installation-and-packaging.md`](docs/13-installation-and-packaging.md)
for exact platform boundaries, Docker live-operation examples, upgrades,
uninstall, signals, privacy, and validation status.

## Configure the environment

Copy the example configuration and edit the new `.env` file:

```bash
cp .env.example .env
```

Set `HASHPHERE_BITCOIN_ADDRESS` to a public Bitcoin receive address. The
default endpoint is Solo CKPool at `stratum.ckpool.org:3333`, the default
password is CKPool's conventional `x`, and `HASHPHERE_WORKER_NAME=auto`
derives a sanitized worker name from the hostname. No seed phrase, private key,
or wallet password is needed or should be placed in `.env`.

`HASHPHERE_COMPUTE_BACKEND` selects the nonce-search implementation. Its
default, `auto`, deliberately continues to select `python`; native or CUDA
availability does not change that choice. Exact selectors `python`, `native`,
`native-parallel`, `cuda`, and `cuda-multi` are supported, and the earlier `cpu` value
remains a compatibility alias for `python`. The native modes require the
optional C extension. CUDA additionally requires the explicitly enabled CUDA
extension. `cuda` owns one selected device; `cuda-multi` owns an explicit set.
There is no fallback after an explicit selection.

`HASHPHERE_COMPUTE_WORKERS` configures only `native-parallel`. It is a strict
unpadded ASCII decimal integer from `1` through `256` and defaults to `2`.
Ranges shorter than the configured count create fewer nonempty assignments.
`HASHPHERE_COMPUTE_PROFILE` optionally selects `lite`, `auto`, `max`, or
`custom`. When omitted, legacy backend behavior is unchanged. A CLI `--profile`
wins over the environment. Lite/Auto/Max reject ambiguous manual controls
except that Auto and Max accept an explicit CUDA ordinal or exact ordinal list.
Custom requires its critical controls explicitly. See
[`docs/12-performance-profiles.md`](docs/12-performance-profiles.md).

`HASHPHERE_CUDA_DEVICE` configures only an explicitly selected `cuda` backend.
It defaults to ordinal `0` and must be an unpadded ASCII decimal integer from
`0` through `2147483647`. Invalid CUDA device syntax or an unavailable device
fails before live networking. The setting is ignored by CPU backends. UUIDs,
serial numbers, PCI addresses, and driver paths are never included in normal
console or JSONL output.

`HASHPHERE_CUDA_DEVICES` is required only for `cuda-multi`, for example `0,1`.
It accepts one to 256 unique ordinals, tolerates surrounding element
whitespace, and canonicalizes them into ascending order. Hashsphere never
selects all visible devices implicitly. One-device `cuda-multi` is allowed for
integration testing but is not a multi-GPU performance claim.

`HASHPHERE_SEARCH_STRATEGY` selects where mining looks next, while
`HASHPHERE_COMPUTE_BACKEND` selects how that assigned range is hashed. The
default strategy is the exact lowercase name `sequential`; experimental
`orbiting-bit` is the alternative. `auto` is a static alias for `sequential`,
not adaptive tuning.
Unknown strategy names fail before networking and never fall back. One strategy
definition is selected per mining invocation, including reconnects, while a
fresh compact cursor begins at the configured start nonce for each new pool
job, extra-nonce value, rolled network time, or recovered session. One-shot
mining preserves its explicitly requested range; bounded chunked and continuous
mining obtain their parent ranges from the strategy.

The backend is validated before a mining command opens a live connection,
selected exactly once, and reused across every job, work variant, and Stratum
reconnect in that invocation. An execution failure is terminal: Hashphere does
not retry the range with another backend or silently fall back. The parallel
backend creates contiguous, balanced, nonoverlapping assignments whose union is
the exact parent range, then deterministically chooses the lowest qualifying
nonce after all running workers finish. These worker assignments remain private
to the backend and do not change the selected global strategy. CUDA follows
the same parent-range boundary and uses deterministic smallest-candidate
reduction. SIMD, multiprocessing, cooperative mid-range cancellation,
automatic all-device selection and real multi-GPU hardware validation remain
deferred. The validated CUDA path specializes Bitcoin header hashing and reuses
device-owned work/result buffers without changing the parent-range contract.

## Choose a search strategy

**What:** `HASHPHERE_SEARCH_STRATEGY` controls the order of parent nonce ranges.
The supported exact values are `sequential`, `orbiting-bit`, and the static
`auto` alias for `sequential`.

**Why:** A genuinely different ordering policy can now be tested without
changing SHA-256, Bitcoin work construction, Stratum, compute backends,
recovery, progression, or submission.

**Plain talk:** Sequential walks the nonce map from left to right. Orbiting-bit
jumps between widely separated regions, but still visits every region exactly
once.

For eight equal parent ranges, the physical range-index orders are:

```text
sequential:   0, 1, 2, 3, 4, 5, 6, 7
orbiting-bit: 0, 4, 2, 6, 1, 5, 3, 7
```

Orbiting-bit reverses a fixed-width binary permutation counter. When the number
of ranges is not a power of two, the enclosing power-of-two domain includes
invalid physical indexes; those are skipped internally and never become
backend calls, chunks, or range events. Every valid parent range remains one
ordinary contiguous half-open interval. The exact union, unique hashes, and
Bitcoin search space are unchanged.

Orbiting-bit changes order only. It does not make any nonce more likely to
succeed, increase the probability for a fixed number of unique hashes, predict
a valid hash, or provide a proven odds advantage. It is marked experimental.
Backend selection and worker count remain independent: `native-parallel`
privately partitions whichever parent range the strategy emits.

Select it with:

```bash
HASHPHERE_SEARCH_STRATEGY=orbiting-bit
```

See [`docs/09-orbiting-bit.md`](docs/09-orbiting-bit.md) for the accessible
algorithm and coverage proof.

## Bitcoin Core true solo

**What:** `bitcoin-core-check` is a read-only readiness boundary and
`solo-mine` is a separate, bounded complete-block mining boundary. Neither
command loads Stratum settings or opens a Stratum socket.

**Why:** Pool jobs own coinbase and submission policy. True solo must instead
obtain one strict `getblocktemplate` from the operator's node, construct the
BIP34/SegWit coinbase and merkle tree, search only the network target, validate
the complete block in proposal mode, and call `submitblock` once.

**Plain talk:** Your node supplies the current puzzle. Hashphere builds the
whole block, searches it, and returns a winner to that same node without a
pool in the middle.

Sensitive RPC and payout settings are environment-only. The endpoint defaults
to loopback (`127.0.0.1:8332`); there are no default credentials or payout
destination. Configure either the explicit user/password pair or one explicit
cookie path, never both. Cookie contents, credentials, addresses, scripts,
templates, headers, targets, nonce material, transactions, and serialized
blocks are excluded from console and JSONL output. Ordinary Bitcoin Core RPC
here is HTTP, not TLS, and should remain on a trusted local networking boundary;
a remote host is possible only through explicit operator configuration.

```dotenv
HASHPHERE_BITCOIN_RPC_HOST=127.0.0.1
HASHPHERE_BITCOIN_RPC_PORT=8332
HASHPHERE_BITCOIN_RPC_USER=YOUR_RPC_USER
HASHPHERE_BITCOIN_RPC_PASSWORD=YOUR_RPC_PASSWORD
HASHPHERE_SOLO_PAYOUT_ADDRESS=YOUR_ADDRESS_FOR_THE_CONNECTED_CHAIN
```

Cookie authentication replaces both user/password lines:

```dotenv
HASHPHERE_BITCOIN_RPC_COOKIE_FILE=EXPLICIT_COOKIE_PATH
```

The general, non-wallet `validateaddress` RPC supplies the exact payout
`scriptPubKey` and enforces the connected chain's address rules. Hashphere does
not create, load, modify, or import into a wallet. The readiness command has a
dedicated opt-in and never mines, proposes, or submits:

```bash
HASHPHERE_ENABLE_BITCOIN_RPC_CHECK=1 \
uv run hashphere bitcoin-core-check
```

Mining requires two distinct opt-ins and at least one finite chunk or runtime
limit. Proposal validation is mandatory and fail-closed; an unavailable or
rejected proposal prevents submission. The example below is intentionally
short but can submit a valid block, so review the connected chain first:

```bash
HASHPHERE_ENABLE_TRUE_SOLO=1 \
HASHPHERE_ENABLE_BLOCK_SUBMISSION=1 \
uv run hashphere solo-mine \
  --profile auto \
  --max-chunks 1 \
  --max-runtime-seconds 30 \
  --event-log logs/solo.jsonl
```

Lite, Auto, Max, Custom, Python, native, native-parallel, CUDA, cuda-multi,
sequential, orbiting-bit, chunk limits, runtime limits, SIGINT, and SIGTERM all
retain their existing boundaries. Template polling is bounded and defaults to
30 seconds; candidates force a freshness check before proposal and another
after an accepted proposal. Long polling is deferred. No mainnet submission
was executed during this milestone. The deterministic isolated regtest gate is
implemented but skipped when a compatible existing `bitcoind` is unavailable.
See [`docs/14-bitcoin-core-true-solo.md`](docs/14-bitcoin-core-true-solo.md).

## Choose a performance profile

**What:** Profiles translate Lite, Auto, Max, or Custom intent into existing
validated compute controls before a backend is constructed.

**Why:** Operators can choose resource intensity without changing search order,
lifecycle limits, connection settings, Bitcoin work, or candidate correctness.

**Plain talk:** Pick gentle, sensible, full power, or fully manual.

Inspect a profile without Stratum configuration or mining:

```bash
uv run python -m hashphere profile-info --profile auto
```

Use a profile for continuous mining by omitting the legacy required chunk size;
the profile owns chunk sizing and pacing:

```bash
uv run python -m hashphere stratum-mine \
  --profile lite \
  --max-runtime-seconds 60 \
  --max-reconnect-attempts 5
```

Live opt-in environment switches and normal connection configuration are still
required. Lite uses no more than one GPU and inserts an interruptible delay
between completed parent ranges. Auto and Max never consume multiple visible
GPUs without an explicit list. Custom exposes only the validated backend,
worker, ordinal, launch-size, chunk, and pacing controls. Existing `.env` files
with all legacy compute knobs must remove those lines before using a preset.
Full contracts, measured Spark evidence, raw/effective rate semantics, and
examples are in
[`docs/12-performance-profiles.md`](docs/12-performance-profiles.md).

## Build the optional CUDA backend

**What:** CUDA support is a narrow optional extension containing one
correctness-first Bitcoin double-SHA256 kernel. Normal source and wheel builds
remain CPU-only and do not require a CUDA toolkit.

**Why:** GPU execution should plug into the existing compute contract without
moving strategy, Stratum, progression, recovery, submission, or JSONL
ownership onto the device.

**Plain talk:** The selected strategy hands the GPU one ordinary bounded range.
The GPU checks every nonce in that range and reports the smallest match. Python
then rebuilds and hashes that candidate again before it can be submitted.

Prerequisites are a supported NVIDIA GPU, installed CUDA toolkit and runtime,
and `nvcc` on `PATH`. CUDA builds require an explicit, narrowly validated
architecture; the build never probes a GPU or accepts raw compiler flags. On
the DGX Spark GB10 validation host use:

```bash
HASHPHERE_BUILD_CUDA=1 \
HASHPHERE_CUDA_ARCH=121 \
uv sync --locked --reinstall-package hashphere
```

Without `HASHPHERE_BUILD_CUDA=1`, `_cuda.cu` is included in source
distributions but is not compiled or imported. CPU-only packages continue to
provide Python and optional native CPU operation. When the switch is set,
missing `nvcc` or compilation failure stops the CUDA-enabled build instead of
silently producing a package that appears CUDA-capable. Omitting
`HASHPHERE_CUDA_ARCH`, or setting anything other than the currently tested
numeric values `120` and `121`, stops the CUDA build. Future Windows and Linux
hosts must select an explicitly supported architecture for their GPU and
toolkit; normal package imports never probe hardware.

CUDA remains explicit:

```bash
HASHPHERE_COMPUTE_BACKEND=cuda
HASHPHERE_CUDA_DEVICE=0
```

`auto` and `cpu` still select Python. CUDA initialization occurs only during
CUDA listing or explicit CUDA selection. An absent extension, failed runtime
initialization, or missing device is a controlled unavailable-backend error
before Stratum networking. CUDA execution or host-verification failure is
terminal, with no CPU fallback and no Stratum reconnect.

After offline parity succeeds on the target device, the controlled sequential
live command is:

```bash
HASHPHERE_COMPUTE_BACKEND=cuda \
HASHPHERE_CUDA_DEVICE=0 \
HASHPHERE_SEARCH_STRATEGY=sequential \
HASHPHERE_ENABLE_LIVE_STRATUM=1 \
HASHPHERE_ENABLE_LIVE_MINING=1 \
uv run python -m hashphere stratum-mine \
  --start-nonce 0 \
  --chunk-size 1000000 \
  --max-chunks 3 \
  --max-reconnect-attempts 5 \
  --log-file logs/hashphere.jsonl
```

The controlled orbiting-bit form changes only the strategy:

```bash
HASHPHERE_COMPUTE_BACKEND=cuda \
HASHPHERE_CUDA_DEVICE=0 \
HASHPHERE_SEARCH_STRATEGY=orbiting-bit \
HASHPHERE_ENABLE_LIVE_STRATUM=1 \
HASHPHERE_ENABLE_LIVE_MINING=1 \
uv run python -m hashphere stratum-mine \
  --start-nonce 0 \
  --chunk-size 1000000 \
  --max-chunks 3 \
  --max-reconnect-attempts 5 \
  --log-file logs/hashphere.jsonl
```

These commands are documentation only and are not executed automatically.

Real validation on an NVIDIA GB10 with compute capability 12.1 and CUDA 13.0
completed an AArch64 extension build, verified an `sm_121` cubin, and passed
the gated Python/CUDA hardware-parity suite.
Both sequential and orbiting-bit strategies supply the same validated parent
ranges to that backend. Later controlled measurements are recorded in the CUDA
documentation; their local rates are evidence for this host, not a general
speed or pool-performance claim.
See [`docs/10-cuda-backend.md`](docs/10-cuda-backend.md).
Multi-device ownership, reduction, failure policy, one-device Spark evidence,
and the future two-device gate are in
[`docs/11-multi-gpu.md`](docs/11-multi-gpu.md).

`uv sync --locked` attempts to compile the optional self-contained C extension
with the platform C compiler. Python-only operation remains available if that
optional build is unavailable; explicit `native` and `native-parallel`
selection then fail safely before a live connection. The current source build
has been validated with Apple Clang on Apple Silicon. See
[`docs/06-native-cpu.md`](docs/06-native-cpu.md) for build and portability
details.

## Benchmark compute backends offline

The explicit-backend benchmark form requires no `.env`, live opt-in, Stratum
connection, or event log. It searches fixed public synthetic work that is not
valid pool work:

```bash
uv run python -m hashphere compute-benchmark \
  --backend python \
  --hash-count 100000
```

```bash
uv run python -m hashphere compute-benchmark \
  --backend native \
  --hash-count 100000
```

```bash
uv run python -m hashphere compute-benchmark \
  --backend native-parallel \
  --workers 4 \
  --hash-count 1000000
```

After an explicit CUDA build, benchmark one selected device offline with:

```bash
uv run python -m hashphere compute-benchmark \
  --backend cuda \
  --device 0 \
  --hash-count 1000000
```

Benchmark an explicit device set with:

```bash
uv run python -m hashphere compute-benchmark \
  --backend cuda-multi \
  --devices 0,1 \
  --hash-count 500000000
```

Profiles can resolve that same offline benchmark before backend construction:

```bash
uv run python -m hashphere compute-benchmark \
  --profile auto \
  --hash-count 500000000 \
  --warmup-runs 1 \
  --repetitions 5
```

Profile benchmark output is explicitly labeled as raw compute rate; it does not
apply inter-range pacing. Effective profile rate including pacing is reported
by profiled continuous mining.

`--backend` must be exactly `cuda`, `cuda-multi`, `python`, `native`, or
`native-parallel`.
`--workers` is valid only for `native-parallel`, accepts the same strict range
as production configuration, and defaults to `2`. `--device` is valid only
for CUDA, uses the same strict ordinal syntax, and defaults to `0`.
`--devices` is required only for `cuda-multi` and uses the production list
syntax. Multi-device output adds device count and sanitized ascending ordinals.
`--hash-count` is a positive
unpadded ASCII decimal integer; optional `--start-nonce` uses the same strict
syntax and defaults to zero. The selected range may end at `2**32` but cannot
exceed it. Output contains backend, implementation, hashes checked, elapsed
nanoseconds, calculated H/s, worker count for parallel execution, and
exhausted/candidate status. Parallel rate is aggregate actual hashes divided by
wall-clock elapsed time; worker times or rounded worker rates are never summed.
The command never prints the fixture header, targets, digest, candidate nonce,
or per-thread data.

The ordinary command remains one-shot. Explicit repeated tuning adds a separate
first launch, optional warmups, and bounded repetitions:

```bash
uv run python -m hashphere compute-benchmark \
  --backend cuda \
  --device 0 \
  --hash-count 100000000 \
  --warmup-runs 2 \
  --repetitions 7
```

`--warmup-runs` accepts `0` through `100`; `--repetitions` accepts `1` through
`100`. Their defaults, zero and one, preserve the original output and duration.
Repeated output separates initialization, first launch, measured median and
minimum/maximum, total backend-call wall time, and cleanup. It still reports no
work, candidate, per-lane, or device-identity material.

Rates are local measurements for that process, build, machine, and synthetic
fixture. They are not pool performance guarantees and should not be converted
into a general speedup claim without controlled evidence.

Hardware parity tests remain separately gated and never run in default pytest:

```bash
HASHPHERE_ENABLE_CUDA_TESTS=1 \
HASHPHERE_CUDA_DEVICE=0 \
uv run pytest -q tests/test_cuda_hardware.py
```

## Run a live Stratum handshake

Live network access is opt-in. Run exactly:

```bash
HASHPHERE_ENABLE_LIVE_STRATUM=1 \
uv run python -m hashphere stratum-handshake \
  --log-file logs/hashphere.jsonl
```

The command loads `.env` through `Settings.from_env()`, connects to the
configured host and port, subscribes, authorizes, prints a sanitized summary,
and closes the connection. The summary contains:

- Stratum host and port
- a partially masked Stratum username
- `extra_nonce_1`
- `extra_nonce_2_size`
- the final handshake state (`AUTHORIZED` on success)

It never prints the configured Stratum password, complete payout address, or
complete Stratum username.

A successful handshake exits with status `0`. Invalid configuration, a missing
opt-in flag, connection failure, malformed protocol data, a pool error, or
authorization rejection produces a concise error on standard error and exits
nonzero. The command has no mining, share submission, reconnect, thread, or
async behavior.

The live command is the manual integration check. It is not invoked by the
default pytest suite and cannot contact CKPool unless
`HASHPHERE_ENABLE_LIVE_STRATUM=1` is explicitly set.

## Observe live Stratum notifications

To complete a handshake and wait for both supported mining notification types,
run exactly:

```bash
HASHPHERE_ENABLE_LIVE_STRATUM=1 \
uv run python -m hashphere stratum-observe \
  --log-file logs/hashphere.jsonl
```

The observer consumes parsed notifications through `StratumClient` until it
has seen at least one `mining.set_difficulty` and one `mining.notify`, in either
order. Notifications queued during the handshake are included. It then prints
a sanitized summary and closes the connection. For example:

```text
Stratum notification observation succeeded.
Endpoint: stratum.ckpool.org:3333
Username: bc1q…ook1
Arrival order: mining.set_difficulty -> mining.notify
Difficulty: 500000
Job ID: 1a2b3c
Previous block hash: 00000000…89abcdef
Coinbase part 1 hex characters: 184
Coinbase part 2 hex characters: 196
Merkle branch count: 12
Version: 20000000
Network bits: 170fffff
Network time: 68764abc
Clean jobs: true
Extra nonce 1: 08000002
Extra nonce 2 size: 4
State: AUTHORIZED
```

The difficulty and job are reported as independent observations. The command
does not claim that the observed difficulty applies to the displayed job, and
it does not combine them into a mining-job model. It also does not mine, hash,
submit shares, reconnect, poll, or start threads. Complete payout addresses,
usernames, coinbase parts, passwords, and raw JSON are never displayed.

The existing transport read timeout bounds how long the command waits for each
incoming message. Missing opt-in or invalid configuration exits nonzero;
connection, authorization, timeout, malformed-message, unsupported-
notification, and other protocol failures emit a generic sanitized error and
also exit nonzero.

## Run one bounded live mining range

This command performs real hashing and is permitted to send `mining.submit`.
It therefore requires two exact opt-ins. Run:

```bash
HASHPHERE_ENABLE_LIVE_STRATUM=1 \
HASHPHERE_ENABLE_LIVE_MINING=1 \
uv run python -m hashphere stratum-mine-once \
  --start-nonce 0 \
  --hash-count 100000 \
  --log-file logs/hashphere.jsonl
```

`--hash-count` is required and must be a positive ASCII decimal integer no
larger than `2**32`. `--start-nonce` defaults to `0` and may be an ASCII decimal
integer through `0xffffffff`. Together they select the unchanged half-open
range `[start_nonce, start_nonce + hash_count)`. A range extending beyond
`2**32` is rejected rather than wrapped, shortened, or split.

The command handshakes, waits for a job that arrives after a known difficulty,
generates one `extra_nonce_2`, prepares fixed work once, and searches the range
once. A typical exhausted result is:

```text
Bounded Stratum mining completed.
Endpoint: stratum.ckpool.org:3333
Username: bc1q…ook1
Compute backend: python
Search strategy: sequential
Job ID: 1a2b3c
Difficulty: 10000
Network bits: 17023ad4
Extra nonce 2 size: 4
Start nonce: 0
Exclusive stop nonce: 100000
Hashes checked: 100000
Elapsed time: 250000000 ns
Hashes per second: 400000.00
Result: no qualifying hash found
```

If a qualifying hash is returned, it is submitted exactly once. Accepted output
adds fields like:

```text
Matched nonce: 305419896
Submitted nonce hex: 78563412
Raw block hash: 12345678…00000000
Meets share target: true
Meets network target: false
Pool result: accepted
```

A normal pool rejection uses the same sanitized summary with:

```text
Pool result: rejected
```

Bounded exhaustion, acceptance, and rejection are completed runs and return
exit status `0`. Configuration, opt-in, connection, protocol, preparation,
search, submission, or cleanup failures return nonzero. The client is always
closed, and there is no retry, reconnect, resubmission, next-range search,
extra-nonce progression, or network-time rolling.

A small bounded run will probably find no share at difficulty 10000. CKPool
worker statistics require an accepted submitted share; a successful handshake,
observed job, or exhausted local range is not enough. This command is a
one-shot engineering runner, not a continuous miner.

Output includes a masked username and abbreviated block hash. It never includes
the password, complete payout address or username, complete coinbase
transaction, raw job JSON, authorization request, or complete submission
request.

## Run a bounded sequence of mining chunks

The chunked command remains finite, but divides one global hash budget into
sequential searches and checks for immediately available Stratum notifications
between them:

```bash
HASHPHERE_ENABLE_LIVE_STRATUM=1 \
HASHPHERE_ENABLE_LIVE_MINING=1 \
uv run python -m hashphere stratum-mine-chunks \
  --start-nonce 0 \
  --chunk-size 100000 \
  --max-hashes 1000000 \
  --log-file logs/hashphere.jsonl
```

Both opt-ins are required. `--chunk-size` and `--max-hashes` are required
positive, unpadded ASCII decimal integers no larger than `2**32`.
`--start-nonce` defaults to `0` and may range through `0xffffffff`. The global
budget may not extend beyond the remaining 32-bit nonce space from that start.
Each search uses a half-open range. Adjacent chunks for one job have no gaps or
overlap, and the final chunk is shortened when less than one configured chunk
remains.

After each exhausted nonfinal chunk, the client performs nonblocking polls and
drains every immediately available notification in arrival order. Difficulty
changes apply only to jobs announced after them; they never rebuild the current
job retroactively. A newly announced job replaces current work before the next
chunk. Hashphere deliberately switches to the newest announced job for both
`clean_jobs=true` and `clean_jobs=false` as a freshness policy. If several jobs
arrive together, only the final newest job is searched.

A replacement job restarts at the configured start nonce, but hashes already
checked remain consumed from the invocation-wide budget. One `extra_nonce_2`
is generated and reused for every prepared job. A chunk already running is not
interrupted, and no notification poll occurs between candidate discovery and
its one immediate submission.

Budget exhaustion, pool acceptance, and pool rejection are successful finite
outcomes. The command does not continue indefinitely, roll time or extra nonce
values, reconnect, retry, or schedule background work. It is a bounded
engineering step, not yet the unlimited continuous miner.

## Run continuous live Stratum mining

The continuous command repeats bounded nonce chunks, handles new difficulty and
job notifications between chunks, and stops cleanly on Ctrl-C, SIGTERM, or an
optional internal monotonic runtime limit. It requires the same two exact
live-mining opt-ins:

```bash
HASHPHERE_ENABLE_LIVE_STRATUM=1 \
HASHPHERE_ENABLE_LIVE_MINING=1 \
uv run python -m hashphere stratum-mine \
  --start-nonce 0 \
  --chunk-size 100000 \
  --max-reconnect-attempts 5 \
  --log-file logs/hashphere.jsonl
```

`--chunk-size` is required. `--start-nonce` defaults to `0`. Both use strict,
unpadded ASCII decimal syntax and the unsigned 32-bit nonce range. With no
`--max-chunks`, the command continues until Ctrl-C, a candidate is submitted,
or an unrecoverable error occurs. There is no hidden chunk limit.

`--max-reconnect-attempts` defaults to `5`, accepts strict unpadded ASCII
decimal values from `0` through `100`, and counts retries after a failed active
connection or initial attempt. Zero disables retries. The fixed retry delays
are 1, 2, 4, 8, and 16 seconds for the default policy, with longer configured
sequences capped at 30 seconds. There is no random jitter or endpoint
failover. Ctrl-C interrupts backoff before another client is created.

`--max-runtime-seconds` accepts a positive finite decimal duration, including a
fractional duration, through a maximum of `31536000` seconds (365 days). The
clock starts after settings and backend/strategy validation, when the command
enters its active session lifecycle. The option uses monotonic time and is
checked during bounded session waits and reconnect backoff and at safe mining
boundaries. Reaching it is the successful `runtime_limit_reached` outcome; the
final summary and `command_completed` retain all aggregate counters. Omitting
it preserves unlimited runtime.

Two independent opt-in liveness limits are also available and disabled by
default:

- `--max-server-silence-seconds` measures monotonic time since any supported,
  complete incoming Stratum notification. Difficulty and job notifications
  both refresh it.
- `--max-job-age-seconds` measures monotonic time since the active job was
  received. Unrelated difficulty traffic does not refresh it.

Both accept positive finite decimal values through `31536000` seconds. There
is no hard-coded CKPool interval. A quiet connection or old job is not
universally invalid; an operator must deliberately enable the applicable
policy for the selected server. Completed ranges update a separate work clock
and never masquerade as server activity.

At an exact configured boundary Hashsphere accounts for the current completed
range, suppresses any candidate returned from the newly stale session, closes
that session, and enters the existing reconnect policy. Fresh subscribe,
authorization, difficulty, job, and session-specific extra-nonce state are
required before another range. Runtime limits and SIGINT/SIGTERM remain active
during recovery, and no reconnect begins after a stop request.

For a controlled live validation, cap the number of actual searches:

```bash
HASHPHERE_SEARCH_STRATEGY=sequential \
HASHPHERE_COMPUTE_BACKEND=python \
HASHPHERE_ENABLE_LIVE_STRATUM=1 \
HASHPHERE_ENABLE_LIVE_MINING=1 \
uv run python -m hashphere stratum-mine \
  --start-nonce 0 \
  --chunk-size 100000 \
  --max-chunks 3 \
  --max-reconnect-attempts 5 \
  --log-file logs/hashphere.jsonl
```

`--max-chunks` is an optional positive, unpadded ASCII decimal integer. Idle
notification waits and job replacements do not consume it. Reaching the limit
is a successful `chunk_limit_reached` outcome, and the final permitted chunk
may still submit its candidate.

To validate progression without a large hash run, search the final nonce of
three consecutive work variants:

```bash
HASHPHERE_ENABLE_LIVE_STRATUM=1 \
HASHPHERE_ENABLE_LIVE_MINING=1 \
uv run python -m hashphere stratum-mine \
  --start-nonce 4294967295 \
  --chunk-size 1 \
  --max-chunks 3 \
  --max-reconnect-attempts 5 \
  --log-file logs/hashphere.jsonl
```

Chunks for unchanged work are adjacent half-open ranges with no skipped or
repeated nonce. Difficulty notifications affect later jobs only. When several
jobs are available between chunks, only the final newest job is prepared and
searched. Both `clean_jobs=true` and `clean_jobs=false` select the newest job
under Hashphere's freshness policy, and replacement work restarts at the
configured start nonce without resetting cumulative counters.

The first Ctrl-C/SIGINT or SIGTERM requests cooperative shutdown, and repeated
signals are idempotent. Previous handlers are restored after the command, so
normal `KeyboardInterrupt` behavior remains unchanged elsewhere. The current
backend range may finish, but no later range or reconnect attempt starts; a
candidate returned by that exact completed search is still submitted once.
CUDA has no unsafe mid-kernel cancellation, so stop responsiveness is bounded
by the active range duration. After offline tuning, a 500,000,000-nonce
synthetic range measured about 0.20 seconds locally; a later human-controlled
live gate must establish the corresponding pool workload behavior.

For an external-signal check, invoke the project interpreter directly rather
than placing `uv` between the signal sender and Hashphere:

```bash
HASHPHERE_ENABLE_LIVE_STRATUM=1 \
HASHPHERE_ENABLE_LIVE_MINING=1 \
./.venv/bin/python -m hashphere stratum-mine \
  --chunk-size 500000000 \
  --max-runtime-seconds 60 \
  --log-file logs/hashphere.jsonl
```

A graceful signal writes `command_completed` with `stopped_by_user`. SIGKILL
cannot run Python cleanup or write a terminal event, so its run remains
incomplete in `logs-summary`.

New TCP sockets enable portable `SO_KEEPALIVE` with operating-system default
timing. No global or privileged tuning is performed. Keepalive can detect some
dead peers but cannot prove that a Stratum application is delivering fresh
work, so it does not replace the explicit liveness policy. Portable automatic
suspend detection remains deferred: supported systems differ on whether their
monotonic clock advances during sleep, and an ordinary scheduling delay is not
safe proof of suspend.

The five-minute manually authorized liveness gate used this conservative form:

```bash
HASHPHERE_COMPUTE_BACKEND=cuda \
HASHPHERE_CUDA_DEVICE=0 \
HASHPHERE_ENABLE_LIVE_STRATUM=1 \
HASHPHERE_ENABLE_LIVE_MINING=1 \
./.venv/bin/python -m hashphere stratum-mine \
  --chunk-size 500000000 \
  --max-runtime-seconds 300 \
  --max-server-silence-seconds 120 \
  --max-job-age-seconds 600 \
  --max-reconnect-attempts 5 \
  --log-file logs/liveness-check.jsonl
```

That pre-tuning gate completed at approximately 307.62 MH/s. Later authorized
post-tuning gates sustained approximately 2.462 GH/s for 60 seconds and 2.461
GH/s for five minutes. The five-minute run checked 737,414,244,096 hashes over
1,547 ranges and ended with `runtime_limit_reached`, with no duplicate work,
connection loss, reconnect, stale session, or command failure. No live command
was run while implementing multi-device orchestration.
Deterministic local recovery needs no internet:

```bash
uv run pytest -q tests/test_stratum_liveness.py \
  tests/test_stratum_recovery.py \
  tests/test_continuous_mining.py -k 'liveness or stale'
```

Inspect only sanitized liveness transitions with:

```bash
jq 'select(.event | startswith("stratum_liveness") or contains("stale"))' \
  logs/liveness-check.jsonl
```

If one prepared variant exhausts the remaining 32-bit nonce space, Hashphere
does not wrap or repeat its nonce ranges. It first drains queued pool
notifications. A genuinely newer selected job takes priority and restarts at
the configured nonce with the current session's original extra-nonce seed.
Otherwise, Hashphere advances `extra_nonce_2` by one modulo its negotiated
fixed-width space, rebuilds the coinbase-derived work once, and restarts the
nonce position. One random seed is generated only after each successful
session authorization, using that session's negotiated width; all later values
inside the session are deterministic. A reconnect may negotiate a different
width and therefore receives one new seed, which is never printed or logged.

After every negotiated `extra_nonce_2` value has been searched once at one
network time, the time advances by exactly one second and extra-nonce
progression restarts from the original seed. Local time never wraps beyond
`ffffffff`; only then does the command wait with bounded notification polls
for newer pool work. Compact cursor and effective-work identities prevent
duplicate range searches without retaining an unbounded nonce history. A
changed target is treated as a new acceptance context, while an identical pool
reannouncement does not restart already searched work.

On a genuine connection or transport-availability failure during handshake,
initial work acquisition, between-chunk polling, or terminal replacement wait,
Hashphere closes the failed client best-effort and creates a new client for the
same configured endpoint. It requires a fresh subscription, authorization,
difficulty, and later usable job before resuming. Old notifications, assembler
state, prepared work, request IDs, local extra-nonce progress, and rolled time
are discarded. Invocation-wide chunk consumption, hashes, elapsed mining time,
candidates, submissions, and recovery counters remain cumulative. New-session
work is a new Stratum acceptance context even if its prepared header and
targets happen to match the prior session.

Malformed protocol data, authorization rejection, mining invariants, logging
errors, and other non-connection failures remain terminal. Most importantly,
a `mining.submit` transport failure is never retried: its outcome is uncertain,
and resending could duplicate a submission. Pool failover, random backoff
jitter, cooperative mid-chunk cancellation, SIMD, multiprocessing, and real
multi-GPU hardware validation remain deferred.

To validate explicit native selection with a bounded live gate, use:

```bash
HASHPHERE_COMPUTE_BACKEND=native \
HASHPHERE_ENABLE_LIVE_STRATUM=1 \
HASHPHERE_ENABLE_LIVE_MINING=1 \
uv run python -m hashphere stratum-mine \
  --start-nonce 0 \
  --chunk-size 100000 \
  --max-chunks 3 \
  --max-reconnect-attempts 5 \
  --log-file logs/hashphere.jsonl
```

This command is documented for an explicitly authorized manual gate and is not
run by automated tests.

To validate the parallel backend under the same controlled gate, use:

```bash
HASHPHERE_SEARCH_STRATEGY=sequential \
HASHPHERE_COMPUTE_BACKEND=native-parallel \
HASHPHERE_COMPUTE_WORKERS=4 \
HASHPHERE_ENABLE_LIVE_STRATUM=1 \
HASHPHERE_ENABLE_LIVE_MINING=1 \
uv run python -m hashphere stratum-mine \
  --start-nonce 0 \
  --chunk-size 1000000 \
  --max-chunks 3 \
  --max-reconnect-attempts 5 \
  --log-file logs/hashphere.jsonl
```

The executor is reused through chunks, work changes, and reconnects, then
closed when the command exits. A stop requested during a parallel call takes
effect after that call completes; running native workers cannot yet be
cooperatively interrupted.

To validate experimental orbiting-bit ordering with the same bounded gate, use:

```bash
HASHPHERE_SEARCH_STRATEGY=orbiting-bit \
HASHPHERE_COMPUTE_BACKEND=native-parallel \
HASHPHERE_COMPUTE_WORKERS=4 \
HASHPHERE_ENABLE_LIVE_STRATUM=1 \
HASHPHERE_ENABLE_LIVE_MINING=1 \
uv run python -m hashphere stratum-mine \
  --start-nonce 0 \
  --chunk-size 1000000 \
  --max-chunks 3 \
  --max-reconnect-attempts 5 \
  --log-file logs/hashphere.jsonl
```

This command is documented for an explicitly authorized manual gate and is not
run by automated tests. Its first three parent ranges are expected to be
`[0, 1000000)`, `[4096000000, 4097000000)`, and
`[2048000000, 2049000000)`.

The actual generated or advanced extra nonce is never displayed or logged.
Final output and JSONL analysis expose only safe aggregate variant, advance,
cycle, network-time-roll, duplicate, connection-loss, and reconnect counts.
They also report the stable backend name (`python`, `native`, or
`native-parallel`) and safe parallel worker count without hardware, thread, or
device identifiers. Mining output also reports the stable search strategy name
without cursor positions or assignment history.

Final output reports sanitized aggregate counters and weighted hash rate,
including reconnect attempts, successful reconnects, failed reconnect
attempts, and sessions established.
Controlled stop, runtime-limit, chunk-limit, accepted-share, and rejected-share
outcomes exit with status `0`; syntax or opt-in failures return `2`, and runtime
or cleanup failures return `1` without printing arbitrary exception details.

## Write structured JSONL event logs

The `--log-file PATH` option is available on `stratum-handshake`,
`stratum-observe`, `stratum-mine-once`, `stratum-mine-chunks`, and
`stratum-mine`. It is optional: when omitted, no log file is created and the
existing console output is unchanged.

When requested, Hashphere creates missing parent directories and appends
sanitized events to the UTF-8 file without truncating existing records. Each
event is one compact JSON object on one line and is flushed immediately, so the
file can be tailed while a command runs. Separate command invocations receive
separate nonsecret run IDs, and event sequences restart at one. The local
`logs/` directory is ignored by Git.

Structured logs omit passwords, payout addresses, complete usernames, both
extra nonces, coinbase data, raw jobs, request payloads, response payloads, and
arbitrary exception messages. The human-readable console summary remains the
primary interactive output; JSONL provides stable machine-readable events.

On macOS and Linux:

```bash
tail -f logs/hashphere.jsonl
jq . logs/hashphere.jsonl
jq 'select(.level == "ERROR")' logs/hashphere.jsonl
jq 'select(.event == "nonce_range_completed")' logs/hashphere.jsonl
```

On Windows PowerShell:

```powershell
Get-Content .\logs\hashphere.jsonl -Wait
Get-Content .\logs\hashphere.jsonl |
  ForEach-Object { $_ | ConvertFrom-Json }
```

Failure to initialize, write, or close an explicitly requested event log is
reported with a nonzero exit status; logging is never silently disabled.

## Summarize structured logs locally

Hashphere can validate and summarize an existing schema-version-1 event log
without `jq` or a network connection:

```bash
uv run python -m hashphere logs-summary \
  --log-file logs/hashphere.jsonl
```

`logs-summary` is read-only. It does not require either live-network opt-in,
load Stratum configuration, initialize an event sink, modify the source file,
or create a missing path. Each physical line must be one valid JSON event;
blank, malformed, semantically invalid, or run-inconsistent lines fail the
whole analysis instead of being silently skipped.

Example sanitized output:

```text
Hashphere log summary.
Log file: logs/hashphere.jsonl
Records: 6
Runs: 2
Completed runs: 2
Failed runs: 0
Incomplete runs: 0
First event: 2026-07-27T12:15:31.632625Z
Last event: 2026-07-27T12:16:17.817473Z

Commands:
  stratum-handshake: 2
  stratum-observe: 0
  stratum-mine-once: 0
  stratum-mine-chunks: 0
  stratum-mine: 0

Completion outcomes:
  handshake_succeeded: 2

Mining:
  Difficulty events: 0
  Jobs received: 0
  Work variants searched: 0
  Extra nonce 2 advances: 0
  Extra nonce 2 cycles: 0
  Network-time rolls: 0
  Duplicate work ignored: 0
  Connection losses: 0
  Reconnect attempts: 0
  Reconnect successes: 0
  Reconnect failures: 0
  Reconnect exhausted events: 0
  Nonce ranges completed: 0
  Hashes checked: 0
  Mining elapsed: 0 ns
  Weighted hashes per second: unavailable
  Share candidates: 0
  Shares submitted: 0
  Shares accepted: 0
  Shares rejected: 0

Failures:
  command_failed events: 0
```

The weighted mining rate is calculated from total hashes checked divided by
total mining elapsed time, not by averaging the rounded per-range rates stored
in the log. It is unavailable when there are no completed ranges or their
total elapsed time is zero. The summary contains aggregate counts only; it
does not print run IDs, job IDs, nonces, block hashes, credentials, or raw
records.

The direct `tail`, `jq`, and PowerShell inspection commands above remain useful
when record-level access is explicitly wanted. Machine-readable summary output,
log rotation, and Prometheus/Grafana-compatible metrics remain deferred future
observability options.
