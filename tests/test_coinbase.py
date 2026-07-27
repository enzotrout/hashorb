"""Tests for deterministic coinbase transaction assembly."""

from __future__ import annotations

from dataclasses import replace

import pytest

from hashphere.mining import (
    CoinbaseValidationError,
    MiningJob,
    MiningJobAssembler,
    build_coinbase_transaction,
)
from hashphere.network.stratum import (
    MiningNotifyNotification,
    SetDifficultyNotification,
    SubscribeResult,
)


def valid_job(
    *,
    coinbase_part_1: str = "01000000",
    coinbase_part_2: str = "DEADBEEF",
    extra_nonce_1: str = "A1B2",
    extra_nonce_2_size: int = 2,
) -> MiningJob:
    """Return a valid job with compact recognizable coinbase components."""

    return MiningJob(
        job_id="job-coinbase",
        previous_block_hash="00" * 32,
        coinbase_part_1=coinbase_part_1,
        coinbase_part_2=coinbase_part_2,
        merkle_branches=(),
        version="20000000",
        network_bits="170fffff",
        network_time="65f04abc",
        clean_jobs=True,
        extra_nonce_1=extra_nonce_1,
        extra_nonce_2_size=extra_nonce_2_size,
        difficulty=2048,
    )


def test_build_coinbase_transaction_uses_exact_protocol_order() -> None:
    job = valid_job()

    transaction = build_coinbase_transaction(job, "00ff")

    assert isinstance(transaction, bytes)
    assert transaction == bytes.fromhex("01000000A1B200ffDEADBEEF")
    assert transaction.hex() == "01000000a1b200ffdeadbeef"


def test_repeated_calls_are_deterministic() -> None:
    job = valid_job()

    first = build_coinbase_transaction(job, "1234")
    second = build_coinbase_transaction(job, "1234")

    assert first == second


@pytest.mark.parametrize(
    ("job", "extra_nonce_2", "expected"),
    [
        (valid_job(extra_nonce_1="AABB"), "CCDD", "01000000AABBCCDDDEADBEEF"),
        (
            valid_job(
                coinbase_part_1="abcdef",
                coinbase_part_2="fedcba",
                extra_nonce_1="aabb",
            ),
            "ccdd",
            "abcdefaabbccddfedcba",
        ),
        (
            valid_job(
                coinbase_part_1="AbCdEf",
                coinbase_part_2="fEdCbA",
                extra_nonce_1="aAbB",
            ),
            "CcDd",
            "AbCdEfaAbBCcDdfEdCbA",
        ),
    ],
)
def test_upper_lower_and_mixed_case_hex_is_accepted_without_mutation(
    job: MiningJob,
    extra_nonce_2: str,
    expected: str,
) -> None:
    original_job = replace(job)
    original_extra_nonce_2 = extra_nonce_2

    transaction = build_coinbase_transaction(job, extra_nonce_2)

    assert transaction == bytes.fromhex(expected)
    assert job == original_job
    assert extra_nonce_2 == original_extra_nonce_2


@pytest.mark.parametrize(
    ("size", "extra_nonce_2"),
    [
        (1, "00"),
        (2, "0000"),
        (4, "00000000"),
        (8, "0000000000000000"),
    ],
)
def test_minimum_value_for_multiple_extra_nonce_sizes(
    size: int,
    extra_nonce_2: str,
) -> None:
    job = valid_job(extra_nonce_2_size=size)

    transaction = build_coinbase_transaction(job, extra_nonce_2)

    assert transaction == bytes.fromhex(
        job.coinbase_part_1 + job.extra_nonce_1 + extra_nonce_2 + job.coinbase_part_2
    )


@pytest.mark.parametrize(
    ("size", "extra_nonce_2"),
    [
        (1, "ff"),
        (2, "ffff"),
        (4, "ffffffff"),
        (8, "ffffffffffffffff"),
    ],
)
def test_maximum_all_ff_value_for_multiple_extra_nonce_sizes(
    size: int,
    extra_nonce_2: str,
) -> None:
    job = valid_job(extra_nonce_2_size=size)

    transaction = build_coinbase_transaction(job, extra_nonce_2)

    assert transaction == bytes.fromhex(
        job.coinbase_part_1 + job.extra_nonce_1 + extra_nonce_2 + job.coinbase_part_2
    )


@pytest.mark.parametrize(
    ("extra_nonce_2", "message"),
    [
        ("00", "exactly 4 hexadecimal characters"),
        ("000000", "exactly 4 hexadecimal characters"),
        ("", "must not be empty"),
        ("0", "whole number of bytes"),
        ("000", "whole number of bytes"),
        ("00xz", "only hexadecimal characters"),
        ("00 0", "only hexadecimal characters"),
    ],
)
def test_invalid_extra_nonce_2_is_rejected(
    extra_nonce_2: str,
    message: str,
) -> None:
    with pytest.raises(CoinbaseValidationError, match=message):
        build_coinbase_transaction(valid_job(), extra_nonce_2)


@pytest.mark.parametrize("value", [None, 0, True, b"0000", ["0000"]])
def test_non_string_extra_nonce_2_is_not_coerced(value: object) -> None:
    with pytest.raises(CoinbaseValidationError, match="must be a string"):
        build_coinbase_transaction(valid_job(), value)  # type: ignore[arg-type]


def test_non_job_input_is_rejected() -> None:
    with pytest.raises(CoinbaseValidationError, match="job must be a MiningJob"):
        build_coinbase_transaction(object(), "0000")  # type: ignore[arg-type]


def test_notification_derived_job_is_not_mutated() -> None:
    subscription = SubscribeResult(
        subscriptions=(("mining.notify", "subscription-id"),),
        extra_nonce_1="A1B2",
        extra_nonce_2_size=2,
    )
    notification = MiningNotifyNotification(
        job_id="job-from-notification",
        previous_block_hash="00" * 32,
        coinbase_part_1="01000000",
        coinbase_part_2="DEADBEEF",
        merkle_branches=(),
        version="20000000",
        network_bits="170fffff",
        network_time="65f04abc",
        clean_jobs=False,
    )
    assembler = MiningJobAssembler(subscription)
    assembler.apply_difficulty(SetDifficultyNotification(difficulty=1024))
    job = assembler.build_job(notification)
    original_job = replace(job)
    original_notification = replace(notification)

    build_coinbase_transaction(job, "00ff")

    assert job == original_job
    assert notification == original_notification


def test_returned_transaction_bytes_are_immutable() -> None:
    transaction = build_coinbase_transaction(valid_job(), "00ff")

    with pytest.raises(TypeError):
        transaction[0] = 0  # type: ignore[index]


def test_component_bytes_are_not_endian_reversed() -> None:
    job = valid_job(
        coinbase_part_1="01020304",
        extra_nonce_1="A1B2",
        coinbase_part_2="C1D2E3F4",
    )

    transaction = build_coinbase_transaction(job, "1020")

    assert transaction == b"\x01\x02\x03\x04\xa1\xb2\x10\x20\xc1\xd2\xe3\xf4"
