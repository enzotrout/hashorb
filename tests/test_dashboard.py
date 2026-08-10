"""Behavior tests for the read-only HashOrb terminal dashboard."""

from __future__ import annotations

import io
import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import hashorb.__main__ as hashorb_cli
import hashorb.dashboard as dashboard_module
from hashorb.dashboard import (
    DashboardLogError,
    DashboardLogReader,
    DashboardRecord,
    DashboardState,
    probe_nvidia_metrics,
    render_dashboard,
    run_dashboard,
)


def _record(
    sequence: int,
    event: str,
    seconds: float,
    *,
    run_id: str = "run-a",
    command: str = "stratum-mine",
    **fields: object,
) -> DashboardRecord:
    return DashboardRecord(
        timestamp=datetime(2026, 8, 10, 10, 0, tzinfo=UTC) + timedelta(seconds=seconds),
        run_id=run_id,
        sequence=sequence,
        level="INFO",
        event=event,
        command=command,
        fields=fields,  # type: ignore[arg-type]
    )


def _json_record(sequence: int, event: str, seconds: int, **fields: object) -> str:
    timestamp = datetime(2026, 8, 10, 10, 0, tzinfo=UTC) + timedelta(seconds=seconds)
    payload: dict[str, object] = {
        "schema_version": 1,
        "timestamp": timestamp.isoformat(timespec="microseconds").replace("+00:00", "Z"),
        "run_id": "run-a",
        "sequence": sequence,
        "level": "INFO",
        "event": event,
        "command": "stratum-mine",
        **fields,
    }
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def test_dashboard_tracks_auto_raw_and_effective_rates_and_nonce_progress() -> None:
    state = DashboardState()
    state.apply(_record(1, "command_started", 0.0))
    state.apply(
        _record(
            2,
            "compute_profile_resolved",
            0.01,
            requested_profile="auto",
            effective_profile="auto",
            effective_backend="cuda",
            chunk_size=500_000_000,
            inter_range_delay_seconds=0.08,
            resolution_reason="AutoCudaDevice",
            device_ordinal=0,
            threads_per_block=256,
        )
    )
    state.apply(
        _record(
            3,
            "compute_backend_selected",
            0.02,
            backend_name="cuda",
            backend_kind="gpu",
            implementation="cuda",
            supports_parallel_search=True,
            supports_cooperative_cancellation=False,
            supports_device_selection=True,
            device_ordinal=0,
        )
    )
    state.apply(
        _record(
            4,
            "search_strategy_selected",
            0.03,
            strategy_name="sequential",
            implementation="sequential",
            deterministic=True,
            contiguous_parent_ranges=True,
            exhaustive=True,
            experimental=False,
        )
    )
    state.apply(
        _record(
            5,
            "stratum_authorized",
            0.04,
            endpoint="pool.example:3333",
            extra_nonce_2_size=8,
        )
    )
    state.apply(_record(6, "difficulty_received", 0.05, difficulty=10_000))
    state.apply(
        _record(
            7,
            "mining_job_received",
            0.06,
            job_id="job-00000001",
            network_bits="1702353d",
            clean_jobs=True,
            merkle_branch_count=12,
        )
    )

    sequence = 8
    elapsed_per_range_ns = 181_000_000
    for index in range(8):
        start = index * 500_000_000
        stop = min(start + 500_000_000, 1 << 32)
        completion_time = 0.181 + index * 0.261
        state.apply(
            _record(
                sequence,
                "nonce_range_started",
                completion_time - 0.18,
                job_id="job-00000001",
                start_nonce=start,
                stop_nonce=stop,
            )
        )
        sequence += 1
        state.apply(
            _record(
                sequence,
                "nonce_range_completed",
                completion_time,
                job_id="job-00000001",
                start_nonce=start,
                stop_nonce=stop,
                hashes_checked=stop - start,
                elapsed_ns=elapsed_per_range_ns,
                hashes_per_second=(stop - start) * 1_000_000_000 / elapsed_per_range_ns,
                match_found=False,
            )
        )
        sequence += 1

    assert state.profile_effective == "auto"
    assert state.backend_name == "cuda"
    assert state.device_ordinal == 0
    assert state.strategy_name == "sequential"
    assert state.raw_hashes_per_second is not None
    assert state.raw_hashes_per_second > 2.7e9
    effective = state.effective_hashes_per_second(
        datetime(2026, 8, 10, 10, 0, tzinfo=UTC) + timedelta(seconds=2.1)
    )
    assert effective is not None
    assert 1.8e9 < effective < 2.1e9
    assert state.ranges_completed == 8
    assert state.hashes_checked == 4_000_000_000
    assert sum(state.nonce_buckets) > 50

    rendered = render_dashboard(
        state,
        width=140,
        now=datetime(2026, 8, 10, 10, 0, tzinfo=UTC) + timedelta(seconds=2.1),
        color=False,
    )
    assert "HashOrb Dashboard" in rendered
    assert "PROFILE auto" in rendered
    assert "BACKEND cuda" in rendered
    assert "NONCE SPACE EXPLORATION — sequential" in rendered
    assert "Sequential: contiguous parent ranges" in rendered
    assert "Raw 2.762 GH/s" in rendered
    assert "Effective " in rendered


