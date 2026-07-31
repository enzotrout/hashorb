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

The final log target must be a regular file and not a symlink. On POSIX, a new
file is created as mode `0600`; an existing file must be owned by the effective
user and have no group or other permissions. Windows confidentiality depends
on the containing directory ACL. Initialization fails safely when these
conditions, the path, or the filesystem are unsuitable.

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

- `compute_backend_selected` once after backend selection and before the live
  connection
- `search_strategy_selected` once after compatible strategy selection and
  before the live connection
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

Profiled continuous mining additionally emits `compute_profile_resolved` once
after command-time resolution and before final backend construction. It contains
only requested/effective profile, effective backend, optional safe worker or
ordinal metadata, validated launch size, parent chunk, pacing, and a controlled
resolution reason. These fields are not repeated per range. Profiled terminal
completion may add `profile_wall_elapsed_ns` and
`effective_hashes_per_second`; existing range elapsed and weighted rate retain
their compute-only meaning.

`compute_backend_selected` is shared by all three mining commands. Its fields
are limited to `backend_name`, `backend_kind`, `implementation`,
`supports_parallel_search`, `supports_cooperative_cancellation`,
`supports_device_selection`, and the safe optional `worker_count` or
`device_ordinal`. It is emitted once per invocation rather than once per
range, so existing range events remain the authoritative parent-search records.
Hardware serial numbers, thread identifiers, assignment bounds, device paths,
availability-error text, work bytes, and credentials are never included.

An explicitly selected native backend uses the same event with
`backend_name=native`, `backend_kind=cpu`, and `implementation=c`. Its
capability Booleans remain false for parallel search and cooperative
cancellation. Compiler paths, build commands, CPU identity, extension import
details, and raw native failures are not event fields. The offline
`compute-benchmark` command intentionally does not create JSONL events.

The parallel backend uses `backend_name=native-parallel`, `backend_kind=cpu`,
`implementation=c-threadpool`, `supports_parallel_search=true`,
`supports_cooperative_cancellation=false`, and its configured worker count.
There is no event per executor worker or assignment. Existing
`nonce_range_started` and `nonce_range_completed` events continue to report the
parent range, aggregate actual hashes, and wall-clock elapsed time. Read-only
summary aggregation naturally counts the stable parallel backend name without
creating worker-count aggregates.

CUDA uses `backend_name=cuda`, `backend_kind=gpu`, `implementation=cuda`,
`supports_parallel_search=true`, `supports_cooperative_cancellation=false`,
`supports_device_selection=true`, and one nonnegative device ordinal. That
ordinal is the only device identity permitted. GPU UUID, serial number, PCI
address, driver or compiler path, target and header data, candidate values, raw
CUDA errors, and per-thread, per-block, or per-kernel-lane events are excluded.
The read-only summary naturally counts stable `cuda` selections without a
device-specific aggregate.

Multi-device CUDA uses `backend_name=cuda-multi` and
`implementation=cuda-multi`. Its only additional fields are a positive device
count and canonical ascending ordinal list. These fields are validated when
present, while old logs remain valid without them. Parent-range events retain
aggregate count, elapsed time, and rate; there is no per-device event stream.
UUIDs, serials, PCI addresses, topology, raw futures, and CUDA errors remain
excluded.

`search_strategy_selected` is shared by all mining commands and follows
`compute_backend_selected`. Its exact safe fields are `strategy_name`,
`implementation`, `deterministic`, `contiguous_parent_ranges`, `exhaustive`,
and `experimental`. It is emitted once per invocation, including invocations
that later reconnect; fresh per-work cursors do not produce another selection
event. Cursor positions, assignment indexes, job IDs, headers, extra nonces,
credentials, protocol data, and raw failures are excluded. Existing
`nonce_range_started` and `nonce_range_completed` records remain the only
per-parent-assignment events, avoiding a second high-volume decision stream.

Orbiting-bit uses the same event with `strategy_name=orbiting-bit`,
`implementation=bit-reversal`, `deterministic=true`,
`contiguous_parent_ranges=false`, `exhaustive=true`, and `experimental=true`.
The contiguity flag describes global parent-range order; every individual
searched range remains contiguous. Invalid physical indexes in a non-power-of-
two permutation domain are skipped internally. They create no event and do not
alter chunk, hash, elapsed-time, or weighted-rate totals. Permutation counters,
physical indexes, cursor state, and skip history are not logged.

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

Configured liveness recovery additionally emits sanitized
`stratum_liveness_warning`, `stratum_session_stale`,
`stratum_stale_reconnect_started`, `stratum_stale_reconnect_succeeded`, and
`stratum_stale_reconnect_failed` transitions. Fields are limited to the stable
`server_silence` or `job_age` reason, configured threshold, and sampled
monotonic elapsed duration. They contain no job identity, session nonce
material, endpoint credentials, payload, socket error, or raw exception.

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
Signal-controlled stop emits `mining_stop_requested` before terminal
`command_completed`. Runtime expiry instead records
`runtime_limit_reached` directly in `command_completed`; it is not mislabeled as
a user request. No event follows either terminal record.

An exhausted range emits no share events. The completion outcome is one of
`handshake_succeeded`, `observation_succeeded`, `range_exhausted`,
`hash_budget_exhausted`, `stopped_by_user`, `chunk_limit_reached`,
`runtime_limit_reached`, `share_accepted`, or `share_rejected`.

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

Any `command_completed` record, including `stopped_by_user` after graceful
SIGINT/SIGTERM and `runtime_limit_reached`, is a completed run. A forced SIGKILL
cannot execute cleanup or append a terminal event, so a started run remains
incomplete. Historical records are never rewritten, and unknown future
outcomes remain visible only under their exact recorded value.

## Aggregation and Privacy

The immutable summary reports record and run status counts, chronological
first and last UTC timestamps, sorted command, compute-backend, requested and
effective profile, search-strategy, and completion-outcome counts,
known mining event counts, work variants searched, extra-nonce advances and
cycles, network-time rolls, duplicate work ignored, connection losses,
reconnect attempts, reconnect successes, reconnect failures, reconnect
exhaustion events, liveness warnings, stale sessions, stale reconnect
starts/successes/failures, configured liveness limits, stable stale-reason
counts, range totals, accepted and rejected submission counts, and sorted
controlled failure-stage/category counts. Command counts
are per distinct run ID rather than per record. The human-readable CLI always
shows the five currently known commands in a stable order, including zero
counts, followed by any future command names in sorted order.

Backend aggregates count validated `compute_backend_selected` events by their
stable name and are displayed only when present. Future backend names remain
forward-compatible. The summary does not expose implementation errors or
hardware identifiers, and backend counts do not affect nonce-range totals or
weighted hash rate.

Profile aggregates count validated `compute_profile_resolved` events by
requested and effective name. Old logs without profile events remain readable,
and unknown future nonblank profile names are counted conservatively rather
than rejected.

Strategy aggregates likewise count validated `search_strategy_selected` events
by stable name and appear only when present. Future strategy names remain
forward-compatible. Strategy counts do not expose cursor state or change range
totals, elapsed-time totals, or weighted hash rate.

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

Liveness configuration is read only from optional safe numeric fields on
`command_started`; old logs without those fields remain valid. Stale events are
counted by occurrence and stable reason. Unknown future reasons remain visible
without changing current mining totals.

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
deferred. Operators should use bounded invocations plus filesystem quotas or a
reviewed rotation policy.
