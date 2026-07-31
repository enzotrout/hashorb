"""Deterministic fake-clock and fake-RPC tests for true-solo orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

import hashorb.bitcoin.solo as solo_module
from hashorb.bitcoin.rpc import (
    BitcoinRpcRemoteError,
    BitcoinRpcTransportError,
    ProposalOutcome,
    SubmissionOutcome,
)
from hashorb.bitcoin.solo import (
    HashOnlyCandidatePolicy,
    ProposalSubmissionCandidatePolicy,
    SoloMiningError,
    SoloMiningOutcome,
    SoloMiningPlan,
    SoloMiningResult,
    SoloMiningValidationError,
    run_solo_mining,
)
from hashorb.bitcoin.template import (
    BlockTemplate,
    calculate_hash_merkle_root,
    parse_block_template,
)
from hashorb.compute.python import PythonSequentialBackend
from hashorb.crypto import double_sha256
from hashorb.mining import (
    NonceSearchMatch,
    NonceSearchResult,
    StopController,
    search_nonce_range,
    select_search_strategy,
)
from hashorb.mining.target import decode_compact_target

_PAYOUT_SCRIPT = bytes.fromhex("0014" + "51" * 20)
_COMMITMENT_PREFIX = bytes.fromhex("6a24aa21a9ed")


def _template(*, marker: int = 0x11, current_time: int = 1_700_000_001) -> BlockTemplate:
    commitment = _COMMITMENT_PREFIX + double_sha256(
        calculate_hash_merkle_root((bytes(32),)) + bytes(32)
    )
    target = decode_compact_target("207fffff")
    return parse_block_template(
        {
            "previousblockhash": (bytes((marker,)) * 32).hex(),
            "version": 0x20000000,
            "bits": "207fffff",
            "target": f"{target:064x}",
            "height": 101,
            "curtime": current_time,
            "mintime": 1_700_000_000,
            "transactions": [],
            "coinbasevalue": 5_000_000_000,
            "coinbaseaux": {"flags": "51"},
            "rules": ["csv", "segwit", "taproot"],
            "mutable": ["time", "transactions", "prevblock"],
            "sizelimit": 1_000_000,
            "weightlimit": 4_000_000,
            "default_witness_commitment": commitment.hex(),
        }
    )


def _exhausted(work: object, start: int, stop: int) -> NonceSearchResult:
    del work
    return NonceSearchResult(start, stop, stop - start, 10, None)


@dataclass
class RecordingObserver:
    """Record safe lifecycle ordering without persisting sensitive objects."""

    events: list[str] = field(default_factory=list)
    terminal_results: list[SoloMiningResult] = field(default_factory=list)

    def template_received(self, template: BlockTemplate, replacement: bool) -> None:
        del template, replacement
        self.events.append("template")

    def template_replaced(
        self, previous: BlockTemplate, current: BlockTemplate, reason: str
    ) -> None:
        del previous, current, reason
        self.events.append("replacement")

    def work_variant_started(
        self,
        variant: object,
        variant_index: int,
        coinbase_advance_count: int,
        timestamp_roll_count: int,
    ) -> None:
        del variant, variant_index, coinbase_advance_count, timestamp_roll_count
        self.events.append("variant")

    def range_completed(self, variant: object, result: NonceSearchResult) -> None:
        del variant, result
        self.events.append("range")

    def coinbase_extra_nonce_advanced(self, advance_count: int) -> None:
        del advance_count
        self.events.append("extra_nonce")

    def timestamp_rolled(self, roll_count: int) -> None:
        del roll_count
        self.events.append("time")

    def candidate_found(self, variant: object, submission_enabled: bool) -> None:
        del variant
        self.events.append("candidate_submission" if submission_enabled else "candidate_hash_only")

    def candidate_suppressed(self, reason: str, suppression_count: int) -> None:
        del reason, suppression_count
        self.events.append("suppressed")

    def proposal_completed(self, outcome: ProposalOutcome) -> None:
        del outcome
        self.events.append("proposal")

    def submission_completed(self, outcome: SubmissionOutcome) -> None:
        del outcome
        self.events.append("submission")

    def completed(self, result: SoloMiningResult) -> None:
        self.events.append("completed")
        self.terminal_results.append(result)


def _run(
    *,
    template: BlockTemplate | None = None,
    backend: PythonSequentialBackend | None = None,
    plan: SoloMiningPlan | None = None,
    stop: StopController | None = None,
    fetch_template: object | None = None,
    candidate_policy: object | None = None,
    propose_block: object | None = None,
    submit_block: object | None = None,
    observer: RecordingObserver | None = None,
    strategy_name: str = "sequential",
    monotonic_clock: object | None = None,
    wall_clock: object | None = None,
    wait: object | None = None,
    initial_coinbase_extra_nonce: int = 0,
    nonce_limit: int = 1 << 32,
) -> SoloMiningResult:
    selected_template = _template() if template is None else template
    selected_fetch = (lambda: selected_template) if fetch_template is None else fetch_template
    return run_solo_mining(
        SoloMiningPlan(0, 128, max_chunks=1) if plan is None else plan,
        chain="regtest",
        payout_script=_PAYOUT_SCRIPT,
        initial_template=selected_template,
        backend=PythonSequentialBackend() if backend is None else backend,
        strategy=select_search_strategy(strategy_name),
        stop_token=StopController() if stop is None else stop,
        fetch_template=selected_fetch,  # type: ignore[arg-type]
        candidate_policy=(
            ProposalSubmissionCandidatePolicy(
                propose_block=(
                    (lambda block: ProposalOutcome(True, "accepted"))
                    if propose_block is None
                    else propose_block
                ),  # type: ignore[arg-type]
                submit_block=(
                    (lambda block: SubmissionOutcome(True, "accepted"))
                    if submit_block is None
                    else submit_block
                ),  # type: ignore[arg-type]
            )
            if candidate_policy is None
            else candidate_policy
        ),  # type: ignore[arg-type]
        observer=observer,
        monotonic_clock=(lambda: 0.0) if monotonic_clock is None else monotonic_clock,  # type: ignore[arg-type]
        wall_clock=(lambda: 1_700_000_100.0) if wall_clock is None else wall_clock,  # type: ignore[arg-type]
        wait=wait,  # type: ignore[arg-type]
        initial_coinbase_extra_nonce=initial_coinbase_extra_nonce,
        nonce_limit=nonce_limit,
    )


@pytest.mark.parametrize(
    "values",
    [
        {"chunk_size": 0, "max_chunks": 1},
        {"chunk_size": 1, "max_chunks": None, "max_runtime_seconds": None},
        {"chunk_size": 1, "max_chunks": 0},
        {"chunk_size": 1, "max_chunks": 1, "template_poll_seconds": 0},
        {"chunk_size": 1, "max_chunks": 1, "inter_range_delay_seconds": -0.1},
        {"chunk_size": 1, "max_chunks": 1, "max_time_roll_seconds": 7_201},
    ],
)
def test_solo_plan_requires_strict_finite_non_busy_bounds(values: dict[str, object]) -> None:
    with pytest.raises(SoloMiningValidationError):
        SoloMiningPlan(start_nonce=0, **values)  # type: ignore[arg-type]


def test_current_candidate_is_independently_proposed_submitted_and_terminal_once() -> None:
    observer = RecordingObserver()
    proposed: list[bytes] = []
    submitted: list[bytes] = []

    result = _run(
        observer=observer,
        propose_block=lambda block: proposed.append(block) or ProposalOutcome(True, "accepted"),
        submit_block=lambda block: submitted.append(block) or SubmissionOutcome(True, "accepted"),
    )

    assert result.outcome is SoloMiningOutcome.BLOCK_ACCEPTED
    assert (
        result.candidates_found == result.proposals_performed == result.submissions_performed == 1
    )
    assert result.templates_received == 3
    assert len(proposed) == len(submitted) == 1
    assert proposed[0] == submitted[0]
    assert observer.events == [
        "template",
        "variant",
        "range",
        "template",
        "candidate_submission",
        "proposal",
        "template",
        "submission",
        "completed",
    ]
    assert observer.terminal_results == [result]


def test_hash_only_candidate_is_verified_without_block_or_rpc_capabilities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observer = RecordingObserver()

    def forbidden_assembly(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("hash-only policy must not assemble a block")

    monkeypatch.setattr(solo_module, "assemble_solo_block", forbidden_assembly)
    policy = HashOnlyCandidatePolicy()

    result = _run(candidate_policy=policy, observer=observer)

    assert result.outcome is SoloMiningOutcome.CANDIDATE_FOUND_SUBMISSION_DISABLED
    assert result.candidates_found == 1
    assert result.proposals_performed == result.submissions_performed == 0
    assert observer.events == [
        "template",
        "variant",
        "range",
        "template",
        "candidate_hash_only",
        "completed",
    ]
    assert not hasattr(policy, "propose_block")
    assert not hasattr(policy, "submit_block")


def test_proposal_rejection_prevents_submission() -> None:
    submit_calls: list[bytes] = []
    result = _run(
        propose_block=lambda block: ProposalOutcome(False, "consensus_rejected"),
        submit_block=lambda block: (
            submit_calls.append(block) or SubmissionOutcome(True, "accepted")
        ),
    )

    assert result.outcome is SoloMiningOutcome.PROPOSAL_REJECTED
    assert result.proposals_performed == 1
    assert result.submissions_performed == 0
    assert result.proposal_category == "consensus_rejected"
    assert submit_calls == []


def test_replacement_after_proposal_suppresses_submission() -> None:
    initial = _template(marker=0x11)
    replacement = _template(marker=0x22)
    templates = [initial, replacement]
    submit_calls: list[bytes] = []

    result = _run(
        template=initial,
        fetch_template=lambda: templates.pop(0),
        submit_block=lambda block: (
            submit_calls.append(block) or SubmissionOutcome(True, "accepted")
        ),
    )

    assert result.outcome is SoloMiningOutcome.CANDIDATE_SUPPRESSED
    assert result.candidates_found == result.proposals_performed == 1
    assert result.candidates_suppressed == 1
    assert result.submissions_performed == 0
    assert submit_calls == []


def test_proposal_unavailability_has_fail_closed_policy() -> None:
    def unavailable(block: bytes) -> ProposalOutcome:
        del block
        raise BitcoinRpcRemoteError("method_unavailable")

    result = _run(propose_block=unavailable)

    assert result.outcome is SoloMiningOutcome.PROPOSAL_UNAVAILABLE
    assert result.proposals_performed == result.submissions_performed == 0


def test_submit_rejection_and_transport_failure_are_distinct() -> None:
    rejected = _run(submit_block=lambda block: SubmissionOutcome(False, "duplicate_invalid"))
    assert rejected.outcome is SoloMiningOutcome.BLOCK_REJECTED
    assert rejected.submission_category == "duplicate_invalid"

    def failed(block: bytes) -> SubmissionOutcome:
        del block
        raise BitcoinRpcTransportError("sanitized")

    transport_failure = _run(submit_block=failed)
    assert transport_failure.outcome is SoloMiningOutcome.RPC_FAILURE
    assert transport_failure.submissions_performed == 0


def test_candidate_on_replaced_template_is_suppressed_without_proposal() -> None:
    initial = _template(marker=0x11)
    replacement = _template(marker=0x22)
    observer = RecordingObserver()
    proposal_calls: list[bytes] = []

    result = _run(
        template=initial,
        fetch_template=lambda: replacement,
        observer=observer,
        candidate_policy=HashOnlyCandidatePolicy(),
        propose_block=lambda block: (
            proposal_calls.append(block) or ProposalOutcome(True, "accepted")
        ),
    )

    assert result.outcome is SoloMiningOutcome.CHUNK_LIMIT_REACHED
    assert result.template_replacements == 1
    assert result.candidates_suppressed == 1
    assert (
        result.candidates_found == result.proposals_performed == result.submissions_performed == 0
    )
    assert proposal_calls == []
    assert "replacement" in observer.events
    assert "suppressed" in observer.events


def test_rpc_invalidation_suppresses_candidate_and_terminates() -> None:
    def invalidated() -> BlockTemplate:
        raise BitcoinRpcTransportError("sanitized")

    result = _run(fetch_template=invalidated)

    assert result.outcome is SoloMiningOutcome.RPC_FAILURE
    assert result.candidates_suppressed == 1
    assert result.candidates_found == result.submissions_performed == 0


def test_user_stop_and_runtime_expiry_suppress_post_compute_candidate() -> None:
    stop = StopController()

    def stopped_search(work: object, start: int, stop_nonce: int) -> NonceSearchResult:
        result = search_nonce_range(work, start, stop_nonce)  # type: ignore[arg-type]
        stop.request_stop()
        return result

    stopped = _run(backend=PythonSequentialBackend(stopped_search), stop=stop)
    assert stopped.outcome is SoloMiningOutcome.STOPPED_BY_USER
    assert stopped.candidates_suppressed == 1
    assert stopped.submissions_performed == 0

    clock_value = [0.0]
    runtime_stop = StopController(1.0, clock=lambda: clock_value[0])

    def expired_search(work: object, start: int, stop_nonce: int) -> NonceSearchResult:
        result = search_nonce_range(work, start, stop_nonce)  # type: ignore[arg-type]
        clock_value[0] = 1.0
        return result

    expired = _run(
        backend=PythonSequentialBackend(expired_search),
        stop=runtime_stop,
        candidate_policy=HashOnlyCandidatePolicy(),
    )
    assert expired.outcome is SoloMiningOutcome.RUNTIME_LIMIT_REACHED
    assert expired.candidates_suppressed == 1


def test_nonce_exhaustion_advances_coinbase_and_never_repeats_work() -> None:
    observer = RecordingObserver()
    result = _run(
        backend=PythonSequentialBackend(_exhausted),
        plan=SoloMiningPlan(0, 2, max_chunks=3),
        observer=observer,
        nonce_limit=4,
    )

    assert result.outcome is SoloMiningOutcome.CHUNK_LIMIT_REACHED
    assert result.chunks_completed == 3
    assert result.work_variants_used == 2
    assert result.coinbase_extra_nonce_advances == 1
    assert result.timestamp_rolls == 0
    assert observer.events.count("variant") == 2
    assert observer.events.count("extra_nonce") == 1


def test_extra_nonce_wrap_rolls_time_only_within_local_and_configured_bounds() -> None:
    result = _run(
        backend=PythonSequentialBackend(_exhausted),
        plan=SoloMiningPlan(0, 2, max_chunks=2, max_time_roll_seconds=1),
        initial_coinbase_extra_nonce=(1 << 64) - 1,
        nonce_limit=2,
        wall_clock=lambda: 1_700_000_100.0,
    )

    assert result.outcome is SoloMiningOutcome.CHUNK_LIMIT_REACHED
    assert result.coinbase_extra_nonce_advances == 1
    assert result.timestamp_rolls == 1
    assert result.work_variants_used == 2


def test_same_template_is_not_stale_and_periodic_replacement_is_prompt() -> None:
    current = _template(marker=0x11)
    newer = _template(marker=0x22)
    now = [0.0]
    calls = [newer]

    def search(work: object, start: int, stop: int) -> NonceSearchResult:
        now[0] = 31.0
        return _exhausted(work, start, stop)

    result = _run(
        template=current,
        backend=PythonSequentialBackend(search),
        plan=SoloMiningPlan(0, 2, max_chunks=2, template_poll_seconds=30),
        fetch_template=lambda: calls.pop(0),
        candidate_policy=HashOnlyCandidatePolicy(),
        monotonic_clock=lambda: now[0],
        nonce_limit=4,
    )

    assert result.outcome is SoloMiningOutcome.CHUNK_LIMIT_REACHED
    assert result.templates_received == 2
    assert result.template_replacements == 1


@pytest.mark.parametrize("strategy_name", ["sequential", "orbiting-bit"])
def test_both_strategies_preserve_exact_bounded_coverage(strategy_name: str) -> None:
    ranges: list[tuple[int, int]] = []

    def search(work: object, start: int, stop: int) -> NonceSearchResult:
        ranges.append((start, stop))
        return _exhausted(work, start, stop)

    result = _run(
        backend=PythonSequentialBackend(search),
        plan=SoloMiningPlan(0, 2, max_chunks=4),
        candidate_policy=HashOnlyCandidatePolicy(),
        strategy_name=strategy_name,
        nonce_limit=8,
    )

    assert result.chunks_completed == 4
    assert sorted(ranges) == [(0, 2), (2, 4), (4, 6), (6, 8)]


def test_backend_candidate_flag_is_not_trusted_without_python_verification() -> None:
    def dishonest(work: object, start: int, stop: int) -> NonceSearchResult:
        del work
        match = NonceSearchMatch(start, bytes(32), True, True)
        return NonceSearchResult(start, stop, 1, 1, match)

    with pytest.raises(SoloMiningError, match="independent verification"):
        _run(
            backend=PythonSequentialBackend(dishonest),
            candidate_policy=HashOnlyCandidatePolicy(),
        )


def test_profile_pacing_wait_is_stop_aware_and_called_between_ranges() -> None:
    waits: list[float] = []
    result = _run(
        backend=PythonSequentialBackend(_exhausted),
        plan=SoloMiningPlan(0, 1, max_chunks=2, inter_range_delay_seconds=0.25),
        wait=lambda seconds, stop: waits.append(seconds),
        nonce_limit=4,
    )

    assert result.outcome is SoloMiningOutcome.CHUNK_LIMIT_REACHED
    assert waits == [0.25]
