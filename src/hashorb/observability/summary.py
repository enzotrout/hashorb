"""Strict read-only aggregation of HashOrb JSON Lines event logs."""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Never

_SCHEMA_VERSION = 1
_LEVELS = frozenset({"INFO", "WARNING", "ERROR"})
_EVENT_NAME = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
_UTC_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")
_ENVELOPE_FIELDS = (
    "schema_version",
    "timestamp",
    "run_id",
    "sequence",
    "level",
    "event",
    "command",
)


class LogSummaryError(RuntimeError):
    """Raised when a JSONL log cannot be read or fails schema validation."""


@dataclass(frozen=True, slots=True)
class LogSummary:
    """Immutable aggregate information from one validated event log."""

    record_count: int
    run_count: int
    completed_run_count: int
    failed_run_count: int
    incomplete_run_count: int
    first_timestamp: str | None
    last_timestamp: str | None
    command_counts: tuple[tuple[str, int], ...]
    compute_backend_counts: tuple[tuple[str, int], ...]
    requested_profile_counts: tuple[tuple[str, int], ...]
    effective_profile_counts: tuple[tuple[str, int], ...]
    search_strategy_counts: tuple[tuple[str, int], ...]
    completion_outcome_counts: tuple[tuple[str, int], ...]
    difficulty_event_count: int
    mining_job_event_count: int
    work_variant_count: int
    extra_nonce_2_advance_count: int
    extra_nonce_2_cycle_count: int
    network_time_roll_count: int
    duplicate_work_ignored_count: int
    connection_loss_count: int
    reconnect_attempt_count: int
    reconnect_success_count: int
    reconnect_failure_count: int
    reconnect_exhausted_count: int
    liveness_warning_count: int
    stale_session_count: int
    stale_reconnect_started_count: int
    stale_reconnect_success_count: int
    stale_reconnect_failure_count: int
    stale_reason_counts: tuple[tuple[str, int], ...]
    configured_server_silence_limits: tuple[tuple[float, int], ...]
    configured_job_age_limits: tuple[tuple[float, int], ...]
    completed_nonce_range_count: int
    total_hashes_checked: int
    total_mining_elapsed_ns: int
    weighted_hashes_per_second: float | None
    share_candidate_count: int
    share_submission_count: int
    accepted_share_count: int
    rejected_share_count: int
    command_failure_count: int
    failure_stage_category_counts: tuple[tuple[str, str, int], ...]
    solo_chain_counts: tuple[tuple[str, int], ...]
    solo_template_count: int
    solo_template_replacement_count: int
    solo_work_variant_count: int
    solo_coinbase_extra_nonce_advance_count: int
    solo_timestamp_roll_count: int
    solo_completed_nonce_range_count: int
    solo_total_hashes_checked: int
    solo_total_elapsed_ns: int
    solo_weighted_hashes_per_second: float | None
    solo_candidate_count: int
    solo_candidate_suppressed_count: int
    solo_proposal_outcome_counts: tuple[tuple[str, int], ...]
    solo_submission_outcome_counts: tuple[tuple[str, int], ...]
    solo_accepted_block_count: int
    solo_rejected_block_count: int
    solo_rpc_failure_count: int


@dataclass(slots=True)
class _RunState:
    command: str
    next_sequence: int = 1
    terminal_event: str | None = None


