# Hashphere

Hashphere is an experimental Python Bitcoin mining project. Live operations are
explicitly opt-in and include Stratum inspection, bounded mining, and a
synchronous continuous lifecycle that searches one Python nonce chunk at a
time. Continuous mining now expands deterministic Bitcoin work space after a
nonce boundary; reconnect and session recovery are not implemented yet.

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
  --log-file logs/hashphere.jsonl
```

`--chunk-size` is required. `--start-nonce` defaults to `0`. Both use strict,
unpadded ASCII decimal syntax and the unsigned 32-bit nonce range. With no
`--max-chunks`, the command continues until Ctrl-C, a candidate is submitted,
or an unrecoverable error occurs. There is no hidden chunk limit.

For a controlled live validation, cap the number of actual searches:

```bash
HASHPHERE_ENABLE_LIVE_STRATUM=1 \
HASHPHERE_ENABLE_LIVE_MINING=1 \
uv run python -m hashphere stratum-mine \
  --start-nonce 0 \
  --chunk-size 100000 \
  --max-chunks 3 \
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
the configured nonce with the invocation's original extra-nonce seed.
Otherwise, Hashphere advances `extra_nonce_2` by one modulo its negotiated
fixed-width space, rebuilds the coinbase-derived work once, and restarts the
nonce position. The single random seed is generated only at invocation start;
all later values are deterministic.

After every negotiated `extra_nonce_2` value has been searched once at one
network time, the time advances by exactly one second and extra-nonce
progression restarts from the original seed. Local time never wraps beyond
`ffffffff`; only then does the command wait with bounded notification polls
for newer pool work. Compact cursor and effective-work identities prevent
duplicate range searches without retaining an unbounded nonce history. A
changed target is treated as a new acceptance context, while an identical pool
reannouncement does not restart already searched work.

The actual generated or advanced extra nonce is never displayed or logged.
Final output and JSONL analysis expose only safe aggregate variant, advance,
cycle, network-time-roll, and duplicate counts. Reconnect, retry,
multiprocessing, native backends, and GPU execution remain unimplemented.

Final output reports sanitized aggregate counters and weighted hash rate.
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
