"""Command-line entry point for Hashphere development operations."""

from __future__ import annotations

import os
import secrets
import signal
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from types import FrameType

from hashphere.compute import (
    ComputeBackendError,
    ComputeBackendSelectionError,
    ComputeBackendValidationError,
    MiningComputeBackend,
    builtin_compute_backend_registry,
    close_compute_backend,
    compute_backend_device_ordinal,
    compute_backend_worker_count,
    deterministic_benchmark_work,
    select_compute_backend,
)
from hashphere.config import DEFAULT_CUDA_DEVICE, MAX_CUDA_DEVICE, Settings
from hashphere.mining import (
    MAX_LIVENESS_SECONDS,
    MAX_RECONNECT_ATTEMPTS,
    MAX_RUNTIME_SECONDS,
    BlockHeaderError,
    ChunkedMiningError,
    ChunkedMiningPlan,
    ChunkedMiningResult,
    CoinbaseError,
    ContinuousMiningError,
    ContinuousMiningOutcome,
    ContinuousMiningPlan,
    ContinuousMiningResult,
    MerkleError,
    MiningJob,
    MiningJobAssembler,
    MiningJobError,
    MiningSearchStrategy,
    MiningWorkProgressionError,
    NonceSearchError,
    NonceSearchMatch,
    NonceSearchResult,
    PreparedMiningWork,
    ReconnectPolicy,
    SearchStrategyError,
    SearchStrategySelectionError,
    SearchStrategyValidationError,
    SessionRecoveryError,
    SessionRecoveryExhaustedError,
    StopController,
    StratumLivenessViolation,
    StratumRecoveryStage,
    StratumRecoveryStatistics,
    StratumSessionRecovery,
    TargetError,
    builtin_search_strategy_registry,
    prepare_mining_work,
    run_chunked_mining,
    run_continuous_mining,
    search_nonce_range,
    select_search_strategy,
    validate_search_strategy_compatibility,
    wait_for_reconnect_delay,
)
from hashphere.network.stratum import (
    MiningNotifyNotification,
    SetDifficultyNotification,
    StratumAuthorizationError,
    StratumClient,
    StratumClientError,
    StratumClientState,
    StratumConnectionError,
    StratumMessageError,
    StratumTransportError,
    SubscribeResult,
)
from hashphere.observability import (
    EventLogError,
    EventSink,
    JsonlEventSink,
    LogSummary,
    LogSummaryError,
    NullEventSink,
    summarize_jsonl,
)
from hashphere.observability.events import EventValue

_LIVE_STRATUM_FLAG = "HASHPHERE_ENABLE_LIVE_STRATUM"
_LIVE_MINING_FLAG = "HASHPHERE_ENABLE_LIVE_MINING"
_STRATUM_USER_AGENT = "Hashphere/0.1"
_NONCE_LIMIT = 1 << 32
_MAX_NONCE = _NONCE_LIMIT - 1
_KNOWN_LOG_COMMANDS = (
    "stratum-handshake",
    "stratum-observe",
    "stratum-mine-once",
    "stratum-mine-chunks",
    "stratum-mine",
)
_USAGE = (
    "Usage: python -m hashphere "
    "{stratum-handshake,stratum-observe,stratum-mine-once,stratum-mine-chunks,"
    "stratum-mine,logs-summary,compute-benchmark} [options]"
)

type _PythonSignalHandler = Callable[[int, FrameType | None], object]
type _PreviousSignalHandler = int | _PythonSignalHandler | None


class _SignalLifecycleError(RuntimeError):
    """Raised when portable stop-signal handlers cannot be managed safely."""


class _StopSignalScope:
    """Install and restore signal handlers that only request cooperative stop."""

    __slots__ = ("_controller", "_previous")

    def __init__(self, controller: StopController) -> None:
        self._controller = controller
        self._previous: list[tuple[signal.Signals, _PreviousSignalHandler]] = []

    def install(self) -> None:
        """Install handlers for the portable shutdown signals on this platform."""

        try:
            for signal_number in _supported_stop_signals():
                previous = signal.getsignal(signal_number)
                signal.signal(signal_number, self._handle_signal)
                self._previous.append((signal_number, previous))
        except (OSError, RuntimeError, ValueError) as exc:
            try:
                self.restore()
            except _SignalLifecycleError:
                pass
            raise _SignalLifecycleError("could not install stop signal handlers") from exc

    def restore(self) -> None:
        """Restore every installed previous handler, attempting all restorations."""

        failed = False
        for signal_number, previous in reversed(self._previous):
            try:
                if previous is None:
                    failed = True
                    continue
                signal.signal(signal_number, previous)
            except (OSError, RuntimeError, ValueError):
                failed = True
        self._previous.clear()
        if failed:
            raise _SignalLifecycleError("could not restore stop signal handlers")

    def _handle_signal(self, signal_number: int, frame: FrameType | None) -> None:
        """Translate a signal into an idempotent stop request without reporting it."""

        del signal_number, frame
        self._controller.request_stop()


def _supported_stop_signals() -> tuple[signal.Signals, ...]:
    """Return Ctrl-C and termination signals available on the current platform."""

    result: list[signal.Signals] = [signal.SIGINT]
    termination = getattr(signal, "SIGTERM", None)
    if isinstance(termination, signal.Signals) and termination not in result:
        result.append(termination)
    return tuple(result)


@dataclass(frozen=True, slots=True)
class _MiningOutcome:
    """Values produced by one bounded live mining session."""

    job: MiningJob
    work: PreparedMiningWork
    result: NonceSearchResult
    pool_accepted: bool | None


class _ChunkedEventObserver:
    """Translate passive chunk-orchestration callbacks into safe events."""

    __slots__ = ("_events",)

    def __init__(self, events: EventSink) -> None:
        self._events = events

    def notification_received(
        self,
        notification: SetDifficultyNotification | MiningNotifyNotification,
    ) -> None:
        """Emit one parsed notification in arrival order."""

        if isinstance(notification, SetDifficultyNotification):
            _emit_difficulty_received(self._events, notification)
        else:
            _emit_mining_job_received(self._events, notification)

    def chunk_started(
        self,
        work: PreparedMiningWork,
        start_nonce: int,
        stop_nonce: int,
    ) -> None:
        """Emit the exact half-open range passed to search."""

        self._events.emit(
            "nonce_range_started",
            fields={
                "job_id": work.job_id,
                "start_nonce": start_nonce,
                "stop_nonce": stop_nonce,
            },
        )

    def chunk_completed(
        self,
        work: PreparedMiningWork,
        result: NonceSearchResult,
    ) -> None:
        """Emit metrics for one completed chunk."""

        self._events.emit(
            "nonce_range_completed",
            fields={
                "job_id": work.job_id,
                "start_nonce": result.start_nonce,
                "stop_nonce": result.stop_nonce,
                "hashes_checked": result.hashes_checked,
                "elapsed_ns": result.elapsed_ns,
                "hashes_per_second": result.hashes_per_second,
                "match_found": result.match is not None,
            },
        )

    def job_replaced(
        self,
        previous_job: MiningJob,
        new_job: MiningJob,
        replacement_index: int,
    ) -> None:
        """Emit safe metadata for one current-work replacement."""

        self._events.emit(
            "mining_job_replaced",
            fields={
                "previous_job_id": previous_job.job_id,
                "new_job_id": new_job.job_id,
                "clean_jobs": new_job.clean_jobs,
                "replacement_index": replacement_index,
            },
        )

    def candidate_found(
        self,
        work: PreparedMiningWork,
        match: NonceSearchMatch,
    ) -> None:
        """Emit safe candidate metadata before submission."""

        self._events.emit(
            "share_candidate_found",
            fields={
                "job_id": work.job_id,
                "nonce": match.nonce,
                "abbreviated_block_hash": _abbreviate_hex(match.block_hash.hex()),
                "meets_share_target": match.meets_share_target,
                "meets_network_target": match.meets_network_target,
            },
        )

    def submission_completed(
        self,
        work: PreparedMiningWork,
        match: NonceSearchMatch,
        accepted: bool,
    ) -> None:
        """Emit the single controlled pool response."""

        self._events.emit(
            "share_submission_completed",
            level="INFO" if accepted else "WARNING",
            fields={
                "job_id": work.job_id,
                "nonce": match.nonce,
                "accepted": accepted,
            },
        )


