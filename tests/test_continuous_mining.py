"""Tests for stop-aware continuous mining orchestration."""

from __future__ import annotations

from collections import deque
from dataclasses import FrozenInstanceError, dataclass, field

import pytest

from hashphere.mining import (
    MAX_RUNTIME_SECONDS,
    ContinuousMiningError,
    ContinuousMiningOutcome,
    ContinuousMiningPlan,
    ContinuousMiningResult,
    ContinuousMiningValidationError,
    MiningJob,
    MiningJobAssembler,
    MiningSearchStrategy,
    NonceSearchMatch,
    NonceSearchResult,
    OrbitingBitSearchStrategy,
    PreparedMiningWork,
    SearchStrategyCursor,
    SearchStrategyExecutionError,
    SequentialSearchStrategy,
    StopController,
    StratumMiningSession,
    StratumRecoveryStage,
    StratumRecoveryStatistics,
    run_continuous_mining,
)
from hashphere.mining.continuous import MiningNotification
from hashphere.network.stratum import (
    MiningNotifyNotification,
    SetDifficultyNotification,
    StratumClientState,
    StratumConnectionError,
    StratumProtocolError,
    SubscribeResult,
)


def notification(
    job_id: str,
    *,
    clean_jobs: bool = True,
    network_time: str = "65f04abc",
) -> MiningNotifyNotification:
    """Build one valid synthetic Stratum job notification."""

    return MiningNotifyNotification(
        job_id=job_id,
        previous_block_hash="00" * 32,
        coinbase_part_1="01000000cafebabe",
        coinbase_part_2="ffffffffdeadbeef",
        merkle_branches=("11" * 32,),
        version="20000000",
        network_bits="170fffff",
        network_time=network_time,
        clean_jobs=clean_jobs,
    )


def make_assembler(
    difficulty: int | float = 100,
    *,
    extra_nonce_2_size: int = 4,
) -> MiningJobAssembler:
    """Build an assembler with an established difficulty."""

    assembler = MiningJobAssembler(
        SubscribeResult(
            subscriptions=(("mining.notify", "subscription-id"),),
            extra_nonce_1="08000002",
            extra_nonce_2_size=extra_nonce_2_size,
        )
    )
    assembler.apply_difficulty(SetDifficultyNotification(difficulty=difficulty))
    return assembler


def make_work(job: MiningJob, extra_nonce_2: str) -> PreparedMiningWork:
    """Build deterministic prepared work without cryptographic calculations."""

    marker = bytes.fromhex(job.network_time + extra_nonce_2)
    job_marker = job.job_id.encode("ascii")
    header_prefix = (marker + job_marker + bytes(76))[:76]
    return PreparedMiningWork(
        job_id=job.job_id,
        extra_nonce_2=extra_nonce_2,
        network_time=job.network_time,
        header_prefix=header_prefix,
        network_target=1,
        share_target=int(job.difficulty),
    )


@dataclass
class Harness:
    """Deterministic callback, stop, and observer state."""

    controller: StopController = field(default_factory=StopController)
    notifications: deque[object] = field(default_factory=deque)
    timed_notifications: deque[object] = field(default_factory=deque)
    elapsed_values: deque[int] = field(default_factory=deque)
    match_call: int | None = None
    match_flags: tuple[bool, bool] = (True, False)
    accepted: bool = True
    stop_during_search_call: int | None = None
    stop_during_receive_call: int | None = None
    stop_during_wait: bool = False
    stop_during_prepare_call: int | None = None
    prepare_calls: list[tuple[MiningJob, str]] = field(default_factory=list)
    search_calls: list[tuple[PreparedMiningWork, int, int]] = field(default_factory=list)
    receive_timeouts: list[float] = field(default_factory=list)
    submit_calls: list[tuple[PreparedMiningWork, NonceSearchMatch]] = field(default_factory=list)
    observations: list[tuple[object, ...]] = field(default_factory=list)

    def prepare(self, job: MiningJob, extra_nonce_2: str) -> PreparedMiningWork:
        self.prepare_calls.append((job, extra_nonce_2))
        if self.stop_during_prepare_call == len(self.prepare_calls):
            self.controller.request_stop()
        return make_work(job, extra_nonce_2)

    def search(
        self,
        work: PreparedMiningWork,
        start_nonce: int,
        stop_nonce: int,
    ) -> NonceSearchResult:
        self.search_calls.append((work, start_nonce, stop_nonce))
        call_number = len(self.search_calls)
        if self.stop_during_search_call == call_number:
            self.controller.request_stop()
        elapsed_ns = self.elapsed_values.popleft() if self.elapsed_values else 100
        match: NonceSearchMatch | None = None
        hashes_checked = stop_nonce - start_nonce
        if self.match_call == call_number:
            meets_share, meets_network = self.match_flags
            match = NonceSearchMatch(
                nonce=start_nonce,
                block_hash=bytes.fromhex("12" * 32),
                meets_share_target=meets_share,
                meets_network_target=meets_network,
            )
            hashes_checked = 1
        return NonceSearchResult(
            start_nonce=start_nonce,
            stop_nonce=stop_nonce,
            hashes_checked=hashes_checked,
            elapsed_ns=elapsed_ns,
            match=match,
        )

    def receive(self, timeout_seconds: float) -> MiningNotification | None:
        self.receive_timeouts.append(timeout_seconds)
        if self.stop_during_receive_call == len(self.receive_timeouts):
            self.controller.request_stop()
        if timeout_seconds > 0.0 and self.stop_during_wait:
            self.controller.request_stop()
        values = self.timed_notifications if timeout_seconds > 0.0 else self.notifications
        if not values:
            return None
        value = values.popleft()
        if isinstance(value, BaseException):
            raise value
        return value  # type: ignore[return-value]

    def submit(self, work: PreparedMiningWork, match: NonceSearchMatch) -> bool:
        self.submit_calls.append((work, match))
        return self.accepted

    def notification_received(self, received: MiningNotification) -> None:
        if isinstance(received, SetDifficultyNotification):
            self.observations.append(("difficulty", received.difficulty))
        else:
            self.observations.append(("job", received.job_id))

    def chunk_started(
        self,
        work: PreparedMiningWork,
        start_nonce: int,
        stop_nonce: int,
    ) -> None:
        self.observations.append(("started", work.job_id, start_nonce, stop_nonce))

    def chunk_completed(
        self,
        work: PreparedMiningWork,
        result: NonceSearchResult,
    ) -> None:
        self.observations.append(("completed", work.job_id, result.start_nonce, result.stop_nonce))

    def job_replaced(
        self,
        previous_job: MiningJob,
        new_job: MiningJob,
        replacement_index: int,
    ) -> None:
        self.observations.append(
            ("replaced", previous_job.job_id, new_job.job_id, replacement_index)
        )

    def candidate_found(
        self,
        work: PreparedMiningWork,
        match: NonceSearchMatch,
    ) -> None:
        self.observations.append(("candidate", work.job_id, match.nonce))

    def submission_completed(
        self,
        work: PreparedMiningWork,
        match: NonceSearchMatch,
        accepted: bool,
    ) -> None:
        self.observations.append(("submitted", work.job_id, match.nonce, accepted))

    def stop_requested(self) -> None:
        self.observations.append(("stopped",))

    def nonce_space_exhausted(self, work: PreparedMiningWork) -> None:
        self.observations.append(("exhausted", work.job_id))

    def waiting_for_job(self, work: PreparedMiningWork) -> None:
        self.observations.append(("waiting", work.job_id))

    def work_advanced(
        self,
        reason: str,
        work_variant_index: int,
        extra_nonce_2_advance_count: int,
        network_time_roll_count: int,
    ) -> None:
        self.observations.append(
            (
                "work",
                reason,
                work_variant_index,
                extra_nonce_2_advance_count,
                network_time_roll_count,
            )
        )

    def extra_nonce_2_cycle_completed(self, cycle_count: int) -> None:
        self.observations.append(("extra-cycle", cycle_count))

    def network_time_rolled(self, roll_count: int) -> None:
        self.observations.append(("time-roll", roll_count))

    def duplicate_work_ignored(self, duplicate_count: int, reason: str) -> None:
        self.observations.append(("duplicate", duplicate_count, reason))


