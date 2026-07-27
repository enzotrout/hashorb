"""Tests for raw block-hash interpretation and network-target comparison."""

from __future__ import annotations

import pytest

import hashphere.mining.target as target_module
from hashphere.mining import (
    MiningJob,
    TargetValidationError,
    block_hash_to_int,
    build_coinbase_transaction,
    calculate_merkle_root,
    decode_compact_target,
    hash_block_header,
    hash_coinbase_transaction,
    hash_meets_target,
    serialize_block_header,
)

MAX_UINT256 = (1 << 256) - 1
GENESIS_HEADER = bytes.fromhex(
    "01000000"
    + ("00" * 32)
    + "3ba3edfd7a7b12b27ac72c3e67768f61"
    + "7fc81bc3888a51323a9fb8aa4b1e5e4a"
    + "29ab5f49"
    + "ffff001d"
    + "1dac2b7c"
)
GENESIS_RAW_DIGEST = bytes.fromhex(
    "6fe28c0ab6f1b372c1a6a246ae63f74f931e8365e15a089c68d6190000000000"
)
GENESIS_HASH_INTEGER = int(
    "000000000019d6689c085ae165831e934ff763ae46a2a6c172b3f1b60a8ce26f",
    16,
)


def genesis_job() -> MiningJob:
    """Return a valid job containing the mainnet genesis header fields."""

    return MiningJob(
        job_id="genesis",
        previous_block_hash="00" * 32,
        coinbase_part_1="00",
        coinbase_part_2="00",
        merkle_branches=(),
        version="00000001",
        network_bits="1d00ffff",
        network_time="495fab29",
        clean_jobs=True,
        extra_nonce_1="00",
        extra_nonce_2_size=1,
        difficulty=1,
    )


def synthetic_job() -> MiningJob:
    """Return a valid synthetic job for end-to-end comparison."""

    return MiningJob(
        job_id="job-proof-of-work",
        previous_block_hash=("000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f"),
        coinbase_part_1="01000000",
        coinbase_part_2="DEADBEEF",
        merkle_branches=("11" * 32, "22" * 32),
        version="20000000",
        network_bits="03000001",
        network_time="65f04abc",
        clean_jobs=True,
        extra_nonce_1="A1B2",
        extra_nonce_2_size=2,
        difficulty=2048,
    )


def hash_bytes(value: int) -> bytes:
    """Encode a test integer in the raw block-hash byte representation."""

    return value.to_bytes(32, byteorder="little", signed=False)


def test_genesis_raw_digest_has_known_integer_value() -> None:
    digest = hash_block_header(GENESIS_HEADER)

    assert digest == GENESIS_RAW_DIGEST
    assert block_hash_to_int(digest) == GENESIS_HASH_INTEGER


@pytest.mark.parametrize(
    ("block_hash", "expected"),
    [
        (bytes(32), 0),
        (b"\xff" * 32, MAX_UINT256),
    ],
)
def test_hash_extremes_are_accepted(block_hash: bytes, expected: int) -> None:
    result = block_hash_to_int(block_hash)

    assert result == expected
    assert isinstance(result, int)
    assert not isinstance(result, bool)


def test_distinguishable_pattern_is_interpreted_as_little_endian() -> None:
    block_hash = bytes(range(32))
    expected = int.from_bytes(block_hash, byteorder="little", signed=False)
    big_endian = int.from_bytes(block_hash, byteorder="big", signed=False)

    result = block_hash_to_int(block_hash)

    assert result == expected
    assert result != big_endian


def test_hash_conversion_is_deterministic_and_does_not_mutate_input() -> None:
    block_hash = bytes(range(32))
    original = block_hash

    assert block_hash_to_int(block_hash) == block_hash_to_int(block_hash)
    assert block_hash == original