class _ContinuousEventObserver(_ChunkedEventObserver):
    """Translate continuous lifecycle callbacks into sanitized stable events."""

    __slots__ = ("_settings", "_stop_emitted")

    def __init__(self, events: EventSink, settings: Settings) -> None:
        super().__init__(events)
        self._settings = settings
        self._stop_emitted = False

    def session_authorized(self, subscription: SubscribeResult) -> None:
        """Emit sanitized metadata for every newly authorized session."""

        _emit_stratum_authorized(self._events, self._settings, subscription)

    def connection_lost(
        self,
        stage: StratumRecoveryStage,
        error_category: str,
    ) -> None:
        """Emit one sanitized recoverable connection-loss event."""

        self._events.emit(
            "stratum_connection_lost",
            level="WARNING",
            fields={
                "recovery_stage": stage.value,
                "error_category": error_category,
            },
        )

    def reconnect_scheduled(
        self,
        attempt: int,
        maximum_attempts: int,
        delay_seconds: float,
        stage: StratumRecoveryStage,
    ) -> None:
        """Emit one deterministic scheduled reconnect delay."""

        self._events.emit(
            "stratum_reconnect_scheduled",
            fields={
                "attempt": attempt,
                "maximum_attempts": maximum_attempts,
                "delay_seconds": delay_seconds,
                "recovery_stage": stage.value,
            },
        )

    def reconnect_attempted(
        self,
        attempt: int,
        maximum_attempts: int,
        stage: StratumRecoveryStage,
    ) -> None:
        """Emit one reconnect client-creation attempt."""

        self._events.emit(
            "stratum_reconnect_attempted",
            fields={
                "attempt": attempt,
                "maximum_attempts": maximum_attempts,
                "recovery_stage": stage.value,
            },
        )

    def reconnect_succeeded(
        self,
        attempt: int,
        successful_reconnect_count: int,
        session_index: int,
    ) -> None:
        """Emit success only after fresh authorized usable work exists."""

        self._events.emit(
            "stratum_reconnect_succeeded",
            fields={
                "attempt": attempt,
                "successful_reconnect_count": successful_reconnect_count,
                "session_index": session_index,
            },
        )

    def reconnect_failed(
        self,
        attempt: int,
        maximum_attempts: int,
        stage: StratumRecoveryStage,
        error_category: str,
    ) -> None:
        """Emit one sanitized failed reconnect attempt."""

        self._events.emit(
            "stratum_reconnect_failed",
            level="WARNING",
            fields={
                "attempt": attempt,
                "maximum_attempts": maximum_attempts,
                "recovery_stage": stage.value,
                "error_category": error_category,
            },
        )

    def reconnect_exhausted(
        self,
        attempts: int,
        maximum_attempts: int,
        stage: StratumRecoveryStage,
        error_category: str,
    ) -> None:
        """Emit terminal sanitized reconnect exhaustion metadata."""

        self._events.emit(
            "stratum_reconnect_exhausted",
            level="ERROR",
            fields={
                "attempts": attempts,
                "maximum_attempts": maximum_attempts,
                "recovery_stage": stage.value,
                "error_category": error_category,
            },
        )

    def stop_requested(self) -> None:
        """Emit one controlled stop event without signal details."""

        if self._stop_emitted:
            return
        self._stop_emitted = True
        self._events.emit("mining_stop_requested")

    def session_stale(self, violation: StratumLivenessViolation) -> None:
        """Emit one warning and stale transition with sanitized timing only."""

        fields: dict[str, EventValue] = {
            "reason": violation.reason.value,
            "threshold_seconds": violation.threshold_seconds,
            "elapsed_seconds": violation.elapsed_seconds,
        }
        self._events.emit("stratum_liveness_warning", level="WARNING", fields=fields)
        self._events.emit("stratum_session_stale", level="WARNING", fields=fields)

    def stale_reconnect_started(self, violation: StratumLivenessViolation) -> None:
        """Emit stale-session entry into the shared reconnect owner."""

        self._events.emit(
            "stratum_stale_reconnect_started",
            fields={"reason": violation.reason.value},
        )

    def stale_reconnect_succeeded(self, violation: StratumLivenessViolation) -> None:
        """Emit fresh usable session installation after stale recovery."""

        self._events.emit(
            "stratum_stale_reconnect_succeeded",
            fields={"reason": violation.reason.value},
        )

    def stale_reconnect_failed(self, violation: StratumLivenessViolation) -> None:
        """Emit stale recovery failure without raw errors."""

        self._events.emit(
            "stratum_stale_reconnect_failed",
            level="ERROR",
            fields={"reason": violation.reason.value},
        )

    def nonce_space_exhausted(self, work: PreparedMiningWork) -> None:
        """Emit safe metadata when one prepared work exhausts its nonce space."""

        self._events.emit(
            "nonce_space_exhausted",
            fields={"job_id": work.job_id},
        )

    def waiting_for_job(self, work: PreparedMiningWork) -> None:
        """Emit safe metadata when waiting for a newer mining job."""

        self._events.emit(
            "mining_waiting_for_job",
            fields={"job_id": work.job_id},
        )

    def work_advanced(
        self,
        reason: str,
        work_variant_index: int,
        extra_nonce_2_advance_count: int,
        network_time_roll_count: int,
    ) -> None:
        """Emit safe counters when a prepared variant reaches its first search."""

        self._events.emit(
            "mining_work_advanced",
            fields={
                "reason": reason,
                "work_variant_index": work_variant_index,
                "extra_nonce_2_advance_count": extra_nonce_2_advance_count,
                "network_time_roll_count": network_time_roll_count,
            },
        )

    def extra_nonce_2_cycle_completed(self, cycle_count: int) -> None:
        """Emit a safe cumulative extra-nonce cycle count."""

        self._events.emit(
            "extra_nonce_2_cycle_completed",
            fields={"cycle_count": cycle_count},
        )

    def network_time_rolled(self, roll_count: int) -> None:
        """Emit a safe cumulative network-time roll count."""

        self._events.emit(
            "network_time_rolled",
            fields={"roll_count": roll_count},
        )

    def duplicate_work_ignored(self, duplicate_count: int, reason: str) -> None:
        """Emit safe metadata for one ignored duplicate pool context."""

        self._events.emit(
            "duplicate_work_ignored",
            fields={"duplicate_count": duplicate_count, "reason": reason},
        )


def main(argv: Sequence[str] | None = None) -> int:
    """Run the selected Hashphere command and return its process status."""

    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == "compute-benchmark":
        try:
            backend_name, worker_count, device_ordinal, start_nonce, stop_nonce = (
                _parse_compute_benchmark_arguments(arguments[1:])
            )
        except ValueError as exc:
            print(f"Argument error: {exc}", file=sys.stderr)
            print(_USAGE, file=sys.stderr)
            return 2
        return _run_compute_benchmark(
            backend_name,
            worker_count,
            device_ordinal,
            start_nonce,
            stop_nonce,
        )
    if arguments and arguments[0] == "logs-summary":
        try:
            summary_log_file = _parse_summary_command_arguments(arguments[1:])
        except ValueError as exc:
            print(f"Argument error: {exc}", file=sys.stderr)
            print(_USAGE, file=sys.stderr)
            return 2
        return _run_log_summary(summary_log_file)
    if arguments and arguments[0] == "stratum-handshake":
        try:
            log_file = _parse_log_file(arguments[1:])
        except ValueError:
            print(_USAGE, file=sys.stderr)
            return 2
        return _run_with_event_sink(
            "stratum-handshake",
            log_file,
            _run_stratum_handshake,
        )
    if arguments and arguments[0] == "stratum-observe":
        try:
            log_file = _parse_log_file(arguments[1:])
        except ValueError:
            print(_USAGE, file=sys.stderr)
            return 2
        return _run_with_event_sink(
            "stratum-observe",
            log_file,
            _run_stratum_observer,
        )
    if arguments and arguments[0] == "stratum-mine-once":
        try:
            start_nonce, stop_nonce, log_file = _parse_mining_command_arguments(arguments[1:])
        except ValueError as exc:
            print(f"Argument error: {exc}", file=sys.stderr)
            print(_USAGE, file=sys.stderr)
            return 2
        return _run_with_event_sink(
            "stratum-mine-once",
            log_file,
            lambda events: _run_stratum_mine_once(
                start_nonce,
                stop_nonce,
                events,
            ),
        )
    if arguments and arguments[0] == "stratum-mine-chunks":
        try:
            plan, log_file = _parse_chunked_mining_arguments(arguments[1:])
        except ValueError as exc:
            print(f"Argument error: {exc}", file=sys.stderr)
            print(_USAGE, file=sys.stderr)
            return 2
        return _run_with_event_sink(
            "stratum-mine-chunks",
            log_file,
            lambda events: _run_stratum_mine_chunks(plan, events),
        )
    if arguments and arguments[0] == "stratum-mine":
        try:
            continuous_plan, reconnect_policy, log_file = _parse_continuous_mining_arguments(
                arguments[1:]
            )
        except ValueError as exc:
            print(f"Argument error: {exc}", file=sys.stderr)
            print(_USAGE, file=sys.stderr)
            return 2
        return _run_with_event_sink(
            "stratum-mine",
            log_file,
            lambda events: _run_stratum_mine(
                continuous_plan,
                reconnect_policy,
                events,
            ),
            started_fields={
                **(
                    {"max_server_silence_seconds": continuous_plan.max_server_silence_seconds}
                    if continuous_plan.max_server_silence_seconds is not None
                    else {}
                ),
                **(
                    {"max_job_age_seconds": continuous_plan.max_job_age_seconds}
                    if continuous_plan.max_job_age_seconds is not None
                    else {}
                ),
            },
        )

    print(_USAGE, file=sys.stderr)
    return 2


def _run_compute_benchmark(
    backend_name: str,
    worker_count: int | None,
    device_ordinal: int | None,
    start_nonce: int,
    stop_nonce: int,
) -> int:
    """Run one offline synthetic range through an explicitly selected backend."""

    try:
        backend = _select_benchmark_compute_backend(
            backend_name,
            worker_count,
            device_ordinal,
        )
    except (ComputeBackendSelectionError, ComputeBackendValidationError):
        print("Compute benchmark backend is unavailable or invalid.", file=sys.stderr)
        return 2

    result: NonceSearchResult | None = None
    status = 0
    try:
        result = backend.search_nonce_range(
            deterministic_benchmark_work(),
            start_nonce,
            stop_nonce,
        )
    except ComputeBackendError:
        print("Compute benchmark failed.", file=sys.stderr)
        status = 1
    finally:
        try:
            close_compute_backend(backend)
        except ComputeBackendError:
            if status == 0:
                print("Compute benchmark cleanup failed.", file=sys.stderr)
                status = 1

    if status != 0:
        return status
    if result is None:
        raise RuntimeError("compute benchmark completed without a result")
    _print_compute_benchmark(backend, result)
    return 0


def _select_benchmark_compute_backend(
    backend_name: str,
    worker_count: int | None,
    device_ordinal: int | None,
) -> MiningComputeBackend:
    """Select one offline backend without loading runtime configuration."""

    registry = builtin_compute_backend_registry(
        python_searcher=search_nonce_range,
        worker_count=worker_count if worker_count is not None else 2,
        cuda_device=(device_ordinal if device_ordinal is not None else DEFAULT_CUDA_DEVICE),
        initialize_cuda=backend_name == "cuda",
    )
    return select_compute_backend(backend_name, registry)


