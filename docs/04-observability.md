# Structured Event Logging

## Purpose

Hashphere preserves concise human-readable console output while optionally
writing sanitized, machine-readable events for live Stratum commands. The
logging boundary is synchronous and local. It opens no network connections and
creates no threads or background queues.

When `--log-file PATH` is absent, a `NullEventSink` validates and discards
events without creating a file. When requested, `JsonlEventSink` creates
missing parent directories and appends UTF-8 JSON Lines records. CLI
orchestration emits through the common `EventSink` interface, so networking,
cryptographic, and mining-domain modules never write files directly.

## Event Envelope

Every record contains:

| Field | Meaning |
|---|---|
| `schema_version` | Integer `1` for this event schema |
| `timestamp` | UTC RFC3339 timestamp ending in `Z` |
| `run_id` | Unique nonsecret identifier for one command invocation |
| `sequence` | Integer starting at `1` and increasing once per emitted event |
| `level` | Exact value `INFO`, `WARNING`, or `ERROR` |
| `event` | Stable nonblank `snake_case` event name |
| `command` | Live CLI command that owns the invocation |

Each sink instance creates one run ID. A new command invocation creates a new
sink, obtains a different run ID, and restarts its sequence at one. Timestamp
and run-ID generation are injectable for deterministic tests.

Event-specific fields cannot overwrite envelope fields. Keys must be nonblank
strings. Values must already be JSON-safe primitives or supported nested
lists, tuples, and string-keyed mappings. Bytes, arbitrary objects, NaN, and
infinity are rejected rather than stringified.

## Event Catalog

All live commands emit:

- `command_started`
- `stratum_authorized`
- `command_completed`
- `command_failed` when an expected operation fails

The observer also emits:

- `difficulty_received`
- `mining_job_received`
- `notification_observation_completed`

Bounded mining also emits:

- `difficulty_received`
- `mining_job_received`
- `nonce_range_started`
- `nonce_range_completed`
- `share_candidate_found` when a match exists
- `share_submission_completed` after an accepted or rejected response

Bounded chunked mining may emit multiple ordered `nonce_range_started` and
`nonce_range_completed` pairs in one run. When a notification changes prepared
work, it additionally emits `mining_job_replaced` with the previous and new job
IDs, the new job's `clean_jobs` value, and a one-based replacement index. The
replacement event precedes the next range start. It is diagnostic only and
does not control orchestration. One notification-drain boundary emits at most
one replacement event: the previously searched job transitions directly to
the final newest job selected for the next range. Superseded intermediate jobs
still emit `mining_job_received`, but do not emit misleading replacement
events.

Continuous mining reuses those range, notification, replacement, candidate,
and submission events across an open-ended number of chunks. Each actual
search still has exactly one ordered `nonce_range_started` and
`nonce_range_completed` pair whose bounds match the invoked half-open range.
The analyzer therefore aggregates continuous hashes, elapsed nanoseconds, and
weighted rate without a new range schema.

Stable lifecycle and progression events describe controlled state without
exposing signals or raw work:

- `mining_stop_requested` records one idempotent cooperative stop request and
  contains no signal number, signal name, frame, or exception text.
- `nonce_space_exhausted` records the safe current job ID after its remaining
  unsigned 32-bit nonce range has been searched.
- `mining_work_advanced` records a safe reason, per-job variant index, and
  cumulative extra-nonce/time counters immediately before a variant's first
  search.
- `extra_nonce_2_cycle_completed` records the cumulative completed-cycle
  count, never the negotiated values.
- `network_time_rolled` records the cumulative local roll count, never an
  actual network time.
- `duplicate_work_ignored` records a controlled reason and cumulative count.
- `mining_waiting_for_job` records entry into bounded replacement-job waiting
  only after local progression is terminally exhausted.

Continuous session recovery additionally emits:

- `stratum_connection_lost` when a genuine connection failure enters recovery.
- `stratum_reconnect_scheduled` before one deterministic interruptible delay.
- `stratum_reconnect_attempted` immediately before creating the fresh client.
- `stratum_reconnect_failed` after a recoverable attempt fails.
- `stratum_reconnect_succeeded` only after fresh authorization, difficulty, and
  usable job establish the replacement session.
- `stratum_reconnect_exhausted` before terminal failure when the policy permits
  no further attempt.

Their stable fields are limited to attempt and maximum counts, delay seconds,
controlled recovery stage and error category, successful reconnect count, and
session index. Event order follows execution; a scheduled event precedes its
attempt, and success never means merely that a TCP socket opened.

For each prepared variant, `nonce_space_exhausted` follows the completed final
range. Queued pool work is observed and selected before local progression. A
local successor emits any cycle/time transition followed by
`mining_work_advanced` before its first range. `mining_waiting_for_job` is
emitted only after the complete extra-nonce cycle at maximum network time. A
later `mining_job_replaced` precedes the next work-advance and range events.
Controlled stop emits `mining_stop_requested` before terminal
`command_completed`; no event follows that terminal record.

An exhausted range emits no share events. The completion outcome is one of
`handshake_succeeded`, `observation_succeeded`, `range_exhausted`,
`hash_budget_exhausted`, `stopped_by_user`, `chunk_limit_reached`,
`share_accepted`, or `share_rejected`.

## Safe Field Policy

Events contain only fields deliberately selected by CLI orchestration. Safe
examples include endpoint, difficulty, job ID, network bits, clean-jobs state,
Merkle branch count, nonce-range bounds, local search metrics, an abbreviated
block hash, target-match flags, and pool acceptance.

