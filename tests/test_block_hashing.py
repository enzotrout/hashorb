"""Tests for deterministic Bitcoin block-header hashing."""

from __future__ import annotations

import hashlib

import pytest

import hashorb.mining.header as header_module
from hashorb.mining import (
    BlockHeaderValidationError,
    MiningJob,
    build_coinbase_transaction,
    calculate_merkle_root,
    hash_block_header,
    hash_coinbase_transaction,
    serialize_block_header,
)

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
GENESIS_DISPLAY_HASH = "000000000019d6689c085ae165831e934ff763ae46a2a6c172b3f1b60a8ce26f"


def manual_double_sha256(data: bytes) -> bytes:
    """Calculate a test-only independent nested SHA-256 digest."""

    return hashlib.sha256(hashlib.sha256(data).digest()).digest()


def raw_digest_to_display_hex(raw_digest: bytes) -> str:
    """Apply the presentation-only reversal used by the known vector."""

    return bytes(reversed(raw_digest)).hex()


def valid_job() -> MiningJob:
    """Return a synthetic valid job for the end-to-end hashing test."""

    return MiningJob(
        job_id="job-block-hashing",
        previous_block_hash=("000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f"),
        coinbase_part_1="01000000",
        coinbase_part_2="DEADBEEF",
        merkle_branches=("11" * 32, "22" * 32),
        version="20000000",
        network_bits="170fffff",
        network_time="65f04abc",
        clean_jobs=True,
        extra_nonce_1="A1B2",
        extra_nonce_2_size=2,
        difficulty=2048,
    )


def test_mainnet_genesis_header_returns_known_raw_digest() -> None:
    # Bitcoin Core defines the genesis values and displayed block hash:
    # https://github.com/bitcoin/bitcoin/blob/master/src/kernel/chainparams.cpp
    digest = hash_block_header(GENESIS_HEADER)

    assert digest == GENESIS_RAW_DIGEST
    assert isinstance(digest, bytes)
    assert len(digest) == 32


def test_displayed_genesis_hash_uses_only_explicit_test_side_reversal() -> None:
    raw_digest = hash_block_header(GENESIS_HEADER)

    assert raw_digest_to_display_hex(raw_digest) == GENESIS_DISPLAY_HASH


def test_hash_matches_independently_calculated_nested_hashlib() -> None:
    header = bytes(range(80))

    assert hash_block_header(header) == manual_double_sha256(header)


def test_hashing_delegates_to_existing_double_sha256(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    header = bytes(range(80))
    expected = b"\x42" * 32
    received: list[bytes] = []

    def fake_double_sha256(data: bytes) -> bytes:
        received.append(data)
        return expected

    monkeypatch.setattr(header_module, "double_sha256", fake_double_sha256)

    assert hash_block_header(header) == expected
    assert received == [header]


@pytest.mark.parametrize(
    "header",
    [
        bytes(80),
        b"\xff" * 80,
        bytes(range(80)),
    ],
)
def test_synthetic_headers_match_independent_hashing(header: bytes) -> None:
    digest = hash_block_header(header)

    assert digest == manual_double_sha256(header)
    assert isinstance(digest, bytes)
    assert len(digest) == 32


def test_hashing_is_deterministic_and_does_not_mutate_header() -> None:
    header = bytes(range(80))
    original_header = header

    first = hash_block_header(header)
    second = hash_block_header(header)

    assert first == second
    assert header == original_header


def test_raw_digest_is_not_reversed_or_reinterpreted() -> None:
    expected_raw = manual_double_sha256(GENESIS_HEADER)

    digest = hash_block_header(GENESIS_HEADER)

    assert digest == expected_raw
    assert digest != bytes(reversed(expected_raw))


def test_returned_digest_is_immutable() -> None:
    digest = hash_block_header(bytes(80))

    with pytest.raises(TypeError):
        digest[0] = 0  # type: ignore[index]


@pytest.mark.parametrize(
    "value",
    ["00" * 80, bytearray(80), memoryview(bytes(80)), 80, None, [0] * 80],
)
def test_non_bytes_header_is_rejected_without_coercion(value: object) -> None:
    with pytest.raises(BlockHeaderValidationError, match="header must be bytes"):
        hash_block_header(value)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "header",
    [
        b"",
        bytes(1),
        bytes(40),
        bytes(79),
        bytes(81),
        bytes(160),
    ],
)
def test_header_must_contain_exactly_80_bytes(header: bytes) -> None:
    with pytest.raises(BlockHeaderValidationError, match="exactly 80 bytes"):
        hash_block_header(header)


def test_end_to_end_public_mining_boundaries() -> None:
    job = valid_job()
    transaction = build_coinbase_transaction(job, "00ff")
    coinbase_hash = hash_coinbase_transaction(transaction)
    merkle_root = calculate_merkle_root(coinbase_hash, job.merkle_branches)
    header = serialize_block_header(job, merkle_root, 0x12345678)

    digest = hash_block_header(header)

    assert digest == manual_double_sha256(header)
    assert isinstance(digest, bytes)
    assert len(digest) == 32
