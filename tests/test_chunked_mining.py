"""Tests for finite nonce-chunk orchestration and job replacement."""

from __future__ import annotations

from collections import deque
from dataclasses import FrozenInstanceError, dataclass, field

import pytest

from hashorb.mining import (
    ChunkedMiningError,
    ChunkedMiningPlan,
    ChunkedMiningResult,
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
    run_chunked_mining,
)
from hashorb.mining.chunks import MiningNotification
from hashorb.network.stratum import (
    MiningNotifyNotification,
    SetDifficultyNotification,
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


def make_assembler(difficulty: int | float = 100) -> MiningJobAssembler:
    """Build an assembler with an established current difficulty."""

    assembler = MiningJobAssembler(
        SubscribeResult(
            subscriptions=(("mining.notify", "subscription-id"),),
            extra_nonce_1="08000002",
            extra_nonce_2_size=4,
        )
    )
    assembler.apply_difficulty(SetDifficultyNotification(difficulty=difficulty))
    return assembler


def make_work(job: MiningJob, extra_nonce_2: str) -> PreparedMiningWork:
    """Build deterministic prepared work without hashing."""

    return PreparedMiningWork(
        job_id=job.job_id,
        extra_nonce_2=extra_nonce_2,
        network_time=job.network_time,
        header_prefix=bytes(range(76)),
        network_target=1,
        share_target=2,
    )


@dataclass
class Harness:
    """Deterministic callback and observer state."""

    notifications: deque[object] = field(default_factory=deque)
    elapsed_values: deque[int] = field(default_factory=deque)
    match_call: int | None = None
    match_flags: tuple[bool, bool] = (True, False)
    accepted: bool = True
    prepare_calls: list[tuple[MiningJob, str]] = field(default_factory=list)
    search_calls: list[tuple[PreparedMiningWork, int, int]] = field(default_factory=list)
    poll_calls: int = 0
    submit_calls: list[tuple[PreparedMiningWork, NonceSearchMatch]] = field(default_factory=list)
    observations: list[tuple[object, ...]] = field(default_factory=list)

    def prepare(self, job: MiningJob, extra_nonce_2: str) -> PreparedMiningWork:
        self.prepare_calls.append((job, extra_nonce_2))
        return make_work(job, extra_nonce_2)

    def search(
        self,
        work: PreparedMiningWork,
        start_nonce: int,
        stop_nonce: int,
    ) -> NonceSearchResult:
        self.search_calls.append((work, start_nonce, stop_nonce))
        call_number = len(self.search_calls)
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

    def poll(self) -> MiningNotification | None:
        self.poll_calls += 1
        if not self.notifications:
            return None
        value = self.notifications.popleft()
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


class TrackingStrategy(SequentialSearchStrategy):
    """Record fresh cursor creation without changing sequential behavior."""

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


def run_with_harness(
    plan: ChunkedMiningPlan,
    harness: Harness,
    *,
    difficulty: int | float = 100,
    strategy: MiningSearchStrategy | None = None,
) -> tuple[MiningJobAssembler, MiningJob, ChunkedMiningResult]:
    """Run orchestration against one prepared synthetic initial job."""

    assembler = make_assembler(difficulty)
    initial_job = assembler.build_job(notification("initial-job"))
    result = run_chunked_mining(
        plan,
        assembler,
        initial_job,
        "abababab",
        poll_notification=harness.poll,
        submit_share=harness.submit,
        observer=harness,
        strategy=strategy,
        prepare_work=harness.prepare,
        search_range=harness.search,
    )
    return assembler, initial_job, result


@pytest.mark.parametrize(
    ("plan", "ranges"),
    [
        (ChunkedMiningPlan(0, 10, 1), [(0, 1)]),
        (ChunkedMiningPlan(7, 3, 9), [(7, 10), (10, 13), (13, 16)]),
        (ChunkedMiningPlan(7, 4, 10), [(7, 11), (11, 15), (15, 17)]),
        (ChunkedMiningPlan(3, 100, 5), [(3, 8)]),
        (ChunkedMiningPlan(0xFFFFFFFF, 5, 1), [(0xFFFFFFFF, 2**32)]),
    ],
)
def test_exact_finite_chunk_ranges(
    plan: ChunkedMiningPlan,
    ranges: list[tuple[int, int]],
) -> None:
    harness = Harness()

    _, _, result = run_with_harness(plan, harness)

    assert [(start, stop) for _, start, stop in harness.search_calls] == ranges
    assert result.chunks_completed == len(ranges)
    assert result.total_hashes_checked == plan.max_hashes
    assert len(harness.prepare_calls) == 1
    assert harness.poll_calls == max(0, len(ranges) - 1)


def test_orbiting_bit_ranges_use_exact_emission_order_and_budget() -> None:
    harness = Harness(elapsed_values=deque([10, 20, 30, 40, 50]))

    _, _, result = run_with_harness(
        ChunkedMiningPlan(0, 10, 50),
        harness,
        strategy=OrbitingBitSearchStrategy(),
    )

    assert [(start, stop) for _, start, stop in harness.search_calls] == [
        (0, 10),
        (40, 50),
        (20, 30),
        (10, 20),
        (30, 40),
    ]
    assert result.chunks_completed == 5
    assert result.total_hashes_checked == 50
    assert result.total_elapsed_ns == 150


def test_orbiting_bit_internal_skips_do_not_create_searches_or_events() -> None:
    harness = Harness()

    _, _, result = run_with_harness(
        ChunkedMiningPlan(0, 10, 30),
        harness,
        strategy=OrbitingBitSearchStrategy(),
    )

    assert [(start, stop) for _, start, stop in harness.search_calls] == [
        (0, 10),
        (20, 30),
        (10, 20),
    ]
    assert [item for item in harness.observations if item[0] == "started"] == [
        ("started", "initial-job", 0, 10),
        ("started", "initial-job", 20, 30),
        ("started", "initial-job", 10, 20),
    ]
    assert result.chunks_completed == 3


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("start_nonce", True),
        ("start_nonce", -1),
        ("start_nonce", 2**32),
        ("chunk_size", True),
        ("chunk_size", 0),
        ("chunk_size", 2**32 + 1),
        ("max_hashes", True),
        ("max_hashes", 0),
        ("max_hashes", 2**32 + 1),
    ],
)
def test_plan_rejects_invalid_values(field_name: str, value: object) -> None:
    values: dict[str, object] = {
        "start_nonce": 0,
        "chunk_size": 1,
        "max_hashes": 1,
    }
    values[field_name] = value

    with pytest.raises((ChunkedMiningError, TypeError, ValueError)):
        ChunkedMiningPlan(**values)  # type: ignore[arg-type]