@pytest.mark.parametrize(
    "value",
    [
        "00" * 32,
        bytearray(32),
        memoryview(bytes(32)),
        0,
        True,
        None,
        [0] * 32,
        object(),
    ],
)
def test_non_bytes_hash_is_rejected_without_coercion(value: object) -> None:
    with pytest.raises(TargetValidationError, match="block_hash must be bytes"):
        block_hash_to_int(value)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "block_hash",
    [
        b"",
        bytes(1),
        bytes(16),
        bytes(31),
        bytes(33),
        bytes(64),
    ],
)
def test_hash_must_contain_exactly_32_bytes(block_hash: bytes) -> None:
    with pytest.raises(TargetValidationError, match="exactly 32 bytes"):
        block_hash_to_int(block_hash)


@pytest.mark.parametrize(
    ("hash_value", "target", "expected"),
    [
        (41, 42, True),
        (42, 42, True),
        (43, 42, False),
    ],
)
def test_hash_comparison_boundary(
    hash_value: int,
    target: int,
    expected: bool,
) -> None:
    result = hash_meets_target(hash_bytes(hash_value), target)

    assert result is expected
    assert isinstance(result, bool)


def test_minimum_target_is_valid() -> None:
    assert hash_meets_target(hash_bytes(0), 1) is True
    assert hash_meets_target(hash_bytes(1), 1) is True
    assert hash_meets_target(hash_bytes(2), 1) is False


def test_maximum_target_is_valid() -> None:
    assert hash_meets_target(hash_bytes(MAX_UINT256), MAX_UINT256) is True


@pytest.mark.parametrize("target", [0, -1, MAX_UINT256 + 1])
def test_out_of_range_target_is_rejected(target: int) -> None:
    with pytest.raises(TargetValidationError, match=r"between 1 and 2\*\*256 - 1"):
        hash_meets_target(bytes(32), target)


@pytest.mark.parametrize(
    "target",
    [True, False, 1.0, "1", b"1", None, [1], object()],
)
def test_non_integer_target_is_rejected_without_coercion(target: object) -> None:
    with pytest.raises(TargetValidationError, match="target must be an integer"):
        hash_meets_target(bytes(32), target)  # type: ignore[arg-type]


def test_hash_validation_delegates_to_block_hash_conversion() -> None:
    with pytest.raises(TargetValidationError, match="block_hash must be bytes"):
        hash_meets_target(bytearray(32), 1)  # type: ignore[arg-type]


def test_comparison_calls_block_hash_conversion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    block_hash = bytes(range(32))
    received: list[bytes] = []

    def fake_block_hash_to_int(value: bytes) -> int:
        received.append(value)
        return 7

    monkeypatch.setattr(target_module, "block_hash_to_int", fake_block_hash_to_int)

    assert hash_meets_target(block_hash, 7) is True
    assert received == [block_hash]


def test_comparison_does_not_mutate_inputs() -> None:
    block_hash = bytes(range(32))
    target = MAX_UINT256
    original_hash = block_hash
    original_target = target

    hash_meets_target(block_hash, target)

    assert block_hash == original_hash
    assert target == original_target


def test_genesis_header_meets_its_network_target() -> None:
    job = genesis_job()
    header = serialize_block_header(job, GENESIS_HEADER[36:68], 0x7C2BAC1D)
    digest = hash_block_header(header)
    target = decode_compact_target(job.network_bits)

    assert header == GENESIS_HEADER
    assert block_hash_to_int(digest) == GENESIS_HASH_INTEGER
    assert block_hash_to_int(digest) <= target
    assert hash_meets_target(digest, target) is True


def test_synthetic_end_to_end_header_does_not_meet_target() -> None:
    job = synthetic_job()
    transaction = build_coinbase_transaction(job, "00ff")
    coinbase_hash = hash_coinbase_transaction(transaction)
    merkle_root = calculate_merkle_root(coinbase_hash, job.merkle_branches)
    header = serialize_block_header(job, merkle_root, 0x12345678)
    digest = hash_block_header(header)
    target = decode_compact_target(job.network_bits)
    independently_expected = int.from_bytes(digest, byteorder="little", signed=False) <= target

    assert independently_expected is False
    assert hash_meets_target(digest, target) is independently_expected