@dataclass(slots=True)
class _Accumulator:
    record_count: int = 0
    runs: dict[str, _RunState] = field(default_factory=dict)
    earliest: datetime | None = None
    latest: datetime | None = None
    outcome_counts: Counter[str] = field(default_factory=Counter)
    compute_backend_counts: Counter[str] = field(default_factory=Counter)
    requested_profile_counts: Counter[str] = field(default_factory=Counter)
    effective_profile_counts: Counter[str] = field(default_factory=Counter)
    search_strategy_counts: Counter[str] = field(default_factory=Counter)
    failure_counts: Counter[tuple[str, str]] = field(default_factory=Counter)
    difficulty_event_count: int = 0
    mining_job_event_count: int = 0
    work_variant_count: int = 0
    extra_nonce_2_advance_count: int = 0
    extra_nonce_2_cycle_count: int = 0
    network_time_roll_count: int = 0
    duplicate_work_ignored_count: int = 0
    connection_loss_count: int = 0
    reconnect_attempt_count: int = 0
    reconnect_success_count: int = 0
    reconnect_failure_count: int = 0
    reconnect_exhausted_count: int = 0
    liveness_warning_count: int = 0
    stale_session_count: int = 0
    stale_reconnect_started_count: int = 0
    stale_reconnect_success_count: int = 0
    stale_reconnect_failure_count: int = 0
    stale_reason_counts: Counter[str] = field(default_factory=Counter)
    configured_server_silence_limits: Counter[float] = field(default_factory=Counter)
    configured_job_age_limits: Counter[float] = field(default_factory=Counter)
    completed_nonce_range_count: int = 0
    total_hashes_checked: int = 0
    total_mining_elapsed_ns: int = 0
    share_candidate_count: int = 0
    share_submission_count: int = 0
    accepted_share_count: int = 0
    rejected_share_count: int = 0
    command_failure_count: int = 0
    solo_chain_counts: Counter[str] = field(default_factory=Counter)
    solo_template_count: int = 0
    solo_template_replacement_count: int = 0
    solo_work_variant_count: int = 0
    solo_coinbase_extra_nonce_advance_count: int = 0
    solo_timestamp_roll_count: int = 0
    solo_completed_nonce_range_count: int = 0
    solo_total_hashes_checked: int = 0
    solo_total_elapsed_ns: int = 0
    solo_candidate_count: int = 0
    solo_candidate_suppressed_count: int = 0
    solo_proposal_outcome_counts: Counter[str] = field(default_factory=Counter)
    solo_submission_outcome_counts: Counter[str] = field(default_factory=Counter)
    solo_accepted_block_count: int = 0
    solo_rejected_block_count: int = 0
    solo_rpc_failure_count: int = 0


class _RecordValidationError(ValueError):
    """Internal validation error with a safe field-oriented message."""


class _DuplicateKeyError(ValueError):
    """Internal duplicate-JSON-key marker."""


class _NonFiniteNumberError(ValueError):
    """Internal non-finite JSON-number marker."""


def summarize_jsonl(path: str | Path) -> LogSummary:
    """Validate and summarize a UTF-8 HashOrb schema-version-1 JSONL file."""

    log_path = _validate_path(path)
    accumulator = _Accumulator()

    try:
        stream = log_path.open("r", encoding="utf-8", newline="")
    except (OSError, ValueError) as exc:
        raise LogSummaryError(f"could not read log file: {log_path}") from exc

    line_number = 0
    try:
        with stream:
            while True:
                try:
                    line = stream.readline()
                except (OSError, UnicodeError) as exc:
                    raise LogSummaryError(
                        f"could not read log file at line {line_number + 1}: {log_path}"
                    ) from exc
                if line == "":
                    break
                line_number += 1
                try:
                    _consume_line(accumulator, line, line_number)
                except LogSummaryError as exc:
                    raise LogSummaryError(f"{exc}; log file: {log_path}") from exc
    except OSError as exc:
        raise LogSummaryError(f"could not read log file: {log_path}") from exc

    return _build_summary(accumulator)


def _validate_path(path: str | Path) -> Path:
    if isinstance(path, str):
        if not path.strip():
            raise ValueError("log path must not be blank")
        return Path(path)
    if isinstance(path, Path):
        return path
    raise TypeError("log path must be a string or Path")


def _consume_line(accumulator: _Accumulator, line: str, line_number: int) -> None:
    if not line.strip():
        raise LogSummaryError(f"invalid blank JSONL record at line {line_number}")

    try:
        decoded: object = json.loads(
            line,
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_nonfinite_number,
        )
    except (json.JSONDecodeError, _DuplicateKeyError, _NonFiniteNumberError) as exc:
        raise LogSummaryError(f"invalid JSONL record at line {line_number}") from exc

    if not isinstance(decoded, dict):
        raise LogSummaryError(f"invalid JSON object at line {line_number}")

    try:
        _validate_finite_json_numbers(decoded)
        _consume_record(accumulator, decoded)
    except _RecordValidationError as exc:
        raise LogSummaryError(f"invalid log record at line {line_number}: {exc}") from exc