def _print_compute_benchmark(
    backend: MiningComputeBackend,
    result: NonceSearchResult,
) -> None:
    """Print stable aggregate benchmark data without candidate or fixture values."""

    capabilities = backend.capabilities
    rate = result.hashes_per_second
    print("Hashphere compute benchmark completed.")
    print(f"Backend: {capabilities.backend_name}")
    print(f"Implementation: {capabilities.implementation}")
    worker_count = compute_backend_worker_count(backend)
    if worker_count is not None:
        print(f"Workers: {worker_count}")
    device_ordinal = compute_backend_device_ordinal(backend)
    if device_ordinal is not None:
        print(f"CUDA device: {device_ordinal}")
    print(f"Hashes checked: {result.hashes_checked}")
    print(f"Elapsed time: {result.elapsed_ns} ns")
    print(f"Hashes per second: {'unavailable' if rate is None else f'{rate:.2f}'}")
    print(f"Result: {'candidate found' if result.match is not None else 'range exhausted'}")


def _run_log_summary(log_file: str) -> int:
    """Print one sanitized aggregate summary without modifying its source log."""

    try:
        summary = summarize_jsonl(log_file)
    except LogSummaryError as exc:
        print(f"Log summary failed: {exc}", file=sys.stderr)
        return 1

    _print_log_summary(log_file, summary)
    return 0


def _print_log_summary(log_file: str, summary: LogSummary) -> None:
    """Render one stable human-readable aggregate summary."""

    first_timestamp = summary.first_timestamp or "unavailable"
    last_timestamp = summary.last_timestamp or "unavailable"
    print("Hashphere log summary.")
    print(f"Log file: {log_file}")
    print(f"Records: {summary.record_count}")
    print(f"Runs: {summary.run_count}")
    print(f"Completed runs: {summary.completed_run_count}")
    print(f"Failed runs: {summary.failed_run_count}")
    print(f"Incomplete runs: {summary.incomplete_run_count}")
    print(f"First event: {first_timestamp}")
    print(f"Last event: {last_timestamp}")

    command_counts = dict(summary.command_counts)
    print("\nCommands:")
    for command in _KNOWN_LOG_COMMANDS:
        print(f"  {command}: {command_counts.pop(command, 0)}")
    for command, count in sorted(command_counts.items()):
        print(f"  {command}: {count}")

    if summary.compute_backend_counts:
        print("\nCompute backends:")
        for backend_name, count in summary.compute_backend_counts:
            print(f"  {backend_name}: {count}")

    if summary.search_strategy_counts:
        print("\nSearch strategies:")
        for strategy_name, count in summary.search_strategy_counts:
            print(f"  {strategy_name}: {count}")

    if summary.completion_outcome_counts:
        print("\nCompletion outcomes:")
        for outcome, count in summary.completion_outcome_counts:
            print(f"  {outcome}: {count}")

    weighted_rate = (
        "unavailable"
        if summary.weighted_hashes_per_second is None
        else f"{summary.weighted_hashes_per_second:.2f}"
    )
    print("\nMining:")
    print(f"  Difficulty events: {summary.difficulty_event_count}")
    print(f"  Jobs received: {summary.mining_job_event_count}")
    print(f"  Work variants searched: {summary.work_variant_count}")
    print(f"  Extra nonce 2 advances: {summary.extra_nonce_2_advance_count}")
    print(f"  Extra nonce 2 cycles: {summary.extra_nonce_2_cycle_count}")
    print(f"  Network-time rolls: {summary.network_time_roll_count}")
    print(f"  Duplicate work ignored: {summary.duplicate_work_ignored_count}")
    print(f"  Connection losses: {summary.connection_loss_count}")
    print(f"  Reconnect attempts: {summary.reconnect_attempt_count}")
    print(f"  Reconnect successes: {summary.reconnect_success_count}")
    print(f"  Reconnect failures: {summary.reconnect_failure_count}")
    print(f"  Reconnect exhausted events: {summary.reconnect_exhausted_count}")
    print(f"  Liveness warnings: {summary.liveness_warning_count}")
    print(f"  Stale sessions: {summary.stale_session_count}")
    print(f"  Stale reconnect starts: {summary.stale_reconnect_started_count}")
    print(f"  Stale reconnect successes: {summary.stale_reconnect_success_count}")
    print(f"  Stale reconnect failures: {summary.stale_reconnect_failure_count}")
    for reason, count in summary.stale_reason_counts:
        print(f"  Stale reason {reason}: {count}")
    for limit, count in summary.configured_server_silence_limits:
        print(f"  Configured server silence {limit:g} seconds: {count}")
    for limit, count in summary.configured_job_age_limits:
        print(f"  Configured job age {limit:g} seconds: {count}")
    print(f"  Nonce ranges completed: {summary.completed_nonce_range_count}")
    print(f"  Hashes checked: {summary.total_hashes_checked}")
    print(f"  Mining elapsed: {summary.total_mining_elapsed_ns} ns")
    print(f"  Weighted hashes per second: {weighted_rate}")
    print(f"  Share candidates: {summary.share_candidate_count}")
    print(f"  Shares submitted: {summary.share_submission_count}")
    print(f"  Shares accepted: {summary.accepted_share_count}")
    print(f"  Shares rejected: {summary.rejected_share_count}")

    print("\nFailures:")
    print(f"  command_failed events: {summary.command_failure_count}")
    for stage, category, count in summary.failure_stage_category_counts:
        print(f"  {stage}/{category}: {count}")


def _run_with_event_sink(
    command: str,
    log_file: str | None,
    operation: Callable[[EventSink], int],
    *,
    started_fields: Mapping[str, EventValue] | None = None,
) -> int:
    """Run one command with an initialized sink and deterministic cleanup."""

    try:
        events: EventSink = (
            NullEventSink() if log_file is None else JsonlEventSink(log_file, command)
        )
    except (EventLogError, TypeError, ValueError):
        print("Could not initialize structured event logging.", file=sys.stderr)
        return 2

    status = 1
    raised = False
    try:
        events.emit("command_started", fields=started_fields)
        status = operation(events)
    except EventLogError:
        print("Structured event logging failed.", file=sys.stderr)
        status = 1
    except BaseException:
        raised = True
        raise
    finally:
        try:
            events.close()
        except EventLogError:
            if not raised and status == 0:
                print("Could not close structured event logging cleanly.", file=sys.stderr)
                status = 1

    return status


def _run_stratum_handshake(events: EventSink) -> int:
    """Run one explicitly enabled live Stratum handshake."""

    settings = _load_live_settings("handshake")
    if settings is None:
        _emit_command_failed(events, "configuration", "ConfigurationOrOptInError")
        return 2

    client: StratumClient | None = None
    try:
        client = StratumClient(settings, _STRATUM_USER_AGENT)
        result = client.handshake()
        final_state = client.state
        _emit_stratum_authorized(events, settings, result)
    except StratumAuthorizationError as exc:
        _emit_command_failed(events, "handshake", _error_category(exc))
        print("Stratum authorization failed.", file=sys.stderr)
        return 1
    except StratumConnectionError as exc:
        _emit_command_failed(events, "handshake", _error_category(exc))
        print("Could not connect to the configured Stratum endpoint.", file=sys.stderr)
        return 1
    except (StratumTransportError, StratumMessageError, StratumClientError) as exc:
        _emit_command_failed(events, "handshake", _error_category(exc))
        print("Stratum protocol handshake failed.", file=sys.stderr)
        return 1
    except (TypeError, ValueError) as exc:
        _emit_command_failed(events, "configuration", _error_category(exc))
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    finally:
        if client is not None:
            client.close()

    events.emit("command_completed", fields={"outcome": "handshake_succeeded"})
    _print_success(settings, result.extra_nonce_1, result.extra_nonce_2_size, final_state)
    return 0


def _run_stratum_observer(events: EventSink) -> int:
    """Handshake and observe one difficulty and one mining job notification."""

    settings = _load_live_settings("notification observation")
    if settings is None:
        _emit_command_failed(events, "configuration", "ConfigurationOrOptInError")
        return 2

    client: StratumClient | None = None
    try:
        client = StratumClient(settings, _STRATUM_USER_AGENT)
        subscription = client.handshake()
        _emit_stratum_authorized(events, settings, subscription)
        difficulty, job, arrival_order = _observe_required_notifications(client, events)
        final_state = client.state
    except StratumAuthorizationError as exc:
        _emit_command_failed(events, "handshake", _error_category(exc))
        print("Stratum authorization failed.", file=sys.stderr)
        return 1
    except StratumConnectionError as exc:
        _emit_command_failed(events, "handshake", _error_category(exc))
        print("Could not connect to the configured Stratum endpoint.", file=sys.stderr)
        return 1
    except (StratumTransportError, StratumMessageError, StratumClientError) as exc:
        _emit_command_failed(events, "notification_observation", _error_category(exc))
        print("Stratum notification observation failed.", file=sys.stderr)
        return 1
    except (TypeError, ValueError) as exc:
        _emit_command_failed(events, "configuration", _error_category(exc))
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    finally:
        if client is not None:
            client.close()

    events.emit("command_completed", fields={"outcome": "observation_succeeded"})
    _print_observation_success(
        settings,
        subscription.extra_nonce_1,
        subscription.extra_nonce_2_size,
        difficulty,
        job,
        arrival_order,
        final_state,
    )
    return 0


