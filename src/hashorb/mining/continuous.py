"""Continuous synchronous mining orchestration over bounded nonce chunks."""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from hashorb.mining.job import MiningJob, MiningJobAssembler
from hashorb.mining.liveness import (
    StratumLivenessPolicy,
    StratumLivenessTracker,
    StratumLivenessViolation,
)
from hashorb.mining.progression import (
    MiningJobContextIdentity,
    MiningWorkCursor,
    mining_job_context_identity,
    mining_work_identity,
    prepare_work_variant,
)
from hashorb.mining.recovery import (
    StratumMiningSession,
    StratumRecoveryStage,
    StratumRecoveryStatistics,
)
from hashorb.mining.search import (
    NonceSearchMatch,
    NonceSearchResult,
    PreparedMiningWork,
    prepare_mining_work,
    search_nonce_range,
)
from hashorb.mining.strategy import (
    MiningSearchStrategy,
    SearchAssignment,
    SearchStrategyCursor,
    SearchStrategyExecutionError,
    SearchStrategyValidationError,
    SequentialSearchStrategy,
)
from hashorb.network.stratum.client import StratumRequestError
from hashorb.network.stratum.messages import (
    MiningNotifyNotification,
    SetDifficultyNotification,
)
from hashorb.network.stratum.transport import StratumConnectionError

type MiningNotification = SetDifficultyNotification | MiningNotifyNotification
type NotificationReceiver = Callable[[float], MiningNotification | None]
type WorkPreparer = Callable[[MiningJob, str], PreparedMiningWork]
type RangeSearcher = Callable[[PreparedMiningWork, int, int], NonceSearchResult]
type ShareSubmitter = Callable[[PreparedMiningWork, NonceSearchMatch], bool]
type SessionRecoverer = Callable[
    [StratumConnectionError, StratumRecoveryStage],
    StratumMiningSession | None,
]
type StaleSessionRecoverer = Callable[[], StratumMiningSession | None]
type RecoveryStatisticsProvider = Callable[[], StratumRecoveryStatistics]

_NONCE_LIMIT = 1 << 32
_MAX_NONCE = _NONCE_LIMIT - 1
_MAX_CHUNKS = _NONCE_LIMIT
MAX_RUNTIME_SECONDS = 31_536_000.0
MAX_INTER_RANGE_DELAY_SECONDS = 60.0
_NANOSECONDS_PER_SECOND = 1_000_000_000
_NOTIFICATION_WAIT_SECONDS = 0.25
_PACING_WAIT_SECONDS = 0.1

_SHARE_REJECTION_CATEGORIES = {
    21: "job_not_found",
    22: "duplicate_share",
    23: "low_difficulty",
    24: "unauthorized_worker",
    25: "not_subscribed",
}


class ContinuousMiningError(Exception):
    """Base error for continuous mining orchestration."""


class ContinuousMiningValidationError(ContinuousMiningError, ValueError):
    """Raised when continuous mining input violates a public invariant."""


class ContinuousMiningOutcome(StrEnum):
    """Controlled terminal outcomes for one continuous mining session."""

    STOPPED_BY_USER = "stopped_by_user"
    RUNTIME_LIMIT_REACHED = "runtime_limit_reached"
    CHUNK_LIMIT_REACHED = "chunk_limit_reached"
    SHARE_ACCEPTED = "share_accepted"
    SHARE_REJECTED = "share_rejected"


@dataclass(frozen=True, slots=True)
class ContinuousMiningPlan:
    """Validated nonce-chunk policy with an optional searched-chunk limit."""

    start_nonce: int
    chunk_size: int
    max_chunks: int | None = None
    max_runtime_seconds: float | None = None
    max_server_silence_seconds: float | None = None
    max_job_age_seconds: float | None = None
    inter_range_delay_seconds: float = 0.0

    def __post_init__(self) -> None:
        """Validate the plan without inventing a hidden session limit."""

        _validate_integer(self.start_nonce, "start_nonce")
        _validate_integer(self.chunk_size, "chunk_size")
        if not 0 <= self.start_nonce <= _MAX_NONCE:
            raise ContinuousMiningValidationError("start_nonce must be between 0 and 0xffffffff")
        if not 1 <= self.chunk_size <= _NONCE_LIMIT:
            raise ContinuousMiningValidationError("chunk_size must be between 1 and 2**32")
        if self.max_chunks is not None:
            _validate_integer(self.max_chunks, "max_chunks")
            if not 1 <= self.max_chunks <= _MAX_CHUNKS:
                raise ContinuousMiningValidationError("max_chunks must be between 1 and 2**32")
        if self.max_runtime_seconds is not None:
            if isinstance(self.max_runtime_seconds, bool) or not isinstance(
                self.max_runtime_seconds, (int, float)
            ):
                raise ContinuousMiningValidationError("max_runtime_seconds must be a number")
            if not math.isfinite(self.max_runtime_seconds):
                raise ContinuousMiningValidationError("max_runtime_seconds must be finite")
            if not 0 < self.max_runtime_seconds <= MAX_RUNTIME_SECONDS:
                raise ContinuousMiningValidationError(
                    f"max_runtime_seconds must be between 0 and {int(MAX_RUNTIME_SECONDS)}"
                )
        try:
            StratumLivenessPolicy(
                self.max_server_silence_seconds,
                self.max_job_age_seconds,
            )
        except ValueError as exc:
            raise ContinuousMiningValidationError(str(exc)) from exc
        if isinstance(self.inter_range_delay_seconds, bool) or not isinstance(
            self.inter_range_delay_seconds, (int, float)
        ):
            raise ContinuousMiningValidationError("inter_range_delay_seconds must be a number")
        if (
            not math.isfinite(self.inter_range_delay_seconds)
            or not 0 <= self.inter_range_delay_seconds <= MAX_INTER_RANGE_DELAY_SECONDS
        ):
            raise ContinuousMiningValidationError(
                "inter_range_delay_seconds must be finite and between 0 and 60"
            )