class TrackingStrategy(SequentialSearchStrategy):
    """Record each legitimate per-work cursor reset."""

    def __init__(self) -> None:
        self.create_calls: list[tuple[int, int, int]] = []

    def create_cursor(
        self,
        start_nonce: int,
        chunk_size: int,
        *,
        nonce_limit: int = 1 << 32,
    ) -> SearchStrategyCursor:
        self.create_calls.append((start_nonce, chunk_size, nonce_limit))
        return super().create_cursor(start_nonce, chunk_size, nonce_limit=nonce_limit)


class RecoveredClient:
    """Authorized socket-free client owned by one recovered test session."""

    def __init__(
        self,
        result: SubscribeResult,
        *,
        notifications: list[object | None] | None = None,
        submit_failure: BaseException | None = None,
    ) -> None:
        self.result = result
        self.notifications = deque(notifications or [])
        self.submit_failure = submit_failure
        self.state = StratumClientState.AUTHORIZED
        self.submit_calls: list[tuple[str, str, str, int]] = []
        self.close_calls = 0

    def handshake(self) -> SubscribeResult:
        return self.result

    def poll_notification(
        self,
        timeout_seconds: float = 0.0,
    ) -> SetDifficultyNotification | MiningNotifyNotification | None:
        del timeout_seconds
        if not self.notifications:
            return None
        value = self.notifications.popleft()
        if isinstance(value, BaseException):
            raise value
        return value  # type: ignore[return-value]

    def submit_share(
        self,
        job_id: str,
        extra_nonce_2: str,
        network_time: str,
        nonce: int,
    ) -> bool:
        self.submit_calls.append((job_id, extra_nonce_2, network_time, nonce))
        if self.submit_failure is not None:
            raise self.submit_failure
        return True

    def close(self) -> None:
        self.close_calls += 1
        self.state = StratumClientState.DISCONNECTED


def recovered_session(
    *,
    job_id: str = "recovered-job",
    extra_nonce_2_size: int = 1,
    seed: str = "cd",
    network_time: str = "65f04abd",
    difficulty_value: int | float = 200,
    notifications: list[object | None] | None = None,
    submit_failure: BaseException | None = None,
    session_index: int = 2,
) -> tuple[StratumMiningSession, RecoveredClient]:
    """Build one fresh-session result for continuous recovery tests."""

    result = SubscribeResult(
        subscriptions=(("mining.notify", "subscription-id"),),
        extra_nonce_1="09000003",
        extra_nonce_2_size=extra_nonce_2_size,
    )
    assembler = MiningJobAssembler(result)
    assembler.apply_difficulty(SetDifficultyNotification(difficulty=difficulty_value))
    initial_job = assembler.build_job(notification(job_id, network_time=network_time))
    client = RecoveredClient(
        result,
        notifications=notifications,
        submit_failure=submit_failure,
    )
    return (
        StratumMiningSession(
            client=client,
            subscription=result,
            assembler=assembler,
            initial_job=initial_job,
            extra_nonce_2_seed=seed,
            session_index=session_index,
        ),
        client,
    )


def run_with_harness(
    plan: ContinuousMiningPlan,
    harness: Harness,
    *,
    difficulty: int | float = 100,
    extra_nonce_2_size: int = 4,
    extra_nonce_2: str | None = None,
    network_time: str = "65f04abc",
    strategy: MiningSearchStrategy | None = None,
) -> tuple[MiningJobAssembler, MiningJob, ContinuousMiningResult]:
    """Run continuous orchestration against one synthetic initial job."""

    assembler = make_assembler(
        difficulty,
        extra_nonce_2_size=extra_nonce_2_size,
    )
    initial_job = assembler.build_job(notification("initial-job", network_time=network_time))
    result = run_continuous_mining(
        plan,
        assembler,
        initial_job,
        "ab" * extra_nonce_2_size if extra_nonce_2 is None else extra_nonce_2,
        harness.controller,
        receive_notification=harness.receive,
        submit_share=harness.submit,
        observer=harness,
        strategy=strategy,
        prepare_work=harness.prepare,
        search_range=harness.search,
    )
    return assembler, initial_job, result