def _object_without_duplicate_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError
        result[key] = value
    return result


def _reject_nonfinite_number(value: str) -> Never:
    raise _NonFiniteNumberError(value)


def _validate_finite_json_numbers(value: object) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise _RecordValidationError("record contains a non-finite number")
    if isinstance(value, dict):
        for nested_value in value.values():
            _validate_finite_json_numbers(nested_value)
    elif isinstance(value, list):
        for nested_value in value:
            _validate_finite_json_numbers(nested_value)


def _consume_record(accumulator: _Accumulator, record: dict[str, object]) -> None:
    for field_name in _ENVELOPE_FIELDS:
        if field_name not in record:
            raise _RecordValidationError(f"missing {field_name}")

    schema_version = _actual_int(record["schema_version"], "schema_version")
    if schema_version != _SCHEMA_VERSION:
        raise _RecordValidationError("unsupported schema_version")
    timestamp = _parse_timestamp(record["timestamp"])
    run_id = _nonblank_string(record["run_id"], "run_id")
    sequence = _actual_int(record["sequence"], "sequence")
    if sequence <= 0:
        raise _RecordValidationError("sequence must be positive")
    level = record["level"]
    if not isinstance(level, str) or level not in _LEVELS:
        raise _RecordValidationError("level must be INFO, WARNING, or ERROR")
    event = record["event"]
    if not isinstance(event, str) or _EVENT_NAME.fullmatch(event) is None:
        raise _RecordValidationError("event must be a nonblank snake_case string")
    command = _nonblank_string(record["command"], "command")

    _validate_run_integrity(accumulator, run_id, sequence, event, command)
    _aggregate_known_event(accumulator, event, record)
    accumulator.record_count += 1
    if accumulator.earliest is None or timestamp < accumulator.earliest:
        accumulator.earliest = timestamp
    if accumulator.latest is None or timestamp > accumulator.latest:
        accumulator.latest = timestamp


def _validate_run_integrity(
    accumulator: _Accumulator,
    run_id: str,
    sequence: int,
    event: str,
    command: str,
) -> None:
    state = accumulator.runs.get(run_id)
    if state is None:
        if sequence != 1:
            raise _RecordValidationError("run sequence must begin at 1")
        if event != "command_started":
            raise _RecordValidationError("first run event must be command_started")
        accumulator.runs[run_id] = _RunState(command=command, next_sequence=2)
        return

    if command != state.command:
        raise _RecordValidationError("command changed within run")
    if sequence != state.next_sequence:
        raise _RecordValidationError("run sequence must be contiguous")
    if state.terminal_event is not None:
        raise _RecordValidationError("event occurred after terminal event")
    if event == "command_started":
        raise _RecordValidationError("command_started may occur only once")

    state.next_sequence += 1
    if event in {"command_completed", "command_failed"}:
        state.terminal_event = event