def test_orbiting_bit_nonce_visualization_reflects_observed_scattered_ranges() -> None:
    state = DashboardState()
    state.apply(_record(1, "command_started", 0.0))
    state.apply(
        _record(
            2,
            "search_strategy_selected",
            0.01,
            strategy_name="orbiting-bit",
            implementation="bit-reversal",
            deterministic=True,
            contiguous_parent_ranges=False,
            exhaustive=True,
            experimental=True,
        )
    )
    bucket_size = (1 << 32) // 64
    for index, bucket in enumerate((0, 32, 16, 48), start=3):
        start = bucket * bucket_size
        state.apply(
            _record(
                index,
                "nonce_range_completed",
                float(index),
                job_id="job-orbit",
                start_nonce=start,
                stop_nonce=start + bucket_size,
                hashes_checked=bucket_size,
                elapsed_ns=10_000_000,
                hashes_per_second=1_000_000_000.0,
                match_found=False,
            )
        )

    rendered = render_dashboard(state, width=120, color=False)
    assert "NONCE SPACE EXPLORATION — orbiting-bit" in rendered
    assert "Orbiting-bit: observed parent ranges jump through bit-reversal order" in rendered
    assert "Observed range path: 00 → 32 → 16 → 48" in rendered
    assert "█" in rendered
    assert "·" in rendered


def test_newer_mining_run_replaces_older_interleaved_run() -> None:
    state = DashboardState()
    state.apply(_record(1, "command_started", 0.0, run_id="old"))
    state.apply(_record(2, "difficulty_received", 1.0, run_id="old", difficulty=100))
    state.apply(_record(1, "command_started", 2.0, run_id="new"))
    state.apply(_record(3, "difficulty_received", 3.0, run_id="old", difficulty=200))
    state.apply(_record(2, "difficulty_received", 4.0, run_id="new", difficulty=10_000))

    assert state.active_run_id == "new"
    assert state.difficulty == 10_000


def test_nonce_visualization_resets_when_work_variant_advances() -> None:
    state = DashboardState()
    state.apply(_record(1, "command_started", 0.0))
    state.apply(
        _record(
            2,
            "nonce_range_completed",
            1.0,
            job_id="job-a",
            start_nonce=0,
            stop_nonce=500_000_000,
            hashes_checked=500_000_000,
            elapsed_ns=200_000_000,
            hashes_per_second=2_500_000_000.0,
            match_found=False,
        )
    )
    assert any(state.nonce_buckets)

    state.apply(
        _record(
            3,
            "mining_work_advanced",
            2.0,
            reason="extra_nonce_2",
            work_variant_index=2,
            extra_nonce_2_advance_count=1,
            network_time_roll_count=0,
        )
    )

    assert not any(state.nonce_buckets)
    assert not state.recent_bucket_visits
    assert state.work_variant_index == 2
    assert state.extra_nonce_2_advances == 1