def test_plan_represents_unlimited_session_and_is_frozen_and_slotted() -> None:
    plan = ContinuousMiningPlan(start_nonce=0, chunk_size=100)

    assert plan.max_chunks is None
    assert plan.max_runtime_seconds is None
    assert not hasattr(plan, "__dict__")
    with pytest.raises(FrozenInstanceError):
        plan.chunk_size = 1  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("start_nonce", True),
        ("start_nonce", -1),
        ("start_nonce", 2**32),
        ("chunk_size", True),
        ("chunk_size", 0),
        ("chunk_size", 2**32 + 1),
        ("max_chunks", True),
        ("max_chunks", 0),
        ("max_chunks", 2**32 + 1),
        ("max_runtime_seconds", True),
        ("max_runtime_seconds", 0),
        ("max_runtime_seconds", -1),
        ("max_runtime_seconds", float("nan")),
        ("max_runtime_seconds", float("inf")),
        ("max_runtime_seconds", -float("inf")),
        ("max_runtime_seconds", MAX_RUNTIME_SECONDS + 1),
    ],
)
def test_plan_rejects_invalid_values(field_name: str, value: object) -> None:
    values: dict[str, object] = {
        "start_nonce": 0,
        "chunk_size": 1,
        "max_chunks": 1,
        "max_runtime_seconds": 1.0,
    }
    values[field_name] = value

    with pytest.raises(ContinuousMiningValidationError):
        ContinuousMiningPlan(**values)  # type: ignore[arg-type]


def test_sequential_chunks_are_gap_free_and_limit_counts_searches() -> None:
    harness = Harness(elapsed_values=deque([100, 300, 600]))

    _, _, result = run_with_harness(ContinuousMiningPlan(7, 2, 3), harness)

    assert [(start, stop) for _, start, stop in harness.search_calls] == [
        (7, 9),
        (9, 11),
        (11, 13),
    ]
    assert harness.receive_timeouts == [0.0, 0.0]
    assert len(harness.prepare_calls) == 1
    assert result.outcome is ContinuousMiningOutcome.CHUNK_LIMIT_REACHED
    assert result.chunks_completed == 3
    assert result.jobs_used == 1
    assert result.total_hashes_checked == 6
    assert result.total_elapsed_ns == 1000
    assert result.weighted_hashes_per_second == 6_000_000.0


def test_orbiting_bit_chunks_follow_exact_global_range_order() -> None:
    harness = Harness(elapsed_values=deque([10, 20, 30, 40, 50]))
    start_nonce = 2**32 - 50

    _, _, result = run_with_harness(
        ContinuousMiningPlan(start_nonce, 10, 5),
        harness,
        strategy=OrbitingBitSearchStrategy(),
    )

    assert [(start, stop) for _, start, stop in harness.search_calls] == [
        (start_nonce, start_nonce + 10),
        (start_nonce + 40, 2**32),
        (start_nonce + 20, start_nonce + 30),
        (start_nonce + 10, start_nonce + 20),
        (start_nonce + 30, start_nonce + 40),
    ]
    assert result.chunks_completed == 5
    assert result.total_hashes_checked == 50
    assert result.total_elapsed_ns == 150


def test_orbiting_bit_difficulty_only_preserves_current_cursor() -> None:
    harness = Harness(notifications=deque([SetDifficultyNotification(difficulty=200), None]))
    start_nonce = 2**32 - 40

    assembler, _, result = run_with_harness(
        ContinuousMiningPlan(start_nonce, 10, 2),
        harness,
        strategy=OrbitingBitSearchStrategy(),
    )

    assert [(start, stop) for _, start, stop in harness.search_calls] == [
        (start_nonce, start_nonce + 10),
        (start_nonce + 20, start_nonce + 30),
    ]
    assert assembler.current_difficulty == 200
    assert result.job_replacements == 0


def test_orbiting_bit_pool_job_has_priority_and_receives_fresh_cursor() -> None:
    harness = Harness(notifications=deque([notification("replacement"), None]))
    start_nonce = 2**32 - 40

    _, _, result = run_with_harness(
        ContinuousMiningPlan(start_nonce, 10, 2),
        harness,
        strategy=OrbitingBitSearchStrategy(),
    )

    assert [(work.job_id, start, stop) for work, start, stop in harness.search_calls] == [
        ("initial-job", start_nonce, start_nonce + 10),
        ("replacement", start_nonce, start_nonce + 10),
    ]
    assert result.job_replacements == 1


def test_orbiting_bit_exhaustion_advances_work_and_resets_permutation() -> None:
    harness = Harness()
    start_nonce = 2**32 - 40

    _, _, result = run_with_harness(
        ContinuousMiningPlan(start_nonce, 10, 5),
        harness,
        extra_nonce_2_size=1,
        extra_nonce_2="fe",
        strategy=OrbitingBitSearchStrategy(),
    )

    assert [(start, stop) for _, start, stop in harness.search_calls] == [
        (start_nonce, start_nonce + 10),
        (start_nonce + 20, start_nonce + 30),
        (start_nonce + 10, start_nonce + 20),
        (start_nonce + 30, 2**32),
        (start_nonce, start_nonce + 10),
    ]
    assert [extra for _, extra in harness.prepare_calls] == ["fe", "ff"]
    assert result.work_variants_used == 2
    assert result.extra_nonce_2_advances == 1


def test_final_nonce_space_chunk_is_shortened_without_wrap() -> None:
    harness = Harness()

    _, _, result = run_with_harness(
        ContinuousMiningPlan(0xFFFFFFFE, 100, 1),
        harness,
    )

    assert [(start, stop) for _, start, stop in harness.search_calls] == [(0xFFFFFFFE, 2**32)]
    assert result.total_hashes_checked == 2
    assert harness.receive_timeouts == []


def test_zero_elapsed_time_has_unavailable_weighted_rate() -> None:
    harness = Harness(elapsed_values=deque([0]))

    _, _, result = run_with_harness(ContinuousMiningPlan(0, 1, 1), harness)

    assert result.weighted_hashes_per_second is None


def test_stop_before_first_chunk_does_not_prepare_search_poll_or_submit() -> None:
    harness = Harness()
    harness.controller.request_stop()

    _, _, result = run_with_harness(ContinuousMiningPlan(0, 1), harness)

    assert result.outcome is ContinuousMiningOutcome.STOPPED_BY_USER
    assert result.chunks_completed == 0
    assert result.jobs_used == 0
    assert harness.prepare_calls == []
    assert harness.search_calls == []
    assert harness.receive_timeouts == []
    assert harness.submit_calls == []
    assert harness.observations == [("stopped",)]


