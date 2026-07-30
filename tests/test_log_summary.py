"""Tests for strict read-only Hashphere JSONL log aggregation."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

import hashphere.__main__ as cli_module
from hashphere.observability import LogSummary, LogSummaryError, summarize_jsonl

FIRST_TIME = "2026-07-27T12:15:31.632625Z"
SECOND_TIME = "2026-07-27T12:16:17.817473Z"


def event_record(
    run_id: str,
    sequence: int,
    event: str,
    *,
    command: str = "stratum-handshake",
    timestamp: str = FIRST_TIME,
    level: str = "INFO",
    **fields: object,
) -> dict[str, object]:
    """Build one synthetic schema-version-1 event record."""

    return {
        "schema_version": 1,
        "timestamp": timestamp,
        "run_id": run_id,
        "sequence": sequence,
        "level": level,
        "event": event,
        "command": command,
        **fields,
    }


def write_records(path: Path, records: list[dict[str, object]]) -> None:
    """Write compact local JSONL fixtures without using the production sink."""

    path.write_text(
        "".join(
            f"{json.dumps(record, allow_nan=False, separators=(',', ':'))}\n" for record in records
        ),
        encoding="utf-8",
    )


def completed_run(
    run_id: str,
    *,
    command: str = "stratum-handshake",
    outcome: str = "handshake_succeeded",
) -> list[dict[str, object]]:
    """Build one minimal valid completed run."""

    return [
        event_record(run_id, 1, "command_started", command=command),
        event_record(
            run_id,
            2,
            "command_completed",
            command=command,
            timestamp=SECOND_TIME,
            outcome=outcome,
        ),
    ]


def test_empty_file_returns_immutable_zero_summary(tmp_path: Path) -> None:
    path = tmp_path / "empty.jsonl"
    path.write_bytes(b"")

    summary = summarize_jsonl(path)

    assert summary == LogSummary(
        record_count=0,
        run_count=0,
        completed_run_count=0,
        failed_run_count=0,
        incomplete_run_count=0,
        first_timestamp=None,
        last_timestamp=None,
        command_counts=(),
        compute_backend_counts=(),
        requested_profile_counts=(),
        effective_profile_counts=(),
        search_strategy_counts=(),
        completion_outcome_counts=(),
        difficulty_event_count=0,
        mining_job_event_count=0,
        work_variant_count=0,
        extra_nonce_2_advance_count=0,
        extra_nonce_2_cycle_count=0,
        network_time_roll_count=0,
        duplicate_work_ignored_count=0,
        connection_loss_count=0,
        reconnect_attempt_count=0,
        reconnect_success_count=0,
        reconnect_failure_count=0,
        reconnect_exhausted_count=0,
        liveness_warning_count=0,
        stale_session_count=0,
        stale_reconnect_started_count=0,
        stale_reconnect_success_count=0,
        stale_reconnect_failure_count=0,
        stale_reason_counts=(),
        configured_server_silence_limits=(),
        configured_job_age_limits=(),
        completed_nonce_range_count=0,
        total_hashes_checked=0,
        total_mining_elapsed_ns=0,
        weighted_hashes_per_second=None,
        share_candidate_count=0,
        share_submission_count=0,
        accepted_share_count=0,
        rejected_share_count=0,
        command_failure_count=0,
        failure_stage_category_counts=(),
        solo_chain_counts=(),
        solo_template_count=0,
        solo_template_replacement_count=0,
        solo_work_variant_count=0,
        solo_coinbase_extra_nonce_advance_count=0,
        solo_timestamp_roll_count=0,
        solo_completed_nonce_range_count=0,
        solo_total_hashes_checked=0,
        solo_total_elapsed_ns=0,
        solo_weighted_hashes_per_second=None,
        solo_candidate_count=0,
        solo_candidate_suppressed_count=0,
        solo_proposal_outcome_counts=(),
        solo_submission_outcome_counts=(),
        solo_accepted_block_count=0,
        solo_rejected_block_count=0,
        solo_rpc_failure_count=0,
    )
    with pytest.raises(FrozenInstanceError):
        summary.record_count = 1  # type: ignore[misc]
    assert not hasattr(summary, "__dict__")


def test_two_runs_restart_sequences_and_count_commands_and_outcomes(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    write_records(path, completed_run("run-a"))
    with path.open("a", encoding="utf-8") as stream:
        for record in completed_run("run-b"):
            stream.write(f"{json.dumps(record, separators=(',', ':'))}\n")

    summary = summarize_jsonl(path)

    assert summary.record_count == 4
    assert summary.run_count == 2
    assert summary.completed_run_count == 2
    assert summary.failed_run_count == 0
    assert summary.incomplete_run_count == 0
    assert summary.command_counts == (("stratum-handshake", 2),)
    assert summary.completion_outcome_counts == (("handshake_succeeded", 2),)


def test_profile_events_are_summarized_and_future_names_remain_readable(tmp_path: Path) -> None:
    path = tmp_path / "profiles.jsonl"
    write_records(
        path,
        [
            event_record("run", 1, "command_started", command="stratum-mine"),
            event_record(
                "run",
                2,
                "compute_profile_resolved",
                command="stratum-mine",
                requested_profile="future-gentle",
                effective_profile="future-gentle",
                effective_backend="python",
                chunk_size=100,
                inter_range_delay_seconds=0.5,
                resolution_reason="FuturePolicy",
            ),
            event_record(
                "run",
                3,
                "command_completed",
                command="stratum-mine",
                outcome="stopped_by_user",
            ),
        ],
    )

    summary = summarize_jsonl(path)

    assert summary.requested_profile_counts == (("future-gentle", 1),)
    assert summary.effective_profile_counts == (("future-gentle", 1),)


def test_final_newline_is_optional_and_relative_paths_are_supported(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    path = Path("events.jsonl")
    path.write_text(json.dumps(completed_run("run")[0]), encoding="utf-8")

    summary = summarize_jsonl("events.jsonl")

    assert summary.record_count == 1
    assert summary.incomplete_run_count == 1


def test_interleaved_runs_are_validated_independently(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    write_records(
        path,
        [
            event_record("run-b", 1, "command_started", command="stratum-observe"),
            event_record("run-a", 1, "command_started"),
            event_record("run-a", 2, "future_event", timestamp=SECOND_TIME, future_field=[1, 2]),
            event_record(
                "run-b",
                2,
                "command_completed",
                command="stratum-observe",
                outcome="observation_succeeded",
            ),
            event_record("run-a", 3, "command_completed", outcome="handshake_succeeded"),
        ],
    )

    summary = summarize_jsonl(path)

    assert summary.run_count == 2
    assert summary.completed_run_count == 2
    assert summary.command_counts == (
        ("stratum-handshake", 1),
        ("stratum-observe", 1),
    )
    assert summary.record_count == 5


def test_incomplete_and_failed_runs_are_counted(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    write_records(
        path,
        [
            event_record("incomplete", 1, "command_started"),
            event_record("failed", 1, "command_started", command="stratum-observe"),
            event_record(
                "failed",
                2,
                "command_failed",
                command="stratum-observe",
                level="ERROR",
                stage="notification",
                error_category="ProtocolError",
            ),
        ],
    )

    summary = summarize_jsonl(path)

    assert summary.completed_run_count == 0
    assert summary.failed_run_count == 1
    assert summary.incomplete_run_count == 1
    assert summary.command_failure_count == 1
    assert summary.failure_stage_category_counts == (("notification", "ProtocolError", 1),)


def test_first_and_last_timestamps_use_chronological_values(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    write_records(
        path,
        [
            event_record("run", 1, "command_started", timestamp=SECOND_TIME),
            event_record(
                "run",
                2,
                "command_completed",
                timestamp="2026-07-27T12:15:31Z",
                outcome="done",
            ),
        ],
    )

    summary = summarize_jsonl(path)

    assert summary.first_timestamp == "2026-07-27T12:15:31.000000Z"
    assert summary.last_timestamp == SECOND_TIME


def test_mining_events_are_aggregated_with_weighted_rate(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    records = [
        event_record("mine", 1, "command_started", command="stratum-mine-once"),
        event_record("mine", 2, "difficulty_received", command="stratum-mine-once", difficulty=2.5),
        event_record(
            "mine",
            3,
            "mining_job_received",
            command="stratum-mine-once",
            job_id="private-job-a",
        ),
        event_record(
            "mine",
            4,
            "nonce_range_completed",
            command="stratum-mine-once",
            hashes_checked=100,
            elapsed_ns=100,
            match_found=False,
            hashes_per_second=999_999_999.0,
        ),
        event_record(
            "mine",
            5,
            "nonce_range_completed",
            command="stratum-mine-once",
            hashes_checked=300,
            elapsed_ns=900,
            match_found=True,
            hashes_per_second=1.0,
        ),
        event_record("mine", 6, "share_candidate_found", command="stratum-mine-once"),
        event_record(
            "mine",
            7,
            "share_submission_completed",
            command="stratum-mine-once",
            accepted=True,
        ),
        event_record(
            "mine",
            8,
            "share_submission_completed",
            command="stratum-mine-once",
            accepted=False,
        ),
        event_record(
            "mine",
            9,
            "command_completed",
            command="stratum-mine-once",
            outcome="share_rejected",
        ),
    ]
    write_records(path, records)

    summary = summarize_jsonl(path)

    assert summary.difficulty_event_count == 1
    assert summary.mining_job_event_count == 1
    assert summary.completed_nonce_range_count == 2
    assert summary.total_hashes_checked == 400
    assert summary.total_mining_elapsed_ns == 1000
    assert summary.weighted_hashes_per_second == 400_000_000.0
    assert summary.weighted_hashes_per_second != (999_999_999.0 + 1.0) / 2
    assert summary.share_candidate_count == 1
    assert summary.share_submission_count == 2
    assert summary.accepted_share_count == 1
    assert summary.rejected_share_count == 1


def test_backend_selections_are_aggregated_without_affecting_weighted_rate(
    tmp_path: Path,
) -> None:
    path = tmp_path / "backends.jsonl"
    write_records(
        path,
        [
            event_record("python-a", 1, "command_started", command="stratum-mine"),
            event_record(
                "python-a",
                2,
                "compute_backend_selected",
                command="stratum-mine",
                backend_name="python",
                backend_kind="cpu",
                implementation="python",
                supports_parallel_search=False,
                supports_cooperative_cancellation=False,
            ),
            event_record(
                "python-a",
                3,
                "nonce_range_completed",
                command="stratum-mine",
                hashes_checked=5,
                elapsed_ns=10,
                match_found=False,
                hashes_per_second=500_000_000.0,
            ),
            event_record("future", 1, "command_started", command="stratum-mine"),
            event_record(
                "future",
                2,
                "compute_backend_selected",
                command="stratum-mine",
                backend_name="future_native",
                backend_kind="cpu",
                implementation="native",
                supports_parallel_search=True,
                supports_cooperative_cancellation=True,
            ),
        ],
    )

    summary = summarize_jsonl(path)

    assert summary.compute_backend_counts == (("future_native", 1), ("python", 1))
    assert summary.weighted_hashes_per_second == 500_000_000.0


def test_multi_cuda_selection_metadata_is_validated_and_aggregated(
    tmp_path: Path,
) -> None:
    path = tmp_path / "cuda-multi.jsonl"
    write_records(
        path,
        [
            event_record("multi", 1, "command_started", command="stratum-mine"),
            event_record(
                "multi",
                2,
                "compute_backend_selected",
                command="stratum-mine",
                backend_name="cuda-multi",
                backend_kind="gpu",
                implementation="cuda-multi",
                supports_parallel_search=True,
                supports_cooperative_cancellation=False,
                device_count=2,
                device_ordinals=[0, 3],
            ),
        ],
    )

    assert summarize_jsonl(path).compute_backend_counts == (("cuda-multi", 1),)


@pytest.mark.parametrize(
    ("count", "ordinals"),
    [(2, [0]), (1, [1, 0]), (2, [0, 0]), (1, "0"), (0, [])],
)
def test_multi_cuda_selection_rejects_inconsistent_device_metadata(
    tmp_path: Path,
    count: object,
    ordinals: object,
) -> None:
    path = tmp_path / "invalid-cuda-multi.jsonl"
    write_records(
        path,
        [
            event_record(
                "multi",
                1,
                "compute_backend_selected",
                backend_name="cuda-multi",
                backend_kind="gpu",
                implementation="cuda-multi",
                supports_parallel_search=True,
                supports_cooperative_cancellation=False,
                device_count=count,
                device_ordinals=ordinals,
            )
        ],
    )

    with pytest.raises(LogSummaryError, match="line 1"):
        summarize_jsonl(path)


def test_strategy_selections_are_aggregated_without_cursor_state(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "strategies.jsonl"
    write_records(
        path,
        [
            event_record("one", 1, "command_started", command="stratum-mine"),
            event_record(
                "one",
                2,
                "search_strategy_selected",
                command="stratum-mine",
                strategy_name="sequential",
                implementation="sequential",
                deterministic=True,
                contiguous_parent_ranges=True,
                exhaustive=True,
                experimental=False,
            ),
            event_record("two", 1, "command_started", command="stratum-mine"),
            event_record(
                "two",
                2,
                "search_strategy_selected",
                command="stratum-mine",
                strategy_name="future",
                implementation="future",
                deterministic=False,
                contiguous_parent_ranges=False,
                exhaustive=False,
                experimental=True,
            ),
            event_record("three", 1, "command_started", command="stratum-mine"),
            event_record(
                "three",
                2,
                "search_strategy_selected",
                command="stratum-mine",
                strategy_name="orbiting-bit",
                implementation="bit-reversal",
                deterministic=True,
                contiguous_parent_ranges=False,
                exhaustive=True,
                experimental=True,
            ),
        ],
    )

    summary = summarize_jsonl(path)

    assert summary.search_strategy_counts == (
        ("future", 1),
        ("orbiting-bit", 1),
        ("sequential", 1),
    )
    assert summary.weighted_hashes_per_second is None
    assert cli_module.main(["logs-summary", "--log-file", str(path)]) == 0
    assert (
        "Search strategies:\n  future: 1\n  orbiting-bit: 1\n  sequential: 1"
        in capsys.readouterr().out
    )


def test_progression_events_are_aggregated_without_affecting_weighted_rate(
    tmp_path: Path,
) -> None:
    path = tmp_path / "progression.jsonl"
    records = [
        event_record("mine", 1, "command_started", command="stratum-mine"),
        event_record(
            "mine",
            2,
            "mining_work_advanced",
            command="stratum-mine",
            reason="initial_job",
            work_variant_index=0,
            extra_nonce_2_advance_count=0,
            network_time_roll_count=0,
        ),
        event_record(
            "mine",
            3,
            "mining_work_advanced",
            command="stratum-mine",
            reason="extra_nonce_2",
            work_variant_index=1,
            extra_nonce_2_advance_count=1,
            network_time_roll_count=0,
        ),
        event_record(
            "mine",
            4,
            "extra_nonce_2_cycle_completed",
            command="stratum-mine",
            cycle_count=1,
        ),
        event_record(
            "mine",
            5,
            "network_time_rolled",
            command="stratum-mine",
            roll_count=1,
        ),
        event_record(
            "mine",
            6,
            "mining_work_advanced",
            command="stratum-mine",
            reason="network_time",
            work_variant_index=2,
            extra_nonce_2_advance_count=2,
            network_time_roll_count=1,
        ),
        event_record(
            "mine",
            7,
            "duplicate_work_ignored",
            command="stratum-mine",
            duplicate_count=1,
            reason="pool_context",
        ),
    ]
    write_records(path, records)

    summary = summarize_jsonl(path)

    assert summary.work_variant_count == 3
    assert summary.extra_nonce_2_advance_count == 2
    assert summary.extra_nonce_2_cycle_count == 1
    assert summary.network_time_roll_count == 1
    assert summary.duplicate_work_ignored_count == 1
    assert summary.completed_nonce_range_count == 0
    assert summary.weighted_hashes_per_second is None


def test_recovery_events_are_aggregated_without_affecting_weighted_rate(
    tmp_path: Path,
) -> None:
    path = tmp_path / "recovery.jsonl"
    records = [
        event_record("mine", 1, "command_started", command="stratum-mine"),
        event_record(
            "mine",
            2,
            "stratum_connection_lost",
            command="stratum-mine",
            level="WARNING",
            recovery_stage="notification_poll",
            error_category="StratumConnectionError",
        ),
        event_record(
            "mine",
            3,
            "stratum_reconnect_scheduled",
            command="stratum-mine",
            attempt=1,
            maximum_attempts=5,
            delay_seconds=1.0,
            recovery_stage="notification_poll",
        ),
        event_record(
            "mine",
            4,
            "stratum_reconnect_attempted",
            command="stratum-mine",
            attempt=1,
            maximum_attempts=5,
            recovery_stage="notification_poll",
        ),
        event_record(
            "mine",
            5,
            "stratum_reconnect_failed",
            command="stratum-mine",
            level="WARNING",
            attempt=1,
            maximum_attempts=5,
            recovery_stage="notification_poll",
            error_category="StratumConnectionError",
        ),
        event_record(
            "mine",
            6,
            "stratum_reconnect_scheduled",
            command="stratum-mine",
            attempt=2,
            maximum_attempts=5,
            delay_seconds=2,
            recovery_stage="notification_poll",
        ),
        event_record(
            "mine",
            7,
            "stratum_reconnect_attempted",
            command="stratum-mine",
            attempt=2,
            maximum_attempts=5,
            recovery_stage="notification_poll",
        ),
        event_record(
            "mine",
            8,
            "stratum_reconnect_succeeded",
            command="stratum-mine",
            attempt=2,
            successful_reconnect_count=1,
            session_index=2,
        ),
        event_record(
            "mine",
            9,
            "stratum_connection_lost",
            command="stratum-mine",
            level="WARNING",
            recovery_stage="replacement_wait",
            error_category="StratumConnectionError",
        ),
        event_record(
            "mine",
            10,
            "stratum_reconnect_exhausted",
            command="stratum-mine",
            level="ERROR",
            attempts=0,
            maximum_attempts=0,
            recovery_stage="replacement_wait",
            error_category="StratumConnectionError",
        ),
    ]
    write_records(path, records)

    summary = summarize_jsonl(path)

    assert summary.connection_loss_count == 2
    assert summary.reconnect_attempt_count == 2
    assert summary.reconnect_success_count == 1
    assert summary.reconnect_failure_count == 1
    assert summary.reconnect_exhausted_count == 1
    assert summary.completed_nonce_range_count == 0
    assert summary.weighted_hashes_per_second is None


@pytest.mark.parametrize("with_range", [False, True])
def test_weighted_rate_is_unavailable_without_positive_elapsed_time(
    tmp_path: Path,
    with_range: bool,
) -> None:
    path = tmp_path / "events.jsonl"
    records = [event_record("run", 1, "command_started")]
    if with_range:
        records.append(
            event_record(
                "run",
                2,
                "nonce_range_completed",
                hashes_checked=10,
                elapsed_ns=0,
                match_found=False,
                hashes_per_second=None,
            )
        )
    write_records(path, records)

    assert summarize_jsonl(path).weighted_hashes_per_second is None


@pytest.mark.parametrize(
    "records",
    [
        [event_record("run", 2, "command_started")],
        [event_record("run", 1, "future_event")],
        [
            event_record("run", 1, "command_started"),
            event_record("run", 2, "command_started"),
        ],
        [
            event_record("run", 1, "command_started"),
            event_record("run", 3, "future_event"),
        ],
        [
            event_record("run", 1, "command_started"),
            event_record("run", 2, "future_event", command="stratum-observe"),
        ],
        [
            event_record("run", 1, "command_started"),
            event_record("run", 2, "command_completed", outcome="done"),
            event_record("run", 3, "future_event"),
        ],
        [
            event_record("run", 1, "command_started"),
            event_record("run", 2, "command_completed", outcome="done"),
            event_record("run", 3, "command_failed", stage="x", error_category="Failure"),
        ],
    ],
)
def test_run_integrity_violations_are_rejected(
    tmp_path: Path,
    records: list[dict[str, object]],
) -> None:
    path = tmp_path / "invalid.jsonl"
    write_records(path, records)

    with pytest.raises(LogSummaryError, match="line"):
        summarize_jsonl(path)


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("schema_version", True),
        ("schema_version", 2),
        ("timestamp", "2026-07-27 12:15:31Z"),
        ("timestamp", "not-a-time"),
        ("run_id", " "),
        ("sequence", 0),
        ("sequence", True),
        ("level", "DEBUG"),
        ("event", "Not-Snake"),
        ("command", ""),
    ],
)
def test_invalid_envelope_values_are_rejected(
    tmp_path: Path,
    field_name: str,
    invalid_value: object,
) -> None:
    path = tmp_path / "invalid.jsonl"
    record = event_record("run", 1, "command_started")
    record[field_name] = invalid_value
    write_records(path, [record])

    with pytest.raises(LogSummaryError, match="line 1"):
        summarize_jsonl(path)


def test_missing_envelope_field_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "invalid.jsonl"
    record = event_record("run", 1, "command_started")
    del record["level"]
    write_records(path, [record])

    with pytest.raises(LogSummaryError, match="missing level"):
        summarize_jsonl(path)


@pytest.mark.parametrize(
    "content",
    [
        "\n",
        "   \n",
        "not-json\n",
        "[]\n",
        "null\n",
        '{"schema_version":1,"schema_version":1}\n',
        '{"value":NaN}\n',
        '{"value":Infinity}\n',
        '{"value":-Infinity}\n',
        '{"value":1e9999}\n',
    ],
)
def test_malformed_physical_records_are_never_skipped(
    tmp_path: Path,
    content: str,
) -> None:
    path = tmp_path / "invalid.jsonl"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(LogSummaryError, match="line 1"):
        summarize_jsonl(path)


def test_invalid_utf8_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "invalid.jsonl"
    path.write_bytes(b"\xff\n")

    with pytest.raises(LogSummaryError, match="line 1"):
        summarize_jsonl(path)


@pytest.mark.parametrize(
    ("event", "fields"),
    [
        ("command_completed", {"outcome": " "}),
        ("command_failed", {"stage": "x", "error_category": 1}),
        (
            "compute_backend_selected",
            {
                "backend_name": "python",
                "backend_kind": "cpu",
                "implementation": "python",
                "supports_parallel_search": 0,
                "supports_cooperative_cancellation": False,
            },
        ),
        ("difficulty_received", {"difficulty": True}),
        ("difficulty_received", {"difficulty": 0}),
        ("mining_job_received", {"job_id": ""}),
        (
            "nonce_range_completed",
            {
                "hashes_checked": True,
                "elapsed_ns": 1,
                "match_found": False,
                "hashes_per_second": 1.0,
            },
        ),
        (
            "nonce_range_completed",
            {
                "hashes_checked": 1,
                "elapsed_ns": -1,
                "match_found": False,
                "hashes_per_second": 1.0,
            },
        ),
        (
            "nonce_range_completed",
            {
                "hashes_checked": 1,
                "elapsed_ns": 1,
                "match_found": "false",
                "hashes_per_second": 1.0,
            },
        ),
        (
            "nonce_range_completed",
            {
                "hashes_checked": 1,
                "elapsed_ns": 1,
                "match_found": False,
                "hashes_per_second": -1.0,
            },
        ),
        ("share_submission_completed", {"accepted": 1}),
        ("share_submission_completed", {"accepted": "true"}),
        (
            "mining_work_advanced",
            {
                "reason": "extra_nonce_2",
                "work_variant_index": True,
                "extra_nonce_2_advance_count": 1,
                "network_time_roll_count": 0,
            },
        ),
        ("extra_nonce_2_cycle_completed", {"cycle_count": 0}),
        ("network_time_rolled", {"roll_count": -1}),
        (
            "duplicate_work_ignored",
            {"duplicate_count": 1, "reason": ""},
        ),
        (
            "stratum_connection_lost",
            {"recovery_stage": "", "error_category": "StratumConnectionError"},
        ),
        (
            "stratum_reconnect_scheduled",
            {
                "attempt": 1,
                "maximum_attempts": 5,
                "delay_seconds": -1,
                "recovery_stage": "handshake",
            },
        ),
        (
            "stratum_reconnect_attempted",
            {"attempt": True, "maximum_attempts": 5, "recovery_stage": "handshake"},
        ),
        (
            "stratum_reconnect_succeeded",
            {"attempt": 1, "successful_reconnect_count": 0, "session_index": 1},
        ),
        (
            "stratum_reconnect_failed",
            {
                "attempt": 1,
                "maximum_attempts": 5,
                "recovery_stage": "handshake",
                "error_category": "",
            },
        ),
        (
            "stratum_reconnect_exhausted",
            {
                "attempts": -1,
                "maximum_attempts": 5,
                "recovery_stage": "handshake",
                "error_category": "StratumConnectionError",
            },
        ),
    ],
)
def test_malformed_known_event_fields_are_rejected(
    tmp_path: Path,
    event: str,
    fields: dict[str, object],
) -> None:
    path = tmp_path / "invalid.jsonl"
    write_records(
        path,
        [
            event_record("run", 1, "command_started"),
            event_record("run", 2, event, **fields),
        ],
    )

    with pytest.raises(LogSummaryError, match="line 2"):
        summarize_jsonl(path)


def test_missing_file_directory_and_invalid_path_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(LogSummaryError):
        summarize_jsonl(tmp_path / "missing.jsonl")
    with pytest.raises(LogSummaryError):
        summarize_jsonl(tmp_path)
    with pytest.raises(ValueError, match="blank"):
        summarize_jsonl(" ")
    with pytest.raises(TypeError, match="string or Path"):
        summarize_jsonl(1)  # type: ignore[arg-type]


def test_read_failure_is_wrapped_portably(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "events.jsonl"
    path.write_bytes(b"")

    def fail_open(
        self: Path,
        mode: str = "r",
        buffering: int = -1,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> object:
        raise PermissionError

    monkeypatch.setattr(Path, "open", fail_open)
    with pytest.raises(LogSummaryError, match="could not read"):
        summarize_jsonl(path)


@pytest.mark.parametrize(
    "arguments",
    [
        ["logs-summary"],
        ["logs-summary", "--log-file"],
        ["logs-summary", "--log-file", ""],
        ["logs-summary", "--log-file", "   "],
        ["logs-summary", "--log-file", "one", "--log-file", "two"],
        ["logs-summary", "--log-file", "--unknown"],
        ["logs-summary", "--unknown", "path"],
        ["logs-summary", "path"],
    ],
)
def test_cli_rejects_invalid_summary_syntax(
    arguments: list[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli_module.main(arguments) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Argument error:" in captured.err
    assert "logs-summary" in captured.err


def test_cli_empty_summary_requires_no_live_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    path = tmp_path / "events.jsonl"
    path.write_bytes(b"")
    original = path.read_bytes()

    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("live or writer dependency was accessed")

    monkeypatch.delenv("HASHPHERE_ENABLE_LIVE_STRATUM", raising=False)
    monkeypatch.delenv("HASHPHERE_ENABLE_LIVE_MINING", raising=False)
    monkeypatch.setattr(cli_module.Settings, "from_env", classmethod(forbidden))
    monkeypatch.setattr(cli_module, "StratumClient", forbidden)
    monkeypatch.setattr(cli_module, "JsonlEventSink", forbidden)

    assert cli_module.main(["logs-summary", "--log-file", str(path)]) == 0

    captured = capsys.readouterr()
    assert captured.err == ""
    assert "Records: 0" in captured.out
    assert "Runs: 0" in captured.out
    assert "First event: unavailable" in captured.out
    assert "Weighted hashes per second: unavailable" in captured.out
    assert path.read_bytes() == original


def test_cli_output_is_stable_weighted_and_sanitized(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    path = tmp_path / "events.jsonl"
    sensitive_values = (
        "private-job-id",
        "private-user.worker",
        "bc1qprivateaddress",
        "private-exception-message",
        "0123456789abcdef" * 4,
    )
    write_records(
        path,
        [
            event_record("mine", 1, "command_started", command="stratum-mine-once"),
            event_record(
                "mine",
                2,
                "compute_backend_selected",
                command="stratum-mine-once",
                backend_name="python",
                backend_kind="cpu",
                implementation="python",
                supports_parallel_search=False,
                supports_cooperative_cancellation=False,
            ),
            event_record(
                "mine",
                3,
                "mining_job_received",
                command="stratum-mine-once",
                job_id=sensitive_values[0],
                username=sensitive_values[1],
                address=sensitive_values[2],
            ),
            event_record(
                "mine",
                4,
                "nonce_range_completed",
                command="stratum-mine-once",
                hashes_checked=100,
                elapsed_ns=200,
                match_found=True,
                hashes_per_second=1.0,
                nonce=123,
                block_hash=sensitive_values[4],
            ),
            event_record(
                "mine",
                5,
                "share_submission_completed",
                command="stratum-mine-once",
                accepted=False,
            ),
            event_record(
                "mine",
                6,
                "command_completed",
                command="stratum-mine-once",
                outcome="share_rejected",
            ),
            event_record("failed", 1, "command_started", command="stratum-observe"),
            event_record(
                "failed",
                2,
                "command_failed",
                command="stratum-observe",
                level="ERROR",
                stage="notification",
                error_category="ProtocolError",
                message=sensitive_values[3],
            ),
        ],
    )

    assert cli_module.main(["logs-summary", "--log-file", str(path)]) == 0

    captured = capsys.readouterr()
    assert captured.err == ""
    assert "  stratum-handshake: 0" in captured.out
    assert "  stratum-observe: 1" in captured.out
    assert "  stratum-mine-once: 1" in captured.out
    assert "Completion outcomes:\n  share_rejected: 1" in captured.out
    assert "Compute backends:\n  python: 1" in captured.out
    assert "Weighted hashes per second: 500000000.00" in captured.out
    assert "Failures:\n  command_failed events: 1\n  notification/ProtocolError: 1" in (
        captured.out
    )
    for sensitive in sensitive_values:
        assert sensitive not in captured.out
        assert sensitive not in captured.err


def test_cli_input_failures_return_one_without_echoing_raw_record(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    path = tmp_path / "invalid.jsonl"
    secret_raw_line = '{"password":"do-not-echo"}'
    path.write_text(f"{secret_raw_line}\n", encoding="utf-8")

    assert cli_module.main(["logs-summary", "--log-file", str(path)]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "line 1" in captured.err
    assert str(path) in captured.err
    assert secret_raw_line not in captured.err
    assert "do-not-echo" not in captured.err


def test_cli_missing_file_returns_one_and_does_not_create_parent(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    path = tmp_path / "missing-parent" / "events.jsonl"

    assert cli_module.main(["logs-summary", "--log-file", str(path)]) == 1

    assert not path.parent.exists()
    assert capsys.readouterr().out == ""


def test_general_usage_includes_logs_summary(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli_module.main([]) == 2
    assert "logs-summary" in capsys.readouterr().err