def test_plan_rejects_global_budget_past_nonce_space() -> None:
    with pytest.raises(ChunkedMiningError, match="remaining"):
        ChunkedMiningPlan(start_nonce=1, chunk_size=1, max_hashes=2**32)


def test_plan_and_result_are_frozen_and_slotted() -> None:
    plan = ChunkedMiningPlan(start_nonce=0, chunk_size=1, max_hashes=1)
    harness = Harness()
    _, _, result = run_with_harness(plan, harness)

    with pytest.raises(FrozenInstanceError):
        plan.start_nonce = 1  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        result.chunks_completed = 2  # type: ignore[misc]
    assert not hasattr(plan, "__dict__")
    assert not hasattr(result, "__dict__")


def test_totals_and_weighted_rate_use_integer_aggregates() -> None:
    harness = Harness(elapsed_values=deque([100, 900]))
    _, _, result = run_with_harness(ChunkedMiningPlan(0, 1, 2), harness)

    assert result.total_hashes_checked == 2
    assert result.total_elapsed_ns == 1000
    assert result.weighted_hashes_per_second == 2_000_000.0


def test_zero_total_elapsed_has_unavailable_rate() -> None:
    harness = Harness(elapsed_values=deque([0, 0]))
    _, _, result = run_with_harness(ChunkedMiningPlan(0, 1, 2), harness)

    assert result.weighted_hashes_per_second is None


def test_difficulty_alone_does_not_rebuild_or_reset_current_work() -> None:
    harness = Harness(notifications=deque([SetDifficultyNotification(difficulty=200), None]))
    strategy = TrackingStrategy()
    assembler, _, result = run_with_harness(
        ChunkedMiningPlan(5, 2, 4),
        harness,
        strategy=strategy,
    )

    assert [(start, stop) for _, start, stop in harness.search_calls] == [
        (5, 7),
        (7, 9),
    ]
    assert len(harness.prepare_calls) == 1
    assert result.job_replacements == 0
    assert assembler.current_difficulty == 200
    assert strategy.create_calls == [(5, 2, 9)]


@pytest.mark.parametrize("clean_jobs", [True, False])
def test_new_job_replaces_work_and_restarts_nonce_for_both_clean_values(
    clean_jobs: bool,
) -> None:
    harness = Harness(
        notifications=deque([notification("replacement", clean_jobs=clean_jobs), None])
    )
    strategy = TrackingStrategy()

    _, _, result = run_with_harness(
        ChunkedMiningPlan(5, 2, 4),
        harness,
        strategy=strategy,
    )

    assert [(work.job_id, start, stop) for work, start, stop in harness.search_calls] == [
        ("initial-job", 5, 7),
        ("replacement", 5, 7),
    ]
    assert [extra_nonce for _, extra_nonce in harness.prepare_calls] == [
        "abababab",
        "abababab",
    ]
    assert result.jobs_used == 2
    assert result.job_replacements == 1
    assert result.total_hashes_checked == 4
    assert strategy.create_calls == [(5, 2, 9), (5, 2, 7)]