def test_stop_during_exhausted_chunk_takes_effect_before_poll_or_next_search() -> None:
    harness = Harness(stop_during_search_call=1)

    _, _, result = run_with_harness(ContinuousMiningPlan(0, 2), harness)

    assert result.outcome is ContinuousMiningOutcome.STOPPED_BY_USER
    assert len(harness.search_calls) == 1
    assert harness.receive_timeouts == []
    assert harness.submit_calls == []
    assert harness.observations[-1] == ("stopped",)


def test_repeated_stop_requests_are_safe() -> None:
    controller = StopController()

    controller.request_stop()
    controller.request_stop()

    assert controller.stop_requested is True
    assert controller.runtime_limit_reached is False


def test_runtime_limit_before_first_chunk_has_distinct_outcome() -> None:
    now = [10.0]
    controller = StopController(1.0, clock=lambda: now[0])
    harness = Harness(controller=controller)
    now[0] = 11.0

    _, _, result = run_with_harness(ContinuousMiningPlan(0, 1, max_runtime_seconds=1), harness)

    assert result.outcome is ContinuousMiningOutcome.RUNTIME_LIMIT_REACHED
    assert result.chunks_completed == 0
    assert harness.search_calls == []
    assert ("stopped",) not in harness.observations


def test_runtime_limit_after_one_range_prevents_the_next_range() -> None:
    now = [0.0]
    controller = StopController(1.0, clock=lambda: now[0])
    harness = Harness(controller=controller)
    original_search = harness.search

    def advance_after_search(
        work: PreparedMiningWork, start_nonce: int, stop_nonce: int
    ) -> NonceSearchResult:
        result = original_search(work, start_nonce, stop_nonce)
        now[0] = 1.0
        return result

    harness.search = advance_after_search  # type: ignore[method-assign]

    _, _, result = run_with_harness(ContinuousMiningPlan(0, 1, max_runtime_seconds=1), harness)

    assert result.outcome is ContinuousMiningOutcome.RUNTIME_LIMIT_REACHED
    assert result.chunks_completed == 1
    assert len(harness.search_calls) == 1
    assert result.total_hashes_checked == 1


def test_runtime_limit_during_job_replacement_prevents_replacement_search() -> None:
    now = [0.0]
    controller = StopController(1.0, clock=lambda: now[0])
    harness = Harness(
        controller=controller,
        notifications=deque([notification("replacement"), None]),
    )
    original_prepare = harness.prepare

    def advance_during_replacement(job: MiningJob, extra_nonce_2: str) -> PreparedMiningWork:
        work = original_prepare(job, extra_nonce_2)
        if len(harness.prepare_calls) == 2:
            now[0] = 1.0
        return work

    harness.prepare = advance_during_replacement  # type: ignore[method-assign]

    _, _, result = run_with_harness(ContinuousMiningPlan(0, 1, max_runtime_seconds=1), harness)

    assert result.outcome is ContinuousMiningOutcome.RUNTIME_LIMIT_REACHED
    assert [work.job_id for work, _, _ in harness.search_calls] == ["initial-job"]
    assert result.job_replacements == 1


def test_runtime_limit_during_progression_prevents_successor_search() -> None:
    now = [0.0]
    controller = StopController(1.0, clock=lambda: now[0])
    harness = Harness(controller=controller)
    original_prepare = harness.prepare

    def advance_during_progression(job: MiningJob, extra_nonce_2: str) -> PreparedMiningWork:
        work = original_prepare(job, extra_nonce_2)
        if len(harness.prepare_calls) == 2:
            now[0] = 1.0
        return work

    harness.prepare = advance_during_progression  # type: ignore[method-assign]

    _, _, result = run_with_harness(
        ContinuousMiningPlan(0xFFFFFFFF, 1, max_runtime_seconds=1),
        harness,
        extra_nonce_2_size=1,
        extra_nonce_2="fe",
    )

    assert result.outcome is ContinuousMiningOutcome.RUNTIME_LIMIT_REACHED
    assert len(harness.search_calls) == 1
    assert [extra for _, extra in harness.prepare_calls] == ["fe", "ff"]


def test_stop_during_replacement_preparation_prevents_next_search() -> None:
    harness = Harness(
        notifications=deque([notification("replacement"), None]),
        stop_during_prepare_call=2,
    )

    _, _, result = run_with_harness(ContinuousMiningPlan(0, 1), harness)

    assert result.outcome is ContinuousMiningOutcome.STOPPED_BY_USER
    assert [work.job_id for work, _, _ in harness.search_calls] == ["initial-job"]
    assert result.jobs_used == 1
    assert result.job_replacements == 1
    assert result.final_job.job_id == "replacement"
    assert harness.receive_timeouts == [0.0, 0.0]


@pytest.mark.parametrize("clean_jobs", [True, False])
def test_newest_job_replaces_work_resets_nonce_and_reuses_extra_nonce(
    clean_jobs: bool,
) -> None:
    harness = Harness(
        notifications=deque([notification("replacement", clean_jobs=clean_jobs), None])
    )

    _, _, result = run_with_harness(ContinuousMiningPlan(5, 2, 2), harness)

    assert [(work.job_id, start, stop) for work, start, stop in harness.search_calls] == [
        ("initial-job", 5, 7),
        ("replacement", 5, 7),
    ]
    assert [(job.job_id, extra) for job, extra in harness.prepare_calls] == [
        ("initial-job", "abababab"),
        ("replacement", "abababab"),
    ]
    assert result.jobs_used == 2
    assert result.job_replacements == 1
    assert result.total_hashes_checked == 4


def test_difficulty_only_does_not_change_prepared_work() -> None:
    harness = Harness(notifications=deque([SetDifficultyNotification(difficulty=200), None]))

    assembler, _, result = run_with_harness(ContinuousMiningPlan(3, 1, 2), harness)

    assert len(harness.prepare_calls) == 1
    assert [(start, stop) for _, start, stop in harness.search_calls] == [(3, 4), (4, 5)]
    assert assembler.current_difficulty == 200
    assert result.job_replacements == 0


