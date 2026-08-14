"""Regression tests for nonterminal share handling in continuous mining."""

from __future__ import annotations

import pytest
from test_continuous_mining import Harness, run_with_harness

from hashorb.mining import (
    ContinuousMiningOutcome,
    ContinuousMiningPlan,
    NonceSearchMatch,
    PreparedMiningWork,
)
from hashorb.network.stratum.client import StratumRequestError
from hashorb.network.stratum.messages import StratumError


class PoolRejectingHarness(Harness):
    """Return one definite structured Stratum share rejection."""

    def submit(self, work: PreparedMiningWork, match: NonceSearchMatch) -> bool:
        self.submit_calls.append((work, match))
        raise StratumRequestError(
            "synthetic pool rejection",
            request_id=3,
            error=StratumError(23, "Low difficulty share", None),
        )


class AmbiguousRequestFailureHarness(Harness):
    """Raise an unclassified request failure that must remain fatal."""

    def submit(self, work: PreparedMiningWork, match: NonceSearchMatch) -> bool:
        self.submit_calls.append((work, match))
        raise StratumRequestError("synthetic ambiguous failure", request_id=3)


@pytest.mark.parametrize("accepted", [True, False])
def test_complete_range_share_response_does_not_end_continuous_mining(
    accepted: bool,
) -> None:
    harness = Harness(match_call=1, accepted=accepted)

    _, _, result = run_with_harness(
        ContinuousMiningPlan(start_nonce=0, chunk_size=1, max_chunks=2),
        harness,
    )

    assert result.outcome is ContinuousMiningOutcome.CHUNK_LIMIT_REACHED
    assert len(harness.search_calls) == 2
    assert len(harness.submit_calls) == 1
    assert result.candidates_found == 1
    assert result.submissions_performed == 1
    assert result.accepted_submissions == int(accepted)
    assert result.rejected_submissions == int(not accepted)
    assert ("submitted", "initial-job", 0, accepted) in harness.observations


def test_structured_pool_rejection_is_recorded_and_mining_continues() -> None:
    harness = PoolRejectingHarness(match_call=1)

    _, _, result = run_with_harness(
        ContinuousMiningPlan(start_nonce=0, chunk_size=1, max_chunks=2),
        harness,
    )

    assert result.outcome is ContinuousMiningOutcome.CHUNK_LIMIT_REACHED
    assert len(harness.search_calls) == 2
    assert len(harness.submit_calls) == 1
    assert result.candidates_found == 1
    assert result.submissions_performed == 1
    assert result.accepted_submissions == 0
    assert result.rejected_submissions == 1
    assert ("submitted", "initial-job", 0, False) in harness.observations
    assert ("rejection", 23, "low_difficulty") in harness.observations


def test_partial_range_match_keeps_existing_terminal_behavior() -> None:
    harness = Harness(match_call=1, accepted=False)

    _, _, result = run_with_harness(
        ContinuousMiningPlan(start_nonce=0, chunk_size=2, max_chunks=2),
        harness,
    )

    assert result.outcome is ContinuousMiningOutcome.SHARE_REJECTED
    assert len(harness.search_calls) == 1
    assert len(harness.submit_calls) == 1


def test_network_target_match_remains_terminal_even_after_complete_range() -> None:
    harness = Harness(match_call=1, match_flags=(True, True), accepted=True)

    _, _, result = run_with_harness(
        ContinuousMiningPlan(start_nonce=0, chunk_size=1, max_chunks=2),
        harness,
    )

    assert result.outcome is ContinuousMiningOutcome.SHARE_ACCEPTED
    assert len(harness.search_calls) == 1
    assert len(harness.submit_calls) == 1


def test_ambiguous_request_failure_is_not_silently_downgraded() -> None:
    harness = AmbiguousRequestFailureHarness(match_call=1)

    with pytest.raises(StratumRequestError, match="synthetic ambiguous failure"):
        run_with_harness(
            ContinuousMiningPlan(start_nonce=0, chunk_size=1, max_chunks=2),
            harness,
        )