@dataclass(frozen=True, slots=True)
class ContinuousMiningResult:
    """Immutable aggregate outcome of one continuous mining session."""

    outcome: ContinuousMiningOutcome
    initial_job: MiningJob
    final_job: MiningJob
    match: NonceSearchMatch | None
    pool_accepted: bool | None
    chunks_completed: int
    jobs_used: int
    job_replacements: int
    work_variants_used: int
    extra_nonce_2_advances: int
    extra_nonce_2_cycles_completed: int
    network_time_rolls: int
    duplicate_work_ignored: int
    reconnect_attempts: int
    successful_reconnects: int
    failed_reconnect_attempts: int
    sessions_established: int
    total_hashes_checked: int
    total_elapsed_ns: int
    candidate_count: int | None = None
    submission_count: int | None = None
    accepted_submission_count: int | None = None
    rejected_submission_count: int | None = None

    def __post_init__(self) -> None:
        """Validate aggregate counters and terminal submission state."""

        if not isinstance(self.outcome, ContinuousMiningOutcome):
            raise ContinuousMiningValidationError("outcome must be a ContinuousMiningOutcome")
        if not isinstance(self.initial_job, MiningJob):
            raise ContinuousMiningValidationError("initial_job must be a MiningJob")
        if not isinstance(self.final_job, MiningJob):
            raise ContinuousMiningValidationError("final_job must be a MiningJob")
        for name, value in (
            ("chunks_completed", self.chunks_completed),
            ("jobs_used", self.jobs_used),
            ("job_replacements", self.job_replacements),
            ("work_variants_used", self.work_variants_used),
            ("extra_nonce_2_advances", self.extra_nonce_2_advances),
            ("extra_nonce_2_cycles_completed", self.extra_nonce_2_cycles_completed),
            ("network_time_rolls", self.network_time_rolls),
            ("duplicate_work_ignored", self.duplicate_work_ignored),
            ("reconnect_attempts", self.reconnect_attempts),
            ("successful_reconnects", self.successful_reconnects),
            ("failed_reconnect_attempts", self.failed_reconnect_attempts),
            ("sessions_established", self.sessions_established),
            ("total_hashes_checked", self.total_hashes_checked),
            ("total_elapsed_ns", self.total_elapsed_ns),
        ):
            _validate_nonnegative_integer(value, name)
        if self.jobs_used > self.chunks_completed:
            raise ContinuousMiningValidationError("jobs_used cannot exceed chunks_completed")
        if self.work_variants_used > self.chunks_completed:
            raise ContinuousMiningValidationError(
                "work_variants_used cannot exceed chunks_completed"
            )
        if self.job_replacements > self.chunks_completed:
            raise ContinuousMiningValidationError(
                "job_replacements cannot exceed completed chunk boundaries"
            )
        if self.sessions_established <= 0:
            raise ContinuousMiningValidationError("sessions_established must be positive")
        if self.successful_reconnects > self.reconnect_attempts:
            raise ContinuousMiningValidationError(
                "successful_reconnects cannot exceed reconnect_attempts"
            )
        if self.failed_reconnect_attempts > self.reconnect_attempts:
            raise ContinuousMiningValidationError(
                "failed_reconnect_attempts cannot exceed reconnect_attempts"
            )
        if self.successful_reconnects > self.sessions_established:
            raise ContinuousMiningValidationError(
                "successful_reconnects cannot exceed sessions_established"
            )

        optional_counts = (
            ("candidate_count", self.candidate_count),
            ("submission_count", self.submission_count),
            (
                "accepted_submission_count",
                self.accepted_submission_count,
            ),
            (
                "rejected_submission_count",
                self.rejected_submission_count,
            ),
        )
        for optional_name, optional_value in optional_counts:
            if optional_value is not None:
                _validate_nonnegative_integer(
                    optional_value,
                    optional_name,
                )

        counts = tuple(value for _, value in optional_counts)
        if all(value is not None for value in counts):
            candidate_count = self.candidate_count
            submission_count = self.submission_count
            accepted_count = self.accepted_submission_count
            rejected_count = self.rejected_submission_count

            assert candidate_count is not None
            assert submission_count is not None
            assert accepted_count is not None
            assert rejected_count is not None

            if submission_count > candidate_count:
                raise ContinuousMiningValidationError(
                    "submission_count cannot exceed candidate_count"
                )
            if accepted_count + rejected_count != submission_count:
                raise ContinuousMiningValidationError(
                    "accepted/rejected counts must equal submission_count"
                )

        submitted_outcome = self.outcome in {
            ContinuousMiningOutcome.SHARE_ACCEPTED,
            ContinuousMiningOutcome.SHARE_REJECTED,
        }
        if submitted_outcome:
            if not isinstance(self.match, NonceSearchMatch):
                raise ContinuousMiningValidationError(
                    "submitted outcomes require a NonceSearchMatch"
                )
            expected_acceptance = self.outcome is ContinuousMiningOutcome.SHARE_ACCEPTED
            if self.pool_accepted is not expected_acceptance:
                raise ContinuousMiningValidationError(
                    "pool_accepted must agree with the submission outcome"
                )
        elif self.match is not None or self.pool_accepted is not None:
            raise ContinuousMiningValidationError(
                "non-submission outcomes cannot contain submission state"
            )

    @property
    def candidates_found(self) -> int:
        """Return every share candidate observed during the session."""

        if self.candidate_count is not None:
            return self.candidate_count
        return int(self.match is not None)

    @property
    def submissions_performed(self) -> int:
        """Return every share submission performed during the session."""

        if self.submission_count is not None:
            return self.submission_count
        return int(self.pool_accepted is not None)

    @property
    def accepted_submissions(self) -> int:
        """Return every accepted share submission."""

        if self.accepted_submission_count is not None:
            return self.accepted_submission_count
        return int(self.pool_accepted is True)

    @property
    def rejected_submissions(self) -> int:
        """Return every rejected share submission."""

        if self.rejected_submission_count is not None:
            return self.rejected_submission_count
        return int(self.pool_accepted is False)

    @property
    def weighted_hashes_per_second(self) -> float | None:
        """Return the rate derived from aggregate integer counts and time."""

        if self.total_elapsed_ns == 0:
            return None
        return self.total_hashes_checked * _NANOSECONDS_PER_SECOND / self.total_elapsed_ns