def test_arrival_order_snapshots_difficulty_and_only_final_job_is_prepared() -> None:
    harness = Harness(
        notifications=deque(
            [
                notification("old-difficulty-job"),
                SetDifficultyNotification(difficulty=200),
                notification("new-difficulty-job"),
                SetDifficultyNotification(difficulty=300),
                None,
            ]
        )
    )

    assembler, _, result = run_with_harness(ContinuousMiningPlan(0, 1, 2), harness)

    assert [(job.job_id, job.difficulty) for job, _ in harness.prepare_calls] == [
        ("initial-job", 100),
        ("new-difficulty-job", 200),
    ]
    assert [work.job_id for work, _, _ in harness.search_calls] == [
        "initial-job",
        "new-difficulty-job",
    ]
    assert result.jobs_used == 2
    assert result.job_replacements == 1
    assert assembler.current_difficulty == 300
    assert (
        "replaced",
        "initial-job",
        "new-difficulty-job",
        1,
    ) in harness.observations
    assert all(
        observation[:3] != ("replaced", "initial-job", "old-difficulty-job")
        for observation in harness.observations
    )


def test_nonce_space_exhaustion_waits_and_new_job_resumes_at_start() -> None:
    harness = Harness(
        timed_notifications=deque(
            [
                SetDifficultyNotification(difficulty=250),
                None,
                notification("later-job"),
            ]
        )
    )

    _, _, result = run_with_harness(
        ContinuousMiningPlan(0xFFFFFFFF, 5, 257),
        harness,
        extra_nonce_2_size=1,
        extra_nonce_2="00",
        network_time="ffffffff",
    )

    assert len(harness.search_calls) == 257
    assert all((start, stop) == (0xFFFFFFFF, 2**32) for _, start, stop in harness.search_calls)
    assert harness.search_calls[-1][0].job_id == "later-job"
    assert harness.receive_timeouts.count(0.25) == 3
    assert ("exhausted", "initial-job") in harness.observations
    assert ("waiting", "initial-job") in harness.observations
    assert result.final_job.difficulty == 250
    assert result.chunks_completed == 257
    assert result.total_hashes_checked == 257
    assert result.extra_nonce_2_cycles_completed == 1


def test_immediately_available_replacement_still_reports_nonce_exhaustion() -> None:
    harness = Harness(notifications=deque([notification("ready-job"), None]))

    _, _, result = run_with_harness(
        ContinuousMiningPlan(0xFFFFFFFF, 1, 2),
        harness,
    )

    assert ("exhausted", "initial-job") in harness.observations
    assert ("waiting", "initial-job") not in harness.observations
    assert result.final_job.job_id == "ready-job"


def test_nonce_boundary_advances_extra_nonce_and_restarts_configured_range() -> None:
    harness = Harness(elapsed_values=deque([10, 20, 30]))
    strategy = TrackingStrategy()

    _, _, result = run_with_harness(
        ContinuousMiningPlan(0xFFFFFFFF, 1, 3),
        harness,
        extra_nonce_2_size=1,
        extra_nonce_2="fe",
        strategy=strategy,
    )

    assert [extra for _, extra in harness.prepare_calls] == ["fe", "ff", "00"]
    assert [(start, stop) for _, start, stop in harness.search_calls] == [
        (0xFFFFFFFF, 2**32),
        (0xFFFFFFFF, 2**32),
        (0xFFFFFFFF, 2**32),
    ]
    assert len({work.header_prefix for work, _, _ in harness.search_calls}) == 3
    assert result.work_variants_used == 3
    assert result.extra_nonce_2_advances == 2
    assert result.extra_nonce_2_cycles_completed == 0
    assert result.network_time_rolls == 0
    assert result.jobs_used == 1
    assert result.total_hashes_checked == 3
    assert result.total_elapsed_ns == 60
    assert [item[1] for item in harness.observations if item[0] == "work"] == [
        "initial_job",
        "extra_nonce_2",
        "extra_nonce_2",
    ]
    assert strategy.create_calls == [(0xFFFFFFFF, 1, 2**32)] * 3


def test_identical_pool_job_is_ignored_without_restarting_completed_work() -> None:
    harness = Harness(
        notifications=deque([notification("initial-job"), None]),
    )

    _, _, result = run_with_harness(
        ContinuousMiningPlan(0xFFFFFFFF, 1, 2),
        harness,
        extra_nonce_2_size=1,
        extra_nonce_2="10",
    )

    assert [extra for _, extra in harness.prepare_calls] == ["10", "11"]
    searched = [(work.header_prefix, start, stop) for work, start, stop in harness.search_calls]
    assert len(searched) == len(set(searched)) == 2
    assert result.job_replacements == 0
    assert result.duplicate_work_ignored == 1
    assert ("duplicate", 1, "pool_context") in harness.observations


def test_clean_jobs_only_change_is_duplicate_but_target_change_is_new_context() -> None:
    duplicate_harness = Harness(
        notifications=deque([notification("initial-job", clean_jobs=False), None])
    )

    _, _, duplicate_result = run_with_harness(
        ContinuousMiningPlan(0, 1, 2),
        duplicate_harness,
    )

    assert duplicate_result.duplicate_work_ignored == 1
    assert duplicate_result.job_replacements == 0

    target_harness = Harness(
        notifications=deque(
            [
                SetDifficultyNotification(difficulty=200),
                notification("initial-job"),
                None,
            ]
        )
    )
    _, _, target_result = run_with_harness(
        ContinuousMiningPlan(0, 1, 2),
        target_harness,
    )

    assert [job.difficulty for job, _ in target_harness.prepare_calls] == [100, 200]
    assert target_result.job_replacements == 1
    assert target_result.duplicate_work_ignored == 0


def test_pool_job_has_priority_over_local_extra_nonce_advancement() -> None:
    harness = Harness(
        notifications=deque(
            [
                notification("intermediate"),
                notification("newest"),
                None,
            ]
        )
    )
    strategy = TrackingStrategy()

    _, _, result = run_with_harness(
        ContinuousMiningPlan(0xFFFFFFFF, 1, 2),
        harness,
        extra_nonce_2_size=1,
        extra_nonce_2="7f",
        strategy=strategy,
    )

    assert [(job.job_id, extra) for job, extra in harness.prepare_calls] == [
        ("initial-job", "7f"),
        ("newest", "7f"),
    ]
    assert [work.job_id for work, _, _ in harness.search_calls] == [
        "initial-job",
        "newest",
    ]
    assert result.extra_nonce_2_advances == 0
    assert result.job_replacements == 1
    assert strategy.create_calls == [(0xFFFFFFFF, 1, 2**32)] * 2