def _run_stratum_mine_once(
    start_nonce: int,
    stop_nonce: int,
    events: EventSink,
) -> int:
    """Run one explicitly enabled, bounded live Stratum mining range."""

    settings = _load_live_mining_settings()
    if settings is None:
        _emit_command_failed(events, "configuration", "ConfigurationOrOptInError")
        return 2
    selection = _select_live_mining_components(settings, events)
    if selection is None:
        return 2
    backend, strategy = selection

    client: StratumClient | None = None
    outcome: _MiningOutcome | None = None
    status = 0
    pending_failure: BaseException | None = None
    try:
        client = StratumClient(settings, _STRATUM_USER_AGENT)
        subscription = client.handshake()
        _emit_stratum_authorized(events, settings, subscription)
        outcome = _mine_one_range(
            client,
            subscription,
            start_nonce,
            stop_nonce,
            events,
            backend,
        )
    except StratumAuthorizationError as exc:
        pending_failure = exc
        print("Stratum authorization failed.", file=sys.stderr)
        status = 1
        _emit_command_failed(events, "handshake", _error_category(exc))
    except StratumConnectionError as exc:
        pending_failure = exc
        print("Could not connect to the configured Stratum endpoint.", file=sys.stderr)
        status = 1
        _emit_command_failed(events, "handshake", _error_category(exc))
    except (
        StratumTransportError,
        StratumMessageError,
        StratumClientError,
        MiningJobError,
        CoinbaseError,
        MerkleError,
        BlockHeaderError,
        TargetError,
        NonceSearchError,
        ComputeBackendError,
        SearchStrategyError,
        TypeError,
        ValueError,
    ) as exc:
        pending_failure = exc
        print("Bounded Stratum mining failed.", file=sys.stderr)
        status = 1
        _emit_command_failed(events, "bounded_mining", _error_category(exc))
    except BaseException as exc:
        pending_failure = exc
        try:
            _emit_command_failed(events, "bounded_mining", "UnexpectedError")
        except EventLogError:
            pass
        raise
    finally:
        if client is not None:
            try:
                client.close()
            except BaseException:
                if pending_failure is None:
                    print("Could not close the Stratum connection cleanly.", file=sys.stderr)
                    status = 1
                    _emit_command_failed(events, "cleanup", "ClientCloseError")
        try:
            close_compute_backend(backend)
        except ComputeBackendError:
            if pending_failure is None and status == 0:
                print("Could not close the compute backend cleanly.", file=sys.stderr)
                status = 1
                _emit_command_failed(events, "cleanup", "ComputeBackendCloseError")

    if status != 0:
        return status
    if outcome is None:
        raise RuntimeError("bounded mining completed without an outcome")

    if outcome.result.match is None:
        completion_level = "INFO"
        completion_outcome = "range_exhausted"
    elif outcome.pool_accepted:
        completion_level = "INFO"
        completion_outcome = "share_accepted"
    else:
        completion_level = "WARNING"
        completion_outcome = "share_rejected"
    events.emit(
        "command_completed",
        level=completion_level,
        fields={"outcome": completion_outcome},
    )
    _print_mining_outcome(
        settings,
        backend.capabilities.backend_name,
        compute_backend_worker_count(backend),
        strategy.capabilities.strategy_name,
        start_nonce,
        stop_nonce,
        outcome,
    )
    return 0


def _run_stratum_mine_chunks(
    plan: ChunkedMiningPlan,
    events: EventSink,
) -> int:
    """Run one explicitly enabled finite multi-chunk mining invocation."""

    settings = _load_live_mining_settings()
    if settings is None:
        _emit_command_failed(events, "configuration", "ConfigurationOrOptInError")
        return 2
    selection = _select_live_mining_components(settings, events)
    if selection is None:
        return 2
    backend, strategy = selection

    client: StratumClient | None = None
    result: ChunkedMiningResult | None = None
    status = 0
    pending_failure: BaseException | None = None
    try:
        client = StratumClient(settings, _STRATUM_USER_AGENT)
        subscription = client.handshake()
        _emit_stratum_authorized(events, settings, subscription)
        assembler = MiningJobAssembler(subscription)
        initial_job = _receive_buildable_job(client, assembler, events)
        extra_nonce_2 = _generate_extra_nonce_2(subscription.extra_nonce_2_size)
        observer = _ChunkedEventObserver(events)
        result = run_chunked_mining(
            plan,
            assembler,
            initial_job,
            extra_nonce_2,
            poll_notification=lambda: client.poll_notification(timeout_seconds=0.0),
            submit_share=lambda work, match: client.submit_share(
                work.job_id,
                work.extra_nonce_2,
                work.network_time,
                match.nonce,
            ),
            observer=observer,
            strategy=strategy,
            prepare_work=prepare_mining_work,
            search_range=backend.search_nonce_range,
        )
    except StratumAuthorizationError as exc:
        pending_failure = exc
        print("Stratum authorization failed.", file=sys.stderr)
        status = 1
        _emit_command_failed(events, "handshake", _error_category(exc))
    except StratumConnectionError as exc:
        pending_failure = exc
        print("Could not connect to the configured Stratum endpoint.", file=sys.stderr)
        status = 1
        _emit_command_failed(events, "handshake", _error_category(exc))
    except (
        StratumTransportError,
        StratumMessageError,
        StratumClientError,
        MiningJobError,
        CoinbaseError,
        MerkleError,
        BlockHeaderError,
        TargetError,
        NonceSearchError,
        ComputeBackendError,
        SearchStrategyError,
        ChunkedMiningError,
        TypeError,
        ValueError,
    ) as exc:
        pending_failure = exc
        print("Chunked Stratum mining failed.", file=sys.stderr)
        status = 1
        _emit_command_failed(events, "chunked_mining", _error_category(exc))
    except BaseException as exc:
        pending_failure = exc
        try:
            _emit_command_failed(events, "chunked_mining", "UnexpectedError")
        except EventLogError:
            pass
        raise
    finally:
        if client is not None:
            try:
                client.close()
            except BaseException:
                if pending_failure is None:
                    print("Could not close the Stratum connection cleanly.", file=sys.stderr)
                    status = 1
                    _emit_command_failed(events, "cleanup", "ClientCloseError")
        try:
            close_compute_backend(backend)
        except ComputeBackendError:
            if pending_failure is None and status == 0:
                print("Could not close the compute backend cleanly.", file=sys.stderr)
                status = 1
                _emit_command_failed(events, "cleanup", "ComputeBackendCloseError")

    if status != 0:
        return status
    if result is None:
        raise RuntimeError("chunked mining completed without an outcome")

    if result.match is None:
        completion_level = "INFO"
        completion_outcome = "hash_budget_exhausted"
    elif result.pool_accepted:
        completion_level = "INFO"
        completion_outcome = "share_accepted"
    else:
        completion_level = "WARNING"
        completion_outcome = "share_rejected"
    events.emit(
        "command_completed",
        level=completion_level,
        fields={"outcome": completion_outcome},
    )
    _print_chunked_mining_outcome(
        settings,
        backend.capabilities.backend_name,
        compute_backend_worker_count(backend),
        strategy.capabilities.strategy_name,
        plan,
        result,
    )
    return 0