def _aggregate_known_event(
    accumulator: _Accumulator,
    event: str,
    record: dict[str, object],
) -> None:
    if event == "command_started":
        if "max_server_silence_seconds" in record:
            limit = float(
                _positive_number(
                    record["max_server_silence_seconds"],
                    "max_server_silence_seconds",
                )
            )
            accumulator.configured_server_silence_limits[limit] += 1
        if "max_job_age_seconds" in record:
            limit = float(_positive_number(record["max_job_age_seconds"], "max_job_age_seconds"))
            accumulator.configured_job_age_limits[limit] += 1
    elif event == "command_completed":
        outcome = _nonblank_string(_required(record, "outcome"), "outcome")
        accumulator.outcome_counts[outcome] += 1
    elif event == "command_failed":
        stage = _nonblank_string(_required(record, "stage"), "stage")
        category = _nonblank_string(_required(record, "error_category"), "error_category")
        accumulator.command_failure_count += 1
        accumulator.failure_counts[(stage, category)] += 1
        if record["command"] in {"solo-hash", "solo-mine", "bitcoin-core-check"} and category in {
            "authentication_failure",
            "protocol_failure",
            "remote_failure",
            "rpc_failure",
            "transport_failure",
        }:
            accumulator.solo_rpc_failure_count += 1
    elif event == "bitcoin_rpc_connected":
        chain = _nonblank_string(_required(record, "chain"), "chain")
        if chain not in {"main", "test", "testnet4", "signet", "regtest"}:
            raise _RecordValidationError("chain is unsupported")
        _actual_bool(_required(record, "initial_block_download"), "initial_block_download")
        accumulator.solo_chain_counts[chain] += 1
    elif event == "solo_template_received":
        _safe_identity(_required(record, "template_identity"), "template_identity")
        _actual_bool(_required(record, "replacement"), "replacement")
        accumulator.solo_template_count += 1
    elif event == "solo_template_replaced":
        _safe_identity(
            _required(record, "previous_template_identity"),
            "previous_template_identity",
        )
        _safe_identity(_required(record, "template_identity"), "template_identity")
        _nonblank_string(_required(record, "reason"), "reason")
        accumulator.solo_template_replacement_count += 1
    elif event == "solo_work_variant_started":
        _safe_identity(_required(record, "work_identity"), "work_identity")
        _positive_int(_required(record, "work_variant_index"), "work_variant_index")
        _nonnegative_int(
            _required(record, "coinbase_extra_nonce_advance_count"),
            "coinbase_extra_nonce_advance_count",
        )
        _nonnegative_int(_required(record, "timestamp_roll_count"), "timestamp_roll_count")
        accumulator.solo_work_variant_count += 1
    elif event == "solo_coinbase_extra_nonce_advanced":
        _positive_int(_required(record, "advance_count"), "advance_count")
        accumulator.solo_coinbase_extra_nonce_advance_count += 1
    elif event == "solo_timestamp_rolled":
        _positive_int(_required(record, "roll_count"), "roll_count")
        accumulator.solo_timestamp_roll_count += 1
    elif event == "solo_nonce_range_completed":
        hashes = _nonnegative_int(_required(record, "hashes_checked"), "hashes_checked")
        elapsed = _nonnegative_int(_required(record, "elapsed_ns"), "elapsed_ns")
        _optional_nonnegative_number(_required(record, "hashes_per_second"), "hashes_per_second")
        _actual_bool(_required(record, "candidate_found"), "candidate_found")
        accumulator.solo_completed_nonce_range_count += 1
        accumulator.solo_total_hashes_checked += hashes
        accumulator.solo_total_elapsed_ns += elapsed
    elif event == "solo_candidate_found":
        if record["command"] == "solo-hash" and "submission_enabled" not in record:
            raise _RecordValidationError("hash-only candidate must declare submission disabled")
        if "submission_enabled" in record:
            submission_enabled = _actual_bool(record["submission_enabled"], "submission_enabled")
            if record["command"] == "solo-hash" and submission_enabled:
                raise _RecordValidationError("hash-only candidate cannot enable submission")
        accumulator.solo_candidate_count += 1
    elif event == "solo_candidate_suppressed":
        _nonblank_string(_required(record, "reason"), "reason")
        _positive_int(_required(record, "suppression_count"), "suppression_count")
        accumulator.solo_candidate_suppressed_count += 1
    elif event == "solo_block_proposal_completed":
        if record["command"] == "solo-hash":
            raise _RecordValidationError("hash-only run cannot contain a proposal event")
        accepted = _actual_bool(_required(record, "accepted"), "accepted")
        category = _nonblank_string(_required(record, "status_category"), "status_category")
        if accepted != (category == "accepted"):
            raise _RecordValidationError("proposal category contradicts acceptance")
        accumulator.solo_proposal_outcome_counts[category] += 1
    elif event == "solo_block_submission_completed":
        if record["command"] == "solo-hash":
            raise _RecordValidationError("hash-only run cannot contain a submission event")
        accepted = _actual_bool(_required(record, "accepted"), "accepted")
        category = _nonblank_string(_required(record, "status_category"), "status_category")
        if accepted != (category == "accepted"):
            raise _RecordValidationError("submission category contradicts acceptance")
        accumulator.solo_submission_outcome_counts[category] += 1
        if accepted:
            accumulator.solo_accepted_block_count += 1
        else:
            accumulator.solo_rejected_block_count += 1
    elif event == "compute_backend_selected":
        backend_name = _nonblank_string(
            _required(record, "backend_name"),
            "backend_name",
        )
        _nonblank_string(_required(record, "backend_kind"), "backend_kind")
        _nonblank_string(_required(record, "implementation"), "implementation")
        _actual_bool(
            _required(record, "supports_parallel_search"),
            "supports_parallel_search",
        )
        _actual_bool(
            _required(record, "supports_cooperative_cancellation"),
            "supports_cooperative_cancellation",
        )
        if "device_count" in record or "device_ordinals" in record:
            if "device_count" not in record or "device_ordinals" not in record:
                raise _RecordValidationError(
                    "device_count and device_ordinals must be reported together"
                )
            device_count = _positive_int(record["device_count"], "device_count")
            device_ordinals = record["device_ordinals"]
            if not isinstance(device_ordinals, list):
                raise _RecordValidationError("device_ordinals must be a list")
            parsed_ordinals = tuple(
                _nonnegative_int(item, "device_ordinals") for item in device_ordinals
            )
            if len(parsed_ordinals) != device_count:
                raise _RecordValidationError("device_count is inconsistent")
            if tuple(sorted(set(parsed_ordinals))) != parsed_ordinals:
                raise _RecordValidationError("device_ordinals must be unique and ascending")
        accumulator.compute_backend_counts[backend_name] += 1
    elif event == "compute_profile_resolved":
        requested_profile = _nonblank_string(
            _required(record, "requested_profile"), "requested_profile"
        )
        effective_profile = _nonblank_string(
            _required(record, "effective_profile"), "effective_profile"
        )
        _nonblank_string(_required(record, "effective_backend"), "effective_backend")
        _positive_int(_required(record, "chunk_size"), "chunk_size")
        delay = _nonnegative_number(
            _required(record, "inter_range_delay_seconds"),
            "inter_range_delay_seconds",
        )
        if float(delay) > 60:
            raise _RecordValidationError("inter_range_delay_seconds must not exceed 60")
        _nonblank_string(_required(record, "resolution_reason"), "resolution_reason")
        accumulator.requested_profile_counts[requested_profile] += 1
        accumulator.effective_profile_counts[effective_profile] += 1
    elif event == "search_strategy_selected":
        strategy_name = _nonblank_string(
            _required(record, "strategy_name"),
            "strategy_name",
        )
        _nonblank_string(_required(record, "implementation"), "implementation")
        _actual_bool(_required(record, "deterministic"), "deterministic")
        _actual_bool(
            _required(record, "contiguous_parent_ranges"),
            "contiguous_parent_ranges",
        )
        _actual_bool(_required(record, "exhaustive"), "exhaustive")
        _actual_bool(_required(record, "experimental"), "experimental")
        accumulator.search_strategy_counts[strategy_name] += 1
    elif event == "difficulty_received":
        _positive_number(_required(record, "difficulty"), "difficulty")
        accumulator.difficulty_event_count += 1
    elif event == "mining_job_received":
        _nonblank_string(_required(record, "job_id"), "job_id")
        accumulator.mining_job_event_count += 1
    elif event == "mining_work_advanced":
        reason = _nonblank_string(_required(record, "reason"), "reason")
        _nonnegative_int(_required(record, "work_variant_index"), "work_variant_index")
        _nonnegative_int(
            _required(record, "extra_nonce_2_advance_count"),
            "extra_nonce_2_advance_count",
        )
        _nonnegative_int(
            _required(record, "network_time_roll_count"),
            "network_time_roll_count",
        )
        accumulator.work_variant_count += 1
        if reason in {"extra_nonce_2", "network_time"}:
            accumulator.extra_nonce_2_advance_count += 1
    elif event == "extra_nonce_2_cycle_completed":
        _positive_int(_required(record, "cycle_count"), "cycle_count")
        accumulator.extra_nonce_2_cycle_count += 1
    elif event == "network_time_rolled":
        _positive_int(_required(record, "roll_count"), "roll_count")
        accumulator.network_time_roll_count += 1
    elif event == "duplicate_work_ignored":
        _positive_int(_required(record, "duplicate_count"), "duplicate_count")
        _nonblank_string(_required(record, "reason"), "reason")
        accumulator.duplicate_work_ignored_count += 1
    elif event == "stratum_connection_lost":
        _nonblank_string(_required(record, "recovery_stage"), "recovery_stage")
        _nonblank_string(_required(record, "error_category"), "error_category")
        accumulator.connection_loss_count += 1
    elif event == "stratum_reconnect_scheduled":
        _positive_int(_required(record, "attempt"), "attempt")
        _nonnegative_int(_required(record, "maximum_attempts"), "maximum_attempts")
        _nonnegative_number(_required(record, "delay_seconds"), "delay_seconds")
        _nonblank_string(_required(record, "recovery_stage"), "recovery_stage")
    elif event == "stratum_reconnect_attempted":
        _positive_int(_required(record, "attempt"), "attempt")
        _positive_int(_required(record, "maximum_attempts"), "maximum_attempts")
        _nonblank_string(_required(record, "recovery_stage"), "recovery_stage")
        accumulator.reconnect_attempt_count += 1
    elif event == "stratum_reconnect_succeeded":
        _positive_int(_required(record, "attempt"), "attempt")
        _positive_int(
            _required(record, "successful_reconnect_count"),
            "successful_reconnect_count",
        )
        _positive_int(_required(record, "session_index"), "session_index")
        accumulator.reconnect_success_count += 1
    elif event == "stratum_reconnect_failed":
        _positive_int(_required(record, "attempt"), "attempt")
        _positive_int(_required(record, "maximum_attempts"), "maximum_attempts")
        _nonblank_string(_required(record, "recovery_stage"), "recovery_stage")
        _nonblank_string(_required(record, "error_category"), "error_category")
        accumulator.reconnect_failure_count += 1
    elif event == "stratum_reconnect_exhausted":
        _nonnegative_int(_required(record, "attempts"), "attempts")
        _nonnegative_int(_required(record, "maximum_attempts"), "maximum_attempts")
        _nonblank_string(_required(record, "recovery_stage"), "recovery_stage")
        _nonblank_string(_required(record, "error_category"), "error_category")
        accumulator.reconnect_exhausted_count += 1
    elif event in {
        "stratum_liveness_warning",
        "stratum_session_stale",
    }:
        reason = _nonblank_string(_required(record, "reason"), "reason")
        _positive_number(_required(record, "threshold_seconds"), "threshold_seconds")
        _nonnegative_number(_required(record, "elapsed_seconds"), "elapsed_seconds")
        if event == "stratum_liveness_warning":
            accumulator.liveness_warning_count += 1
        else:
            accumulator.stale_session_count += 1
            accumulator.stale_reason_counts[reason] += 1
    elif event in {
        "stratum_stale_reconnect_started",
        "stratum_stale_reconnect_succeeded",
        "stratum_stale_reconnect_failed",
    }:
        _nonblank_string(_required(record, "reason"), "reason")
        if event == "stratum_stale_reconnect_started":
            accumulator.stale_reconnect_started_count += 1
        elif event == "stratum_stale_reconnect_succeeded":
            accumulator.stale_reconnect_success_count += 1
        else:
            accumulator.stale_reconnect_failure_count += 1
    elif event == "nonce_range_completed":
        hashes_checked = _nonnegative_int(_required(record, "hashes_checked"), "hashes_checked")
        elapsed_ns = _nonnegative_int(_required(record, "elapsed_ns"), "elapsed_ns")
        _actual_bool(_required(record, "match_found"), "match_found")
        _optional_nonnegative_number(_required(record, "hashes_per_second"), "hashes_per_second")
        accumulator.completed_nonce_range_count += 1
        accumulator.total_hashes_checked += hashes_checked
        accumulator.total_mining_elapsed_ns += elapsed_ns
    elif event == "share_candidate_found":
        accumulator.share_candidate_count += 1
    elif event == "share_submission_completed":
        accepted = _actual_bool(_required(record, "accepted"), "accepted")
        accumulator.share_submission_count += 1
        if accepted:
            accumulator.accepted_share_count += 1
        else:
            accumulator.rejected_share_count += 1