The sink rejects these secret-bearing or raw-payload field names at every
supported mapping level:

- `password` and `stratum_password`
- `bitcoin_address` and `payout_address`
- `username` and `stratum_username`
- `extra_nonce_1` and `extra_nonce_2`
- `coinbase`, its transaction parts, and raw coinbase variants
- `raw_job`
- subscribe, authorization, and submit request variants
- `request_payload` and `response_payload`

CLI events omit usernames entirely. They also never include complete payout
addresses, either extra nonce, complete coinbase transactions, raw jobs, raw
subscribe/authorization/submission requests, or arbitrary exception messages.
Failure events contain only a controlled stage and safe exception category.
Progression records contain counts and controlled reasons only: actual starting
or advanced `extra_nonce_2`, `extra_nonce_1`, effective network time, header
prefix, and raw work identity are prohibited.
Recovery records likewise omit endpoint credentials, both extra nonces, job
identity, raw exceptions, protocol messages, and request or response payloads.

## Append, Flush, and Close Behavior

Each event is serialized as one compact JSON object followed by exactly one
newline. Files are opened in append mode, so existing records are not
truncated. Each record is flushed immediately for interactive tailing. Every
line can be parsed independently.

The sink is initialized before a live connection is opened and is closed on
success or failure. Emitting after close raises `EventLogError`; repeated close
calls are safe. Initialization, writing, and closing failures are visible to
the CLI and never silently disable explicitly requested logging. A log-close
failure does not replace an earlier command failure.

## Console and Structured Output

Console summaries and existing exit-code policy remain unchanged when logging
is disabled or succeeds. JSONL is an additional sanitized interface intended
for scripts, inspection, and later telemetry work; it does not replace console
output.

## Read-Only Log Summary

The public analysis boundary is:

```python
class LogSummaryError(RuntimeError): ...

@dataclass(frozen=True, slots=True)
class LogSummary: ...

def summarize_jsonl(path: str | Path) -> LogSummary: ...
```

`summarize_jsonl` opens an existing file in read-only UTF-8 text mode and
processes one physical line at a time. It never creates directories, repairs,
rewrites, truncates, rotates, deletes, or appends to the source. A blank line
is invalid because the JSONL contract assigns exactly one event to every
physical line. A malformed nonblank line fails the entire analysis rather than
being skipped.

Every record must be a JSON object with the schema-version-1 envelope. The
analyzer requires an actual integer schema version and sequence (not Boolean),
a parseable UTC RFC3339 timestamp ending in `Z`, a nonblank run ID and command,
an exact supported level, and a valid `snake_case` event name. It also rejects
duplicate JSON keys, non-finite numbers, unsupported schema versions, and
invalid envelope types. Error output may identify a safe path and physical
line number, but never echoes the raw record.

## Run Integrity and Forward Compatibility

Run IDs may be physically interleaved. Each run is validated independently in
its physical appearance order: its command stays constant; sequence starts at
one and increases contiguously; `command_started` occurs exactly once and
first; at most one terminal `command_completed` or `command_failed` event may
occur; and no event follows a terminal event. A started run with no terminal
event is valid and is counted as incomplete.

Unknown schema-version-1 event names are accepted after envelope and run
validation. This allows future events and additional safe fields without
changing current mining totals. Known events validate only the fields used by
their calculations: completion outcome, controlled failure stage/category,
difficulty, received job identity, completed-range metrics, and submission
acceptance.

## Aggregation and Privacy

The immutable summary reports record and run status counts, chronological
first and last UTC timestamps, sorted command and completion-outcome counts,
known mining event counts, work variants searched, extra-nonce advances and
cycles, network-time rolls, duplicate work ignored, connection losses,
reconnect attempts, reconnect successes, reconnect failures, reconnect
exhaustion events, range totals, accepted and rejected submission counts, and
sorted controlled failure-stage/category counts. Command counts
are per distinct run ID rather than per record. The human-readable CLI always
shows the five currently known commands in a stable order, including zero
counts, followed by any future command names in sorted order.

The analyzer treats progression events as stable known records and validates
only their safe fields. It counts event occurrences rather than trusting or
summing cumulative fields. A `mining_work_advanced` record counts one searched
variant; reasons `extra_nonce_2` and `network_time` each count one deterministic
extra-nonce advance. Cycle, time-roll, and duplicate records each add one to
their corresponding aggregate. Unknown future schema-version-1 events retain
the existing forward-compatible behavior and do not affect current totals.

Recovery event fields are validated before aggregation. The analyzer counts
event occurrences rather than trusting cumulative reconnect or session-index
fields. These recovery counters do not affect nonce-range totals or weighted
hash rate.

Aggregate mining rate is weighted from the integer totals:

```text
weighted_hps = total_hashes_checked * 1_000_000_000 / total_elapsed_ns
```

The analyzer does not average or trust logged per-range rates. The result is
unavailable when no range completed or total elapsed time is zero.

The CLI prints aggregate information only. It omits run IDs, job IDs,
usernames, payout addresses, passwords, extra nonces, coinbase data, nonces,
block hashes, raw events, raw exception messages, and protocol payloads. The
user-supplied path may be displayed.

Long continuous runs can grow append-only JSONL files substantially. File
rotation and retention, machine-readable summary output, compression, remote
export, background delivery, and Prometheus/Grafana-compatible metrics remain
deferred.
