# Hashphere

Hashphere is an experimental Python Bitcoin mining project. Live operations are
explicitly opt-in and include Stratum inspection, bounded mining, and a
synchronous continuous lifecycle that searches one nonce chunk at a time.
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
`native-parallel`, and `cuda` are supported, and the earlier `cpu` value
remains a compatibility alias for `python`. The native modes require the
optional C extension. CUDA additionally requires the explicitly enabled CUDA
extension and one available NVIDIA device. There is no fallback after an
explicit selection.

`HASHPHERE_COMPUTE_WORKERS` configures only `native-parallel`. It is a strict
unpadded ASCII decimal integer from `1` through `256` and defaults to `2`.
Ranges shorter than the configured count create fewer nonempty assignments.
`HASHPHERE_COMPUTE_PROFILE` remains separate; future Lite/Auto/Max/Custom
profiles may choose worker counts and resource policy, but profile behavior is
not implemented yet.

`HASHPHERE_CUDA_DEVICE` configures only an explicitly selected `cuda` backend.
It defaults to ordinal `0` and must be an unpadded ASCII decimal integer from
`0` through `2147483647`. Invalid CUDA device syntax or an unavailable device
fails before live networking. The setting is ignored by CPU backends. UUIDs,
serial numbers, PCI addresses, and driver paths are never included in normal
console or JSONL output. Multi-GPU selection is not implemented.

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
automatic device selection, multi-GPU execution, tuning, and resource profiles
remain deferred.

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
completed an AArch64 extension build, verified an `sm_121` cubin, passed all 7
gated Python/CUDA hardware-parity tests, and passed 60 CUDA host/build tests.
Both sequential and orbiting-bit strategies supply the same validated parent
ranges to that backend. No CUDA benchmark or live CKPool CUDA mining run has
been recorded, so no speed or live-mining claim is made.
See [`docs/10-cuda-backend.md`](docs/10-cuda-backend.md).

`uv sync --locked` attempts to compile the optional self-contained C extension
with the platform C compiler. Python-only operation remains available if that
optional build is unavailable; explicit `native` and `native-parallel`
selection then fail safely before a live connection. The current source build
has been validated with Apple Clang on Apple Silicon. See
[`docs/06-native-cpu.md`](docs/06-native-cpu.md) for build and portability
details.

## Benchmark compute backends offline

The benchmark command requires no `.env`, live opt-in, Stratum connection, or
event log. It searches fixed public synthetic work that is not valid pool work:

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

`--backend` must be exactly `cuda`, `python`, `native`, or `native-parallel`.
`--workers` is valid only for `native-parallel`, accepts the same strict range
as production configuration, and defaults to `2`. `--device` is valid only
for CUDA, uses the same strict ordinal syntax, and defaults to `0`.
`--hash-count` is a positive
unpadded ASCII decimal integer; optional `--start-nonce` uses the same strict
syntax and defaults to zero. The selected range may end at `2**32` but cannot
exceed it. Output contains backend, implementation, hashes checked, elapsed
nanoseconds, calculated H/s, worker count for parallel execution, and
exhausted/candidate status. Parallel rate is aggregate actual hashes divided by
wall-clock elapsed time; worker times or rounded worker rates are never summed.
The command never prints the fixture header, targets, digest, candidate nonce,
or per-thread data.

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
job notifications between chunks, and stops cleanly on Ctrl-C. It requires the
same two exact live-mining opt-ins:

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

Ctrl-C and supported termination signals request cooperative shutdown. The
current Python chunk may finish, but no later chunk or replacement poll starts;
a candidate returned by that exact completed search is still submitted once.
This milestone does not cancel a chunk mid-search.

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
jitter, cooperative mid-chunk cancellation, SIMD, multiprocessing, CUDA
performance tuning, and multi-GPU execution remain deferred.

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
Controlled stop, chunk-limit, accepted-share, and rejected-share outcomes exit
with status `0`; syntax or opt-in failures return `2`, and runtime or cleanup
failures return `1` without printing arbitrary exception details.

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
