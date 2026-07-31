"""Tests for Bitcoin compact network-target decoding."""

from __future__ import annotations

import pytest

from hashorb.mining import (
    MiningJob,
    TargetValidationError,
    decode_compact_target,
)

MAX_UINT256 = (1 << 256) - 1


def valid_job(*, network_bits: str = "17023ad4") -> MiningJob:
    """Return a valid synthetic job carrying the selected compact target."""

    return MiningJob(
        job_id="job-target",
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
        difficulty=2048,
    )


def test_mainnet_genesis_target_vector() -> None:
    target = decode_compact_target("1d00ffff")

    assert target == int(
        "00000000ffff0000000000000000000000000000000000000000000000000000",
        16,
    )
    assert isinstance(target, int)
    assert target > 0


def test_observed_ckpool_target_vector() -> None:
    target = decode_compact_target("17023ad4")

    assert target == int(
        "000000000000000000023ad40000000000000000000000000000000000000000",
        16,
    )


@pytest.mark.parametrize(
    ("network_bits", "expected"),
    [
        ("01123456", 0x12),
        ("02008000", 0x80),
        ("03123456", 0x123456),
        ("05009234", 0x92340000),
        ("04123456", 0x12345600),
    ],
)
def test_documented_exponent_and_mantissa_examples(
    network_bits: str,
    expected: int,
) -> None:
    assert decode_compact_target(network_bits) == expected


@pytest.mark.parametrize("network_bits", ["17023AD4", "17023ad4", "17023aD4"])
def test_upper_lower_and_mixed_case_are_accepted(network_bits: str) -> None:
    original = network_bits
    target = decode_compact_target(network_bits)

    assert target == 0x023AD4 << (8 * (0x17 - 3))
    assert network_bits == original


def test_equivalent_case_variants_return_same_integer() -> None:
    variants = ("17023AD4", "17023ad4", "17023aD4")

    assert len({decode_compact_target(value) for value in variants}) == 1


def test_repeated_calls_are_deterministic() -> None:
    assert decode_compact_target("17023ad4") == decode_compact_target("17023ad4")


@pytest.mark.parametrize(
    ("network_bits", "message"),
    [
        ("", "must not be empty"),
        ("1234567", "exactly 8 hexadecimal characters"),
        ("123456789", "exactly 8 hexadecimal characters"),
        ("1234zzzz", "only hexadecimal characters"),
        ("1234 678", "only hexadecimal characters"),
        (" 1234567", "only hexadecimal characters"),
        ("1234567 ", "only hexadecimal characters"),
        ("0x123456", "only hexadecimal characters"),
        ("+1234567", "only hexadecimal characters"),
        ("-1234567", "only hexadecimal characters"),
        ("12_34567", "only hexadecimal characters"),
    ],
)
def test_malformed_network_bits_is_rejected(network_bits: str, message: str) -> None:
    with pytest.raises(TargetValidationError, match=message):
        decode_compact_target(network_bits)


@pytest.mark.parametrize(
    "value",
    [
        b"1d00ffff",
        bytearray(b"1d00ffff"),
        memoryview(b"1d00ffff"),
        0x1D00FFFF,
        True,
        1.0,
        None,
        ["1d00ffff"],
        object(),
    ],
)
def test_non_string_input_is_rejected_without_coercion(value: object) -> None:
    with pytest.raises(TargetValidationError, match="network_bits must be a string"):
        decode_compact_target(value)  # type: ignore[arg-type]


def test_negative_compact_encoding_is_rejected() -> None:
    with pytest.raises(TargetValidationError, match="negative target"):
        decode_compact_target("04923456")


@pytest.mark.parametrize("network_bits", ["00000000", "01003456"])
def test_zero_target_is_rejected(network_bits: str) -> None:
    with pytest.raises(TargetValidationError, match="nonzero target"):
        decode_compact_target(network_bits)


@pytest.mark.parametrize(
    "network_bits",
    [
        "23000001",  # exponent > 34 with a nonzero mantissa
        "22000100",  # exponent > 33 with mantissa > 0xff
        "21010000",  # exponent > 32 with mantissa > 0xffff
    ],
)
def test_each_bitcoin_core_overflow_condition_is_rejected(network_bits: str) -> None:
    with pytest.raises(TargetValidationError, match="overflows 256 bits"):
        decode_compact_target(network_bits)


@pytest.mark.parametrize(
    ("network_bits", "expected"),
    [
        ("220000ff", 0xFF << 248),
        ("2100ffff", 0xFFFF << 240),
        ("207fffff", 0x7FFFFF << 232),
    ],
)
def test_values_at_overflow_boundaries_are_valid(
    network_bits: str,
    expected: int,
) -> None:
    target = decode_compact_target(network_bits)

    assert target == expected
    assert 1 <= target <= MAX_UINT256


def test_maximum_accepted_target_does_not_exceed_uint256() -> None:
    target = decode_compact_target("2100ffff")

    assert target == 0xFFFF << 240
    assert target <= MAX_UINT256


def test_compact_string_is_parsed_as_written_without_endian_conversion() -> None:
    assert decode_compact_target("04123456") == 0x12345600
    with pytest.raises(TargetValidationError, match="overflows 256 bits"):
        decode_compact_target("56341204")


def test_mining_job_network_bits_integration() -> None:
    job = valid_job()

    target = decode_compact_target(job.network_bits)

    assert target == int(
        "000000000000000000023ad40000000000000000000000000000000000000000",
        16,
    )
    assert target > 0
