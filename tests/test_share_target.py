"""Tests for deterministic Stratum share-target calculation."""

from __future__ import annotations

from decimal import Decimal
from fractions import Fraction

import pytest

from hashorb.mining import (
    MiningJob,
    TargetValidationError,
    decode_compact_target,
    difficulty_to_share_target,
    hash_meets_target,
)

DIFFICULTY_1_TARGET = int(
    "00000000ffff0000000000000000000000000000000000000000000000000000",
    16,
)
MAX_UINT256 = (1 << 256) - 1


def valid_job(
    *,
    difficulty: int | float = 10_000,
    network_bits: str = "17023ad4",
) -> MiningJob:
    """Return a valid synthetic job with independent target inputs."""

    return MiningJob(
        job_id="job-share-target",
        previous_block_hash="00" * 32,
        coinbase_part_1="01000000",
        coinbase_part_2="DEADBEEF",
        merkle_branches=(),
        version="20000000",
        network_bits=network_bits,
        network_time="65f04abc",
        clean_jobs=True,
        extra_nonce_1="A1B2",
        extra_nonce_2_size=2,
        difficulty=difficulty,
    )


@pytest.mark.parametrize(
    ("difficulty", "expected_hex"),
    [
        (
            1,
            "00000000ffff0000000000000000000000000000000000000000000000000000",
        ),
        (
            2,
            "000000007fff8000000000000000000000000000000000000000000000000000",
        ),
        (
            10_000,
            "0000000000068db22d0e5604189374bc6a7ef9db22d0e5604189374bc6a7ef9d",
        ),
        (
            0.5,
            "00000001fffe0000000000000000000000000000000000000000000000000000",
        ),
        (
            2.5,
            "0000000066660000000000000000000000000000000000000000000000000000",
        ),
        (
            10,
            "0000000019998000000000000000000000000000000000000000000000000000",
        ),
        (
            100,
            "00000000028f5999999999999999999999999999999999999999999999999999",
        ),
        (
            0.01,
            "00000063ff9c0000000000000000000000000000000000000000000000000000",
        ),
        (
            123.45,
            "000000000212dcd9bd6f0c21981b5f7e99a986b00eee450dff60bd1f6ab14d74",
        ),
        (
            1_000_000_000,
            "00000000000000044b7eae86bb9b77145e86c9077061bcde6d333434e5e5cd79",
        ),
    ],
)
def test_known_share_target_vectors(
    difficulty: int | float,
    expected_hex: str,
) -> None:
    target = difficulty_to_share_target(difficulty)

    assert target == int(expected_hex, 16)
    assert isinstance(target, int)
    assert not isinstance(target, bool)
    assert 1 <= target <= MAX_UINT256


def test_integer_and_exactly_represented_float_are_equivalent() -> None:
    assert difficulty_to_share_target(2) == difficulty_to_share_target(2.0)


def test_decimal_float_uses_its_string_representation_exactly() -> None:
    difficulty = 0.1
    ratio = Fraction("0.1")
    independently_expected = (DIFFICULTY_1_TARGET * ratio.denominator) // ratio.numerator

    assert difficulty_to_share_target(difficulty) == independently_expected


def test_floor_division_discards_a_nonzero_remainder() -> None:
    difficulty = 7
    target = difficulty_to_share_target(difficulty)

    assert DIFFICULTY_1_TARGET % difficulty != 0
    assert target == DIFFICULTY_1_TARGET // difficulty
    assert target * difficulty < DIFFICULTY_1_TARGET
    assert (target + 1) * difficulty > DIFFICULTY_1_TARGET


def test_repeated_calls_are_deterministic_and_do_not_mutate_input() -> None:
    difficulty = 4096.5
    original = difficulty

    assert difficulty_to_share_target(difficulty) == difficulty_to_share_target(difficulty)
    assert difficulty == original


@pytest.mark.parametrize("difficulty", [0, 0.0, -1, -1.5])
def test_nonpositive_difficulty_is_rejected(difficulty: int | float) -> None:
    with pytest.raises(TargetValidationError, match="greater than zero"):
        difficulty_to_share_target(difficulty)


@pytest.mark.parametrize(
    "difficulty",
    [float("nan"), float("inf"), float("-inf")],
)
def test_nonfinite_difficulty_is_rejected(difficulty: float) -> None:
    with pytest.raises(TargetValidationError, match="difficulty must be finite"):
        difficulty_to_share_target(difficulty)


@pytest.mark.parametrize(
    "difficulty",
    [
        True,
        False,
        "1",
        b"1",
        Decimal("1"),
        Fraction(1, 1),
        None,
        [1],
        object(),
    ],
)
def test_unsupported_difficulty_type_is_rejected(difficulty: object) -> None:
    with pytest.raises(TargetValidationError, match="integer or float"):
        difficulty_to_share_target(difficulty)  # type: ignore[arg-type]


def test_difficulty_producing_target_exactly_one_is_valid() -> None:
    assert difficulty_to_share_target(DIFFICULTY_1_TARGET) == 1


def test_difficulty_producing_zero_target_is_rejected() -> None:
    with pytest.raises(TargetValidationError, match="zero share target"):
        difficulty_to_share_target(DIFFICULTY_1_TARGET + 1)


def test_small_difficulty_producing_near_maximum_target_is_valid() -> None:
    difficulty = 2.3282709094019083e-10
    ratio = Fraction(str(difficulty))
    independently_expected = (DIFFICULTY_1_TARGET * ratio.denominator) // ratio.numerator

    target = difficulty_to_share_target(difficulty)

    assert target == independently_expected
    assert target < MAX_UINT256
    assert MAX_UINT256 - target < 10**60


def test_smaller_difficulty_producing_overflow_is_rejected() -> None:
    with pytest.raises(TargetValidationError, match=r"above 2\*\*256 - 1"):
        difficulty_to_share_target(2.328270909401908e-10)


def test_very_large_float_producing_zero_target_is_rejected() -> None:
    with pytest.raises(TargetValidationError, match="zero share target"):
        difficulty_to_share_target(1e308)


def test_mining_job_difficulty_snapshot_produces_share_target() -> None:
    job = valid_job(difficulty=10_000)

    target = difficulty_to_share_target(job.difficulty)

    assert target == int(
        "0000000000068db22d0e5604189374bc6a7ef9db22d0e5604189374bc6a7ef9d",
        16,
    )
    assert target > 0


def test_share_target_uses_existing_inclusive_hash_comparison() -> None:
    share_target = difficulty_to_share_target(10_000)
    equal_hash = share_target.to_bytes(32, byteorder="little", signed=False)
    above_hash = (share_target + 1).to_bytes(32, byteorder="little", signed=False)

    assert hash_meets_target(equal_hash, share_target) is True
    assert hash_meets_target(above_hash, share_target) is False


def test_network_and_share_targets_are_calculated_independently() -> None:
    job = valid_job(difficulty=10_000, network_bits="17023ad4")

    network_target = decode_compact_target(job.network_bits)
    share_target = difficulty_to_share_target(job.difficulty)

    assert network_target == int(
        "000000000000000000023ad40000000000000000000000000000000000000000",
        16,
    )
    assert share_target == int(
        "0000000000068db22d0e5604189374bc6a7ef9db22d0e5604189374bc6a7ef9d",
        16,
    )
    assert network_target != share_target
