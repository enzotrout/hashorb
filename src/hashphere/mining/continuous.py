"""Continuous synchronous mining orchestration over bounded nonce chunks."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from hashphere.mining.job import MiningJob, MiningJobAssembler
from hashphere.mining.progression import (
    MiningJobContextIdentity,
    MiningWorkCursor,
    mining_job_context_identity,
    mining_work_identity,
    prepare_work_variant,
)
from hashphere.mining.recovery import (
    StratumMiningSession,
    StratumRecoveryStage,
    StratumRecoveryStatistics,
)
from hashphere.mining.search import (
    NonceSearchMatch,
    NonceSearchResult,
    PreparedMiningWork,
    prepare_mining_work,
    search_nonce_range,
)
from hashphere.network.stratum.messages import (
    MiningNotifyNotification,
    SetDifficultyNotification,
)
from hashphere.network.stratum.transport import StratumConnectionError

type MiningNotification = SetDifficultyNotification | MiningNotifyNotification
type NotificationReceiver = Callable[[float], MiningNotification | None]
type WorkPreparer = Callable[[MiningJob, str], PreparedMiningWork]
type RangeSearcher = Callable[[PreparedMiningWork, int, int], NonceSearchResult]
type ShareSubmitter = Callable[[PreparedMiningWork, NonceSearchMatch], bool]
type SessionRecoverer = Callable[
    [StratumConnectionError, StratumRecoveryStage],
    StratumMiningSession | None,
]
type RecoveryStatisticsProvider = Callable[[], StratumRecoveryStatistics]

_NONCE_LIMIT = 1 << 32
_MAX_NONCE = _NONCE_LIMIT - 1
_MAX_CHUNKS = _NONCE_LIMIT
_NANOSECONDS_PER_SECOND = 1_000_000_000
_NOTIFICATION_WAIT_SECONDS = 0.25


class ContinuousMiningError(Exception):
    """Base error for continuous mining orchestration."""


class ContinuousMiningValidationError(ContinuousMiningError, ValueError):
    """Raised when continuous mining input violates a public invariant."""


class ContinuousMiningOutcome(StrEnum):
    """Controlled terminal outcomes for one continuous mining session."""

    STOPPED_BY_USER = "stopped_by_user"
    CHUNK_LIMIT_REACHED = "chunk_limit_reached"
    SHARE_ACCEPTED = "share_accepted"
    SHARE_REJECTED = "share_rejected"


@dataclass(frozen=True, slots=True)
class ContinuousMiningPlan:
    """Validated nonce-chunk policy with an optional searched-chunk limit."""

    start_nonce: int
    chunk_size: int
    max_chunks: int | None = None

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
        """Return the session candidate count, which is bounded to one."""

        return int(self.match is not None)

    @property
    def submissions_performed(self) -> int:
        """Return the session submission count, which is bounded to one."""

        return int(self.pool_accepted is not None)

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

    __slots__ = ("_stop_requested",)

    def __init__(self) -> None:
        self._stop_requested = False

    @property
    def stop_requested(self) -> bool:
        """Return whether a caller has requested graceful shutdown."""

        return self._stop_requested

    def request_stop(self) -> None:
        """Request shutdown; repeated requests have no additional effect."""

        self._stop_requested = True


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
    ) -> None:
        """Observe the one terminal pool response."""

    def stop_requested(self) -> None:
        """Observe a controlled cooperative stop."""

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
    ) -> None:
        """Discard a submission observation."""

    def stop_requested(self) -> None:
        """Discard a stop observation."""

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
    prepare_work: WorkPreparer = prepare_mining_work,
    search_range: RangeSearcher = search_nonce_range,
    recover_session: SessionRecoverer | None = None,
    recovery_statistics: RecoveryStatisticsProvider | None = None,
) -> ContinuousMiningResult:
    """Mine sequential chunks until a controlled terminal outcome occurs."""

    _validate_run_inputs(
        plan,
        assembler,
        initial_job,
        extra_nonce_2,
        stop_token,
        receive_notification,
        submit_share,
        prepare_work,
        search_range,
        recover_session,
        recovery_statistics,
    )
    event_observer: ContinuousMiningObserver = (
        NullContinuousMiningObserver() if observer is None else observer
    )
    if stop_token.stop_requested:
        event_observer.stop_requested()
        return ContinuousMiningResult(
            outcome=ContinuousMiningOutcome.STOPPED_BY_USER,
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
    next_nonce = plan.start_nonce
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
        )

    def observe_stop() -> None:
        nonlocal stop_observed
        if stop_observed:
            return
        stop_observed = True
        event_observer.stop_requested()

    def finish_stopped() -> ContinuousMiningResult:
        observe_stop()
        return finish(ContinuousMiningOutcome.STOPPED_BY_USER)

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
        nonlocal next_nonce
        nonlocal replacements

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
        next_nonce = plan.start_nonce
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
        nonlocal next_nonce
        nonlocal replacements

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
        next_nonce = plan.start_nonce

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

    while True:
        if stop_token.stop_requested:
            return finish_stopped()
        if plan.max_chunks is not None and chunks_completed >= plan.max_chunks:
            return finish(ContinuousMiningOutcome.CHUNK_LIMIT_REACHED)

        stop_nonce = min(next_nonce + plan.chunk_size, _NONCE_LIMIT)
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
        event_observer.chunk_started(current_work, next_nonce, stop_nonce)
        chunk_result = search_range(current_work, next_nonce, stop_nonce)
        _validate_search_result(chunk_result, next_nonce, stop_nonce)
        event_observer.chunk_completed(current_work, chunk_result)
        chunks_completed += 1
        total_hashes += chunk_result.hashes_checked
        total_elapsed_ns += chunk_result.elapsed_ns

        if chunk_result.match is not None:
            match = chunk_result.match
            if stop_token.stop_requested:
                observe_stop()
            event_observer.candidate_found(current_work, match)
            if stop_token.stop_requested:
                observe_stop()
            accepted = current_submit_share(current_work, match)
            if not isinstance(accepted, bool):
                raise ContinuousMiningValidationError("submit_share must return an actual Boolean")
            if stop_token.stop_requested:
                observe_stop()
            event_observer.submission_completed(current_work, match, accepted)
            if stop_token.stop_requested:
                observe_stop()
            outcome = (
                ContinuousMiningOutcome.SHARE_ACCEPTED
                if accepted
                else ContinuousMiningOutcome.SHARE_REJECTED
            )
            return finish(outcome, match=match, pool_accepted=accepted)

        next_nonce = stop_nonce
        if stop_token.stop_requested:
            return finish_stopped()
        if plan.max_chunks is not None and chunks_completed >= plan.max_chunks:
            return finish(ContinuousMiningOutcome.CHUNK_LIMIT_REACHED)

        if next_nonce == _NONCE_LIMIT:
            event_observer.nonce_space_exhausted(current_work)

        try:
            selected_job = _drain_notifications(
                current_assembler,
                stop_token,
                current_receive_notification,
                event_observer,
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

        if next_nonce < _NONCE_LIMIT:
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
            next_nonce = plan.start_nonce
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
                continue
            waiting_job = _apply_notification(
                current_assembler,
                notification,
                event_observer,
            )
            try:
                drained_job = _drain_notifications(
                    current_assembler,
                    stop_token,
                    current_receive_notification,
                    event_observer,
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
) -> MiningJob | None:
    selected_job: MiningJob | None = None
    while not stop_token.stop_requested:
        notification = receive_notification(0.0)
        if notification is None:
            break
        received_job = _apply_notification(assembler, notification, observer)
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
    prepare_work: object,
    search_range: object,
    recover_session: object,
    recovery_statistics: object,
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
    for callback, name in (
        (receive_notification, "receive_notification"),
        (submit_share, "submit_share"),
        (prepare_work, "prepare_work"),
        (search_range, "search_range"),
    ):
        if not callable(callback):
            raise ContinuousMiningValidationError(f"{name} must be callable")
    if (recover_session is None) != (recovery_statistics is None):
        raise ContinuousMiningValidationError(
            "recover_session and recovery_statistics must be provided together"
        )
    for callback, name in (
        (recover_session, "recover_session"),
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


def _validate_integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContinuousMiningValidationError(f"{name} must be an integer")
    return value


def _validate_nonnegative_integer(value: object, name: str) -> None:
    parsed = _validate_integer(value, name)
    if parsed < 0:
        raise ContinuousMiningValidationError(f"{name} must be nonnegative")
