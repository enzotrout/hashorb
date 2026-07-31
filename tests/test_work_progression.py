"""Tests for deterministic mining work-space progression."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest

from hashorb.mining import (
    MiningJob,
    MiningWorkCursor,
    MiningWorkIdentity,
    MiningWorkProgressionError,
    MiningWorkProgressionValidationError,
    PreparedMiningWork,
    mining_job_context_identity,
    mining_work_identity,
    prepare_work_variant,
)


def make_job(
    *,
    job_id: str = "job-a",
    extra_nonce_2_size: int = 1,
    network_time: str = "65f04abc",
    difficulty: int | float = 100,
    clean_jobs: bool = True,
) -> MiningJob:
    """Build one valid synthetic job for cursor tests."""

    return MiningJob(
        job_id=job_id,
        previous_block_hash="00" * 32,
        coinbase_part_1="01000000cafebabe",
        coinbase_part_2="ffffffffdeadbeef",
        merkle_branches=("11" * 32,),
        version="20000000",
        network_bits="170fffff",
        network_time=network_time,
        clean_jobs=clean_jobs,
        extra_nonce_1="08000002",
        extra_nonce_2_size=extra_nonce_2_size,
        difficulty=difficulty,
    )


def fake_prepare(job: MiningJob, extra_nonce_2: str) -> PreparedMiningWork:
    """Encode variant inputs into deterministic fake prepared work."""

    marker = bytes.fromhex(job.network_time + extra_nonce_2)
    header_prefix = (marker + bytes(76))[:76]
    return PreparedMiningWork(
        job_id=job.job_id,
        extra_nonce_2=extra_nonce_2,
        network_time=job.network_time,
        header_prefix=header_prefix,
        network_target=1,
        share_target=int(job.difficulty),
    )


@pytest.mark.parametrize(
    ("starting", "expected"),
    [("00", ["00", "01", "02"]), ("7f", ["7f", "80", "81"]), ("ff", ["ff", "00", "01"])],
)
def test_one_byte_progression_is_fixed_width_and_modular(
    starting: str,
    expected: list[str],
) -> None:
    cursor = MiningWorkCursor.start(make_job(), starting)
    observed = [cursor.current_extra_nonce_2]
    for _ in range(2):
        progress = cursor.advance()
        assert progress.cursor is not None
        cursor = progress.cursor
        observed.append(cursor.current_extra_nonce_2)

    assert observed == expected
    assert cursor.extra_nonce_2_advance_count == 2
    assert cursor.network_time_roll_count == 0


def test_every_one_byte_value_appears_once_before_cycle_completion() -> None:
    cursor = MiningWorkCursor.start(make_job(network_time="00000001"), "a7")
    observed: list[str] = []

    for index in range(256):
        observed.append(cursor.current_extra_nonce_2)
        progress = cursor.advance()
        if index < 255:
            assert progress.extra_nonce_2_cycle_completed is False
            assert progress.cursor is not None
            cursor = progress.cursor

    assert len(observed) == 256
    assert len(set(observed)) == 256
    assert observed[0] == "a7"
    assert "00" in observed
    assert progress.extra_nonce_2_cycle_completed is True
    assert progress.network_time_rolled is True
    assert progress.cursor is not None
    assert progress.cursor.current_extra_nonce_2 == "a7"
    assert progress.cursor.current_network_time == "00000002"
    assert progress.cursor.variants_at_current_time == 1
    assert progress.cursor.extra_nonce_2_advance_count == 256


def test_network_time_roll_uses_lowercase_fixed_width_only_after_full_cycle() -> None:
    cursor = MiningWorkCursor.start(make_job(network_time="ABCDEF00"), "00")
    assert cursor.current_network_time == "ABCDEF00"

    for _ in range(255):
        progress = cursor.advance()
        assert progress.cursor is not None
        assert progress.network_time_rolled is False
        cursor = progress.cursor

    progress = cursor.advance()
    assert progress.cursor is not None
    assert progress.extra_nonce_2_cycle_completed is True
    assert progress.network_time_rolled is True
    assert progress.cursor.current_network_time == "abcdef01"
    assert progress.cursor.current_extra_nonce_2 == "00"
    assert progress.cursor.current_variant.job.network_time == "abcdef01"


def test_maximum_network_time_exhausts_without_wrap() -> None:
    cursor = MiningWorkCursor.start(make_job(network_time="ffffffff"), "80")
    for _ in range(255):
        progress = cursor.advance()
        assert progress.cursor is not None
        cursor = progress.cursor

    progress = cursor.advance()

    assert progress.cursor is None
    assert progress.extra_nonce_2_advanced is False
    assert progress.extra_nonce_2_cycle_completed is True
    assert progress.network_time_rolled is False
    assert progress.progression_exhausted is True


def test_eight_byte_cursor_advances_arithmetically_without_enumeration() -> None:
    cursor = MiningWorkCursor.start(
        make_job(extra_nonce_2_size=8),
        "ffffffffffffffff",
    )

    progress = cursor.advance()

    assert cursor.extra_nonce_2_space_size == 2**64
    assert progress.cursor is not None
    assert progress.cursor.current_extra_nonce_2 == "0000000000000000"
    assert progress.cursor.variants_at_current_time == 2


def test_cursor_and_variant_are_immutable_and_do_not_expose_seed_in_repr() -> None:
    cursor = MiningWorkCursor.start(make_job(), "ab")
    variant = cursor.current_variant

    with pytest.raises(FrozenInstanceError):
        cursor.variant_index = 1  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        variant.extra_nonce_2 = "cd"  # type: ignore[misc]
    assert not hasattr(cursor, "__dict__")
    assert not hasattr(variant, "__dict__")
    assert "ab" not in repr(cursor)
    assert "ab" not in repr(variant)


@pytest.mark.parametrize(
    "starting",
    ["", "0", "0000", "gg", "FF", 0, None],
)
def test_start_rejects_invalid_extra_nonce_values(starting: object) -> None:
    with pytest.raises(MiningWorkProgressionValidationError):
        MiningWorkCursor.start(make_job(), starting)  # type: ignore[arg-type]


def test_direct_cursor_validation_rejects_inconsistent_state() -> None:
    cursor = MiningWorkCursor.start(make_job(), "00")

    with pytest.raises(MiningWorkProgressionValidationError, match="cycle"):
        replace(cursor, variants_at_current_time=257)
    with pytest.raises(MiningWorkProgressionValidationError, match="elapsed"):
        replace(cursor, network_time_value=cursor.network_time_value + 1)


def test_prepare_variant_preserves_inputs_and_does_not_mutate_job() -> None:
    job = make_job()
    original = replace(job)
    cursor = MiningWorkCursor.start(job, "fe")
    first = cursor.current_variant
    next_progress = cursor.advance()
    assert next_progress.cursor is not None
    second = next_progress.cursor.current_variant

    first_work = prepare_work_variant(first, prepare_work=fake_prepare)
    second_work = prepare_work_variant(second, prepare_work=fake_prepare)

    assert first_work.extra_nonce_2 == "fe"
    assert second_work.extra_nonce_2 == "ff"
    assert first_work.header_prefix != second_work.header_prefix
    assert job == original


def test_prepare_variant_rejects_callback_that_changes_variant_metadata() -> None:
    variant = MiningWorkCursor.start(make_job(), "01").current_variant

    def wrong_prepare(job: MiningJob, extra_nonce_2: str) -> PreparedMiningWork:
        del extra_nonce_2
        return fake_prepare(job, "02")

    with pytest.raises(MiningWorkProgressionError, match="extra nonce"):
        prepare_work_variant(variant, prepare_work=wrong_prepare)


def test_effective_identity_includes_header_job_and_target_context() -> None:
    work = fake_prepare(make_job(), "01")
    same = mining_work_identity(work)
    changed_header = replace(work, header_prefix=b"x" + work.header_prefix[1:])
    changed_job = replace(work, job_id="job-b")
    changed_share_target = replace(work, share_target=work.share_target + 1)

    assert same == mining_work_identity(work)
    assert same != mining_work_identity(changed_header)
    assert same != mining_work_identity(changed_job)
    assert same != mining_work_identity(changed_share_target)
    assert isinstance(same, MiningWorkIdentity)


def test_pool_context_ignores_clean_flag_but_includes_job_and_difficulty() -> None:
    job = make_job(clean_jobs=True)
    identity = mining_job_context_identity(job)

    assert identity == mining_job_context_identity(replace(job, clean_jobs=False))
    assert identity != mining_job_context_identity(replace(job, job_id="job-b"))
    assert identity != mining_job_context_identity(replace(job, difficulty=200))
