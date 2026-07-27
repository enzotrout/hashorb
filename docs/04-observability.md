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

An exhausted range emits no share events. The completion outcome is one of
`handshake_succeeded`, `observation_succeeded`, `range_exhausted`,
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

Built-in log summarization and telemetry aggregation are deferred to later
slices. File rotation, retention policy, compression, remote export, and
background event delivery also remain deferred.