def _run_stratum_mine(
    plan: ContinuousMiningPlan,
    reconnect_policy: ReconnectPolicy,
    events: EventSink,
) -> int:
    """Run one explicitly enabled continuous Stratum mining session."""

    settings = _load_live_mining_settings()
    if settings is None:
        _emit_command_failed(events, "configuration", "ConfigurationOrOptInError")
        return 2
    selection = _select_live_mining_components(settings, events)
    if selection is None:
        return 2
    backend, strategy = selection

    controller = StopController(plan.max_runtime_seconds)
    signal_scope = _StopSignalScope(controller)
    observer = _ContinuousEventObserver(events, settings)
    recovery: StratumSessionRecovery | None = None
    subscription: SubscribeResult | None = None
    result: ContinuousMiningResult | None = None
    stopped_before_work = False
    status = 0
    pending_failure: BaseException | None = None
    cleanup_failure_reported = False
    try:
        signal_scope.install()
        recovery = StratumSessionRecovery(
            reconnect_policy,
            controller,
            client_factory=lambda: StratumClient(settings, _STRATUM_USER_AGENT),
            seed_factory=_generate_extra_nonce_2,
            observer=observer,
            backoff_waiter=wait_for_reconnect_delay,
            server_silence_seconds=plan.max_server_silence_seconds,
        )
        session = recovery.establish_initial_session()
        if session is None:
            stopped_before_work = True
            if not controller.runtime_limit_reached:
                observer.stop_requested()
        else:
            subscription = session.subscription
            result = run_continuous_mining(
                plan,
                session.assembler,
                session.initial_job,
                session.extra_nonce_2_seed,
                controller,
                receive_notification=session.receive_notification,
                submit_share=lambda work, match: session.submit_share(
                    work.job_id,
                    work.extra_nonce_2,
                    work.network_time,
                    match.nonce,
                ),
                observer=observer,
                strategy=strategy,
                prepare_work=prepare_mining_work,
                search_range=backend.search_nonce_range,
                recover_session=recovery.recover_session,
                recover_stale_session=recovery.recover_stale_session,
                recovery_statistics=lambda: recovery.statistics,
            )
            if recovery.current_session is not None:
                subscription = recovery.current_session.subscription
    except StratumAuthorizationError as exc:
        pending_failure = exc
        print("Stratum authorization failed.", file=sys.stderr)
        status = 1
        _emit_command_failed(events, "handshake", _error_category(exc))
    except StratumConnectionError as exc:
        pending_failure = exc
        print("Could not connect to the configured Stratum endpoint.", file=sys.stderr)
        status = 1
        _emit_command_failed(events, "continuous_mining", _error_category(exc))
    except SessionRecoveryExhaustedError as exc:
        pending_failure = exc
        print("Stratum reconnect attempts exhausted.", file=sys.stderr)
        status = 1
        _emit_command_failed(
            events,
            "session_recovery",
            _error_category(exc),
            attempts=exc.attempts,
            recovery_stage=exc.recovery_stage.value,
        )
    except (
        StratumTransportError,
        StratumMessageError,
        StratumClientError,
        MiningJobError,
        CoinbaseError,
        MerkleError,
        BlockHeaderError,
        TargetError,
        NonceSearchError,
        ComputeBackendError,
        SearchStrategyError,
        MiningWorkProgressionError,
        SessionRecoveryError,
        ContinuousMiningError,
        _SignalLifecycleError,
        OSError,
        TypeError,
        ValueError,
    ) as exc:
        pending_failure = exc
        print("Continuous Stratum mining failed.", file=sys.stderr)
        status = 1
        _emit_command_failed(events, "continuous_mining", _error_category(exc))
    except Exception as exc:
        pending_failure = exc
        print("Continuous Stratum mining failed.", file=sys.stderr)
        status = 1
        _emit_command_failed(events, "continuous_mining", "UnexpectedError")
    except BaseException as exc:
        pending_failure = exc
        try:
            _emit_command_failed(events, "continuous_mining", "UnexpectedError")
        except EventLogError:
            pass
        raise
    finally:
        if recovery is not None:
            try:
                recovery.close()
            except BaseException:
                if pending_failure is None and status == 0:
                    print("Could not close the Stratum connection cleanly.", file=sys.stderr)
                    status = 1
                    cleanup_failure_reported = True
                    _emit_command_failed(events, "cleanup", "ClientCloseError")
        try:
            signal_scope.restore()
        except _SignalLifecycleError:
            if pending_failure is None and status == 0 and not cleanup_failure_reported:
                print("Could not restore signal handlers cleanly.", file=sys.stderr)
                status = 1
                _emit_command_failed(events, "cleanup", "SignalRestoreError")
        try:
            close_compute_backend(backend)
        except ComputeBackendError:
            if pending_failure is None and status == 0 and not cleanup_failure_reported:
                print("Could not close the compute backend cleanly.", file=sys.stderr)
                status = 1
                cleanup_failure_reported = True
                _emit_command_failed(events, "cleanup", "ComputeBackendCloseError")

    if status != 0:
        return status
    if stopped_before_work:
        outcome = (
            ContinuousMiningOutcome.RUNTIME_LIMIT_REACHED
            if controller.runtime_limit_reached
            else ContinuousMiningOutcome.STOPPED_BY_USER
        )
    elif result is not None:
        outcome = result.outcome
    else:
        raise RuntimeError("continuous mining completed without an outcome")

    completion_level = "WARNING" if outcome is ContinuousMiningOutcome.SHARE_REJECTED else "INFO"
    completion_fields: dict[str, EventValue] = {"outcome": outcome.value}
    if plan.max_server_silence_seconds is not None:
        completion_fields["max_server_silence_seconds"] = plan.max_server_silence_seconds
    if plan.max_job_age_seconds is not None:
        completion_fields["max_job_age_seconds"] = plan.max_job_age_seconds
    events.emit(
        "command_completed",
        level=completion_level,
        fields=completion_fields,
    )
    _print_continuous_mining_outcome(
        settings,
        backend.capabilities.backend_name,
        compute_backend_worker_count(backend),
        strategy.capabilities.strategy_name,
        subscription,
        plan,
        reconnect_policy,
        recovery.statistics if recovery is not None else StratumRecoveryStatistics(0, 0, 0, 0),
        outcome,
        result,
    )
    return 0