def _required(record: dict[str, object], name: str) -> object:
    if name not in record:
        raise _RecordValidationError(f"missing {name}")
    return record[name]


def _actual_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _RecordValidationError(f"{name} must be an integer")
    return value


def _nonnegative_int(value: object, name: str) -> int:
    parsed = _actual_int(value, name)
    if parsed < 0:
        raise _RecordValidationError(f"{name} must be nonnegative")
    return parsed


def _positive_int(value: object, name: str) -> int:
    parsed = _actual_int(value, name)
    if parsed <= 0:
        raise _RecordValidationError(f"{name} must be positive")
    return parsed


def _actual_bool(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise _RecordValidationError(f"{name} must be a Boolean")
    return value


def _nonblank_string(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise _RecordValidationError(f"{name} must be a string")
    if not value.strip():
        raise _RecordValidationError(f"{name} must not be blank")
    return value


def _safe_identity(value: object, name: str) -> str:
    parsed = _nonblank_string(value, name)
    if re.fullmatch(r"[0-9a-f]{16}", parsed) is None:
        raise _RecordValidationError(f"{name} must be a sanitized 16-character identity")
    return parsed


def _positive_number(value: object, name: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _RecordValidationError(f"{name} must be an integer or float")
    if (isinstance(value, float) and not math.isfinite(value)) or value <= 0:
        raise _RecordValidationError(f"{name} must be finite and positive")
    return value


def _optional_nonnegative_number(value: object, name: str) -> int | float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _RecordValidationError(f"{name} must be a number or null")
    if (isinstance(value, float) and not math.isfinite(value)) or value < 0:
        raise _RecordValidationError(f"{name} must be finite and nonnegative")
    return value


def _nonnegative_number(value: object, name: str) -> int | float:
    parsed = _optional_nonnegative_number(value, name)
    if parsed is None:
        raise _RecordValidationError(f"{name} must be a number")
    return parsed


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str) or _UTC_TIMESTAMP.fullmatch(value) is None:
        raise _RecordValidationError("timestamp must be a UTC RFC3339 Z string")
    try:
        parsed = datetime.fromisoformat(f"{value[:-1]}+00:00")
    except ValueError as exc:
        raise _RecordValidationError("timestamp is invalid") from exc
    return parsed.astimezone(UTC)


def _format_timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _build_summary(accumulator: _Accumulator) -> LogSummary:
    command_counts = Counter(state.command for state in accumulator.runs.values())
    completed = sum(
        state.terminal_event == "command_completed" for state in accumulator.runs.values()
    )
    failed = sum(state.terminal_event == "command_failed" for state in accumulator.runs.values())
    run_count = len(accumulator.runs)
    weighted_rate = (
        accumulator.total_hashes_checked * 1_000_000_000 / accumulator.total_mining_elapsed_ns
        if accumulator.completed_nonce_range_count > 0 and accumulator.total_mining_elapsed_ns > 0
        else None
    )
    solo_weighted_rate = (
        accumulator.solo_total_hashes_checked * 1_000_000_000 / accumulator.solo_total_elapsed_ns
        if accumulator.solo_completed_nonce_range_count > 0
        and accumulator.solo_total_elapsed_ns > 0
        else None
    )
    return LogSummary(
        record_count=accumulator.record_count,
        run_count=run_count,
        completed_run_count=completed,
        failed_run_count=failed,
        incomplete_run_count=run_count - completed - failed,
        first_timestamp=_format_timestamp(accumulator.earliest),
        last_timestamp=_format_timestamp(accumulator.latest),
        command_counts=tuple(sorted(command_counts.items())),
        compute_backend_counts=tuple(sorted(accumulator.compute_backend_counts.items())),
        requested_profile_counts=tuple(sorted(accumulator.requested_profile_counts.items())),
        effective_profile_counts=tuple(sorted(accumulator.effective_profile_counts.items())),
        search_strategy_counts=tuple(sorted(accumulator.search_strategy_counts.items())),
        completion_outcome_counts=tuple(sorted(accumulator.outcome_counts.items())),
        difficulty_event_count=accumulator.difficulty_event_count,
        mining_job_event_count=accumulator.mining_job_event_count,
        work_variant_count=accumulator.work_variant_count,
        extra_nonce_2_advance_count=accumulator.extra_nonce_2_advance_count,
        extra_nonce_2_cycle_count=accumulator.extra_nonce_2_cycle_count,
        network_time_roll_count=accumulator.network_time_roll_count,
        duplicate_work_ignored_count=accumulator.duplicate_work_ignored_count,
        connection_loss_count=accumulator.connection_loss_count,
        reconnect_attempt_count=accumulator.reconnect_attempt_count,
        reconnect_success_count=accumulator.reconnect_success_count,
        reconnect_failure_count=accumulator.reconnect_failure_count,
        reconnect_exhausted_count=accumulator.reconnect_exhausted_count,
        liveness_warning_count=accumulator.liveness_warning_count,
        stale_session_count=accumulator.stale_session_count,
        stale_reconnect_started_count=accumulator.stale_reconnect_started_count,
        stale_reconnect_success_count=accumulator.stale_reconnect_success_count,
        stale_reconnect_failure_count=accumulator.stale_reconnect_failure_count,
        stale_reason_counts=tuple(sorted(accumulator.stale_reason_counts.items())),
        configured_server_silence_limits=tuple(
            sorted(accumulator.configured_server_silence_limits.items())
        ),
        configured_job_age_limits=tuple(sorted(accumulator.configured_job_age_limits.items())),
        completed_nonce_range_count=accumulator.completed_nonce_range_count,
        total_hashes_checked=accumulator.total_hashes_checked,
        total_mining_elapsed_ns=accumulator.total_mining_elapsed_ns,
        weighted_hashes_per_second=weighted_rate,
        share_candidate_count=accumulator.share_candidate_count,
        share_submission_count=accumulator.share_submission_count,
        accepted_share_count=accumulator.accepted_share_count,
        rejected_share_count=accumulator.rejected_share_count,
        command_failure_count=accumulator.command_failure_count,
        failure_stage_category_counts=tuple(
            (stage, category, count)
            for (stage, category), count in sorted(accumulator.failure_counts.items())
        ),
        solo_chain_counts=tuple(sorted(accumulator.solo_chain_counts.items())),
        solo_template_count=accumulator.solo_template_count,
        solo_template_replacement_count=accumulator.solo_template_replacement_count,
        solo_work_variant_count=accumulator.solo_work_variant_count,
        solo_coinbase_extra_nonce_advance_count=(
            accumulator.solo_coinbase_extra_nonce_advance_count
        ),
        solo_timestamp_roll_count=accumulator.solo_timestamp_roll_count,
        solo_completed_nonce_range_count=accumulator.solo_completed_nonce_range_count,
        solo_total_hashes_checked=accumulator.solo_total_hashes_checked,
        solo_total_elapsed_ns=accumulator.solo_total_elapsed_ns,
        solo_weighted_hashes_per_second=solo_weighted_rate,
        solo_candidate_count=accumulator.solo_candidate_count,
        solo_candidate_suppressed_count=accumulator.solo_candidate_suppressed_count,
        solo_proposal_outcome_counts=tuple(
            sorted(accumulator.solo_proposal_outcome_counts.items())
        ),
        solo_submission_outcome_counts=tuple(
            sorted(accumulator.solo_submission_outcome_counts.items())
        ),
        solo_accepted_block_count=accumulator.solo_accepted_block_count,
        solo_rejected_block_count=accumulator.solo_rejected_block_count,
        solo_rpc_failure_count=accumulator.solo_rpc_failure_count,
    )
