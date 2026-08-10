"""Read-only terminal dashboard built from sanitized HashOrb JSONL events."""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TextIO, cast

_NONCE_LIMIT = 1 << 32
_DEFAULT_BUCKET_COUNT = 64
_RECENT_EVENT_LIMIT = 8
_RATE_SAMPLE_LIMIT = 80
_EFFECTIVE_WINDOW_SECONDS = 300.0
_MIN_RENDER_WIDTH = 88
_EVENT_NAME = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
_LEVELS = frozenset({"INFO", "WARNING", "ERROR"})
_MINING_COMMANDS = frozenset({"stratum-mine", "stratum-mine-chunks", "stratum-mine-once"})
_ENVELOPE_FIELDS = frozenset(
    {"schema_version", "timestamp", "run_id", "sequence", "level", "event", "command"}
)
_SPARK_BLOCKS = "▁▂▃▄▅▆▇█"
_ANSI_CLEAR_HOME = "\x1b[2J\x1b[H"
_ANSI_HIDE_CURSOR = "\x1b[?25l"
_ANSI_SHOW_CURSOR = "\x1b[?25h"


type JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
type NvidiaRunner = Callable[[tuple[str, ...]], subprocess.CompletedProcess[str]]


class DashboardLogError(RuntimeError):
    """Raised when the read-only dashboard source is invalid or unreadable."""


@dataclass(frozen=True, slots=True)
class DashboardRecord:
    """One validated JSONL envelope plus safe event-specific fields."""

    timestamp: datetime
    run_id: str
    sequence: int
    level: str
    event: str
    command: str
    fields: Mapping[str, JsonValue]


@dataclass(frozen=True, slots=True)
class NvidiaMetrics:
    """Small safe telemetry subset for one already-selected CUDA ordinal."""

    temperature_c: float | None
    power_w: float | None
    utilization_percent: float | None
    memory_total_mib: float | None
    memory_used_mib: float | None


