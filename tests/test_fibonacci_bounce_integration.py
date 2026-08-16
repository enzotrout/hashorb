"""Integration tests for Fibonacci-bounce through continuous mining orchestration."""

from __future__ import annotations

from test_continuous_mining import Harness, notification, run_with_harness

from hashorb.mining import (
    ContinuousMiningOutcome,
    ContinuousMiningPlan,
    FibonacciBounceSearchStrategy,
)

NONCE_LIMIT = 1 << 32


def test_continuous_mining_uses_fibonacci_bounce_parent_range_order() -> None:
    chunk_size = NONCE_LIMIT // 4
    harness = Harness()

    _, _, result = run_with_harness(
        ContinuousMiningPlan(
            start_nonce=0,
            chunk_size=chunk_size,
            max_chunks=4,
        ),
        harness,
        strategy=FibonacciBounceSearchStrategy(),
    )

    assert result.outcome is ContinuousMiningOutcome.CHUNK_LIMIT_REACHED
    assert [(start, stop) for _, start, stop in harness.search_calls] == [
        (0, chunk_size),
        (3 * chunk_size, NONCE_LIMIT),
        (chunk_size, 2 * chunk_size),
        (2 * chunk_size, 3 * chunk_size),
    ]
    assert result.chunks_completed == 4
    assert result.total_hashes_checked == NONCE_LIMIT
    assert result.work_variants_used == 1
    assert result.candidates_found == 0
    assert result.submissions_performed == 0


def test_fibonacci_bounce_cursor_resets_for_replacement_work() -> None:
    chunk_size = NONCE_LIMIT // 4
    harness = Harness()
    harness.notifications.append(notification("replacement-job"))

    _, _, result = run_with_harness(
        ContinuousMiningPlan(
            start_nonce=0,
            chunk_size=chunk_size,
            max_chunks=2,
        ),
        harness,
        strategy=FibonacciBounceSearchStrategy(),
    )

    assert result.outcome is ContinuousMiningOutcome.CHUNK_LIMIT_REACHED
    assert [(work.job_id, start, stop) for work, start, stop in harness.search_calls] == [
        ("initial-job", 0, chunk_size),
        ("replacement-job", 0, chunk_size),
    ]
    assert result.job_replacements == 1
    assert result.jobs_used == 2