def test_incremental_reader_preserves_partial_final_record(tmp_path: Path) -> None:
    path = tmp_path / "live.jsonl"
    first = _json_record(1, "command_started", 0)
    second = _json_record(2, "difficulty_received", 1, difficulty=10_000)
    split = len(second) // 2
    path.write_bytes((first + "\n" + second[:split]).encode())

    reader = DashboardLogReader(path)
    batch = reader.read_available()
    assert [record.event for record in batch.records] == ["command_started"]

    with path.open("ab") as stream:
        stream.write((second[split:] + "\n").encode())
    batch = reader.read_available()
    assert [record.event for record in batch.records] == ["difficulty_received"]
    assert batch.records[0].fields["difficulty"] == 10_000


def test_incremental_reader_rejects_malformed_complete_record(tmp_path: Path) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_text('{"schema_version":1,"schema_version":1}\n', encoding="utf-8")

    with pytest.raises(DashboardLogError, match="malformed JSON"):
        DashboardLogReader(path).read_available(require_complete=True)


def test_once_mode_renders_without_ansi_or_modifying_source(tmp_path: Path) -> None:
    path = tmp_path / "snapshot.jsonl"
    content = (
        "\n".join(
            (
                _json_record(1, "command_started", 0),
                _json_record(2, "difficulty_received", 1, difficulty=10_000),
            )
        )
        + "\n"
    )
    path.write_text(content, encoding="utf-8")
    before = path.read_bytes()
    output = io.StringIO()

    assert run_dashboard(path, once=True, output=output) == 0

    assert "HashOrb Dashboard" in output.getvalue()
    assert "Difficulty 10,000" in output.getvalue()
    assert "\x1b[" not in output.getvalue()
    assert path.read_bytes() == before


def test_nvidia_probe_requests_only_safe_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(dashboard_module.shutil, "which", lambda name: "/safe/nvidia-smi")

    def runner(args: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(
            args,
            0,
            stdout="56, 165.2, 92, 96874, 6656\n",
            stderr="",
        )

    metrics = probe_nvidia_metrics(0, runner=runner)

    assert metrics is not None
    assert metrics.temperature_c == 56
    assert metrics.power_w == 165.2
    assert metrics.utilization_percent == 92
    assert len(calls) == 1
    command = " ".join(calls[0]).lower()
    assert "temperature.gpu" in command
    assert "power.draw" in command
    assert "utilization.gpu" in command
    assert "memory.total" in command
    assert "memory.used" in command
    assert "uuid" not in command
    assert "serial" not in command
    assert "pci" not in command


def test_canonical_cli_routes_dashboard_without_changing_console_entrypoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dashboard_calls: list[tuple[str, float, bool]] = []

    def fake_dashboard(log_file: str, *, refresh_seconds: float, once: bool) -> int:
        dashboard_calls.append((log_file, refresh_seconds, once))
        return 0

    monkeypatch.setattr(hashorb_cli, "run_dashboard", fake_dashboard)

    assert (
        hashorb_cli.main(
            ["dashboard", "--log-file", "logs/live.jsonl", "--refresh-seconds", "0.5", "--once"]
        )
        == 0
    )
    assert dashboard_calls == [("logs/live.jsonl", 0.5, True)]


@pytest.mark.parametrize(
    "arguments",
    [
        ["dashboard"],
        ["dashboard", "--log-file"],
        ["dashboard", "--log-file", ""],
        ["dashboard", "--log-file", "a", "--log-file", "b"],
        ["dashboard", "--log-file", "a", "--refresh-seconds", "0"],
        ["dashboard", "--log-file", "a", "--refresh-seconds", "60.1"],
        ["dashboard", "--log-file", "a", "--once", "--once"],
        ["dashboard", "--log-file", "a", "--unknown", "x"],
    ],
)
def test_canonical_cli_rejects_invalid_dashboard_arguments(
    arguments: list[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert hashorb_cli.main(arguments) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Usage: hashorb dashboard" in captured.err