def test_strategy_failure_is_terminal_before_backend_search() -> None:
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

    with pytest.raises(SearchStrategyExecutionError, match="failed"):
        run_with_harness(
            ChunkedMiningPlan(0, 1, 1),
            harness,
            strategy=FailingStrategy(),
        )

    assert harness.search_calls == []


def test_notification_order_snapshots_difficulty_and_uses_only_final_job() -> None:
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

    _, _, result = run_with_harness(ChunkedMiningPlan(4, 1, 2), harness)

    prepared_jobs = [job for job, _ in harness.prepare_calls]
    assert [(job.job_id, job.difficulty) for job in prepared_jobs] == [
        ("initial-job", 100),
        ("new-difficulty-job", 200),
    ]
    assert [(work.job_id, start, stop) for work, start, stop in harness.search_calls] == [
        ("initial-job", 4, 5),
        ("new-difficulty-job", 4, 5),
    ]
    assert result.final_job.job_id == "new-difficulty-job"
    assert result.final_job.difficulty == 200
    assert result.jobs_used == 2
    assert result.job_replacements == 1
    assert harness.poll_calls == 5
    assert ("job", "old-difficulty-job") in harness.observations
    assert ("job", "new-difficulty-job") in harness.observations
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


def test_multiple_difficulties_use_newest_value_for_following_job() -> None:
    harness = Harness(
        notifications=deque(
            [
                SetDifficultyNotification(difficulty=200),
                SetDifficultyNotification(difficulty=400),
                notification("new-job"),
                None,
            ]
        )
    )

    _, _, result = run_with_harness(ChunkedMiningPlan(0, 1, 2), harness)

    assert result.final_job.difficulty == 400


@pytest.mark.parametrize("accepted", [True, False])
def test_match_submits_exact_work_once_without_polling_or_continuation(
    accepted: bool,
) -> None:
    harness = Harness(
        notifications=deque([notification("must-not-be-polled")]),
        match_call=1,
        accepted=accepted,
    )

    _, _, result = run_with_harness(ChunkedMiningPlan(9, 2, 10), harness)

    assert len(harness.search_calls) == 1
    assert harness.poll_calls == 0
    assert len(harness.submit_calls) == 1
    submitted_work, submitted_match = harness.submit_calls[0]
    assert submitted_work is harness.search_calls[0][0]
    assert submitted_match.nonce == 9
    assert result.pool_accepted is accepted
    assert result.candidates_found == 1
    assert result.submissions_performed == 1
    assert result.total_hashes_checked == 1
    assert harness.observations[-2:] == [
        ("candidate", "initial-job", 9),
        ("submitted", "initial-job", 9, accepted),
    ]


def test_network_only_match_is_submitted() -> None:
    harness = Harness(match_call=1, match_flags=(False, True))

    _, _, result = run_with_harness(ChunkedMiningPlan(0, 1, 1), harness)

    assert len(harness.submit_calls) == 1
    assert result.match is not None
    assert result.match.meets_share_target is False
    assert result.match.meets_network_target is True


def test_budget_exhaustion_never_submits_or_polls_after_final_chunk() -> None:
    harness = Harness(notifications=deque([notification("not-consumed")]))

    _, _, result = run_with_harness(ChunkedMiningPlan(0, 10, 2), harness)

    assert result.hash_budget_exhausted is True
    assert result.candidates_found == 0
    assert result.submissions_performed == 0
    assert harness.submit_calls == []
    assert harness.poll_calls == 0
    assert len(harness.notifications) == 1


def test_polling_failure_stops_before_next_chunk() -> None:
    assembler = make_assembler()
    initial_job = assembler.build_job(notification("initial-job"))
    harness = Harness()

    def fail_poll() -> MiningNotification | None:
        raise RuntimeError("poll failed")

    with pytest.raises(RuntimeError, match="poll failed"):
        run_chunked_mining(
            ChunkedMiningPlan(0, 1, 2),
            assembler,
            initial_job,
            "abababab",
            poll_notification=fail_poll,
            submit_share=harness.submit,
            observer=harness,
            prepare_work=harness.prepare,
            search_range=harness.search,
        )

    assert len(harness.search_calls) == 1
    assert harness.submit_calls == []


def test_unsupported_polled_notification_fails_safely() -> None:
    harness = Harness(notifications=deque([object()]))

    with pytest.raises(ChunkedMiningError, match="unsupported"):
        run_with_harness(ChunkedMiningPlan(0, 1, 2), harness)


def test_inputs_are_not_mutated() -> None:
    assembler = make_assembler()
    initial_job = assembler.build_job(notification("initial-job"))
    plan = ChunkedMiningPlan(1, 2, 3)
    harness = Harness()

    run_chunked_mining(
        plan,
        assembler,
        initial_job,
        "abababab",
        poll_notification=harness.poll,
        submit_share=harness.submit,
        observer=harness,
        prepare_work=harness.prepare,
        search_range=harness.search,
    )

    assert plan == ChunkedMiningPlan(1, 2, 3)
    assert initial_job == make_assembler().build_job(notification("initial-job"))