@runtime_checkable
class StopToken(Protocol):
    """Read-only cooperative stop boundary consumed by orchestration."""

    @property
    def stop_requested(self) -> bool:
        """Return whether graceful shutdown has been requested."""


class StopController:
    """Small idempotent stop controller suitable for CLI signal handlers."""

    __slots__ = ("_clock", "_max_runtime_seconds", "_started_at", "_stop_reason")

    def __init__(
        self,
        max_runtime_seconds: float | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_runtime_seconds is not None:
            ContinuousMiningPlan(0, 1, max_runtime_seconds=max_runtime_seconds)
        if not callable(clock):
            raise ContinuousMiningValidationError("clock must be callable")
        started_at = clock()
        if isinstance(started_at, bool) or not isinstance(started_at, (int, float)):
            raise ContinuousMiningValidationError("clock must return a number")
        if not math.isfinite(started_at):
            raise ContinuousMiningValidationError("clock must return a finite number")
        self._clock = clock
        self._max_runtime_seconds = max_runtime_seconds
        self._started_at = float(started_at)
        self._stop_reason: ContinuousMiningOutcome | None = None

    @property
    def stop_requested(self) -> bool:
        """Return whether a caller has requested graceful shutdown."""

        self._observe_runtime_limit()
        return self._stop_reason is not None

    @property
    def runtime_limit_reached(self) -> bool:
        """Return whether the monotonic runtime boundary caused this stop."""

        self._observe_runtime_limit()
        return self._stop_reason is ContinuousMiningOutcome.RUNTIME_LIMIT_REACHED

    def request_stop(self) -> None:
        """Request shutdown; repeated requests have no additional effect."""

        if self._stop_reason is None:
            self._stop_reason = ContinuousMiningOutcome.STOPPED_BY_USER

    def _observe_runtime_limit(self) -> None:
        if self._stop_reason is not None or self._max_runtime_seconds is None:
            return
        current = self._clock()
        if isinstance(current, bool) or not isinstance(current, (int, float)):
            raise ContinuousMiningValidationError("clock must return a number")
        if not math.isfinite(current):
            raise ContinuousMiningValidationError("clock must return a finite number")
        if float(current) - self._started_at >= self._max_runtime_seconds:
            self._stop_reason = ContinuousMiningOutcome.RUNTIME_LIMIT_REACHED


class ContinuousMiningObserver(Protocol):
    """Passive observation boundary for continuous lifecycle events."""

    def notification_received(self, notification: MiningNotification) -> None:
        """Observe one parsed notification in arrival order."""

    def chunk_started(
        self,
        work: PreparedMiningWork,
        start_nonce: int,
        stop_nonce: int,
    ) -> None:
        """Observe one exact range immediately before its search."""

    def chunk_completed(
        self,
        work: PreparedMiningWork,
        result: NonceSearchResult,
    ) -> None:
        """Observe one completed range search."""

    def job_replaced(
        self,
        previous_job: MiningJob,
        new_job: MiningJob,
        replacement_index: int,
    ) -> None:
        """Observe one searched-work replacement."""

    def candidate_found(
        self,
        work: PreparedMiningWork,
        match: NonceSearchMatch,
    ) -> None:
        """Observe the first candidate before submission."""

    def submission_completed(
        self,
        work: PreparedMiningWork,
        match: NonceSearchMatch,
        accepted: bool,
        *,
        rejection_code: int | None = None,
        rejection_category: str | None = None,
    ) -> None:
        """Observe the one terminal pool response."""

    def stop_requested(self) -> None:
        """Observe a controlled cooperative stop."""

    def session_stale(self, violation: StratumLivenessViolation) -> None:
        """Observe one configured liveness threshold crossing."""

    def stale_reconnect_started(self, violation: StratumLivenessViolation) -> None:
        """Observe entry into existing recovery for a stale session."""

    def stale_reconnect_succeeded(self, violation: StratumLivenessViolation) -> None:
        """Observe fresh usable session state after stale recovery."""

    def stale_reconnect_failed(self, violation: StratumLivenessViolation) -> None:
        """Observe stale recovery escaping without fresh usable state."""

    def nonce_space_exhausted(self, work: PreparedMiningWork) -> None:
        """Observe exhaustion of the current prepared work's nonce space."""

    def waiting_for_job(self, work: PreparedMiningWork) -> None:
        """Observe entry into bounded waiting for replacement work."""

    def work_advanced(
        self,
        reason: str,
        work_variant_index: int,
        extra_nonce_2_advance_count: int,
        network_time_roll_count: int,
    ) -> None:
        """Observe a prepared variant immediately before its first search."""

    def extra_nonce_2_cycle_completed(self, cycle_count: int) -> None:
        """Observe completion of one negotiated extra-nonce cycle."""

    def network_time_rolled(self, roll_count: int) -> None:
        """Observe one safe one-second local network-time advance."""

    def duplicate_work_ignored(self, duplicate_count: int, reason: str) -> None:
        """Observe pool work ignored because it repeats effective work."""


class NullContinuousMiningObserver:
    """No-op observer for callers that do not need lifecycle events."""

    def notification_received(self, notification: MiningNotification) -> None:
        """Discard a parsed notification."""

    def chunk_started(
        self,
        work: PreparedMiningWork,
        start_nonce: int,
        stop_nonce: int,
    ) -> None:
        """Discard a range-start observation."""

    def chunk_completed(
        self,
        work: PreparedMiningWork,
        result: NonceSearchResult,
    ) -> None:
        """Discard a range-completion observation."""

    def job_replaced(
        self,
        previous_job: MiningJob,
        new_job: MiningJob,
        replacement_index: int,
    ) -> None:
        """Discard a work-replacement observation."""

    def candidate_found(
        self,
        work: PreparedMiningWork,
        match: NonceSearchMatch,
    ) -> None:
        """Discard a candidate observation."""

    def submission_completed(
        self,
        work: PreparedMiningWork,
        match: NonceSearchMatch,
        accepted: bool,
        *,
        rejection_code: int | None = None,
        rejection_category: str | None = None,
    ) -> None:
        """Discard a submission observation."""

    def stop_requested(self) -> None:
        """Discard a stop observation."""

    def session_stale(self, violation: StratumLivenessViolation) -> None:
        """Discard a stale-session observation."""

    def stale_reconnect_started(self, violation: StratumLivenessViolation) -> None:
        """Discard stale reconnect entry."""

    def stale_reconnect_succeeded(self, violation: StratumLivenessViolation) -> None:
        """Discard stale reconnect success."""

    def stale_reconnect_failed(self, violation: StratumLivenessViolation) -> None:
        """Discard stale reconnect failure."""

    def nonce_space_exhausted(self, work: PreparedMiningWork) -> None:
        """Discard a nonce-space exhaustion observation."""

    def waiting_for_job(self, work: PreparedMiningWork) -> None:
        """Discard a waiting-state observation."""

    def work_advanced(
        self,
        reason: str,
        work_variant_index: int,
        extra_nonce_2_advance_count: int,
        network_time_roll_count: int,
    ) -> None:
        """Discard a work-variant observation."""

    def extra_nonce_2_cycle_completed(self, cycle_count: int) -> None:
        """Discard an extra-nonce cycle observation."""

    def network_time_rolled(self, roll_count: int) -> None:
        """Discard a network-time roll observation."""

    def duplicate_work_ignored(self, duplicate_count: int, reason: str) -> None:
        """Discard a duplicate-work observation."""


def run_continuous_mining(
    plan: ContinuousMiningPlan,
    assembler: MiningJobAssembler,
    initial_job: MiningJob,
    extra_nonce_2: str,
    stop_token: StopToken,
    *,
    receive_notification: NotificationReceiver,
    submit_share: ShareSubmitter,
    observer: ContinuousMiningObserver | None = None,
    strategy: MiningSearchStrategy | None = None,
    prepare_work: WorkPreparer = prepare_mining_work,
    search_range: RangeSearcher = search_nonce_range,
    recover_session: SessionRecoverer | None = None,
    recover_stale_session: StaleSessionRecoverer | None = None,
    recovery_statistics: RecoveryStatisticsProvider | None = None,
    liveness_clock: Callable[[], float] = time.monotonic,
    pacing_clock: Callable[[], float] = time.monotonic,
) -> ContinuousMiningResult:
    """Mine strategy-scheduled chunks until a controlled terminal outcome occurs."""

    selected_strategy: MiningSearchStrategy = (
        SequentialSearchStrategy() if strategy is None else strategy
    )

    _validate_run_inputs(
        plan,
        assembler,
        initial_job,
        extra_nonce_2,
        stop_token,
        receive_notification,
        submit_share,
        selected_strategy,
        prepare_work,
        search_range,
        recover_session,
        recover_stale_session,
        recovery_statistics,
        liveness_clock,
        pacing_clock,
    )
    event_observer: ContinuousMiningObserver = (
        NullContinuousMiningObserver() if observer is None else observer
    )
    liveness = StratumLivenessTracker(
        StratumLivenessPolicy(
            plan.max_server_silence_seconds,
            plan.max_job_age_seconds,
        ),
        clock=liveness_clock,
    )
    if stop_token.stop_requested:
        outcome = _controlled_stop_outcome(stop_token)
        if outcome is ContinuousMiningOutcome.STOPPED_BY_USER:
            event_observer.stop_requested()
        return ContinuousMiningResult(
            outcome=outcome,
            initial_job=initial_job,
            final_job=initial_job,
            match=None,
            pool_accepted=None,
            chunks_completed=0,
            jobs_used=0,
            job_replacements=0,
            work_variants_used=0,
            extra_nonce_2_advances=0,
            extra_nonce_2_cycles_completed=0,
            network_time_rolls=0,
            duplicate_work_ignored=0,
            reconnect_attempts=0,
            successful_reconnects=0,
            failed_reconnect_attempts=0,
            sessions_established=1,
            total_hashes_checked=0,
            total_elapsed_ns=0,
        )
    current_job = initial_job
    current_assembler = assembler
    current_receive_notification = receive_notification
    current_submit_share = submit_share
    current_extra_nonce_2_seed = extra_nonce_2
    current_cursor = MiningWorkCursor.start(current_job, extra_nonce_2)
    current_work = prepare_work_variant(
        current_cursor.current_variant,
        prepare_work=prepare_work,
    )
    current_work_identity = mining_work_identity(current_work)
    current_pool_identity = mining_job_context_identity(current_job)
    last_ignored_pool_identity: MiningJobContextIdentity | None = None
    current_variant_reason = "initial_job"
    strategy_cursor = _create_strategy_cursor(selected_strategy, plan)
    current_work_searched = False
    current_job_searched = False
    chunks_completed = 0
    jobs_used = 0
    replacements = 0
    work_variants = 0
    extra_nonce_2_advances = 0
    extra_nonce_2_cycles = 0
    network_time_rolls = 0
    duplicates = 0
    total_hashes = 0
    total_elapsed_ns = 0
    candidate_count = 0
    submission_count = 0
    accepted_submission_count = 0
    rejected_submission_count = 0
    stop_observed = False

    def finish(
        outcome: ContinuousMiningOutcome,
        *,
        match: NonceSearchMatch | None = None,
        pool_accepted: bool | None = None,
    ) -> ContinuousMiningResult:
        statistics = (
            StratumRecoveryStatistics(
                reconnect_attempts=0,
                successful_reconnects=0,
                failed_reconnect_attempts=0,
                sessions_established=1,
            )
            if recovery_statistics is None
            else recovery_statistics()
        )
        if not isinstance(statistics, StratumRecoveryStatistics):
            raise ContinuousMiningValidationError(
                "recovery_statistics must return StratumRecoveryStatistics"
            )
        return ContinuousMiningResult(
            outcome=outcome,
            initial_job=initial_job,
            final_job=current_job,
            match=match,
            pool_accepted=pool_accepted,
            chunks_completed=chunks_completed,
            jobs_used=jobs_used,
            job_replacements=replacements,
            work_variants_used=work_variants,
            extra_nonce_2_advances=extra_nonce_2_advances,
            extra_nonce_2_cycles_completed=extra_nonce_2_cycles,
            network_time_rolls=network_time_rolls,
            duplicate_work_ignored=duplicates,
            reconnect_attempts=statistics.reconnect_attempts,
            successful_reconnects=statistics.successful_reconnects,
            failed_reconnect_attempts=statistics.failed_reconnect_attempts,
            sessions_established=statistics.sessions_established,
            total_hashes_checked=total_hashes,
            total_elapsed_ns=total_elapsed_ns,
            candidate_count=candidate_count,
            submission_count=submission_count,
            accepted_submission_count=accepted_submission_count,
            rejected_submission_count=rejected_submission_count,
        )

    def observe_stop() -> None:
        nonlocal stop_observed
        if stop_observed:
            return
        stop_observed = True
        event_observer.stop_requested()

    def finish_stopped() -> ContinuousMiningResult:
        outcome = _controlled_stop_outcome(stop_token)
        if outcome is ContinuousMiningOutcome.STOPPED_BY_USER:
            observe_stop()
        return finish(outcome)

    def ignore_duplicate(reason: str) -> None:
        nonlocal duplicates
        duplicates += 1
        event_observer.duplicate_work_ignored(duplicates, reason)

    def select_pool_job(selected_job: MiningJob) -> bool:
        nonlocal current_cursor
        nonlocal current_job
        nonlocal current_job_searched
        nonlocal current_pool_identity
        nonlocal current_variant_reason
        nonlocal current_work
        nonlocal current_work_identity
        nonlocal current_work_searched
        nonlocal last_ignored_pool_identity
        nonlocal replacements
        nonlocal strategy_cursor

        selected_identity = mining_job_context_identity(selected_job)
        if selected_identity in {current_pool_identity, last_ignored_pool_identity}:
            ignore_duplicate("pool_context")
            return False

        selected_cursor = MiningWorkCursor.start(
            selected_job,
            current_extra_nonce_2_seed,
        )
        selected_work = prepare_work_variant(
            selected_cursor.current_variant,
            prepare_work=prepare_work,
        )
        selected_work_identity = mining_work_identity(selected_work)
        if selected_work_identity == current_work_identity:
            last_ignored_pool_identity = selected_identity
            ignore_duplicate("effective_work")
            return False

        replacements += 1
        event_observer.job_replaced(current_job, selected_job, replacements)
        current_job = selected_job
        current_cursor = selected_cursor
        current_work = selected_work
        current_work_identity = selected_work_identity
        current_pool_identity = selected_identity
        last_ignored_pool_identity = None
        current_variant_reason = "pool_job"
        strategy_cursor = _create_strategy_cursor(selected_strategy, plan)
        current_work_searched = False
        current_job_searched = False
        return True

    def install_recovered_session(session: StratumMiningSession) -> None:
        nonlocal current_assembler
        nonlocal current_cursor
        nonlocal current_extra_nonce_2_seed
        nonlocal current_job
        nonlocal current_job_searched
        nonlocal current_pool_identity
        nonlocal current_receive_notification
        nonlocal current_submit_share
        nonlocal current_variant_reason
        nonlocal current_work
        nonlocal current_work_identity
        nonlocal current_work_searched
        nonlocal last_ignored_pool_identity
        nonlocal replacements
        nonlocal strategy_cursor

        recovered_cursor = MiningWorkCursor.start(
            session.initial_job,
            session.extra_nonce_2_seed,
        )
        recovered_work = prepare_work_variant(
            recovered_cursor.current_variant,
            prepare_work=prepare_work,
        )
        if current_job_searched:
            replacements += 1
            event_observer.job_replaced(
                current_job,
                session.initial_job,
                replacements,
            )
        current_assembler = session.assembler
        current_cursor = recovered_cursor
        current_extra_nonce_2_seed = session.extra_nonce_2_seed
        current_job = session.initial_job
        current_job_searched = False
        current_pool_identity = mining_job_context_identity(session.initial_job)
        current_receive_notification = session.receive_notification

        def submit_recovered_share(
            work: PreparedMiningWork,
            match: NonceSearchMatch,
        ) -> bool:
            return session.submit_share(
                work.job_id,
                work.extra_nonce_2,
                work.network_time,
                match.nonce,
            )

        current_submit_share = submit_recovered_share
        current_variant_reason = "recovered_session"
        current_work = recovered_work
        current_work_identity = mining_work_identity(recovered_work)
        current_work_searched = False
        last_ignored_pool_identity = None
        strategy_cursor = _create_strategy_cursor(selected_strategy, plan)
        liveness.session_replaced()

    def recover_connection(
        error: StratumConnectionError,
        stage: StratumRecoveryStage,
    ) -> bool:
        if recover_session is None:
            raise error
        recovered = recover_session(error, stage)
        if recovered is None:
            return False
        if not isinstance(recovered, StratumMiningSession):
            raise ContinuousMiningValidationError(
                "recover_session must return StratumMiningSession or None"
            )
        install_recovered_session(recovered)
        return True

    def recover_stale(violation: StratumLivenessViolation) -> bool:
        if recover_stale_session is None:
            raise ContinuousMiningError("stale-session recovery is unavailable")
        event_observer.session_stale(violation)
        event_observer.stale_reconnect_started(violation)
        try:
            recovered = recover_stale_session()
        except BaseException:
            event_observer.stale_reconnect_failed(violation)
            raise
        if recovered is None:
            return False
        if not isinstance(recovered, StratumMiningSession):
            raise ContinuousMiningValidationError(
                "recover_stale_session must return StratumMiningSession or None"
            )
        install_recovered_session(recovered)
        event_observer.stale_reconnect_succeeded(violation)
        return True

    while True:
        if stop_token.stop_requested:
            return finish_stopped()
        stale = liveness.violation()
        if stale is not None:
            if not recover_stale(stale):
                return finish_stopped()
            continue
        if plan.max_chunks is not None and chunks_completed >= plan.max_chunks:
            return finish(ContinuousMiningOutcome.CHUNK_LIMIT_REACHED)

        assignment = _next_strategy_assignment(strategy_cursor)
        start_nonce = assignment.start_nonce
        stop_nonce = assignment.stop_nonce
        if not current_work_searched:
            work_variants += 1
            event_observer.work_advanced(
                current_variant_reason,
                current_cursor.variant_index,
                current_cursor.extra_nonce_2_advance_count,
                current_cursor.network_time_roll_count,
            )
            current_work_searched = True
        if not current_job_searched:
            jobs_used += 1
            current_job_searched = True
        event_observer.chunk_started(current_work, start_nonce, stop_nonce)
        chunk_result = search_range(current_work, start_nonce, stop_nonce)
        _validate_search_result(chunk_result, start_nonce, stop_nonce)
        event_observer.chunk_completed(current_work, chunk_result)
        chunks_completed += 1
        total_hashes += chunk_result.hashes_checked
        total_elapsed_ns += chunk_result.elapsed_ns
        liveness.range_completed()

        stale = liveness.violation()
        if stale is not None:
            if not recover_stale(stale):
                return finish_stopped()
            continue

        if chunk_result.match is not None:
            match = chunk_result.match
            candidate_count += 1
            if stop_token.stop_requested:
                observe_stop()
            event_observer.candidate_found(current_work, match)
            if stop_token.stop_requested:
                observe_stop()
            rejection_code: int | None = None
            rejection_category: str | None = None

            try:
                accepted = current_submit_share(current_work, match)
            except StratumRequestError as exc:
                if exc.error is None:
                    raise
                accepted = False
                rejection_code = exc.error.code
                rejection_category = _SHARE_REJECTION_CATEGORIES.get(
                    rejection_code,
                    "pool_rejection",
                )

            if not isinstance(accepted, bool):
                raise ContinuousMiningValidationError("submit_share must return an actual Boolean")

            submission_count += 1
            if accepted:
                accepted_submission_count += 1
            else:
                rejected_submission_count += 1
            liveness.server_message_received()
            if stop_token.stop_requested:
                observe_stop()
            event_observer.submission_completed(
                current_work,
                match,
                accepted,
                rejection_code=rejection_code,
                rejection_category=rejection_category,
            )
            if stop_token.stop_requested:
                observe_stop()
            range_fully_searched = chunk_result.hashes_checked == stop_nonce - start_nonce
            if match.meets_network_target or not range_fully_searched or stop_token.stop_requested:
                outcome = (
                    ContinuousMiningOutcome.SHARE_ACCEPTED
                    if accepted
                    else ContinuousMiningOutcome.SHARE_REJECTED
                )
                return finish(outcome, match=match, pool_accepted=accepted)

        if stop_token.stop_requested:
            return finish_stopped()
        if plan.max_chunks is not None and chunks_completed >= plan.max_chunks:
            return finish(ContinuousMiningOutcome.CHUNK_LIMIT_REACHED)

        if strategy_cursor.exhausted:
            event_observer.nonce_space_exhausted(current_work)

        try:
            selected_job = _drain_notifications(
                current_assembler,
                stop_token,
                current_receive_notification,
                event_observer,
                notification_observer=liveness.notification_received,
            )
        except StratumConnectionError as error:
            if not recover_connection(error, StratumRecoveryStage.NOTIFICATION_POLL):
                return finish_stopped()
            continue
        if stop_token.stop_requested:
            return finish_stopped()
        if selected_job is not None:
            if select_pool_job(selected_job):
                continue

        if plan.inter_range_delay_seconds > 0:
            pacing_deadline = pacing_clock() + plan.inter_range_delay_seconds
            pacing_interrupted = False
            while True:
                if stop_token.stop_requested:
                    return finish_stopped()
                stale = liveness.violation()
                if stale is not None:
                    if not recover_stale(stale):
                        return finish_stopped()
                    pacing_interrupted = True
                    break
                remaining = pacing_deadline - pacing_clock()
                if remaining <= 0:
                    break
                try:
                    notification = current_receive_notification(
                        min(_PACING_WAIT_SECONDS, remaining)
                    )
                except StratumConnectionError as error:
                    if not recover_connection(error, StratumRecoveryStage.NOTIFICATION_POLL):
                        return finish_stopped()
                    pacing_interrupted = True
                    break
                if notification is None:
                    continue
                pacing_job = _apply_notification(
                    current_assembler,
                    notification,
                    event_observer,
                )
                liveness.notification_received(notification)
                try:
                    drained_job = _drain_notifications(
                        current_assembler,
                        stop_token,
                        current_receive_notification,
                        event_observer,
                        notification_observer=liveness.notification_received,
                    )
                except StratumConnectionError as error:
                    if not recover_connection(error, StratumRecoveryStage.NOTIFICATION_POLL):
                        return finish_stopped()
                    pacing_interrupted = True
                    break
                selected_pacing_job = drained_job if drained_job is not None else pacing_job
                if selected_pacing_job is not None and select_pool_job(selected_pacing_job):
                    pacing_interrupted = True
                    break
            if pacing_interrupted:
                continue

        if not strategy_cursor.exhausted:
            continue

        progress = current_cursor.advance()
        if progress.extra_nonce_2_cycle_completed:
            extra_nonce_2_cycles += 1
            event_observer.extra_nonce_2_cycle_completed(extra_nonce_2_cycles)
        if progress.network_time_rolled:
            network_time_rolls += 1
            event_observer.network_time_rolled(network_time_rolls)

        if progress.cursor is not None:
            if progress.extra_nonce_2_advanced:
                extra_nonce_2_advances += 1
            successor_cursor = progress.cursor
            successor_work = prepare_work_variant(
                successor_cursor.current_variant,
                prepare_work=prepare_work,
            )
            successor_identity = mining_work_identity(successor_work)
            if successor_identity == current_work_identity:
                raise ContinuousMiningError(
                    "deterministic progression produced duplicate effective work"
                )
            current_cursor = successor_cursor
            current_work = successor_work
            current_work_identity = successor_identity
            current_variant_reason = (
                "network_time" if progress.network_time_rolled else "extra_nonce_2"
            )
            current_work_searched = False
            strategy_cursor = _create_strategy_cursor(selected_strategy, plan)
            continue

        event_observer.waiting_for_job(current_work)
        while True:
            if stop_token.stop_requested:
                return finish_stopped()
            try:
                notification = current_receive_notification(_NOTIFICATION_WAIT_SECONDS)
            except StratumConnectionError as error:
                if not recover_connection(error, StratumRecoveryStage.REPLACEMENT_WAIT):
                    return finish_stopped()
                break
            if notification is None:
                stale = liveness.violation()
                if stale is not None:
                    if not recover_stale(stale):
                        return finish_stopped()
                    break
                continue
            waiting_job = _apply_notification(
                current_assembler,
                notification,
                event_observer,
            )
            liveness.notification_received(notification)
            try:
                drained_job = _drain_notifications(
                    current_assembler,
                    stop_token,
                    current_receive_notification,
                    event_observer,
                    notification_observer=liveness.notification_received,
                )
            except StratumConnectionError as error:
                if not recover_connection(error, StratumRecoveryStage.REPLACEMENT_WAIT):
                    return finish_stopped()
                break
            if stop_token.stop_requested:
                return finish_stopped()
            if drained_job is not None:
                waiting_job = drained_job
            if waiting_job is not None and select_pool_job(waiting_job):
                break


def _drain_notifications(
    assembler: MiningJobAssembler,
    stop_token: StopToken,
    receive_notification: NotificationReceiver,
    observer: ContinuousMiningObserver,
    *,
    notification_observer: Callable[[MiningNotification], None] | None = None,
) -> MiningJob | None:
    selected_job: MiningJob | None = None
    while not stop_token.stop_requested:
        notification = receive_notification(0.0)
        if notification is None:
            break
        received_job = _apply_notification(assembler, notification, observer)
        if notification_observer is not None:
            notification_observer(notification)
        if received_job is not None:
            selected_job = received_job
    return selected_job


def _apply_notification(
    assembler: MiningJobAssembler,
    notification: object,
    observer: ContinuousMiningObserver,
) -> MiningJob | None:
    if not isinstance(
        notification,
        (SetDifficultyNotification, MiningNotifyNotification),
    ):
        raise ContinuousMiningError("unsupported parsed Stratum notification")
    observer.notification_received(notification)
    if isinstance(notification, SetDifficultyNotification):
        assembler.apply_difficulty(notification)
        return None
    return assembler.build_job(notification)


def _validate_run_inputs(
    plan: object,
    assembler: object,
    initial_job: object,
    extra_nonce_2: object,
    stop_token: object,
    receive_notification: object,
    submit_share: object,
    strategy: object,
    prepare_work: object,
    search_range: object,
    recover_session: object,
    recover_stale_session: object,
    recovery_statistics: object,
    liveness_clock: object,
    pacing_clock: object,
) -> None:
    if not isinstance(plan, ContinuousMiningPlan):
        raise ContinuousMiningValidationError("plan must be a ContinuousMiningPlan")
    if not isinstance(assembler, MiningJobAssembler):
        raise ContinuousMiningValidationError("assembler must be a MiningJobAssembler")
    if not isinstance(initial_job, MiningJob):
        raise ContinuousMiningValidationError("initial_job must be a MiningJob")
    if not isinstance(extra_nonce_2, str) or not extra_nonce_2:
        raise ContinuousMiningValidationError("extra_nonce_2 must be a nonempty string")
    if not isinstance(stop_token, StopToken):
        raise ContinuousMiningValidationError("stop_token must expose stop_requested")
    if not isinstance(strategy, MiningSearchStrategy):
        raise ContinuousMiningValidationError("strategy must implement MiningSearchStrategy")
    for callback, name in (
        (receive_notification, "receive_notification"),
        (submit_share, "submit_share"),
        (prepare_work, "prepare_work"),
        (search_range, "search_range"),
        (liveness_clock, "liveness_clock"),
        (pacing_clock, "pacing_clock"),
    ):
        if not callable(callback):
            raise ContinuousMiningValidationError(f"{name} must be callable")
    if (recover_session is None) != (recovery_statistics is None):
        raise ContinuousMiningValidationError(
            "recover_session and recovery_statistics must be provided together"
        )
    liveness_enabled = (
        plan.max_server_silence_seconds is not None or plan.max_job_age_seconds is not None
    )
    if liveness_enabled and recover_stale_session is None:
        raise ContinuousMiningValidationError(
            "recover_stale_session is required when liveness limits are configured"
        )
    for callback, name in (
        (recover_session, "recover_session"),
        (recover_stale_session, "recover_stale_session"),
        (recovery_statistics, "recovery_statistics"),
    ):
        if callback is not None and not callable(callback):
            raise ContinuousMiningValidationError(f"{name} must be callable")


def _validate_search_result(
    result: object,
    start_nonce: int,
    stop_nonce: int,
) -> None:
    if not isinstance(result, NonceSearchResult):
        raise ContinuousMiningValidationError("search_range must return a NonceSearchResult")
    if result.start_nonce != start_nonce or result.stop_nonce != stop_nonce:
        raise ContinuousMiningValidationError(
            "search_range result must describe the requested nonce range"
        )


def _controlled_stop_outcome(stop_token: StopToken) -> ContinuousMiningOutcome:
    runtime_limit_reached = getattr(stop_token, "runtime_limit_reached", False)
    if not isinstance(runtime_limit_reached, bool):
        raise ContinuousMiningValidationError("runtime_limit_reached must be a Boolean")
    if runtime_limit_reached:
        return ContinuousMiningOutcome.RUNTIME_LIMIT_REACHED
    return ContinuousMiningOutcome.STOPPED_BY_USER


def _create_strategy_cursor(
    strategy: MiningSearchStrategy,
    plan: ContinuousMiningPlan,
) -> SearchStrategyCursor:
    try:
        cursor = strategy.create_cursor(plan.start_nonce, plan.chunk_size)
    except (SearchStrategyValidationError, SearchStrategyExecutionError):
        raise
    except Exception as exc:
        raise SearchStrategyExecutionError("search strategy cursor creation failed") from exc
    if not isinstance(cursor, SearchStrategyCursor):
        raise SearchStrategyExecutionError("search strategy returned an invalid cursor")
    return cursor


def _next_strategy_assignment(cursor: SearchStrategyCursor) -> SearchAssignment:
    try:
        assignment = cursor.next_assignment()
    except (SearchStrategyValidationError, SearchStrategyExecutionError):
        raise
    except Exception as exc:
        raise SearchStrategyExecutionError("search strategy assignment failed") from exc
    if not isinstance(assignment, SearchAssignment):
        raise SearchStrategyExecutionError("search strategy exhausted without new work")
    return assignment


def _validate_integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContinuousMiningValidationError(f"{name} must be an integer")
    return value


def _validate_nonnegative_integer(value: object, name: str) -> None:
    parsed = _validate_integer(value, name)
    if parsed < 0:
        raise ContinuousMiningValidationError(f"{name} must be nonnegative")
