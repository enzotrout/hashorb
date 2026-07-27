"""Finite nonce-chunk orchestration with between-chunk job replacement."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

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
type NotificationPoll = Callable[[], MiningNotification | None]
type WorkPreparer = Callable[[MiningJob, str], PreparedMiningWork]
type RangeSearcher = Callable[[PreparedMiningWork, int, int], NonceSearchResult]
type ShareSubmitter = Callable[[PreparedMiningWork, NonceSearchMatch], bool]

_NONCE_LIMIT = 1 << 32
_MAX_NONCE = _NONCE_LIMIT - 1
_NANOSECONDS_PER_SECOND = 1_000_000_000


class ChunkedMiningError(Exception):
    """Base error for bounded chunked-mining orchestration."""


class ChunkedMiningValidationError(ChunkedMiningError, ValueError):
    """Raised when chunked-mining inputs or callback results are invalid."""


@dataclass(frozen=True, slots=True)
class ChunkedMiningPlan:
    """Validated global hash budget and per-job nonce-range policy."""

    start_nonce: int
    chunk_size: int
    max_hashes: int

    def __post_init__(self) -> None:
        """Validate a finite plan inside the unsigned 32-bit nonce space."""

        _validate_integer(self.start_nonce, "start_nonce")
        _validate_integer(self.chunk_size, "chunk_size")
        _validate_integer(self.max_hashes, "max_hashes")
        if not 0 <= self.start_nonce <= _MAX_NONCE:
            raise ChunkedMiningValidationError("start_nonce must be between 0 and 0xffffffff")
        if not 1 <= self.chunk_size <= _NONCE_LIMIT:
            raise ChunkedMiningValidationError("chunk_size must be between 1 and 2**32")
        if not 1 <= self.max_hashes <= _NONCE_LIMIT:
            raise ChunkedMiningValidationError("max_hashes must be between 1 and 2**32")
        if self.start_nonce + self.max_hashes > _NONCE_LIMIT:
            raise ChunkedMiningValidationError(
                "max_hashes exceeds the nonce space remaining after start_nonce"
            )


@dataclass(frozen=True, slots=True)
class ChunkedMiningResult:
    """Immutable aggregate outcome of one finite chunked-mining invocation."""

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
        """Validate aggregate counters and optional submission state."""

        if not isinstance(self.initial_job, MiningJob):
            raise ChunkedMiningValidationError("initial_job must be a MiningJob")
        if not isinstance(self.final_job, MiningJob):
            raise ChunkedMiningValidationError("final_job must be a MiningJob")
        for name, value in (
            ("chunks_completed", self.chunks_completed),
            ("jobs_used", self.jobs_used),
            ("job_replacements", self.job_replacements),
            ("total_hashes_checked", self.total_hashes_checked),
            ("total_elapsed_ns", self.total_elapsed_ns),
        ):
            _validate_nonnegative_integer(value, name)
        if self.chunks_completed <= 0:
            raise ChunkedMiningValidationError("chunks_completed must be positive")
        if self.jobs_used <= 0:
            raise ChunkedMiningValidationError("jobs_used must be positive")

        if self.match is None:
            if self.pool_accepted is not None:
                raise ChunkedMiningValidationError(
                    "unmatched results cannot contain submission state"
                )
        else:
            if not isinstance(self.match, NonceSearchMatch):
                raise ChunkedMiningValidationError("match must be a NonceSearchMatch or None")
            if not isinstance(self.pool_accepted, bool):
                raise ChunkedMiningValidationError(
                    "pool_accepted must be Boolean when a match exists"
                )

    @property
    def hash_budget_exhausted(self) -> bool:
        """Return whether the invocation ended without a target match."""

        return self.match is None

    @property
    def candidates_found(self) -> int:
        """Return the bounded candidate count."""

        return int(self.match is not None)

    @property
    def submissions_performed(self) -> int:
        """Return the bounded submission count."""

        return int(self.pool_accepted is not None)

    @property
    def weighted_hashes_per_second(self) -> float | None:
        """Return the aggregate rate derived from total hashes and elapsed time."""

        if self.total_elapsed_ns == 0:
            return None
        return self.total_hashes_checked * _NANOSECONDS_PER_SECOND / self.total_elapsed_ns


class ChunkedMiningObserver(Protocol):
    """Passive callbacks for reporting orchestration events."""

    def notification_received(self, notification: MiningNotification) -> None:
        """Observe one between-chunk notification in arrival order."""

    def chunk_started(
        self,
        work: PreparedMiningWork,
        start_nonce: int,
        stop_nonce: int,
    ) -> None:
        """Observe the exact range immediately before search."""

    def chunk_completed(
        self,
        work: PreparedMiningWork,
        result: NonceSearchResult,
    ) -> None:
        """Observe one completed search result."""

    def job_replaced(
        self,
        previous_job: MiningJob,
        new_job: MiningJob,
        replacement_index: int,
    ) -> None:
        """Observe an announced job replacing the current prepared work."""

    def candidate_found(
        self,
        work: PreparedMiningWork,
        match: NonceSearchMatch,
    ) -> None:
        """Observe the first candidate before immediate submission."""

    def submission_completed(
        self,
        work: PreparedMiningWork,
        match: NonceSearchMatch,
        accepted: bool,
    ) -> None:
        """Observe the single pool response."""


class NullChunkedMiningObserver:
    """No-op observer used when orchestration events are not needed."""

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
        """Discard a job-replacement observation."""

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


def run_chunked_mining(
    plan: ChunkedMiningPlan,
    assembler: MiningJobAssembler,
    initial_job: MiningJob,
    extra_nonce_2: str,
    *,
    poll_notification: NotificationPoll,
    submit_share: ShareSubmitter,
    observer: ChunkedMiningObserver | None = None,
    prepare_work: WorkPreparer = prepare_mining_work,
    search_range: RangeSearcher = search_nonce_range,
) -> ChunkedMiningResult:
    """Search a finite global budget and process notifications between chunks."""

    if not isinstance(plan, ChunkedMiningPlan):
        raise ChunkedMiningValidationError("plan must be a ChunkedMiningPlan")
    if not isinstance(assembler, MiningJobAssembler):
        raise ChunkedMiningValidationError("assembler must be a MiningJobAssembler")
    if not isinstance(initial_job, MiningJob):
        raise ChunkedMiningValidationError("initial_job must be a MiningJob")
    if not isinstance(extra_nonce_2, str) or not extra_nonce_2:
        raise ChunkedMiningValidationError("extra_nonce_2 must be a nonempty string")
    for callback, name in (
        (poll_notification, "poll_notification"),
        (submit_share, "submit_share"),
        (prepare_work, "prepare_work"),
        (search_range, "search_range"),
    ):
        if not callable(callback):
            raise ChunkedMiningValidationError(f"{name} must be callable")

    event_observer: ChunkedMiningObserver = (
        NullChunkedMiningObserver() if observer is None else observer
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

    while total_hashes < plan.max_hashes:
        remaining_budget = plan.max_hashes - total_hashes
        hashes_in_chunk = min(plan.chunk_size, remaining_budget)
        stop_nonce = next_nonce + hashes_in_chunk
        if stop_nonce > _NONCE_LIMIT:
            raise ChunkedMiningError("next chunk exceeds the unsigned 32-bit nonce space")

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
                raise ChunkedMiningValidationError("submit_share must return an actual Boolean")
            event_observer.submission_completed(current_work, match, accepted)
            return ChunkedMiningResult(
                initial_job=initial_job,
                final_job=current_job,
                match=match,
                pool_accepted=accepted,
                chunks_completed=chunks_completed,
                jobs_used=jobs_used,
                job_replacements=replacements,
                total_hashes_checked=total_hashes,
                total_elapsed_ns=total_elapsed_ns,
            )

        next_nonce = stop_nonce
        if total_hashes == plan.max_hashes:
            break

        selected_replacement: MiningJob | None = None
        while True:
            notification = poll_notification()
            if notification is None:
                break
            if not isinstance(
                notification,
                (SetDifficultyNotification, MiningNotifyNotification),
            ):
                raise ChunkedMiningError("unsupported parsed Stratum notification")
            event_observer.notification_received(notification)
            if isinstance(notification, SetDifficultyNotification):
                assembler.apply_difficulty(notification)
                continue

            selected_replacement = assembler.build_job(notification)

        if selected_replacement is not None:
            replacement_work = prepare_work(selected_replacement, extra_nonce_2)
            replacements += 1
            event_observer.job_replaced(
                current_job,
                selected_replacement,
                replacements,
            )
            current_job = selected_replacement
            current_work = replacement_work
            next_nonce = plan.start_nonce
            current_work_searched = False

    return ChunkedMiningResult(
        initial_job=initial_job,
        final_job=current_job,
        match=None,
        pool_accepted=None,
        chunks_completed=chunks_completed,
        jobs_used=jobs_used,
        job_replacements=replacements,
        total_hashes_checked=total_hashes,
        total_elapsed_ns=total_elapsed_ns,
    )


def _validate_search_result(
    result: object,
    start_nonce: int,
    stop_nonce: int,
) -> None:
    if not isinstance(result, NonceSearchResult):
        raise ChunkedMiningValidationError("search_range must return a NonceSearchResult")
    if result.start_nonce != start_nonce or result.stop_nonce != stop_nonce:
        raise ChunkedMiningValidationError(
            "search_range result must describe the requested nonce range"
        )


def _validate_integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ChunkedMiningValidationError(f"{name} must be an integer")
    return value


def _validate_nonnegative_integer(value: object, name: str) -> None:
    parsed = _validate_integer(value, name)
    if parsed < 0:
        raise ChunkedMiningValidationError(f"{name} must be nonnegative")
