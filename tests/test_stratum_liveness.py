"""Deterministic tests for separate Stratum liveness clocks."""

from __future__ import annotations

import pytest

from hashphere.mining import (
    MAX_LIVENESS_SECONDS,
    StratumLivenessError,
    StratumLivenessPolicy,
    StratumLivenessTracker,
    StratumStaleReason,
)
from hashphere.network.stratum import (
    MiningNotifyNotification,
    SetDifficultyNotification,
)


def job() -> MiningNotifyNotification:
    return MiningNotifyNotification(
        job_id="synthetic-job",
        previous_block_hash="00" * 32,
        coinbase_part_1="01000000",
        coinbase_part_2="ffffffff",
        merkle_branches=(),
        version="20000000",
        network_bits="170fffff",
        network_time="65f04abc",
        clean_jobs=True,
    )


@pytest.mark.parametrize("values", [(None, None), (1, None), (None, 1.5), (2.5, 3)])
def test_policy_accepts_disabled_integer_and_fractional_limits(
    values: tuple[float | None, float | None],
) -> None:
    policy = StratumLivenessPolicy(*values)
    assert policy.enabled is (values != (None, None))


@pytest.mark.parametrize(
    "value",
    [True, 0, -1, float("nan"), float("inf"), -float("inf"), MAX_LIVENESS_SECONDS + 1],
)
def test_policy_rejects_invalid_limits(value: object) -> None:
    with pytest.raises(StratumLivenessError):
        StratumLivenessPolicy(value)  # type: ignore[arg-type]
    with pytest.raises(StratumLivenessError):
        StratumLivenessPolicy(max_job_age_seconds=value)  # type: ignore[arg-type]


def test_disabled_policy_never_declares_stale() -> None:
    now = [0.0]
    tracker = StratumLivenessTracker(StratumLivenessPolicy(), clock=lambda: now[0])
    now[0] = 10**12
    assert tracker.violation() is None


def test_difficulty_refreshes_server_activity_but_not_job_age() -> None:
    now = [0.0]
    tracker = StratumLivenessTracker(
        StratumLivenessPolicy(max_server_silence_seconds=5, max_job_age_seconds=10),
        clock=lambda: now[0],
    )
    now[0] = 4.0
    tracker.notification_received(SetDifficultyNotification(difficulty=2))
    now[0] = 9.0
    violation = tracker.violation()
    assert violation is not None
    assert violation.reason is StratumStaleReason.SERVER_SILENCE
    now[0] = 10.0
    tracker.notification_received(SetDifficultyNotification(difficulty=3))
    violation = tracker.violation()
    assert violation is not None
    assert violation.reason is StratumStaleReason.JOB_AGE


def test_notify_refreshes_both_server_activity_and_job_age() -> None:
    now = [0.0]
    tracker = StratumLivenessTracker(
        StratumLivenessPolicy(max_server_silence_seconds=5, max_job_age_seconds=5),
        clock=lambda: now[0],
    )
    now[0] = 4.0
    tracker.notification_received(job())
    now[0] = 8.9
    assert tracker.violation() is None


def test_complete_response_refreshes_server_activity_but_not_job_age() -> None:
    now = [0.0]
    tracker = StratumLivenessTracker(
        StratumLivenessPolicy(max_server_silence_seconds=5, max_job_age_seconds=10),
        clock=lambda: now[0],
    )
    now[0] = 4.0
    tracker.server_message_received()
    now[0] = 9.0
    violation = tracker.violation()
    assert violation is not None
    assert violation.reason is StratumStaleReason.SERVER_SILENCE
    now[0] = 10.0
    tracker.server_message_received()
    violation = tracker.violation()
    assert violation is not None
    assert violation.reason is StratumStaleReason.JOB_AGE


def test_range_completion_refreshes_only_work_activity() -> None:
    now = [0.0]
    tracker = StratumLivenessTracker(
        StratumLivenessPolicy(max_server_silence_seconds=5),
        clock=lambda: now[0],
    )
    now[0] = 4.0
    tracker.range_completed()
    assert tracker.work_idle_seconds == 0
    now[0] = 5.0
    violation = tracker.violation()
    assert violation is not None
    assert violation.reason is StratumStaleReason.SERVER_SILENCE


@pytest.mark.parametrize(
    ("policy", "expected"),
    [
        (StratumLivenessPolicy(max_server_silence_seconds=5), StratumStaleReason.SERVER_SILENCE),
        (StratumLivenessPolicy(max_job_age_seconds=5), StratumStaleReason.JOB_AGE),
        (
            StratumLivenessPolicy(max_server_silence_seconds=5, max_job_age_seconds=5),
            StratumStaleReason.SERVER_SILENCE,
        ),
    ],
)
def test_threshold_is_not_crossed_early_and_is_crossed_exactly_at_boundary(
    policy: StratumLivenessPolicy,
    expected: StratumStaleReason,
) -> None:
    now = [0.0]
    tracker = StratumLivenessTracker(policy, clock=lambda: now[0])
    now[0] = 4.999
    assert tracker.violation() is None
    now[0] = 5.0
    violation = tracker.violation()
    assert violation is not None
    assert violation.reason is expected
    assert violation.threshold_seconds == 5
    assert violation.elapsed_seconds == 5


def test_fresh_session_resets_all_activity_clocks() -> None:
    now = [0.0]
    tracker = StratumLivenessTracker(
        StratumLivenessPolicy(max_server_silence_seconds=5, max_job_age_seconds=5),
        clock=lambda: now[0],
    )
    now[0] = 5.0
    assert tracker.violation() is not None
    tracker.session_replaced()
    assert tracker.violation() is None
