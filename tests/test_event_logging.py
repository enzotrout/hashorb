"""Tests for sanitized append-only structured event sinks."""

from __future__ import annotations

import io
import json
import os
import stat
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

import hashorb.__main__ as cli_module
from hashorb.observability import (
    EventLogError,
    EventSink,
    JsonlEventSink,
    NullEventSink,
)

FIXED_TIME = datetime(2026, 7, 27, 12, 34, 56, 123456, tzinfo=UTC)


def read_records(path: Path) -> list[dict[str, object]]:
    """Parse every independent JSON line in an event file."""

    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def make_sink(path: Path, *, run_id: str = "run-test") -> JsonlEventSink:
    """Create a deterministic event sink."""

    return JsonlEventSink(
        path,
        "stratum-observe",
        clock=lambda: FIXED_TIME,
        run_id_factory=lambda: run_id,
    )


def test_null_sink_creates_no_file_and_validates_events(tmp_path: Path) -> None:
    path = tmp_path / "disabled.jsonl"
    sink = NullEventSink()

    sink.emit("command_started")
    sink.close()
    sink.close()

    assert not path.exists()
    with pytest.raises(EventLogError, match="closed"):
        sink.emit("command_completed")


def test_jsonl_sink_creates_parents_and_writes_one_utf8_line_per_event(
    tmp_path: Path,
) -> None:
    path = tmp_path / "missing" / "parents" / "events.jsonl"
    sink = make_sink(path)

    sink.emit("command_started", fields={"label": "เครื่องขุด"})
    sink.emit("command_completed", fields={"outcome": "observation_succeeded"})
    sink.close()

    raw = path.read_bytes()
    assert raw.count(b"\n") == 2
    assert raw.endswith(b"\n")
    records = read_records(path)
    assert [record["event"] for record in records] == [
        "command_started",
        "command_completed",
    ]
    assert records[0] == {
        "schema_version": 1,
        "timestamp": "2026-07-27T12:34:56.123456Z",
        "run_id": "run-test",
        "sequence": 1,
        "level": "INFO",
        "event": "command_started",
        "command": "stratum-observe",
        "label": "เครื่องขุด",
    }
    assert records[1]["sequence"] == 2