def _load_live_settings(operation: str) -> Settings | None:
    """Load settings and enforce the shared explicit live-network opt-in."""

    try:
        settings = Settings.from_env()
    except (TypeError, ValueError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return None

    if os.getenv(_LIVE_STRATUM_FLAG) != "1":
        print(
            f"Live Stratum {operation} disabled; set {_LIVE_STRATUM_FLAG}=1 to enable it.",
            file=sys.stderr,
        )
        return None

    return settings


def _load_live_mining_settings() -> Settings | None:
    """Load settings and enforce both explicit live-mining opt-ins."""

    settings = _load_live_settings("mining")
    if settings is None:
        return None
    if os.getenv(_LIVE_MINING_FLAG) != "1":
        print(
            f"Live Stratum mining disabled; set {_LIVE_MINING_FLAG}=1 to enable it.",
            file=sys.stderr,
        )
        return None
    return settings


def _select_configured_compute_backend(settings: Settings) -> MiningComputeBackend:
    """Select one invocation-local backend without probing hardware."""

    registry = builtin_compute_backend_registry(
        python_searcher=search_nonce_range,
        worker_count=settings.compute_workers,
        cuda_device=settings.cuda_device,
        initialize_cuda=settings.compute_backend == "cuda",
    )
    return select_compute_backend(settings.compute_backend, registry)


def _select_configured_search_strategy(settings: Settings) -> MiningSearchStrategy:
    """Select one invocation-local search strategy without dynamic loading."""

    return select_search_strategy(
        settings.search_strategy,
        builtin_search_strategy_registry(),
    )


def _select_live_mining_components(
    settings: Settings,
    events: EventSink,
) -> tuple[MiningComputeBackend, MiningSearchStrategy] | None:
    """Select a compatible backend and strategy before live networking."""

    try:
        backend = _select_configured_compute_backend(settings)
    except (ComputeBackendSelectionError, ComputeBackendValidationError) as exc:
        print("Compute backend configuration is invalid.", file=sys.stderr)
        _emit_command_failed(events, "configuration", _error_category(exc))
        return None
    try:
        strategy = _select_configured_search_strategy(settings)
        validate_search_strategy_compatibility(strategy, backend.capabilities)
    except (
        SearchStrategySelectionError,
        SearchStrategyValidationError,
        SearchStrategyError,
    ) as exc:
        try:
            close_compute_backend(backend)
        except ComputeBackendError:
            pass
        print("Search strategy configuration is invalid.", file=sys.stderr)
        _emit_command_failed(events, "configuration", _error_category(exc))
        return None
    _emit_compute_backend_selected(events, backend)
    _emit_search_strategy_selected(events, strategy)
    return backend, strategy


def _parse_log_file(arguments: Sequence[str]) -> str | None:
    """Parse the optional structured-log path for a non-mining live command."""

    if not arguments:
        return None
    if len(arguments) != 2 or arguments[0] != "--log-file":
        raise ValueError("unsupported live-command argument")
    return _validate_log_file_path(arguments[1])


def _parse_summary_command_arguments(arguments: Sequence[str]) -> str:
    """Parse the one required read-only log-summary path."""

    if len(arguments) == 2 and arguments[1].startswith("--"):
        raise ValueError("--log-file requires a value")
    option_values = _parse_option_values(
        arguments,
        {"--log-file"},
        unsupported_message="unsupported logs-summary argument",
    )
    if "--log-file" not in option_values:
        raise ValueError("--log-file is required")
    return _validate_log_file_path(option_values["--log-file"])


def _parse_compute_benchmark_arguments(
    arguments: Sequence[str],
) -> tuple[str, int | None, int | None, int, int]:
    """Parse strict offline backend and half-open benchmark-range options."""

    option_values = _parse_option_values(
        arguments,
        {"--backend", "--workers", "--device", "--start-nonce", "--hash-count"},
        unsupported_message="unsupported compute-benchmark argument",
    )
    if "--backend" not in option_values:
        raise ValueError("--backend is required")
    if "--hash-count" not in option_values:
        raise ValueError("--hash-count is required")
    backend_name = option_values["--backend"]
    if backend_name not in {"cuda", "python", "native", "native-parallel"}:
        raise ValueError("--backend must be cuda, python, native, or native-parallel")
    workers_text = option_values.get("--workers")
    if workers_text is not None and backend_name != "native-parallel":
        raise ValueError("--workers is valid only for native-parallel")
    worker_count = (
        _parse_unpadded_decimal_option(
            "--workers",
            workers_text if workers_text is not None else "2",
            minimum=1,
            maximum=256,
        )
        if backend_name == "native-parallel"
        else None
    )
    device_text = option_values.get("--device")
    if device_text is not None and backend_name != "cuda":
        raise ValueError("--device is valid only for cuda")
    device_ordinal = (
        _parse_unpadded_decimal_option(
            "--device",
            device_text if device_text is not None else str(DEFAULT_CUDA_DEVICE),
            minimum=0,
            maximum=MAX_CUDA_DEVICE,
        )
        if backend_name == "cuda"
        else None
    )
    start_nonce = _parse_unpadded_decimal_option(
        "--start-nonce",
        option_values.get("--start-nonce", "0"),
        minimum=0,
        maximum=_MAX_NONCE,
    )
    hash_count = _parse_unpadded_decimal_option(
        "--hash-count",
        option_values["--hash-count"],
        minimum=1,
        maximum=_NONCE_LIMIT,
    )
    stop_nonce = start_nonce + hash_count
    if stop_nonce > _NONCE_LIMIT:
        raise ValueError("the requested benchmark range exceeds 2**32")
    return backend_name, worker_count, device_ordinal, start_nonce, stop_nonce


def _parse_mining_command_arguments(
    arguments: Sequence[str],
) -> tuple[int, int, str | None]:
    """Parse bounded mining and optional structured-log arguments."""

    option_values = _parse_option_values(
        arguments,
        {"--start-nonce", "--hash-count", "--log-file"},
        unsupported_message="unsupported stratum-mine-once argument",
    )
    log_file = (
        _validate_log_file_path(option_values["--log-file"])
        if "--log-file" in option_values
        else None
    )
    start_nonce, stop_nonce = _mining_range_from_options(option_values)
    return start_nonce, stop_nonce, log_file


def _parse_chunked_mining_arguments(
    arguments: Sequence[str],
) -> tuple[ChunkedMiningPlan, str | None]:
    """Parse one finite chunk plan and optional structured-log path."""

    option_values = _parse_option_values(
        arguments,
        {"--start-nonce", "--chunk-size", "--max-hashes", "--log-file"},
        unsupported_message="unsupported stratum-mine-chunks argument",
    )
    log_file = (
        _validate_log_file_path(option_values["--log-file"])
        if "--log-file" in option_values
        else None
    )
    return _chunked_plan_from_options(option_values), log_file


def _parse_chunked_mining_plan(arguments: Sequence[str]) -> ChunkedMiningPlan:
    """Parse strict decimal chunk options into a finite plan."""

    option_values = _parse_option_values(
        arguments,
        {"--start-nonce", "--chunk-size", "--max-hashes"},
        unsupported_message="unsupported stratum-mine-chunks argument",
    )
    return _chunked_plan_from_options(option_values)


def _parse_continuous_mining_arguments(
    arguments: Sequence[str],
) -> tuple[ContinuousMiningPlan, ReconnectPolicy, str | None]:
    """Parse continuous mining options and an optional structured-log path."""

    option_values = _parse_option_values(
        arguments,
        {
            "--start-nonce",
            "--chunk-size",
            "--max-chunks",
            "--max-reconnect-attempts",
            "--max-runtime-seconds",
            "--max-server-silence-seconds",
            "--max-job-age-seconds",
            "--log-file",
        },
        unsupported_message="unsupported stratum-mine argument",
    )
    log_file = (
        _validate_log_file_path(option_values["--log-file"])
        if "--log-file" in option_values
        else None
    )
    return (
        _continuous_plan_from_options(option_values),
        _reconnect_policy_from_options(option_values),
        log_file,
    )


def _parse_continuous_mining_plan(arguments: Sequence[str]) -> ContinuousMiningPlan:
    """Parse strict continuous mining options into a validated plan."""

    option_values = _parse_option_values(
        arguments,
        {
            "--start-nonce",
            "--chunk-size",
            "--max-chunks",
            "--max-reconnect-attempts",
            "--max-runtime-seconds",
            "--max-server-silence-seconds",
            "--max-job-age-seconds",
        },
        unsupported_message="unsupported stratum-mine argument",
    )
    return _continuous_plan_from_options(option_values)


def _reconnect_policy_from_options(option_values: dict[str, str]) -> ReconnectPolicy:
    """Build the bounded reconnect policy from continuous command options."""

    maximum_attempts = _parse_unpadded_decimal_option(
        "--max-reconnect-attempts",
        option_values.get("--max-reconnect-attempts", "5"),
        minimum=0,
        maximum=MAX_RECONNECT_ATTEMPTS,
    )
    return ReconnectPolicy(maximum_attempts=maximum_attempts)


def _continuous_plan_from_options(
    option_values: dict[str, str],
) -> ContinuousMiningPlan:
    """Build one continuous plan without inventing a default chunk limit."""

    if "--chunk-size" not in option_values:
        raise ValueError("--chunk-size is required")
    start_nonce = _parse_unpadded_decimal_option(
        "--start-nonce",
        option_values.get("--start-nonce", "0"),
        minimum=0,
        maximum=_MAX_NONCE,
    )
    chunk_size = _parse_unpadded_decimal_option(
        "--chunk-size",
        option_values["--chunk-size"],
        minimum=1,
        maximum=_NONCE_LIMIT,
    )
    max_chunks = (
        _parse_unpadded_decimal_option(
            "--max-chunks",
            option_values["--max-chunks"],
            minimum=1,
            maximum=_NONCE_LIMIT,
        )
        if "--max-chunks" in option_values
        else None
    )
    max_runtime_seconds = (
        _parse_positive_seconds_option(
            "--max-runtime-seconds",
            option_values["--max-runtime-seconds"],
            MAX_RUNTIME_SECONDS,
        )
        if "--max-runtime-seconds" in option_values
        else None
    )
    max_server_silence_seconds = (
        _parse_positive_seconds_option(
            "--max-server-silence-seconds",
            option_values["--max-server-silence-seconds"],
            MAX_LIVENESS_SECONDS,
        )
        if "--max-server-silence-seconds" in option_values
        else None
    )
    max_job_age_seconds = (
        _parse_positive_seconds_option(
            "--max-job-age-seconds",
            option_values["--max-job-age-seconds"],
            MAX_LIVENESS_SECONDS,
        )
        if "--max-job-age-seconds" in option_values
        else None
    )
    return ContinuousMiningPlan(
        start_nonce=start_nonce,
        chunk_size=chunk_size,
        max_chunks=max_chunks,
        max_runtime_seconds=max_runtime_seconds,
        max_server_silence_seconds=max_server_silence_seconds,
        max_job_age_seconds=max_job_age_seconds,
    )


def _parse_positive_seconds_option(
    option_name: str,
    value: str,
    maximum: float,
) -> float:
    """Parse one positive finite decimal duration without accepting flag syntax."""

    if not value or value != value.strip():
        raise ValueError(f"{option_name} must be a positive decimal number")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{option_name} must be a positive decimal number") from exc
    if not parsed.is_finite() or parsed <= 0 or parsed > Decimal(str(maximum)):
        raise ValueError(f"{option_name} must be finite and between 0 and {int(maximum)}")
    converted = float(parsed)
    if converted == 0.0:
        raise ValueError(f"{option_name} is too small")
    return converted


def _chunked_plan_from_options(option_values: dict[str, str]) -> ChunkedMiningPlan:
    """Validate parsed options into one global chunked-mining budget."""

    if "--chunk-size" not in option_values:
        raise ValueError("--chunk-size is required")
    if "--max-hashes" not in option_values:
        raise ValueError("--max-hashes is required")
    start_nonce = _parse_unpadded_decimal_option(
        "--start-nonce",
        option_values.get("--start-nonce", "0"),
        minimum=0,
        maximum=_MAX_NONCE,
    )
    chunk_size = _parse_unpadded_decimal_option(
        "--chunk-size",
        option_values["--chunk-size"],
        minimum=1,
        maximum=_NONCE_LIMIT,
    )
    max_hashes = _parse_unpadded_decimal_option(
        "--max-hashes",
        option_values["--max-hashes"],
        minimum=1,
        maximum=_NONCE_LIMIT,
    )
    return ChunkedMiningPlan(
        start_nonce=start_nonce,
        chunk_size=chunk_size,
        max_hashes=max_hashes,
    )


def _parse_mining_range(arguments: Sequence[str]) -> tuple[int, int]:
    """Parse strict decimal options into one validated half-open nonce range."""

    option_values = _parse_option_values(
        arguments,
        {"--start-nonce", "--hash-count"},
        unsupported_message="unsupported stratum-mine-once argument",
    )
    return _mining_range_from_options(option_values)


def _parse_option_values(
    arguments: Sequence[str],
    supported_options: set[str],
    *,
    unsupported_message: str,
) -> dict[str, str]:
    """Parse unique name-value CLI options from a flat argument sequence."""

    option_values: dict[str, str] = {}
    index = 0
    while index < len(arguments):
        option = arguments[index]
        if option not in supported_options:
            raise ValueError(unsupported_message)
        if option in option_values:
            raise ValueError(f"{option} may be supplied only once")
        if index + 1 >= len(arguments):
            raise ValueError(f"{option} requires a value")
        option_values[option] = arguments[index + 1]
        index += 2
    return option_values


def _mining_range_from_options(option_values: dict[str, str]) -> tuple[int, int]:
    """Validate parsed options into one half-open nonce range."""

    if "--hash-count" not in option_values:
        raise ValueError("--hash-count is required")

    start_nonce = _parse_decimal_option(
        "--start-nonce",
        option_values.get("--start-nonce", "0"),
        minimum=0,
        maximum=_MAX_NONCE,
    )
    hash_count = _parse_decimal_option(
        "--hash-count",
        option_values["--hash-count"],
        minimum=1,
        maximum=_NONCE_LIMIT,
    )
    stop_nonce = start_nonce + hash_count
    if stop_nonce > _NONCE_LIMIT:
        raise ValueError("the requested nonce range exceeds 2**32")
    return start_nonce, stop_nonce


def _validate_log_file_path(value: str) -> str:
    """Require one nonblank log path without altering its representation."""

    if not isinstance(value, str) or not value.strip():
        raise ValueError("--log-file requires a nonblank path")
    return value


def _parse_decimal_option(
    name: str,
    value: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    """Parse one unpadded ASCII decimal integer inside an inclusive range."""

    if not value or not value.isascii() or not value.isdecimal():
        raise ValueError(f"{name} must be an ASCII decimal integer")
    parsed = int(value, 10)
    if not minimum <= parsed <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return parsed


def _parse_unpadded_decimal_option(
    name: str,
    value: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    """Parse strict ASCII decimal syntax without leading-zero padding."""

    if len(value) > 1 and value.startswith("0"):
        raise ValueError(f"{name} must be an unpadded ASCII decimal integer")
    return _parse_decimal_option(
        name,
        value,
        minimum=minimum,
        maximum=maximum,
    )


def _mine_one_range(
    client: StratumClient,
    subscription: SubscribeResult,
    start_nonce: int,
    stop_nonce: int,
    events: EventSink,
    backend: MiningComputeBackend,
) -> _MiningOutcome:
    """Assemble one current job, search once, and conditionally submit once."""

    assembler = MiningJobAssembler(subscription)
    job = _receive_buildable_job(client, assembler, events)
    extra_nonce_2 = _generate_extra_nonce_2(subscription.extra_nonce_2_size)
    work = prepare_mining_work(job, extra_nonce_2)
    events.emit(
        "nonce_range_started",
        fields={
            "job_id": work.job_id,
            "start_nonce": start_nonce,
            "stop_nonce": stop_nonce,
        },
    )
    result = backend.search_nonce_range(work, start_nonce, stop_nonce)
    events.emit(
        "nonce_range_completed",
        fields={
            "job_id": work.job_id,
            "start_nonce": start_nonce,
            "stop_nonce": stop_nonce,
            "hashes_checked": result.hashes_checked,
            "elapsed_ns": result.elapsed_ns,
            "hashes_per_second": result.hashes_per_second,
            "match_found": result.match is not None,
        },
    )

    pool_accepted: bool | None = None
    if result.match is not None:
        events.emit(
            "share_candidate_found",
            fields={
                "job_id": work.job_id,
                "nonce": result.match.nonce,
                "abbreviated_block_hash": _abbreviate_hex(result.match.block_hash.hex()),
                "meets_share_target": result.match.meets_share_target,
                "meets_network_target": result.match.meets_network_target,
            },
        )
        pool_accepted = client.submit_share(
            work.job_id,
            work.extra_nonce_2,
            work.network_time,
            result.match.nonce,
        )
        events.emit(
            "share_submission_completed",
            level="INFO" if pool_accepted else "WARNING",
            fields={
                "job_id": work.job_id,
                "nonce": result.match.nonce,
                "accepted": pool_accepted,
            },
        )

    return _MiningOutcome(
        job=job,
        work=work,
        result=result,
        pool_accepted=pool_accepted,
    )


def _receive_buildable_job(
    client: StratumClient,
    assembler: MiningJobAssembler,
    events: EventSink,
) -> MiningJob:
    """Return the first valid job arriving after a current difficulty exists."""

    while True:
        notification = client.receive_notification()
        if isinstance(notification, SetDifficultyNotification):
            _emit_difficulty_received(events, notification)
            assembler.apply_difficulty(notification)
            continue
        if not isinstance(notification, MiningNotifyNotification):
            raise StratumClientError("unsupported parsed Stratum notification")
        _emit_mining_job_received(events, notification)
        if assembler.current_difficulty is None:
            continue
        return assembler.build_job(notification)


def _generate_extra_nonce_2(byte_size: int) -> str:
    """Generate one lowercase hexadecimal second extra nonce."""

    return secrets.token_hex(byte_size)


def _observe_required_notifications(
    client: StratumClient,
    events: EventSink,
) -> tuple[
    SetDifficultyNotification,
    MiningNotifyNotification,
    tuple[str, ...],
]:
    """Receive until both notification types appear, preserving arrival order."""

    difficulty: SetDifficultyNotification | None = None
    job: MiningNotifyNotification | None = None
    arrival_order: list[str] = []

    while difficulty is None or job is None:
        notification = client.receive_notification()
        if isinstance(notification, SetDifficultyNotification):
            _emit_difficulty_received(events, notification)
            arrival_order.append("mining.set_difficulty")
            if difficulty is None:
                difficulty = notification
            continue
        if not isinstance(notification, MiningNotifyNotification):
            raise StratumClientError("unsupported parsed Stratum notification")
        _emit_mining_job_received(events, notification)
        arrival_order.append("mining.notify")
        if job is None:
            job = notification

    events.emit(
        "notification_observation_completed",
        fields={"arrival_order": arrival_order},
    )
    return difficulty, job, tuple(arrival_order)


def _emit_stratum_authorized(
    events: EventSink,
    settings: Settings,
    subscription: SubscribeResult,
) -> None:
    """Emit sanitized subscription metadata after authorization."""

    events.emit(
        "stratum_authorized",
        fields={
            "endpoint": f"{settings.stratum_host}:{settings.stratum_port}",
            "extra_nonce_2_size": subscription.extra_nonce_2_size,
        },
    )


def _emit_compute_backend_selected(
    events: EventSink,
    backend: MiningComputeBackend,
) -> None:
    """Emit stable safe capabilities for one selected backend."""

    capabilities = backend.capabilities
    fields: dict[str, EventValue] = {
        "backend_name": capabilities.backend_name,
        "backend_kind": capabilities.backend_kind,
        "implementation": capabilities.implementation,
        "supports_parallel_search": capabilities.supports_parallel_search,
        "supports_cooperative_cancellation": (capabilities.supports_cooperative_cancellation),
        "supports_device_selection": capabilities.supports_device_selection,
    }
    worker_count = compute_backend_worker_count(backend)
    if worker_count is not None:
        fields["worker_count"] = worker_count
    device_ordinal = compute_backend_device_ordinal(backend)
    if device_ordinal is not None:
        fields["device_ordinal"] = device_ordinal
    events.emit(
        "compute_backend_selected",
        fields=fields,
    )


def _emit_search_strategy_selected(
    events: EventSink,
    strategy: MiningSearchStrategy,
) -> None:
    """Emit stable strategy capabilities without cursor or work state."""

    capabilities = strategy.capabilities
    events.emit(
        "search_strategy_selected",
        fields={
            "strategy_name": capabilities.strategy_name,
            "implementation": capabilities.implementation,
            "deterministic": capabilities.deterministic,
            "contiguous_parent_ranges": capabilities.contiguous_parent_ranges,
            "exhaustive": capabilities.exhaustive,
            "experimental": capabilities.experimental,
        },
    )


def _emit_difficulty_received(
    events: EventSink,
    notification: SetDifficultyNotification,
) -> None:
    """Emit one validated difficulty value."""

    events.emit(
        "difficulty_received",
        fields={"difficulty": notification.difficulty},
    )


def _emit_mining_job_received(
    events: EventSink,
    notification: MiningNotifyNotification,
) -> None:
    """Emit sanitized identifiers and structural metadata for one job."""

    events.emit(
        "mining_job_received",
        fields={
            "job_id": notification.job_id,
            "network_bits": notification.network_bits,
            "clean_jobs": notification.clean_jobs,
            "merkle_branch_count": len(notification.merkle_branches),
        },
    )


def _emit_command_failed(
    events: EventSink,
    stage: str,
    error_category: str,
    *,
    attempts: int | None = None,
    recovery_stage: str | None = None,
) -> None:
    """Emit a controlled failure category without arbitrary exception text."""

    fields: dict[str, int | str] = {
        "stage": stage,
        "error_category": error_category,
    }
    if attempts is not None:
        fields["attempts"] = attempts
    if recovery_stage is not None:
        fields["recovery_stage"] = recovery_stage
    events.emit(
        "command_failed",
        level="ERROR",
        fields=fields,
    )


def _error_category(error: BaseException) -> str:
    """Map expected failures to stable categories without using arbitrary text."""

    categories: tuple[tuple[type[BaseException], str], ...] = (
        (StratumAuthorizationError, "StratumAuthorizationError"),
        (StratumConnectionError, "StratumConnectionError"),
        (StratumMessageError, "StratumMessageError"),
        (StratumClientError, "StratumClientError"),
        (StratumTransportError, "StratumTransportError"),
        (MiningJobError, "MiningJobError"),
        (CoinbaseError, "CoinbaseError"),
        (MerkleError, "MerkleError"),
        (BlockHeaderError, "BlockHeaderError"),
        (TargetError, "TargetError"),
        (NonceSearchError, "NonceSearchError"),
        (ComputeBackendSelectionError, "ComputeBackendSelectionError"),
        (ComputeBackendValidationError, "ComputeBackendValidationError"),
        (ComputeBackendError, "ComputeBackendError"),
        (SearchStrategySelectionError, "SearchStrategySelectionError"),
        (SearchStrategyValidationError, "SearchStrategyValidationError"),
        (SearchStrategyError, "SearchStrategyError"),
        (MiningWorkProgressionError, "MiningWorkProgressionError"),
        (SessionRecoveryExhaustedError, "SessionRecoveryExhaustedError"),
        (SessionRecoveryError, "SessionRecoveryError"),
        (ChunkedMiningError, "ChunkedMiningError"),
        (ContinuousMiningError, "ContinuousMiningError"),
        (_SignalLifecycleError, "SignalLifecycleError"),
        (OSError, "OSError"),
        (TypeError, "TypeError"),
        (ValueError, "ValueError"),
    )
    for error_type, category in categories:
        if isinstance(error, error_type):
            return category
    return "UnexpectedError"


def _print_success(
    settings: Settings,
    extra_nonce_1: str,
    extra_nonce_2_size: int,
    final_state: StratumClientState,
) -> None:
    """Print a sanitized summary of a successful handshake."""

    print("Stratum handshake succeeded.")
    print(f"Endpoint: {settings.stratum_host}:{settings.stratum_port}")
    print(f"Username: {_mask_username(settings.stratum_username)}")
    print(f"Extra nonce 1: {extra_nonce_1}")
    print(f"Extra nonce 2 size: {extra_nonce_2_size}")
    print(f"State: {final_state.name}")


def _print_observation_success(
    settings: Settings,
    extra_nonce_1: str,
    extra_nonce_2_size: int,
    difficulty: SetDifficultyNotification,
    job: MiningNotifyNotification,
    arrival_order: tuple[str, ...],
    final_state: StratumClientState,
) -> None:
    """Print a sanitized summary of independently observed notifications."""

    print("Stratum notification observation succeeded.")
    print(f"Endpoint: {settings.stratum_host}:{settings.stratum_port}")
    print(f"Username: {_mask_username(settings.stratum_username)}")
    print(f"Arrival order: {' -> '.join(arrival_order)}")
    print(f"Difficulty: {difficulty.difficulty}")
    print(f"Job ID: {job.job_id}")
    print(f"Previous block hash: {_abbreviate_hex(job.previous_block_hash)}")
    print(f"Coinbase part 1 hex characters: {len(job.coinbase_part_1)}")
    print(f"Coinbase part 2 hex characters: {len(job.coinbase_part_2)}")
    print(f"Merkle branch count: {len(job.merkle_branches)}")
    print(f"Version: {job.version}")
    print(f"Network bits: {job.network_bits}")
    print(f"Network time: {job.network_time}")
    print(f"Clean jobs: {str(job.clean_jobs).lower()}")
    print(f"Extra nonce 1: {extra_nonce_1}")
    print(f"Extra nonce 2 size: {extra_nonce_2_size}")
    print(f"State: {final_state.name}")


def _print_mining_outcome(
    settings: Settings,
    backend_name: str,
    worker_count: int | None,
    strategy_name: str,
    start_nonce: int,
    stop_nonce: int,
    outcome: _MiningOutcome,
) -> None:
    """Print a sanitized summary of one completed bounded mining range."""

    print("Bounded Stratum mining completed.")
    print(f"Endpoint: {settings.stratum_host}:{settings.stratum_port}")
    print(f"Username: {_mask_username(settings.stratum_username)}")
    print(f"Compute backend: {backend_name}")
    if worker_count is not None:
        print(f"Compute workers: {worker_count}")
    print(f"Search strategy: {strategy_name}")
    print(f"Job ID: {outcome.job.job_id}")
    print(f"Difficulty: {outcome.job.difficulty}")
    print(f"Network bits: {outcome.job.network_bits}")
    print(f"Extra nonce 2 size: {outcome.job.extra_nonce_2_size}")
    print(f"Start nonce: {start_nonce}")
    print(f"Exclusive stop nonce: {stop_nonce}")
    print(f"Hashes checked: {outcome.result.hashes_checked}")
    print(f"Elapsed time: {outcome.result.elapsed_ns} ns")
    if outcome.result.hashes_per_second is None:
        print("Hashes per second: unavailable")
    else:
        print(f"Hashes per second: {outcome.result.hashes_per_second:.2f}")

    match = outcome.result.match
    if match is None:
        print("Result: no qualifying hash found")
        return

    nonce_hex = match.nonce.to_bytes(4, byteorder="little", signed=False).hex()
    print(f"Matched nonce: {match.nonce}")
    print(f"Submitted nonce hex: {nonce_hex}")
    print(f"Raw block hash: {_abbreviate_hex(match.block_hash.hex())}")
    print(f"Meets share target: {str(match.meets_share_target).lower()}")
    print(f"Meets network target: {str(match.meets_network_target).lower()}")
    print(f"Pool result: {'accepted' if outcome.pool_accepted else 'rejected'}")


def _print_chunked_mining_outcome(
    settings: Settings,
    backend_name: str,
    worker_count: int | None,
    strategy_name: str,
    plan: ChunkedMiningPlan,
    result: ChunkedMiningResult,
) -> None:
    """Print a sanitized aggregate summary of finite chunked mining."""

    print("Bounded chunked Stratum mining completed.")
    print(f"Endpoint: {settings.stratum_host}:{settings.stratum_port}")
    print(f"Username: {_mask_username(settings.stratum_username)}")
    print(f"Compute backend: {backend_name}")
    if worker_count is not None:
        print(f"Compute workers: {worker_count}")
    print(f"Search strategy: {strategy_name}")
    print(f"Initial job ID: {result.initial_job.job_id}")
    print(f"Final job ID: {result.final_job.job_id}")
    print(f"Final difficulty: {result.final_job.difficulty}")
    print(f"Network bits: {result.final_job.network_bits}")
    print(f"Extra nonce 2 size: {result.final_job.extra_nonce_2_size}")
    print(f"Start nonce: {plan.start_nonce}")
    print(f"Chunk size: {plan.chunk_size}")
    print(f"Maximum hash budget: {plan.max_hashes}")
    print(f"Chunks completed: {result.chunks_completed}")
    print(f"Jobs used: {result.jobs_used}")
    print(f"Job replacements: {result.job_replacements}")
    print(f"Candidates found: {result.candidates_found}")
    print(f"Submissions performed: {result.submissions_performed}")
    print(f"Hashes checked: {result.total_hashes_checked}")
    print(f"Elapsed time: {result.total_elapsed_ns} ns")
    if result.weighted_hashes_per_second is None:
        print("Hashes per second: unavailable")
    else:
        print(f"Hashes per second: {result.weighted_hashes_per_second:.2f}")

    match = result.match
    if match is None:
        print("Result: hash budget exhausted without a qualifying hash")
        return

    nonce_hex = match.nonce.to_bytes(4, byteorder="little", signed=False).hex()
    print(f"Matched nonce: {match.nonce}")
    print(f"Submitted nonce hex: {nonce_hex}")
    print(f"Raw block hash: {_abbreviate_hex(match.block_hash.hex())}")
    print(f"Meets share target: {str(match.meets_share_target).lower()}")
    print(f"Meets network target: {str(match.meets_network_target).lower()}")
    print(f"Pool result: {'accepted' if result.pool_accepted else 'rejected'}")


def _print_continuous_mining_outcome(
    settings: Settings,
    backend_name: str,
    worker_count: int | None,
    strategy_name: str,
    subscription: SubscribeResult | None,
    plan: ContinuousMiningPlan,
    reconnect_policy: ReconnectPolicy,
    recovery_statistics: StratumRecoveryStatistics,
    outcome: ContinuousMiningOutcome,
    result: ContinuousMiningResult | None,
) -> None:
    """Print a sanitized aggregate summary of one continuous session."""

    print("Continuous Stratum mining completed.")
    print(f"Endpoint: {settings.stratum_host}:{settings.stratum_port}")
    print(f"Username: {_mask_username(settings.stratum_username)}")
    print(f"Compute backend: {backend_name}")
    if worker_count is not None:
        print(f"Compute workers: {worker_count}")
    print(f"Search strategy: {strategy_name}")
    if result is None:
        print("Final difficulty: unavailable")
        print("Network bits: unavailable")
    else:
        print(f"Final difficulty: {result.final_job.difficulty}")
        print(f"Network bits: {result.final_job.network_bits}")
    print(
        "Extra nonce 2 size: "
        f"{subscription.extra_nonce_2_size if subscription is not None else 'unavailable'}"
    )
    print(f"Start nonce: {plan.start_nonce}")
    print(f"Chunk size: {plan.chunk_size}")
    print(f"Maximum chunks: {plan.max_chunks if plan.max_chunks is not None else 'unlimited'}")
    print(
        "Maximum runtime seconds: "
        f"{plan.max_runtime_seconds if plan.max_runtime_seconds is not None else 'unlimited'}"
    )
    server_silence = (
        plan.max_server_silence_seconds
        if plan.max_server_silence_seconds is not None
        else "disabled"
    )
    print(f"Maximum server silence seconds: {server_silence}")
    print(
        "Maximum job age seconds: "
        f"{plan.max_job_age_seconds if plan.max_job_age_seconds is not None else 'disabled'}"
    )
    print(f"Maximum reconnect attempts: {reconnect_policy.maximum_attempts}")
    print(f"Chunks completed: {result.chunks_completed if result is not None else 0}")
    print(f"Jobs used: {result.jobs_used if result is not None else 0}")
    print(f"Job replacements: {result.job_replacements if result is not None else 0}")
    print(f"Work variants used: {result.work_variants_used if result is not None else 0}")
    print(f"Extra nonce 2 advances: {result.extra_nonce_2_advances if result is not None else 0}")
    print(
        "Extra nonce 2 cycles: "
        f"{result.extra_nonce_2_cycles_completed if result is not None else 0}"
    )
    print(f"Network-time rolls: {result.network_time_rolls if result is not None else 0}")
    print(f"Duplicate work ignored: {result.duplicate_work_ignored if result is not None else 0}")
    print(f"Reconnect attempts: {recovery_statistics.reconnect_attempts}")
    print(f"Successful reconnects: {recovery_statistics.successful_reconnects}")
    print(f"Failed reconnect attempts: {recovery_statistics.failed_reconnect_attempts}")
    print(f"Sessions established: {recovery_statistics.sessions_established}")
    print(f"Candidates found: {result.candidates_found if result is not None else 0}")
    print(f"Submissions performed: {result.submissions_performed if result is not None else 0}")
    print(f"Hashes checked: {result.total_hashes_checked if result is not None else 0}")
    print(f"Elapsed time: {result.total_elapsed_ns if result is not None else 0} ns")
    rate = result.weighted_hashes_per_second if result is not None else None
    if rate is None:
        print("Hashes per second: unavailable")
    else:
        print(f"Hashes per second: {rate:.2f}")

    if result is not None and result.match is not None:
        match = result.match
        nonce_hex = match.nonce.to_bytes(4, byteorder="little", signed=False).hex()
        print(f"Matched nonce: {match.nonce}")
        print(f"Submitted nonce hex: {nonce_hex}")
        print(f"Raw block hash: {_abbreviate_hex(match.block_hash.hex())}")
        print(f"Meets share target: {str(match.meets_share_target).lower()}")
        print(f"Meets network target: {str(match.meets_network_target).lower()}")
        print(f"Pool result: {'accepted' if result.pool_accepted else 'rejected'}")
    print(f"Result: {outcome.value}")


def _mask_username(username: str) -> str:
    """Mask a Stratum username while retaining a small recognition hint."""

    if len(username) <= 2:
        return "*" * len(username)
    if len(username) <= 8:
        return f"{username[0]}{'*' * (len(username) - 2)}{username[-1]}"
    return f"{username[:4]}…{username[-4:]}"


def _abbreviate_hex(value: str) -> str:
    """Abbreviate a hexadecimal field without returning the complete value."""

    if not value:
        return "<empty>"
    if len(value) <= 2:
        return "*" * len(value)
    if len(value) <= 8:
        return f"{value[:2]}…{value[-2:]}"
    if len(value) <= 16:
        return f"{value[:4]}…{value[-4:]}"
    return f"{value[:8]}…{value[-8:]}"


if __name__ == "__main__":
    raise SystemExit(main())