def test_strategy_failure_is_terminal_without_search_or_recovery() -> None:
    class FailingStrategy(SequentialSearchStrategy):
        def create_cursor(
            self,
            start_nonce: int,
            chunk_size: int,
            *,
            nonce_limit: int = 1 << 32,
        ) -> SearchStrategyCursor:
            del start_nonce, chunk_size, nonce_limit
            raise SearchStrategyExecutionError("failed")

    harness = Harness()
    recover_calls = 0

    def recover(
        error: StratumConnectionError,
        stage: StratumRecoveryStage,
    ) -> StratumMiningSession | None:
        nonlocal recover_calls
        del error, stage
        recover_calls += 1
        return None

    assembler = make_assembler()
    initial_job = assembler.build_job(notification("initial-job"))
    with pytest.raises(SearchStrategyExecutionError, match="failed"):
        run_continuous_mining(
            ContinuousMiningPlan(0, 1, 1),
            assembler,
            initial_job,
            "abababab",
            harness.controller,
            receive_notification=harness.receive,
            submit_share=harness.submit,
            strategy=FailingStrategy(),
            prepare_work=harness.prepare,
            search_range=harness.search,
            recover_session=recover,
            recovery_statistics=lambda: StratumRecoveryStatistics(0, 0, 0, 1),
        )

    assert harness.search_calls == []
    assert recover_calls == 0


def test_network_time_roll_reuses_seed_and_exact_variant_is_submitted() -> None:
    harness = Harness(match_call=257)

    _, initial_job, result = run_with_harness(
        ContinuousMiningPlan(0xFFFFFFFF, 1),
        harness,
        extra_nonce_2_size=1,
        extra_nonce_2="80",
        network_time="0000000a",
    )

    submitted_work, submitted_match = harness.submit_calls[0]
    assert initial_job.network_time == "0000000a"
    assert submitted_work.extra_nonce_2 == "80"
    assert submitted_work.network_time == "0000000b"
    assert submitted_match.nonce == 0xFFFFFFFF
    assert result.chunks_completed == 257
    assert result.work_variants_used == 257
    assert result.extra_nonce_2_advances == 256
    assert result.extra_nonce_2_cycles_completed == 1
    assert result.network_time_rolls == 1
    assert harness.observations.index(("extra-cycle", 1)) < harness.observations.index(
        ("time-roll", 1)
    )
    assert harness.receive_timeouts == [0.0] * 256


def test_new_pool_job_resets_seed_and_does_not_carry_local_network_time() -> None:
    harness = Harness(
        notifications=deque(
            [
                *([None] * 256),
                notification("new-pool-job", network_time="00000005"),
                None,
            ]
        )
    )

    _, _, result = run_with_harness(
        ContinuousMiningPlan(0xFFFFFFFF, 1, 258),
        harness,
        extra_nonce_2_size=1,
        extra_nonce_2="01",
        network_time="00000001",
    )

    assert harness.prepare_calls[-2][0].network_time == "00000002"
    replacement_job, replacement_extra_nonce = harness.prepare_calls[-1]
    assert replacement_job.job_id == "new-pool-job"
    assert replacement_job.network_time == "00000005"
    assert replacement_extra_nonce == "01"
    assert harness.search_calls[-1][1:] == (0xFFFFFFFF, 2**32)
    assert result.job_replacements == 1
    assert result.network_time_rolls == 1


def test_stop_after_boundary_prevents_successor_preparation() -> None:
    harness = Harness(stop_during_search_call=1)

    _, _, result = run_with_harness(
        ContinuousMiningPlan(0xFFFFFFFF, 1),
        harness,
        extra_nonce_2_size=1,
        extra_nonce_2="20",
    )

    assert result.outcome is ContinuousMiningOutcome.STOPPED_BY_USER
    assert [extra for _, extra in harness.prepare_calls] == ["20"]
    assert result.extra_nonce_2_advances == 0


def test_stop_while_waiting_for_new_job_is_controlled_and_does_not_busy_continue() -> None:
    harness = Harness(stop_during_wait=True)

    _, _, result = run_with_harness(
        ContinuousMiningPlan(0xFFFFFFFF, 1),
        harness,
        extra_nonce_2_size=1,
        extra_nonce_2="00",
        network_time="ffffffff",
    )

    assert result.outcome is ContinuousMiningOutcome.STOPPED_BY_USER
    assert len(harness.search_calls) == 256
    assert harness.receive_timeouts[-1] == 0.25
    assert harness.observations[-1] == ("stopped",)


@pytest.mark.parametrize("accepted", [True, False])
def test_first_candidate_submits_exact_work_once_without_polling(
    accepted: bool,
) -> None:
    harness = Harness(
        notifications=deque([notification("must-not-be-read")]),
        match_call=1,
        accepted=accepted,
    )

    _, _, result = run_with_harness(ContinuousMiningPlan(9, 2), harness)

    assert len(harness.search_calls) == 1
    assert harness.receive_timeouts == []
    assert len(harness.submit_calls) == 1
    submitted_work, submitted_match = harness.submit_calls[0]
    assert submitted_work is harness.search_calls[0][0]
    assert submitted_match.nonce == 9
    assert result.outcome is (
        ContinuousMiningOutcome.SHARE_ACCEPTED
        if accepted
        else ContinuousMiningOutcome.SHARE_REJECTED
    )
    assert result.pool_accepted is accepted


def test_orbiting_bit_candidate_is_terminal_with_exact_assignment_metadata() -> None:
    harness = Harness(
        notifications=deque([None, notification("must-not-be-read")]),
        match_call=2,
    )
    start_nonce = 2**32 - 40

    _, _, result = run_with_harness(
        ContinuousMiningPlan(start_nonce, 10),
        harness,
        strategy=OrbitingBitSearchStrategy(),
    )

    assert [(start, stop) for _, start, stop in harness.search_calls] == [
        (start_nonce, start_nonce + 10),
        (start_nonce + 20, start_nonce + 30),
    ]
    assert harness.receive_timeouts == [0.0]
    assert len(harness.submit_calls) == 1
    submitted_work, submitted_match = harness.submit_calls[0]
    assert submitted_work is harness.search_calls[1][0]
    assert submitted_match.nonce == start_nonce + 20
    assert result.outcome is ContinuousMiningOutcome.SHARE_ACCEPTED


