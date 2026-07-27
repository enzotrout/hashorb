"""Tests for the immutable mining-job domain and assembler."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest

from hashphere.mining import (
    MiningJob,
    MiningJobAssembler,
    MiningJobStateError,
    MiningJobValidationError,
)
from hashphere.network.stratum import (
    MiningNotifyNotification,
    SetDifficultyNotification,
    SubscribeResult,
)


def valid_subscription(
    *,
    extra_nonce_1: str = "0800000A",
    extra_nonce_2_size: int = 4,
) -> SubscribeResult:
    """Return valid deterministic subscription data."""

    return SubscribeResult(
        subscriptions=(("mining.notify", "subscription-id"),),
        extra_nonce_1=extra_nonce_1,
        extra_nonce_2_size=extra_nonce_2_size,
    )


def valid_notification(
    *,
    job_id: str = "job-1",
    previous_block_hash: str = "00" * 32,
    coinbase_part_1: str = "0102aB",
    coinbase_part_2: str = "CDef03",
    merkle_branches: tuple[str, ...] = ("11" * 32, "aB" * 32),
    version: str = "2000000A",
    network_bits: str = "170fFFfF",
    network_time: str = "65F04aBc",
    clean_jobs: bool = True,
) -> MiningNotifyNotification:
    """Return a valid deterministic mining notification."""

    return MiningNotifyNotification(
        job_id=job_id,
        previous_block_hash=previous_block_hash,
        coinbase_part_1=coinbase_part_1,
        coinbase_part_2=coinbase_part_2,
        merkle_branches=merkle_branches,
        version=version,
        network_bits=network_bits,
        network_time=network_time,
        clean_jobs=clean_jobs,
    )


def valid_job(**overrides: object) -> MiningJob:
    """Construct a valid job with optional deliberately untyped overrides."""

    values: dict[str, object] = {
        "job_id": "job-1",
        "previous_block_hash": "00" * 32,
        "coinbase_part_1": "0102aB",
        "coinbase_part_2": "CDef03",
        "merkle_branches": ("11" * 32, "aB" * 32),
        "version": "2000000A",
        "network_bits": "170fFFfF",
        "network_time": "65F04aBc",
        "clean_jobs": True,
        "extra_nonce_1": "0800000A",
        "extra_nonce_2_size": 4,
        "difficulty": 2048.5,
    }
    values.update(overrides)
    return MiningJob(**values)  # type: ignore[arg-type]


def initialized_assembler() -> MiningJobAssembler:
    """Return an assembler with a valid current difficulty."""

    assembler = MiningJobAssembler(valid_subscription())
    assembler.apply_difficulty(SetDifficultyNotification(difficulty=2048.5))
    return assembler


def test_assembler_initializes_from_subscription_without_difficulty() -> None:
    subscription = valid_subscription()

    assembler = MiningJobAssembler(subscription)

    assert assembler.extra_nonce_1 == "0800000A"
    assert assembler.extra_nonce_2_size == 4
    assert assembler.current_difficulty is None


def test_apply_difficulty_replaces_current_value() -> None:
    assembler = MiningJobAssembler(valid_subscription())

    assembler.apply_difficulty(SetDifficultyNotification(difficulty=1024))
    assert assembler.current_difficulty == 1024

    assembler.apply_difficulty(SetDifficultyNotification(difficulty=4096.5))
    assert assembler.current_difficulty == 4096.5


def test_build_job_copies_all_fields_and_preserves_protocol_text() -> None:
    notification = valid_notification()
    assembler = initialized_assembler()

    job = assembler.build_job(notification)

    assert job == MiningJob(
        job_id="job-1",
        previous_block_hash="00" * 32,
        coinbase_part_1="0102aB",
        coinbase_part_2="CDef03",
        merkle_branches=("11" * 32, "aB" * 32),
        version="2000000A",
        network_bits="170fFFfF",
        network_time="65F04aBc",
        clean_jobs=True,
        extra_nonce_1="0800000A",
        extra_nonce_2_size=4,
        difficulty=2048.5,
    )
    assert job.merkle_branches is notification.merkle_branches


def test_build_before_difficulty_raises_state_error() -> None:
    assembler = MiningJobAssembler(valid_subscription())

    with pytest.raises(MiningJobStateError, match="before receiving difficulty"):
        assembler.build_job(valid_notification())


def test_difficulty_updates_affect_only_later_jobs() -> None:
    assembler = MiningJobAssembler(valid_subscription())
    assembler.apply_difficulty(SetDifficultyNotification(difficulty=1024))
    first = assembler.build_job(valid_notification(job_id="first"))

    assembler.apply_difficulty(SetDifficultyNotification(difficulty=4096))
    second = assembler.build_job(valid_notification(job_id="second"))

    assert first.difficulty == 1024
    assert second.difficulty == 4096
    assert first.job_id == "first"
    assert second.job_id == "second"


@pytest.mark.parametrize("clean_jobs", [True, False])
def test_clean_jobs_is_preserved_without_interpretation(clean_jobs: bool) -> None:
    assembler = initialized_assembler()

    job = assembler.build_job(valid_notification(clean_jobs=clean_jobs))

    assert job.clean_jobs is clean_jobs


def test_mining_job_and_merkle_branches_are_immutable() -> None:
    job = valid_job()

    assert isinstance(job.merkle_branches, tuple)
    with pytest.raises(FrozenInstanceError):
        job.job_id = "replacement"  # type: ignore[misc]


def test_source_notifications_are_not_mutated() -> None:
    difficulty = SetDifficultyNotification(difficulty=1024)
    notification = valid_notification()
    original_difficulty = replace(difficulty)
    original_notification = replace(notification)
    assembler = MiningJobAssembler(valid_subscription())

    assembler.apply_difficulty(difficulty)
    assembler.build_job(notification)

    assert difficulty == original_difficulty
    assert notification == original_notification


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("previous_block_hash", "00" * 31 + "zz"),
        ("coinbase_part_1", "01xz"),
        ("coinbase_part_2", "-1"),
        ("version", "2000000g"),
        ("network_bits", "170ffff_"),
        ("network_time", "65f04ab "),
        ("extra_nonce_1", "0800000Z"),
        ("merkle_branches", ("11" * 31 + "xy",)),
    ],
)
def test_direct_construction_rejects_invalid_hexadecimal_data(
    field: str,
    value: object,
) -> None:
    with pytest.raises(MiningJobValidationError, match="hexadecimal"):
        valid_job(**{field: value})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("previous_block_hash", "0" * 63),
        ("coinbase_part_1", "123"),
        ("coinbase_part_2", "abcde"),
        ("version", "1234567"),
        ("network_bits", "123456789"),
        ("network_time", "1234567"),
        ("extra_nonce_1", "abc"),
        ("merkle_branches", ("1" * 63,)),
    ],
)
def test_direct_construction_rejects_odd_hexadecimal_lengths(
    field: str,
    value: object,
) -> None:
    with pytest.raises(MiningJobValidationError, match="whole number of bytes"):
        valid_job(**{field: value})


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("previous_block_hash", "00" * 31, "exactly 32 bytes"),
        ("previous_block_hash", "00" * 33, "exactly 32 bytes"),
        ("version", "00" * 3, "exactly 4 bytes"),
        ("network_bits", "00" * 5, "exactly 4 bytes"),
        ("network_time", "00" * 3, "exactly 4 bytes"),
        ("merkle_branches", ("00" * 31,), "exactly 32 bytes"),
        ("merkle_branches", ("00" * 33,), "exactly 32 bytes"),
    ],
)
def test_direct_construction_rejects_incorrect_fixed_lengths(
    field: str,
    value: object,
    message: str,
) -> None:
    with pytest.raises(MiningJobValidationError, match=message):
        valid_job(**{field: value})


@pytest.mark.parametrize("value", [0, -1, True, False, 1.5, "4"])
def test_direct_construction_rejects_invalid_extra_nonce_2_size(value: object) -> None:
    with pytest.raises(MiningJobValidationError, match="positive integer"):
        valid_job(extra_nonce_2_size=value)


@pytest.mark.parametrize(
    "value",
    [0, -1, True, False, float("nan"), float("inf"), float("-inf"), "1024"],
)
def test_direct_construction_rejects_invalid_difficulty(value: object) -> None:
    with pytest.raises(MiningJobValidationError, match="difficulty"):
        valid_job(difficulty=value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("job_id", ""),
        ("job_id", "   "),
        ("previous_block_hash", ""),
        ("coinbase_part_1", ""),
        ("coinbase_part_2", ""),
        ("version", ""),
        ("network_bits", ""),
        ("network_time", ""),
        ("extra_nonce_1", ""),
        ("merkle_branches", ("",)),
    ],
)
def test_direct_construction_rejects_empty_identifiers_and_fields(
    field: str,
    value: object,
) -> None:
    with pytest.raises(MiningJobValidationError, match="must not be empty"):
        valid_job(**{field: value})


def test_direct_construction_rejects_mutable_merkle_collection() -> None:
    with pytest.raises(MiningJobValidationError, match="must be a tuple"):
        valid_job(merkle_branches=["11" * 32])


def test_direct_construction_rejects_non_boolean_clean_jobs() -> None:
    with pytest.raises(MiningJobValidationError, match="must be a boolean"):
        valid_job(clean_jobs=1)


@pytest.mark.parametrize(
    ("extra_nonce_1", "extra_nonce_2_size"),
    [
        ("", 4),
        ("abc", 4),
        ("xyz0", 4),
        ("08000002", 0),
        ("08000002", True),
    ],
)
def test_assembler_rejects_invalid_subscription_session_data(
    extra_nonce_1: str,
    extra_nonce_2_size: int,
) -> None:
    with pytest.raises(MiningJobValidationError):
        MiningJobAssembler(
            valid_subscription(
                extra_nonce_1=extra_nonce_1,
                extra_nonce_2_size=extra_nonce_2_size,
            )
        )


@pytest.mark.parametrize(
    "value",
    [0, -1, True, float("nan"), float("inf")],
)
def test_apply_difficulty_rejects_invalid_notification_values(value: object) -> None:
    assembler = MiningJobAssembler(valid_subscription())

    with pytest.raises(MiningJobValidationError, match="difficulty"):
        assembler.apply_difficulty(
            SetDifficultyNotification(difficulty=value)  # type: ignore[arg-type]
        )