@dataclass(slots=True)
class DashboardState:
    """Mutable projection of the latest mining run for terminal presentation."""

    active_run_id: str | None = None
    command: str | None = None
    status: str = "waiting"
    completion_outcome: str | None = None
    started_at: datetime | None = None
    last_event_at: datetime | None = None
    profile_requested: str | None = None
    profile_effective: str | None = None
    backend_name: str | None = None
    device_ordinal: int | None = None
    worker_count: int | None = None
    strategy_name: str | None = None
    endpoint: str | None = None
    difficulty: float | None = None
    network_bits: str | None = None
    extra_nonce_2_size: int | None = None
    current_job_id: str | None = None
    current_job_received_at: datetime | None = None
    work_variant_index: int = 0
    jobs_received: int = 0
    job_replacements: int = 0
    work_variants: int = 0
    extra_nonce_2_advances: int = 0
    ranges_completed: int = 0
    hashes_checked: int = 0
    mining_elapsed_ns: int = 0
    reconnect_attempts: int = 0
    reconnect_successes: int = 0
    connection_losses: int = 0
    duplicate_work: int = 0
    liveness_warnings: int = 0
    stale_sessions: int = 0
    candidates: int = 0
    submissions: int = 0
    accepted_submissions: int = 0
    rejected_submissions: int = 0
    current_range: tuple[int, int] | None = None
    nonce_buckets: list[bool] = field(
        default_factory=lambda: [False for _ in range(_DEFAULT_BUCKET_COUNT)]
    )
    recent_bucket_visits: deque[int] = field(default_factory=lambda: deque(maxlen=12))
    raw_rate_samples: deque[float] = field(
        default_factory=lambda: deque(maxlen=_RATE_SAMPLE_LIMIT)
    )
    effective_points: deque[tuple[datetime, int]] = field(default_factory=deque)
    recent_events: deque[str] = field(default_factory=lambda: deque(maxlen=_RECENT_EVENT_LIMIT))

    def apply(self, record: DashboardRecord) -> None:
        """Apply one validated record if it belongs to the newest mining run."""

        if record.event == "command_started" and record.command in _MINING_COMMANDS:
            if self.started_at is None or record.timestamp >= self.started_at:
                self._reset_for_run(record)
            return
        if self.active_run_id is None or record.run_id != self.active_run_id:
            return

        self.last_event_at = record.timestamp
        event = record.event
        fields = record.fields

        if event == "compute_profile_resolved":
            self.profile_requested = _required_string(fields, "requested_profile")
            self.profile_effective = _required_string(fields, "effective_profile")
            self.backend_name = _required_string(fields, "effective_backend")
            self.device_ordinal = _optional_integer(fields, "device_ordinal")
            self.worker_count = _optional_integer(fields, "worker_count")
        elif event == "compute_backend_selected":
            self.backend_name = _required_string(fields, "backend_name")
            self.device_ordinal = _optional_integer(fields, "device_ordinal")
            self.worker_count = _optional_integer(fields, "worker_count")
        elif event == "search_strategy_selected":
            self.strategy_name = _required_string(fields, "strategy_name")
        elif event == "stratum_authorized":
            self.endpoint = _required_string(fields, "endpoint")
            self.extra_nonce_2_size = _required_integer(fields, "extra_nonce_2_size")
            self.status = "mining"
            self._remember(record, f"authorized {self.endpoint}")
        elif event == "difficulty_received":
            self.difficulty = _required_number(fields, "difficulty")
            self._remember(record, f"difficulty {self.difficulty:g}")
        elif event == "mining_job_received":
            self.jobs_received += 1
            job_id = _required_string(fields, "job_id")
            self.network_bits = _required_string(fields, "network_bits")
            if self.current_job_id is None:
                self.current_job_id = job_id
                self.current_job_received_at = record.timestamp
            self._remember(record, f"job received {_abbreviate(job_id)}")
        elif event == "mining_job_replaced":
            self.job_replacements += 1
            self.current_job_id = _required_string(fields, "new_job_id")
            self.current_job_received_at = record.timestamp
            self._reset_nonce_variant()
            self._remember(record, f"job replaced → {_abbreviate(self.current_job_id)}")
        elif event == "mining_work_advanced":
            self.work_variants += 1
            self.work_variant_index = _required_integer(fields, "work_variant_index")
            reason = _required_string(fields, "reason")
            if reason == "extra_nonce_2":
                self.extra_nonce_2_advances += 1
            self._reset_nonce_variant()
            self._remember(record, f"work advanced {reason} #{self.work_variant_index}")
        elif event == "nonce_range_started":
            start_nonce = _required_integer(fields, "start_nonce")
            stop_nonce = _required_integer(fields, "stop_nonce")
            _validate_nonce_range(start_nonce, stop_nonce)
            job_id = _required_string(fields, "job_id")
            if self.current_job_id is not None and job_id != self.current_job_id:
                self.current_job_id = job_id
                self.current_job_received_at = record.timestamp
                self._reset_nonce_variant()
            self.current_range = (start_nonce, stop_nonce)
        elif event == "nonce_range_completed":
            start_nonce = _required_integer(fields, "start_nonce")
            stop_nonce = _required_integer(fields, "stop_nonce")
            hashes = _required_integer(fields, "hashes_checked")
            elapsed_ns = _required_integer(fields, "elapsed_ns")
            _validate_nonce_range(start_nonce, stop_nonce)
            if hashes < 0 or elapsed_ns < 0:
                raise DashboardLogError("dashboard range metrics are invalid")
            self.ranges_completed += 1
            self.hashes_checked += hashes
            self.mining_elapsed_ns += elapsed_ns
            rate = _optional_number(fields, "hashes_per_second")
            if rate is not None and rate >= 0:
                self.raw_rate_samples.append(rate)
            self._mark_nonce_range(start_nonce, stop_nonce)
            self.current_range = None
            self.effective_points.append((record.timestamp, self.hashes_checked))
            self._prune_effective_points(record.timestamp)
        elif event == "stratum_connection_lost":
            self.connection_losses += 1
            self.status = "recovering"
            self._remember(record, "connection lost")
        elif event == "stratum_reconnect_attempted":
            self.reconnect_attempts += 1
            self._remember(record, f"reconnect attempt {self.reconnect_attempts}")
        elif event == "stratum_reconnect_succeeded":
            self.reconnect_successes += 1
            self.status = "mining"
            self._remember(record, "reconnected")
        elif event == "duplicate_work_ignored":
            self.duplicate_work += 1
            self._remember(record, "duplicate work ignored")
        elif event == "stratum_liveness_warning":
            self.liveness_warnings += 1
            reason = _required_string(fields, "reason")
            self._remember(record, f"liveness warning {reason}")
        elif event == "stratum_session_stale":
            self.stale_sessions += 1
            self.status = "recovering"
            self._remember(record, "session stale")
        elif event == "share_candidate_found":
            self.candidates += 1
            self._remember(record, "share candidate found")
        elif event == "share_submission_completed":
            accepted = _required_boolean(fields, "accepted")
            self.submissions += 1
            if accepted:
                self.accepted_submissions += 1
            else:
                self.rejected_submissions += 1
            self._remember(record, "share accepted" if accepted else "share rejected")
        elif event == "command_completed":
            self.completion_outcome = _required_string(fields, "outcome")
            self.status = self.completion_outcome
            self._remember(record, f"completed {self.completion_outcome}")
        elif event == "command_failed":
            stage = _required_string(fields, "stage")
            category = _required_string(fields, "error_category")
            self.status = "failed"
            self._remember(record, f"failed {stage}/{category}")
        elif event == "mining_stop_requested":
            self.status = "stopping"
            self._remember(record, "stop requested")

    @property
    def raw_hashes_per_second(self) -> float | None:
        """Return weighted compute-only throughput for the active run."""

        if self.mining_elapsed_ns <= 0:
            return None
        return self.hashes_checked * 1_000_000_000 / self.mining_elapsed_ns

    def effective_hashes_per_second(self, now: datetime | None = None) -> float | None:
        """Return recent wall-clock throughput, including profile pacing and waits."""

        reference = now if now is not None else datetime.now(UTC)
        self._prune_effective_points(reference)
        if len(self.effective_points) < 2:
            return None
        first_time, first_hashes = self.effective_points[0]
        last_time, last_hashes = self.effective_points[-1]
        elapsed = (last_time - first_time).total_seconds()
        if elapsed <= 0 or last_hashes < first_hashes:
            return None
        return (last_hashes - first_hashes) / elapsed

    def uptime_seconds(self, now: datetime | None = None) -> float | None:
        """Return elapsed wall time since the active command started."""

        if self.started_at is None:
            return None
        reference = now if now is not None else datetime.now(UTC)
        return max(0.0, (reference - self.started_at).total_seconds())

    def job_age_seconds(self, now: datetime | None = None) -> float | None:
        """Return age of the currently selected job when known."""

        if self.current_job_received_at is None:
            return None
        reference = now if now is not None else datetime.now(UTC)
        return max(0.0, (reference - self.current_job_received_at).total_seconds())

    def _reset_for_run(self, record: DashboardRecord) -> None:
        self.active_run_id = record.run_id
        self.command = record.command
        self.status = "starting"
        self.completion_outcome = None
        self.started_at = record.timestamp
        self.last_event_at = record.timestamp
        self.profile_requested = None
        self.profile_effective = None
        self.backend_name = None
        self.device_ordinal = None
        self.worker_count = None
        self.strategy_name = None
        self.endpoint = None
        self.difficulty = None
        self.network_bits = None
        self.extra_nonce_2_size = None
        self.current_job_id = None
        self.current_job_received_at = None
        self.work_variant_index = 0
        self.jobs_received = 0
        self.job_replacements = 0
        self.work_variants = 0
        self.extra_nonce_2_advances = 0
        self.ranges_completed = 0
        self.hashes_checked = 0
        self.mining_elapsed_ns = 0
        self.reconnect_attempts = 0
        self.reconnect_successes = 0
        self.connection_losses = 0
        self.duplicate_work = 0
        self.liveness_warnings = 0
        self.stale_sessions = 0
        self.candidates = 0
        self.submissions = 0
        self.accepted_submissions = 0
        self.rejected_submissions = 0
        self.raw_rate_samples.clear()
        self.effective_points.clear()
        self.effective_points.append((record.timestamp, 0))
        self.recent_events.clear()
        self._reset_nonce_variant()
        self._remember(record, f"{record.command} started")

    def _reset_nonce_variant(self) -> None:
        self.current_range = None
        self.nonce_buckets[:] = [False for _ in range(_DEFAULT_BUCKET_COUNT)]
        self.recent_bucket_visits.clear()

    def _mark_nonce_range(self, start_nonce: int, stop_nonce: int) -> None:
        touched: list[int] = []
        for index in range(_DEFAULT_BUCKET_COUNT):
            bucket_start = index * _NONCE_LIMIT // _DEFAULT_BUCKET_COUNT
            bucket_stop = (index + 1) * _NONCE_LIMIT // _DEFAULT_BUCKET_COUNT
            if max(start_nonce, bucket_start) < min(stop_nonce, bucket_stop):
                self.nonce_buckets[index] = True
                touched.append(index)
        if touched:
            self.recent_bucket_visits.append(touched[len(touched) // 2])

    def _prune_effective_points(self, now: datetime) -> None:
        cutoff = now - timedelta(seconds=_EFFECTIVE_WINDOW_SECONDS)
        while len(self.effective_points) > 2 and self.effective_points[1][0] < cutoff:
            self.effective_points.popleft()

    def _remember(self, record: DashboardRecord, message: str) -> None:
        self.recent_events.append(
            f"{record.timestamp.astimezone(UTC).strftime('%H:%M:%S')}  {message}"
        )


@dataclass(frozen=True, slots=True)
class DashboardReadBatch:
    """Records appended since the prior read and whether the source reset."""

    records: tuple[DashboardRecord, ...]
    source_reset: bool


class DashboardLogReader:
    """Incrementally follow one regular JSONL file without modifying it."""

    __slots__ = ("_identity", "_offset", "_partial", "path")

    def __init__(self, path: str | Path) -> None:
        self.path = _validated_path(path)
        self._offset = 0
        self._partial = b""
        self._identity: tuple[int, int] | None = None

    def read_available(self, *, require_complete: bool = False) -> DashboardReadBatch:
        """Read only newly appended complete lines, preserving a partial tail."""

        try:
            metadata = self.path.stat(follow_symlinks=False)
        except (OSError, TypeError, ValueError) as exc:
            raise DashboardLogError("dashboard log is unavailable") from exc
        if self.path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
            raise DashboardLogError("dashboard log must be a regular non-symlink file")

        identity = (metadata.st_dev, metadata.st_ino)
        source_reset = False
        if self._identity is not None and (
            identity != self._identity or metadata.st_size < self._offset
        ):
            self._offset = 0
            self._partial = b""
            source_reset = True
        self._identity = identity

        try:
            with self.path.open("rb") as stream:
                stream.seek(self._offset)
                chunk = stream.read()
        except OSError as exc:
            raise DashboardLogError("dashboard log could not be read") from exc
        self._offset += len(chunk)

        data = self._partial + chunk
        pieces = data.split(b"\n")
        self._partial = pieces.pop()
        records: list[DashboardRecord] = []
        for raw_line in pieces:
            if not raw_line:
                raise DashboardLogError("dashboard log contains a blank record")
            try:
                line = raw_line.decode("utf-8")
            except UnicodeError as exc:
                raise DashboardLogError("dashboard log is not valid UTF-8") from exc
            records.append(parse_dashboard_record(line))

        if require_complete and self._partial:
            raise DashboardLogError("dashboard log ends with an incomplete record")
        return DashboardReadBatch(tuple(records), source_reset)


def parse_dashboard_record(text: str) -> DashboardRecord:
    """Parse and validate the stable envelope required by the dashboard."""

    try:
        raw: object = json.loads(text, object_pairs_hook=_unique_object)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise DashboardLogError("dashboard log contains malformed JSON") from exc
    if not isinstance(raw, dict):
        raise DashboardLogError("dashboard record must be a JSON object")
    record = cast(dict[str, JsonValue], raw)

    schema_version = record.get("schema_version")
    if isinstance(schema_version, bool) or schema_version != 1:
        raise DashboardLogError("dashboard record has an unsupported schema version")
    timestamp = _parse_timestamp(record.get("timestamp"))
    run_id = _required_record_string(record, "run_id")
    sequence = _required_record_integer(record, "sequence")
    if sequence <= 0:
        raise DashboardLogError("dashboard record sequence is invalid")
    level = _required_record_string(record, "level")
    if level not in _LEVELS:
        raise DashboardLogError("dashboard record level is invalid")
    event = _required_record_string(record, "event")
    if _EVENT_NAME.fullmatch(event) is None:
        raise DashboardLogError("dashboard record event name is invalid")
    command = _required_record_string(record, "command")
    fields = {key: value for key, value in record.items() if key not in _ENVELOPE_FIELDS}
    return DashboardRecord(timestamp, run_id, sequence, level, event, command, fields)


def probe_nvidia_metrics(
    device_ordinal: int,
    *,
    runner: NvidiaRunner | None = None,
) -> NvidiaMetrics | None:
    """Read a narrow safe NVIDIA metric set without exposing hardware identity."""

    if (
        isinstance(device_ordinal, bool)
        or not isinstance(device_ordinal, int)
        or device_ordinal < 0
    ):
        return None
    executable = shutil.which("nvidia-smi")
    if executable is None:
        return None
    args = (
        executable,
        f"--id={device_ordinal}",
        "--query-gpu=temperature.gpu,power.draw,utilization.gpu,memory.total,memory.used",
        "--format=csv,noheader,nounits",
    )
    try:
        result = (
            runner(args)
            if runner is not None
            else subprocess.run(
                args,
                check=False,
                capture_output=True,
                text=True,
                timeout=2.0,
                shell=False,
            )
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    line = result.stdout.strip().splitlines()
    if len(line) != 1:
        return None
    values = [item.strip() for item in line[0].split(",")]
    if len(values) != 5:
        return None
    parsed = [_optional_metric_number(value) for value in values]
    return NvidiaMetrics(parsed[0], parsed[1], parsed[2], parsed[3], parsed[4])


def render_dashboard(
    state: DashboardState,
    *,
    width: int = 140,
    now: datetime | None = None,
    nvidia: NvidiaMetrics | None = None,
    color: bool = False,
    source_label: str | None = None,
) -> str:
    """Render one bounded terminal snapshot without terminal side effects."""

    reference = now if now is not None else datetime.now(UTC)
    safe_width = max(_MIN_RENDER_WIDTH, width)
    inner_width = safe_width - 2
    raw_rate = state.raw_hashes_per_second
    effective_rate = state.effective_hashes_per_second(reference)

    lines = [_top_border("HashOrb Dashboard", inner_width)]
    lines.append(
        _row(
            "  ".join(
                (
                    f"PROFILE {_display(state.profile_effective)}",
                    f"BACKEND {_display(state.backend_name)}",
                    f"DEVICE {_display_device(state)}",
                    f"STRATEGY {_display(state.strategy_name)}",
                    f"STATUS {state.status}",
                )
            ),
            inner_width,
        )
    )
    lines.append(_rule("OVERVIEW", inner_width))
    efficiency = (
        effective_rate / raw_rate * 100
        if effective_rate is not None and raw_rate is not None and raw_rate > 0
        else None
    )
    lines.append(
        _row(
            "  |  ".join(
                (
                    f"Effective {_format_rate(effective_rate)}",
                    f"Raw {_format_rate(raw_rate)}",
                    f"Efficiency {_format_percent(efficiency)}",
                    f"Uptime {_format_duration(state.uptime_seconds(reference))}",
                    f"Difficulty {_format_difficulty(state.difficulty)}",
                )
            ),
            inner_width,
        )
    )
    lines.append(
        _row(
            "  |  ".join(
                (
                    f"Hashes {_format_hashes(state.hashes_checked)}",
                    f"Ranges {state.ranges_completed}",
                    f"Jobs {state.jobs_received} ({state.job_replacements} repl.)",
                    f"Variants {state.work_variants}",
                    f"Reconnects {state.reconnect_attempts}/{state.reconnect_successes}",
                    f"Duplicates {state.duplicate_work}",
                )
            ),
            inner_width,
        )
    )
    lines.append(
        _row(
            "  |  ".join(
                (
                    f"Candidates {state.candidates}",
                    f"Shares {state.submissions} ({state.accepted_submissions} accepted / "
                    f"{state.rejected_submissions} rejected)",
                    f"Endpoint {_display(state.endpoint)}",
                    f"Job {_abbreviate(state.current_job_id) if state.current_job_id else 'n/a'}",
                    f"Job age {_format_duration(state.job_age_seconds(reference))}",
                )
            ),
            inner_width,
        )
    )

    lines.append(_rule("DEVICE / RATE", inner_width))
    telemetry = _format_nvidia(nvidia, state.device_ordinal)
    lines.append(_row(telemetry, inner_width))
    spark_width = max(28, min(84, inner_width - 33))
    latest_raw_sample = state.raw_rate_samples[-1] if state.raw_rate_samples else None
    lines.append(
        _row(
            f"Raw range-rate history  {_sparkline(tuple(state.raw_rate_samples), spark_width)}  "
            f"latest {_format_rate(latest_raw_sample)}",
            inner_width,
        )
    )

    strategy = state.strategy_name or "unknown"
    lines.append(_rule(f"NONCE SPACE EXPLORATION — {strategy}", inner_width))
    map_width = max(32, min(_DEFAULT_BUCKET_COUNT, inner_width - 30))
    nonce_map = _render_nonce_map(state, map_width, color=color)
    lines.append(
        _row(
            f"0x00000000  {nonce_map}  0xffffffff",
            inner_width,
            already_colored=color,
        )
    )
    completed_buckets = sum(1 for item in state.nonce_buckets if item)
    lines.append(
        _row(
            f"Observed buckets {completed_buckets}/{_DEFAULT_BUCKET_COUNT}  |  "
            f"Current {_format_range(state.current_range)}  |  Variant #{state.work_variant_index}",
            inner_width,
        )
    )
    lines.append(_row(_strategy_explanation(strategy), inner_width))
    if state.recent_bucket_visits:
        path = " → ".join(f"{item:02d}" for item in state.recent_bucket_visits)
        lines.append(_row(f"Observed range path: {path}", inner_width))
    else:
        lines.append(_row("Observed range path: waiting for completed ranges", inner_width))

    lines.append(_rule("RECENT EVENTS", inner_width))
    recent = list(state.recent_events)[-6:]
    if recent:
        lines.extend(_row(f"{item}", inner_width) for item in recent)
    else:
        lines.append(_row("waiting for mining events", inner_width))

    lines.append(_rule("READ-ONLY CONTROLS", inner_width))
    source = source_label if source_label is not None else "structured JSONL"
    lines.append(
        _row(
            f"Ctrl-C exit  |  source {source}  |  display only: "
            "mining/profile/backend controls deferred",
            inner_width,
        )
    )
    lines.append(_bottom_border(inner_width))

    rendered = "\n".join(lines)
    if not color:
        return rendered
    return _colorize_dashboard(rendered)


def run_dashboard(
    log_file: str | Path,
    *,
    refresh_seconds: float = 1.0,
    once: bool = False,
    output: TextIO | None = None,
    sleeper: Callable[[float], None] = time.sleep,
) -> int:
    """Render a snapshot or follow a live log until Ctrl-C."""

    if not math.isfinite(refresh_seconds) or not 0.1 <= refresh_seconds <= 60.0:
        raise ValueError("refresh_seconds must be finite and between 0.1 and 60")
    destination = output if output is not None else sys.stdout
    reader = DashboardLogReader(log_file)
    state = DashboardState()

    if once:
        batch = reader.read_available(require_complete=True)
        for record in batch.records:
            state.apply(record)
        nvidia = (
            probe_nvidia_metrics(state.device_ordinal)
            if state.device_ordinal is not None
            else None
        )
        width = shutil.get_terminal_size(fallback=(140, 40)).columns
        destination.write(
            render_dashboard(
                state,
                width=width,
                nvidia=nvidia,
                color=False,
                source_label=str(reader.path),
            )
            + "\n"
        )
        destination.flush()
        return 0

    interactive = bool(getattr(destination, "isatty", lambda: False)())
    color = interactive and "NO_COLOR" not in os.environ
    if interactive:
        destination.write(_ANSI_HIDE_CURSOR)
        destination.flush()
    try:
        while True:
            batch = reader.read_available()
            if batch.source_reset:
                state = DashboardState()
            for record in batch.records:
                state.apply(record)
            nvidia = (
                probe_nvidia_metrics(state.device_ordinal)
                if state.device_ordinal is not None
                else None
            )
            width = shutil.get_terminal_size(fallback=(140, 40)).columns
            snapshot = render_dashboard(
                state,
                width=width,
                nvidia=nvidia,
                color=color,
                source_label=str(reader.path),
            )
            if interactive:
                destination.write(_ANSI_CLEAR_HOME)
            destination.write(snapshot + "\n")
            destination.flush()
            sleeper(refresh_seconds)
    except KeyboardInterrupt:
        return 0
    finally:
        if interactive:
            destination.write(_ANSI_SHOW_CURSOR + "\n")
            destination.flush()


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _validated_path(value: str | Path) -> Path:
    if isinstance(value, str):
        if not value.strip():
            raise ValueError("dashboard log path must not be blank")
        return Path(value)
    if isinstance(value, Path):
        return value
    raise TypeError("dashboard log path must be a string or Path")


def _parse_timestamp(value: JsonValue) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise DashboardLogError("dashboard record timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise DashboardLogError("dashboard record timestamp is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise DashboardLogError("dashboard record timestamp is invalid")
    return parsed.astimezone(UTC)


def _required_record_string(record: Mapping[str, JsonValue], name: str) -> str:
    value = record.get(name)
    if not isinstance(value, str) or not value.strip():
        raise DashboardLogError(f"dashboard record {name} is invalid")
    return value


def _required_record_integer(record: Mapping[str, JsonValue], name: str) -> int:
    value = record.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise DashboardLogError(f"dashboard record {name} is invalid")
    return value


def _required_string(fields: Mapping[str, JsonValue], name: str) -> str:
    value = fields.get(name)
    if not isinstance(value, str) or not value.strip():
        raise DashboardLogError("dashboard event field is invalid")
    return value


def _required_integer(fields: Mapping[str, JsonValue], name: str) -> int:
    value = fields.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise DashboardLogError("dashboard event field is invalid")
    return value


def _optional_integer(fields: Mapping[str, JsonValue], name: str) -> int | None:
    value = fields.get(name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise DashboardLogError("dashboard event field is invalid")
    return value


def _required_boolean(fields: Mapping[str, JsonValue], name: str) -> bool:
    value = fields.get(name)
    if not isinstance(value, bool):
        raise DashboardLogError("dashboard event field is invalid")
    return value


def _required_number(fields: Mapping[str, JsonValue], name: str) -> float:
    value = _optional_number(fields, name)
    if value is None:
        raise DashboardLogError("dashboard event field is invalid")
    return value


def _optional_number(fields: Mapping[str, JsonValue], name: str) -> float | None:
    value = fields.get(name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DashboardLogError("dashboard event field is invalid")
    converted = float(value)
    if not math.isfinite(converted):
        raise DashboardLogError("dashboard event field is invalid")
    return converted


def _validate_nonce_range(start_nonce: int, stop_nonce: int) -> None:
    if not 0 <= start_nonce < stop_nonce <= _NONCE_LIMIT:
        raise DashboardLogError("dashboard nonce range is invalid")


def _optional_metric_number(value: str) -> float | None:
    if not value or value.upper() in {"N/A", "[N/A]"}:
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    if not math.isfinite(parsed) or parsed < 0:
        return None
    return parsed


def _format_nvidia(metrics: NvidiaMetrics | None, ordinal: int | None) -> str:
    if ordinal is None:
        return "GPU telemetry: n/a for current backend"
    if metrics is None:
        return f"CUDA device {ordinal}  |  GPU telemetry unavailable (optional)"
    memory = (
        "n/a"
        if metrics.memory_total_mib is None or metrics.memory_used_mib is None
        else f"{metrics.memory_used_mib / 1024:.1f}/{metrics.memory_total_mib / 1024:.1f} GiB"
    )
    return "  |  ".join(
        (
            f"CUDA device {ordinal}",
            f"Temp {_format_optional(metrics.temperature_c, '°C')}",
            f"Power {_format_optional(metrics.power_w, ' W')}",
            f"Util {_format_optional(metrics.utilization_percent, '%')}",
            f"Memory {memory}",
        )
    )


def _format_optional(value: float | None, suffix: str) -> str:
    return "n/a" if value is None else f"{value:.0f}{suffix}"


def _display(value: str | None) -> str:
    return value if value is not None else "n/a"


def _display_device(state: DashboardState) -> str:
    if state.device_ordinal is not None:
        return str(state.device_ordinal)
    if state.worker_count is not None:
        return f"cpu/{state.worker_count}w"
    return "n/a"


def _format_rate(value: float | None) -> str:
    if value is None:
        return "n/a"
    if value >= 1_000_000_000_000:
        return f"{value / 1_000_000_000_000:.3f} TH/s"
    if value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.3f} GH/s"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.3f} MH/s"
    if value >= 1_000:
        return f"{value / 1_000:.3f} kH/s"
    return f"{value:.1f} H/s"


def _format_hashes(value: int) -> str:
    if value >= 1_000_000_000_000:
        return f"{value / 1_000_000_000_000:.2f} T"
    if value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f} G"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f} M"
    if value >= 1_000:
        return f"{value / 1_000:.2f} k"
    return str(value)


def _format_percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f}%"


def _format_duration(value: float | None) -> str:
    if value is None:
        return "n/a"
    seconds = max(0, int(value))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _format_difficulty(value: float | None) -> str:
    if value is None:
        return "n/a"
    if value.is_integer():
        return f"{int(value):,}"
    return f"{value:,.4g}"


def _format_range(value: tuple[int, int] | None) -> str:
    if value is None:
        return "idle/pacing"
    return f"0x{value[0]:08x}..0x{value[1] - 1:08x}"


def _abbreviate(value: str | None) -> str:
    if value is None:
        return "n/a"
    if len(value) <= 12:
        return value
    return f"{value[:8]}…{value[-3:]}"


def _sparkline(values: Sequence[float], width: int) -> str:
    if not values:
        return "·" * width
    samples = list(values[-width:])
    minimum = min(samples)
    maximum = max(samples)
    if maximum <= minimum:
        return _SPARK_BLOCKS[len(_SPARK_BLOCKS) // 2] * len(samples)
    result = []
    for sample in samples:
        ratio = (sample - minimum) / (maximum - minimum)
        index = min(len(_SPARK_BLOCKS) - 1, int(ratio * (len(_SPARK_BLOCKS) - 1)))
        result.append(_SPARK_BLOCKS[index])
    return "".join(result).rjust(width, "·")


def _render_nonce_map(state: DashboardState, width: int, *, color: bool) -> str:
    statuses: list[str] = []
    for output_index in range(width):
        source_start = output_index * _DEFAULT_BUCKET_COUNT // width
        source_stop = max(source_start + 1, (output_index + 1) * _DEFAULT_BUCKET_COUNT // width)
        completed = any(state.nonce_buckets[source_start:source_stop])
        current = False
        if state.current_range is not None:
            map_start = output_index * _NONCE_LIMIT // width
            map_stop = (output_index + 1) * _NONCE_LIMIT // width
            current = max(state.current_range[0], map_start) < min(state.current_range[1], map_stop)
        if current:
            statuses.append("▓")
        elif completed:
            statuses.append("█")
        else:
            statuses.append("·")
    plain = "".join(statuses)
    if not color:
        return plain
    return "".join(
        _ansi(item, "33") if item == "▓" else _ansi(item, "32") if item == "█" else item
        for item in statuses
    )


def _strategy_explanation(strategy: str) -> str:
    if strategy == "sequential":
        return "Sequential: contiguous parent ranges sweep across the nonce space from low to high."
    if strategy == "orbiting-bit":
        return (
            "Orbiting-bit: observed parent ranges jump through bit-reversal order; "
            "visited buckets scatter across the space."
        )
    return (
        f"{strategy}: visualization reflects observed parent ranges only; "
        "no strategy internals are inspected."
    )


def _top_border(title: str, width: int) -> str:
    label = f" {title} "
    return "┌" + label + "─" * max(0, width - len(label)) + "┐"


def _bottom_border(width: int) -> str:
    return "└" + "─" * width + "┘"


def _rule(title: str, width: int) -> str:
    label = f"[ {title} ]"
    return "├" + label + "─" * max(0, width - len(label)) + "┤"


def _row(text: str, width: int, *, already_colored: bool = False) -> str:
    if already_colored:
        return "│" + text + " " * max(0, width - _visible_length(text)) + "│"
    clipped = text if len(text) <= width else text[: max(0, width - 1)] + "…"
    return "│" + clipped.ljust(width) + "│"


def _visible_length(value: str) -> int:
    return len(re.sub(r"\x1b\[[0-9;]*m", "", value))


def _ansi(text: str, code: str) -> str:
    return f"\x1b[{code}m{text}\x1b[0m"


def _colorize_dashboard(rendered: str) -> str:
    lines: list[str] = []
    for line in rendered.splitlines():
        if "[ " in line and " ]" in line:
            lines.append(_ansi(line, "36"))
        elif "HashOrb Dashboard" in line:
            lines.append(_ansi(line, "36;1"))
        elif "STATUS mining" in line:
            lines.append(line.replace("STATUS mining", _ansi("STATUS mining", "32;1")))
        elif "failed" in line.lower():
            lines.append(_ansi(line, "31"))
        else:
            lines.append(line)
    return "\n".join(lines)