def test_candidate_returned_during_stop_requested_chunk_still_submits() -> None:
    harness = Harness(match_call=1, stop_during_search_call=1)

    _, _, result = run_with_harness(ContinuousMiningPlan(0, 1), harness)

    assert result.outcome is ContinuousMiningOutcome.SHARE_ACCEPTED
    assert len(harness.submit_calls) == 1
    assert harness.observations.count(("stopped",)) == 1
    assert harness.observations.index(("stopped",)) < harness.observations.index(
        ("candidate", "initial-job", 0)
    )


def test_network_only_candidate_is_submitted() -> None:
    harness = Harness(match_call=1, match_flags=(False, True))

    _, _, result = run_with_harness(ContinuousMiningPlan(0, 1), harness)

    assert result.match is not None
    assert result.match.meets_share_target is False
    assert result.match.meets_network_target is True
    assert result.submissions_performed == 1


def test_submission_failure_propagates_without_polling_or_continuation() -> None:
    harness = Harness(match_call=1)

    def fail_submit(work: PreparedMiningWork, match: NonceSearchMatch) -> bool:
        harness.submit_calls.append((work, match))
        raise RuntimeError("submission failed")

    assembler = make_assembler()
    initial_job = assembler.build_job(notification("initial-job"))
    with pytest.raises(RuntimeError, match="submission failed"):
        run_continuous_mining(
            ContinuousMiningPlan(0, 1),
            assembler,
            initial_job,
            "abababab",
            harness.controller,
            receive_notification=harness.receive,
            submit_share=fail_submit,
            observer=harness,
            prepare_work=harness.prepare,
            search_range=harness.search,
        )

    assert len(harness.search_calls) == 1
    assert len(harness.submit_calls) == 1
    assert harness.receive_timeouts == []


def test_connection_loss_after_chunk_installs_fresh_session_and_preserves_totals() -> None:
    harness = Harness(
        notifications=deque([StratumConnectionError("connection closed")]),
        elapsed_values=deque([100, 300]),
    )
    session, _ = recovered_session(extra_nonce_2_size=2, seed="cdef")
    recover_calls: list[tuple[StratumConnectionError, StratumRecoveryStage]] = []
    statistics = StratumRecoveryStatistics(1, 1, 0, 2)
    assembler = make_assembler()
    initial_job = assembler.build_job(notification("initial-job"))
    strategy = TrackingStrategy()

    def recover(
        error: StratumConnectionError,
        stage: StratumRecoveryStage,
    ) -> StratumMiningSession:
        recover_calls.append((error, stage))
        return session

    result = run_continuous_mining(
        ContinuousMiningPlan(5, 2, 2),
        assembler,
        initial_job,
        "abababab",
        harness.controller,
        receive_notification=harness.receive,
        submit_share=harness.submit,
        observer=harness,
        strategy=strategy,
        prepare_work=harness.prepare,
        search_range=harness.search,
        recover_session=recover,
        recovery_statistics=lambda: statistics,
    )

    assert [
        (work.job_id, work.extra_nonce_2, start, stop) for work, start, stop in harness.search_calls
    ] == [
        ("initial-job", "abababab", 5, 7),
        ("recovered-job", "cdef", 5, 7),
    ]
    assert recover_calls[0][1] is StratumRecoveryStage.NOTIFICATION_POLL
    assert result.chunks_completed == 2
    assert result.total_hashes_checked == 4
    assert result.total_elapsed_ns == 400
    assert result.jobs_used == 2
    assert result.job_replacements == 1
    assert result.work_variants_used == 2
    assert result.reconnect_attempts == 1
    assert result.successful_reconnects == 1
    assert result.failed_reconnect_attempts == 0
    assert result.sessions_established == 2
    assert strategy.create_calls == [(5, 2, 2**32)] * 2


def test_orbiting_bit_recovery_uses_fresh_cursor_without_reselecting_strategy() -> None:
    harness = Harness(notifications=deque([StratumConnectionError("connection closed")]))
    session, _ = recovered_session(seed="cd")
    assembler = make_assembler()
    initial_job = assembler.build_job(notification("initial-job"))
    strategy = OrbitingBitSearchStrategy()
    start_nonce = 2**32 - 40

    result = run_continuous_mining(
        ContinuousMiningPlan(start_nonce, 10, 2),
        assembler,
        initial_job,
        "abababab",
        harness.controller,
        receive_notification=harness.receive,
        submit_share=harness.submit,
        observer=harness,
        strategy=strategy,
        prepare_work=harness.prepare,
        search_range=harness.search,
        recover_session=lambda error, stage: session,
        recovery_statistics=lambda: StratumRecoveryStatistics(1, 1, 0, 2),
    )

    assert [(work.job_id, start, stop) for work, start, stop in harness.search_calls] == [
        ("initial-job", start_nonce, start_nonce + 10),
        ("recovered-job", start_nonce, start_nonce + 10),
    ]
    assert result.sessions_established == 2
    assert result.successful_reconnects == 1


def test_recovery_after_local_progression_discards_stale_cursor_and_keeps_counters() -> None:
    harness = Harness(
        notifications=deque([None, StratumConnectionError("connection closed")]),
    )
    session, _ = recovered_session(seed="10")
    statistics = StratumRecoveryStatistics(2, 1, 1, 2)
    assembler = make_assembler()
    initial_job = assembler.build_job(notification("initial-job"))

    result = run_continuous_mining(
        ContinuousMiningPlan(0xFFFFFFFF, 1, 3),
        assembler,
        initial_job,
        "abababab",
        harness.controller,
        receive_notification=harness.receive,
        submit_share=harness.submit,
        observer=harness,
        prepare_work=harness.prepare,
        search_range=harness.search,
        recover_session=lambda error, stage: session,
        recovery_statistics=lambda: statistics,
    )

    assert [extra for _, extra in harness.prepare_calls] == [
        "abababab",
        "abababac",
        "10",
    ]
    assert all((start, stop) == (0xFFFFFFFF, 2**32) for _, start, stop in harness.search_calls)
    assert result.extra_nonce_2_advances == 1
    assert result.work_variants_used == 3
    assert result.total_hashes_checked == 3
    assert result.reconnect_attempts == 2
    assert result.failed_reconnect_attempts == 1


