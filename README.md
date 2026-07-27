# Hashphere

Hashphere is an experimental Python Bitcoin mining project. Live operations are
explicitly opt-in and include Stratum inspection plus one bounded, synchronous
mining range that may submit at most one returned match. Hashphere is not yet a
continuous miner.

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

## Write structured JSONL event logs

The `--log-file PATH` option is available on `stratum-handshake`,
`stratum-observe`, and `stratum-mine-once`. It is optional: when omitted, no log
file is created and the existing console output is unchanged.

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

Completion outcomes:
  handshake_succeeded: 2

Mining:
  Difficulty events: 0
  Jobs received: 0
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
observability options and do not block continued mining orchestration work.