def test_existing_log_is_appended_without_truncation(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text('{"existing":true}\n', encoding="utf-8")
    path.chmod(0o600)

    sink = make_sink(path)
    sink.emit("command_started")
    sink.close()

    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == '{"existing":true}'
    assert json.loads(lines[1])["event"] == "command_started"


@pytest.mark.skipif(os.name != "posix", reason="POSIX ownership and mode boundary")
def test_event_log_is_private_and_rejects_symlink_or_insecure_existing_file(
    tmp_path: Path,
) -> None:
    private_log = tmp_path / "private.jsonl"
    sink = make_sink(private_log)
    sink.close()
    assert stat.S_IMODE(private_log.stat().st_mode) == 0o600

    target = tmp_path / "target.jsonl"
    target.write_text("", encoding="utf-8")
    target.chmod(0o600)
    linked = tmp_path / "linked.jsonl"
    linked.symlink_to(target)
    with pytest.raises(EventLogError, match="initialize"):
        make_sink(linked)

    target.chmod(0o640)
    with pytest.raises(EventLogError, match="initialize"):
        make_sink(target)


def test_separate_invocations_have_separate_run_ids_and_restart_sequence(
    tmp_path: Path,
) -> None:
    path = tmp_path / "events.jsonl"
    run_ids = iter(["run-one", "run-two"])

    first = JsonlEventSink(
        path,
        "stratum-handshake",
        clock=lambda: FIXED_TIME,
        run_id_factory=lambda: next(run_ids),
    )
    first.emit("command_started")
    first.emit("command_completed", fields={"outcome": "handshake_succeeded"})
    first.close()

    second = JsonlEventSink(
        path,
        "stratum-handshake",
        clock=lambda: FIXED_TIME,
        run_id_factory=lambda: next(run_ids),
    )
    second.emit("command_started")
    second.close()

    records = read_records(path)
    assert [record["run_id"] for record in records] == ["run-one", "run-one", "run-two"]
    assert [record["sequence"] for record in records] == [1, 2, 1]


def test_default_run_ids_are_unique_between_sink_instances(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"

    first = JsonlEventSink(path, "stratum-handshake")
    first.emit("command_started")
    first.close()
    second = JsonlEventSink(path, "stratum-handshake")
    second.emit("command_started")
    second.close()

    records = read_records(path)
    assert records[0]["run_id"] != records[1]["run_id"]
    assert [record["sequence"] for record in records] == [1, 1]


def test_relative_log_path_is_supported(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    sink = JsonlEventSink(
        "relative/events.jsonl",
        "stratum-handshake",
        clock=lambda: FIXED_TIME,
        run_id_factory=lambda: "run-relative",
    )

    sink.emit("command_started")
    sink.close()

    assert read_records(tmp_path / "relative" / "events.jsonl")[0]["run_id"] == "run-relative"


def test_clock_is_converted_to_utc_rfc3339(tmp_path: Path) -> None:
    local_time = datetime(
        2026,
        7,
        27,
        19,
        34,
        56,
        123456,
        tzinfo=timezone(timedelta(hours=7)),
    )
    sink = JsonlEventSink(
        tmp_path / "events.jsonl",
        "stratum-handshake",
        clock=lambda: local_time,
        run_id_factory=lambda: "run-time",
    )

    sink.emit("command_started")
    sink.close()

    assert read_records(tmp_path / "events.jsonl")[0]["timestamp"] == (
        "2026-07-27T12:34:56.123456Z"
    )


@pytest.mark.parametrize("event", ["", " ", "CommandStarted", "command-started", "_private"])
def test_event_name_must_be_nonblank_snake_case(tmp_path: Path, event: str) -> None:
    sink = make_sink(tmp_path / "events.jsonl")

    with pytest.raises(ValueError, match="snake_case"):
        sink.emit(event)

    sink.close()


@pytest.mark.parametrize("level", ["info", "WARN", "DEBUG", "", 1, None])
def test_level_must_be_one_of_the_supported_exact_values(
    tmp_path: Path,
    level: object,
) -> None:
    sink = make_sink(tmp_path / "events.jsonl")

    with pytest.raises(ValueError, match="level"):
        sink.emit("command_started", level=level)  # type: ignore[arg-type]

    sink.close()


@pytest.mark.parametrize(
    "field_name",
    [
        "password",
        "stratum_password",
        "bitcoin_address",
        "payout_address",
        "username",
        "stratum_username",
        "extra_nonce_1",
        "extra_nonce_2",
        "coinbase",
        "coinbase_part_1",
        "coinbase_part_2",
        "coinbase_transaction",
        "raw_coinbase",
        "raw_job",
        "subscribe_request",
        "authorization_request",
        "submit_request",
        "raw_subscribe_request",
        "raw_authorization_request",
        "raw_submit_request",
        "request_payload",
        "response_payload",
    ],
)
def test_secret_bearing_field_names_are_forbidden(tmp_path: Path, field_name: str) -> None:
    sink = make_sink(tmp_path / "events.jsonl")

    with pytest.raises(ValueError, match="forbidden"):
        sink.emit("command_started", fields={field_name: "must-not-appear"})

    sink.close()
    assert (tmp_path / "events.jsonl").read_text(encoding="utf-8") == ""


def test_masked_username_is_deliberately_allowed(tmp_path: Path) -> None:
    sink = make_sink(tmp_path / "events.jsonl")

    sink.emit("command_started", fields={"masked_username": "bc1q…r-01"})
    sink.close()

    assert read_records(tmp_path / "events.jsonl")[0]["masked_username"] == "bc1q…r-01"


def test_nested_secret_fields_and_envelope_overwrites_are_rejected(tmp_path: Path) -> None:
    sink = make_sink(tmp_path / "events.jsonl")

    with pytest.raises(ValueError, match="forbidden"):
        sink.emit("command_started", fields={"details": {"password": "no"}})
    with pytest.raises(ValueError, match="envelope"):
        sink.emit("command_started", fields={"sequence": 99})

    sink.close()


@pytest.mark.parametrize(
    "value",
    [b"bytes", bytearray(b"bytes"), memoryview(b"bytes"), object(), Decimal("1")],
)
def test_unsupported_event_values_are_not_stringified(tmp_path: Path, value: object) -> None:
    sink = make_sink(tmp_path / "events.jsonl")

    with pytest.raises(TypeError, match="unsupported"):
        sink.emit("command_started", fields={"value": value})  # type: ignore[dict-item]

    sink.close()


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_event_values_are_rejected(tmp_path: Path, value: float) -> None:
    sink = make_sink(tmp_path / "events.jsonl")

    with pytest.raises(ValueError, match="finite"):
        sink.emit("command_started", fields={"value": value})

    sink.close()


def test_failed_validation_does_not_advance_sequence(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    sink = make_sink(path)

    with pytest.raises(ValueError):
        sink.emit("not-valid")
    sink.emit("command_started")
    sink.close()

    assert read_records(path)[0]["sequence"] == 1


def test_supported_collections_are_copied_without_mutation(tmp_path: Path) -> None:
    values = [1, {"nested": (True, None, "text")}]
    original = [1, {"nested": (True, None, "text")}]
    sink = make_sink(tmp_path / "events.jsonl")

    sink.emit("command_started", fields={"values": values})  # type: ignore[dict-item]
    sink.close()

    assert values == original
    assert read_records(tmp_path / "events.jsonl")[0]["values"] == [
        1,
        {"nested": [True, None, "text"]},
    ]


def test_blank_field_keys_and_non_mapping_fields_are_rejected(tmp_path: Path) -> None:
    sink = make_sink(tmp_path / "events.jsonl")

    with pytest.raises(ValueError, match="must not be blank"):
        sink.emit("command_started", fields={"": 1})
    with pytest.raises(TypeError, match="field name"):
        sink.emit("command_started", fields={1: "value"})  # type: ignore[dict-item]
    with pytest.raises(TypeError, match="mapping"):
        sink.emit("command_started", fields=[])  # type: ignore[arg-type]

    sink.close()


class FlushTrackingStream(io.StringIO):
    """In-memory stream that records flush calls."""

    def __init__(self) -> None:
        super().__init__()
        self.flush_calls = 0

    def flush(self) -> None:
        self.flush_calls += 1
        super().flush()


class FailingWriteStream(io.StringIO):
    """Stream that fails every write."""

    def write(self, value: str) -> int:
        raise OSError("synthetic sensitive write failure")


class FailingCloseStream(io.StringIO):
    """Stream that fails while closing."""

    def __init__(self) -> None:
        super().__init__()
        self.failed = False

    def close(self) -> None:
        if not self.failed:
            self.failed = True
            raise OSError("synthetic sensitive close failure")
        super().close()


def test_each_event_is_flushed_promptly(tmp_path: Path) -> None:
    sink = make_sink(tmp_path / "events.jsonl")
    original_stream = sink._stream
    stream = FlushTrackingStream()
    sink._stream = stream
    original_stream.close()

    sink.emit("command_started")
    assert stream.flush_calls == 1
    assert stream.getvalue().endswith("\n")
    sink.close()


def test_write_failure_is_sanitized_and_does_not_advance_sequence(tmp_path: Path) -> None:
    sink = make_sink(tmp_path / "events.jsonl")
    original_stream = sink._stream
    sink._stream = FailingWriteStream()
    original_stream.close()

    with pytest.raises(EventLogError, match="could not write") as captured:
        sink.emit("command_started")

    assert "sensitive" not in str(captured.value)
    assert sink._next_sequence == 1
    sink.close()


def test_close_failure_is_sanitized_and_sink_remains_closed(tmp_path: Path) -> None:
    sink = make_sink(tmp_path / "events.jsonl")
    original_stream = sink._stream
    sink._stream = FailingCloseStream()
    original_stream.close()

    with pytest.raises(EventLogError, match="could not close") as captured:
        sink.close()

    assert "sensitive" not in str(captured.value)
    sink.close()
    with pytest.raises(EventLogError, match="closed"):
        sink.emit("command_started")


def test_initialization_failure_is_sanitized(tmp_path: Path) -> None:
    blocking_file = tmp_path / "not-a-directory"
    blocking_file.write_text("content", encoding="utf-8")

    with pytest.raises(EventLogError, match="initialize") as captured:
        make_sink(blocking_file / "events.jsonl")

    assert str(blocking_file) not in str(captured.value)


@pytest.mark.parametrize(
    "clock_value",
    [datetime(2026, 7, 27), "2026-07-27T00:00:00Z"],
)
def test_clock_must_return_timezone_aware_datetime(tmp_path: Path, clock_value: object) -> None:
    sink = JsonlEventSink(
        tmp_path / "events.jsonl",
        "stratum-handshake",
        clock=lambda: clock_value,  # type: ignore[return-value]
        run_id_factory=lambda: "run-clock",
    )

    with pytest.raises(EventLogError, match="clock"):
        sink.emit("command_started")

    sink.close()


def test_cli_event_sink_creates_warning_error_sibling(
    tmp_path: Path,
) -> None:
    main_path = tmp_path / "mining.jsonl"

    def operation(events: EventSink) -> int:
        events.emit(
            "synthetic_warning",
            level="WARNING",
            fields={"category": "warning_test"},
        )
        events.emit(
            "synthetic_error",
            level="ERROR",
            fields={"category": "error_test"},
        )
        return 0

    assert (
        cli_module._run_with_event_sink(
            "stratum-mine",
            str(main_path),
            operation,
        )
        == 0
    )

    warning_path = tmp_path / "mining.warnings.jsonl"

    main_records = read_records(main_path)
    warning_records = read_records(warning_path)

    assert [record["event"] for record in warning_records] == [
        "synthetic_warning",
        "synthetic_error",
    ]

    assert [record["level"] for record in warning_records] == [
        "WARNING",
        "ERROR",
    ]

    main_by_sequence = {record["sequence"]: record for record in main_records}

    for warning in warning_records:
        primary = main_by_sequence[warning["sequence"]]
        assert warning == primary
        assert warning["run_id"] == primary["run_id"]


def test_warning_mirror_contains_only_warning_and_error(
    tmp_path: Path,
) -> None:
    main_path = tmp_path / "events.jsonl"
    warning_path = tmp_path / "events.warnings.jsonl"

    sink = JsonlEventSink(
        main_path,
        "stratum-mine",
        warning_path=warning_path,
        clock=lambda: FIXED_TIME,
        run_id_factory=lambda: "warning-run",
    )
    sink.emit("normal_event")
    sink.emit(
        "warning_event",
        level="WARNING",
        fields={"category": "warning_test"},
    )
    sink.emit(
        "error_event",
        level="ERROR",
        fields={"category": "error_test"},
    )
    sink.close()

    main_records = read_records(main_path)
    warning_records = read_records(warning_path)

    assert [record["event"] for record in main_records] == [
        "normal_event",
        "warning_event",
        "error_event",
    ]
    assert [record["event"] for record in warning_records] == [
        "warning_event",
        "error_event",
    ]
    assert [record["level"] for record in warning_records] == [
        "WARNING",
        "ERROR",
    ]
    assert [record["sequence"] for record in warning_records] == [2, 3]
    assert all(record["run_id"] == "warning-run" for record in warning_records)