def test_new_session_is_explicit_acceptance_context_even_when_work_identity_matches() -> None:
    harness = Harness(
        notifications=deque([StratumConnectionError("connection closed")]),
    )
    session, _ = recovered_session(
        job_id="initial-job",
        extra_nonce_2_size=4,
        seed="abababab",
        network_time="65f04abc",
        difficulty_value=100,
    )
    assembler = make_assembler()
    initial_job = assembler.build_job(notification("initial-job"))

    result = run_continuous_mining(
        ContinuousMiningPlan(7, 1, 2),
        assembler,
        initial_job,
        "abababab",
        harness.controller,
        receive_notification=harness.receive,
        submit_share=harness.submit,
        observer=harness,
        prepare_work=harness.prepare,
        search_range=harness.search,
        recover_session=lambda error, stage: session,
        recovery_statistics=lambda: StratumRecoveryStatistics(1, 1, 0, 2),
    )

    assert [(start, stop) for _, start, stop in harness.search_calls] == [
        (7, 8),
        (7, 8),
    ]
    assert result.job_replacements == 1
    assert result.duplicate_work_ignored == 0
    assert result.sessions_established == 2


def test_protocol_poll_failure_is_terminal_and_does_not_invoke_recovery() -> None:
    harness = Harness(notifications=deque([StratumProtocolError("malformed")]))
    assembler = make_assembler()
    initial_job = assembler.build_job(notification("initial-job"))
    recover_calls = 0

    def recover(
        error: StratumConnectionError,
        stage: StratumRecoveryStage,
    ) -> StratumMiningSession | None:
        nonlocal recover_calls
        del error, stage
        recover_calls += 1
        return None

    with pytest.raises(StratumProtocolError):
        run_continuous_mining(
            ContinuousMiningPlan(0, 1, 2),
            assembler,
            initial_job,
            "abababab",
            harness.controller,
            receive_notification=harness.receive,
            submit_share=harness.submit,
            prepare_work=harness.prepare,
            search_range=harness.search,
            recover_session=recover,
            recovery_statistics=lambda: StratumRecoveryStatistics(0, 0, 0, 1),
        )

    assert recover_calls == 0
    assert len(harness.search_calls) == 1


def test_submission_connection_failure_is_terminal_and_never_recovers() -> None:
    harness = Harness(match_call=1)
    assembler = make_assembler()
    initial_job = assembler.build_job(notification("initial-job"))
    recover_calls = 0

    def fail_submit(work: PreparedMiningWork, match: NonceSearchMatch) -> bool:
        del work, match
        raise StratumConnectionError("uncertain submission")

    def recover(
        error: StratumConnectionError,
        stage: StratumRecoveryStage,
    ) -> StratumMiningSession | None:
        nonlocal recover_calls
        del error, stage
        recover_calls += 1
        return None

    with pytest.raises(StratumConnectionError, match="uncertain submission"):
        run_continuous_mining(
            ContinuousMiningPlan(0, 1),
            assembler,
            initial_job,
            "abababab",
            harness.controller,
            receive_notification=harness.receive,
            submit_share=fail_submit,
            prepare_work=harness.prepare,
            search_range=harness.search,
            recover_session=recover,
            recovery_statistics=lambda: StratumRecoveryStatistics(0, 0, 0, 1),
        )

    assert recover_calls == 0
    assert len(harness.search_calls) == 1


def test_stop_during_recovery_returns_controlled_outcome_without_new_search() -> None:
    harness = Harness(
        notifications=deque([StratumConnectionError("connection closed")]),
    )
    assembler = make_assembler()
    initial_job = assembler.build_job(notification("initial-job"))

    def recover(
        error: StratumConnectionError,
        stage: StratumRecoveryStage,
    ) -> None:
        del error, stage
        harness.controller.request_stop()

    result = run_continuous_mining(
        ContinuousMiningPlan(0, 1),
        assembler,
        initial_job,
        "abababab",
        harness.controller,
        receive_notification=harness.receive,
        submit_share=harness.submit,
        observer=harness,
        prepare_work=harness.prepare,
        search_range=harness.search,
        recover_session=recover,
        recovery_statistics=lambda: StratumRecoveryStatistics(0, 0, 0, 1),
    )

    assert result.outcome is ContinuousMiningOutcome.STOPPED_BY_USER
    assert len(harness.search_calls) == 1
    assert harness.observations[-1] == ("stopped",)


def test_connection_loss_during_terminal_job_wait_recovers_without_busy_loop() -> None:
    harness = Harness(
        timed_notifications=deque([StratumConnectionError("connection closed")]),
    )
    session, _ = recovered_session(seed="22")
    assembler = make_assembler(extra_nonce_2_size=1)
    initial_job = assembler.build_job(notification("initial-job", network_time="ffffffff"))
    stages: list[StratumRecoveryStage] = []

    def recover(
        error: StratumConnectionError,
        stage: StratumRecoveryStage,
    ) -> StratumMiningSession:
        del error
        stages.append(stage)
        return session

    result = run_continuous_mining(
        ContinuousMiningPlan(0xFFFFFFFF, 1, 257),
        assembler,
        initial_job,
        "00",
        harness.controller,
        receive_notification=harness.receive,
        submit_share=harness.submit,
        observer=harness,
        prepare_work=harness.prepare,
        search_range=harness.search,
        recover_session=recover,
        recovery_statistics=lambda: StratumRecoveryStatistics(1, 1, 0, 2),
    )

    assert stages == [StratumRecoveryStage.REPLACEMENT_WAIT]
    assert len(harness.search_calls) == 257
    assert harness.search_calls[-1][0].job_id == "recovered-job"
    assert result.extra_nonce_2_cycles_completed == 1
    assert result.sessions_established == 2


def test_unsupported_notification_fails_without_another_search() -> None:
    harness = Harness(notifications=deque([object()]))

    with pytest.raises(ContinuousMiningError, match="unsupported"):
        run_with_harness(ContinuousMiningPlan(0, 1, 2), harness)

    assert len(harness.search_calls) == 1


def test_result_is_frozen_and_slotted() -> None:
    harness = Harness()
    _, _, result = run_with_harness(ContinuousMiningPlan(0, 1, 1), harness)

    assert not hasattr(result, "__dict__")
    with pytest.raises(FrozenInstanceError):
        result.chunks_completed = 2  # type: ignore[misc]
