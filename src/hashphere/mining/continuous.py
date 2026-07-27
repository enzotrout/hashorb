"""Continuous synchronous mining orchestration over bounded nonce chunks."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from hashphere.mining.job import MiningJob, MiningJobAssembler
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

type MiningNotification = SetDifficultyNotification | MiningNotifyNotification
type NotificationReceiver = Callable[[float], MiningNotification | None]
type WorkPreparer = Callable[[MiningJob, str], PreparedMiningWork]
type RangeSearcher = Callable[[PreparedMiningWork, int, int], NonceSearchResult]
type ShareSubmitter = Callable[[PreparedMiningWork, NonceSearchMatch], bool]

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
            ("total_hashes_checked", self.total_hashes_checked),
            ("total_elapsed_ns", self.total_elapsed_ns),
        ):
            _validate_nonnegative_integer(value, name)
        if self.jobs_used > self.chunks_completed:
            raise ContinuousMiningValidationError("jobs_used cannot exceed chunks_completed")
        if self.job_replacements > max(0, self.jobs_used - 1):
            raise ContinuousMiningValidationError(
                "job_replacements cannot exceed searched job transitions"
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
            total_hashes_checked=0,
            total_elapsed_ns=0,
        )
    current_job = initial_job
    current_work = prepare_work(current_job, extra_nonce_2)
    next_nonce = plan.start_nonce
    current_work_searched = False
    chunks_completed = 0
    jobs_used = 0
    replacements = 0
    total_hashes = 0
    total_elapsed_ns = 0

    def finish(
        outcome: ContinuousMiningOutcome,
        *,
        match: NonceSearchMatch | None = None,
        pool_accepted: bool | None = None,
    ) -> ContinuousMiningResult:
        return ContinuousMiningResult(
            outcome=outcome,
            initial_job=initial_job,
            final_job=current_job,
            match=match,
            pool_accepted=pool_accepted,
            chunks_completed=chunks_completed,
            jobs_used=jobs_used,
            job_replacements=replacements,
            total_hashes_checked=total_hashes,
            total_elapsed_ns=total_elapsed_ns,
        )

    def finish_stopped() -> ContinuousMiningResult:
        event_observer.stop_requested()
        return finish(ContinuousMiningOutcome.STOPPED_BY_USER)

    while True:
        if stop_token.stop_requested:
            return finish_stopped()
        if plan.max_chunks is not None and chunks_completed >= plan.max_chunks:
            return finish(ContinuousMiningOutcome.CHUNK_LIMIT_REACHED)

        if next_nonce == _NONCE_LIMIT:
            event_observer.nonce_space_exhausted(current_work)
            event_observer.waiting_for_job(current_work)
            selected_job = _wait_for_replacement(
                assembler,
                stop_token,
                receive_notification,
                event_observer,
            )
            if selected_job is None:
                return finish_stopped()
            replacement_work = prepare_work(selected_job, extra_nonce_2)
            replacements += 1
            event_observer.job_replaced(current_job, selected_job, replacements)
            current_job = selected_job
            current_work = replacement_work
            next_nonce = plan.start_nonce
            current_work_searched = False
            continue

        stop_nonce = min(next_nonce + plan.chunk_size, _NONCE_LIMIT)
        if not current_work_searched:
            jobs_used += 1
            current_work_searched = True
        event_observer.chunk_started(current_work, next_nonce, stop_nonce)
        chunk_result = search_range(current_work, next_nonce, stop_nonce)
        _validate_search_result(chunk_result, next_nonce, stop_nonce)
        event_observer.chunk_completed(current_work, chunk_result)
        chunks_completed += 1
        total_hashes += chunk_result.hashes_checked
        total_elapsed_ns += chunk_result.elapsed_ns

        if chunk_result.match is not None:
            match = chunk_result.match
            event_observer.candidate_found(current_work, match)
            accepted = submit_share(current_work, match)
            if not isinstance(accepted, bool):
                raise ContinuousMiningValidationError("submit_share must return an actual Boolean")
            event_observer.submission_completed(current_work, match, accepted)
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

        selected_job = _drain_notifications(
            assembler,
            stop_token,
            receive_notification,
            event_observer,
        )
        if stop_token.stop_requested:
            return finish_stopped()
        if selected_job is not None:
            replacement_work = prepare_work(selected_job, extra_nonce_2)
            replacements += 1
            event_observer.job_replaced(current_job, selected_job, replacements)
            current_job = selected_job
            current_work = replacement_work
            next_nonce = plan.start_nonce
            current_work_searched = False


def _wait_for_replacement(
    assembler: MiningJobAssembler,
    stop_token: StopToken,
    receive_notification: NotificationReceiver,
    observer: ContinuousMiningObserver,
) -> MiningJob | None:
    selected_job: MiningJob | None = None
    while selected_job is None:
        if stop_token.stop_requested:
            return None
        notification = receive_notification(_NOTIFICATION_WAIT_SECONDS)
        if notification is None:
            continue
        selected_job = _apply_notification(assembler, notification, observer)
        drained_job = _drain_notifications(
            assembler,
            stop_token,
            receive_notification,
            observer,
        )
        if drained_job is not None:
            selected_job = drained_job
    if stop_token.stop_requested:
        return None
    return selected_job


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
