"""Dashboard tests for Best Hash, targets, and share outcomes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from hashorb.dashboard import (
    DashboardLogError,
    DashboardRecord,
    DashboardState,
    render_dashboard,
)
from hashorb.mining import decode_compact_target, difficulty_to_share_target

_BASE_TIME = datetime(2026, 8, 12, 10, 0, tzinfo=UTC)


def _record(
    sequence: int,
    event: str,
    *,
    seconds: float | None = None,
    run_id: str = "quality-run",
    **fields: object,
) -> DashboardRecord:
    return DashboardRecord(
        timestamp=_BASE_TIME + timedelta(seconds=sequence if seconds is None else seconds),
        run_id=run_id,
        sequence=sequence,
        level="INFO",
        event=event,
        command="stratum-mine",
        fields=fields,  # type: ignore[arg-type]
    )


def test_dashboard_projects_and_renders_hash_quality_panel() -> None:
    state = DashboardState()
    state.apply(_record(1, "command_started"))
    state.apply(_record(2, "difficulty_received", difficulty=10_000))
    state.apply(
        _record(
            3,
            "mining_job_received",
            job_id="quality-job",
            network_bits="170fffff",
        )
    )
    state.apply(_record(4, "best_hash_improved", best_hash=f"{123456789:064x}"))
    state.apply(
        _record(
            5,
            "share_candidate_found",
            job_id="quality-job",
            nonce=7,
            abbreviated_block_hash="00000000…000",
            meets_share_target=True,
            meets_network_target=False,
        )
    )
    state.apply(
        _record(
            6,
            "share_submission_completed",
            job_id="quality-job",
            nonce=7,
            accepted=True,
        )
    )
    state.apply(
        _record(
            7,
            "share_candidate_found",
            job_id="quality-job",
            nonce=8,
            abbreviated_block_hash="00000000…000",
            meets_share_target=True,
            meets_network_target=True,
        )
    )
    state.apply(
        _record(
            8,
            "share_submission_completed",
            job_id="quality-job",
            nonce=8,
            accepted=False,
        )
    )

    assert state.best_hash == f"{123456789:064x}"
    assert state.best_hash_value == 123456789
    assert state.share_target_hit is True
    assert state.network_target_hit is True
    assert state.submissions == 2
    assert state.accepted_submissions == 1
    assert state.rejected_submissions == 1

    network_target = decode_compact_target("170fffff")
    share_target = difficulty_to_share_target(10_000)

    rendered = render_dashboard(state, width=140, color=False)

    assert "HASH QUALITY / TARGET" in rendered
    assert f"Best Hash       {123456789:064x}" in rendered
    assert "Best Difficulty " in rendered
    assert f"Network Target  0x{network_target:064x}" in rendered
    assert f"Share Target    0x{share_target:064x}" in rendered
    assert "Share Target HIT" in rendered
    assert "Network Target HIT" in rendered
    assert "Shares Submitted 2" in rendered
    assert "Accepted 1" in rendered
    assert "Rejected 1" in rendered


def test_best_hash_and_target_hits_are_run_wide_but_reset_on_new_command() -> None:
    state = DashboardState()
    state.apply(_record(1, "command_started"))
    state.apply(_record(2, "best_hash_improved", best_hash=f"{200:064x}"))
    state.apply(
        _record(
            3,
            "share_candidate_found",
            meets_share_target=True,
            meets_network_target=True,
        )
    )
    state.apply(
        _record(
            4,
            "mining_job_replaced",
            previous_job_id="old",
            new_job_id="new",
            clean_jobs=True,
            replacement_index=1,
        )
    )
    state.apply(
        _record(
            5,
            "mining_work_advanced",
            reason="extra_nonce_2",
            work_variant_index=2,
            extra_nonce_2_advance_count=1,
            network_time_roll_count=0,
        )
    )

    assert state.best_hash == f"{200:064x}"
    assert state.share_target_hit is True
    assert state.network_target_hit is True

    state.apply(
        _record(
            1,
            "command_started",
            seconds=10,
            run_id="new-quality-run",
        )
    )

    assert state.best_hash is None
    assert state.best_hash_value is None
    assert state.share_target_hit is False
    assert state.network_target_hit is False
    assert state.submissions == 0


@pytest.mark.parametrize(
    "value",
    [
        "",
        "0" * 63,
        "0" * 65,
        "G" * 64,
        "A" * 64,
    ],
)
def test_dashboard_rejects_noncanonical_best_hash(value: str) -> None:
    state = DashboardState()
    state.apply(_record(1, "command_started"))

    with pytest.raises(DashboardLogError, match="Best Hash"):
        state.apply(_record(2, "best_hash_improved", best_hash=value))


def test_dashboard_rejects_non_improving_best_hash_event() -> None:
    state = DashboardState()
    state.apply(_record(1, "command_started"))
    state.apply(_record(2, "best_hash_improved", best_hash=f"{100:064x}"))

    with pytest.raises(DashboardLogError, match="not strict"):
        state.apply(_record(3, "best_hash_improved", best_hash=f"{100:064x}"))

    with pytest.raises(DashboardLogError, match="not strict"):
        state.apply(_record(4, "best_hash_improved", best_hash=f"{101:064x}"))


def test_dashboard_rejects_candidate_without_any_target_hit() -> None:
    state = DashboardState()
    state.apply(_record(1, "command_started"))

    with pytest.raises(DashboardLogError, match="no target hit"):
        state.apply(
            _record(
                2,
                "share_candidate_found",
                meets_share_target=False,
                meets_network_target=False,
            )
        )


def test_target_values_are_derived_from_safe_existing_telemetry() -> None:
    state = DashboardState()
    state.apply(_record(1, "command_started"))
    state.apply(_record(2, "difficulty_received", difficulty=1))
    state.apply(
        _record(
            3,
            "mining_job_received",
            job_id="quality-job",
            network_bits="1d00ffff",
        )
    )

    assert state.share_target_value == difficulty_to_share_target(1)
    assert state.network_target_value == decode_compact_target("1d00ffff")


@pytest.mark.parametrize(
    ("event", "fields", "match"),
    [
        ("difficulty_received", {"difficulty": 0}, "difficulty"),
        (
            "mining_job_received",
            {"job_id": "quality-job", "network_bits": "not-bits"},
            "network bits",
        ),
    ],
)
def test_dashboard_rejects_invalid_target_inputs(
    event: str,
    fields: dict[str, object],
    match: str,
) -> None:
    state = DashboardState()
    state.apply(_record(1, "command_started"))

    with pytest.raises(DashboardLogError, match=match):
        state.apply(_record(2, event, **fields))
