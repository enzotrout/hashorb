"""Tests for deterministic Bitcoin block-header serialization."""

from __future__ import annotations

from dataclasses import replace

import pytest

from hashphere.crypto import double_sha256
from hashphere.mining import (
    BlockHeaderValidationError,
    MiningJob,
    serialize_block_header,
)

SYNTHETIC_STRATUM_PREVIOUS_HASH = "000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f"
SYNTHETIC_HEADER_PREVIOUS_HASH = "03020100070605040b0a09080f0e0d0c13121110171615141b1a19181f1e1d1c"


def valid_job(
    *,
    previous_block_hash: str = SYNTHETIC_STRATUM_PREVIOUS_HASH,
    version: str = "01020304",
    network_time: str = "11223344",
    network_bits: str = "55667788",
) -> MiningJob:
    """Return a valid job with distinguishable header fields."""

    return MiningJob(
        job_id="job-header",
        previous_block_hash=previous_block_hash,
        coinbase_part_1="01000000",
        coinbase_part_2="DEADBEEF",
        merkle_branches=(),
        version=version,
        network_bits=network_bits,
        network_time=network_time,
        clean_jobs=True,
        extra_nonce_1="A1B2",
        extra_nonce_2_size=2,
        difficulty=2048,
    )


def test_known_synthetic_header_vector_and_exact_field_offsets() -> None:
    merkle_root = bytes(range(0x20, 0x40))
    header = serialize_block_header(valid_job(), merkle_root, 0x99AABBCC)
    expected_hex = (
        "04030201"
        + SYNTHETIC_HEADER_PREVIOUS_HASH
        + "202122232425262728292a2b2c2d2e2f"
        + "303132333435363738393a3b3c3d3e3f"
        + "44332211"
        + "88776655"
        + "ccbbaa99"
    )

    assert isinstance(header, bytes)
    assert len(header) == 80
    assert header == bytes.fromhex(expected_hex)
    assert header[0:4] == bytes.fromhex("04030201")
    assert header[4:36] == bytes.fromhex(SYNTHETIC_HEADER_PREVIOUS_HASH)
    assert header[36:68] == merkle_root
    assert header[68:72] == bytes.fromhex("44332211")
    assert header[72:76] == bytes.fromhex("88776655")
    assert header[76:80] == bytes.fromhex("ccbbaa99")


@pytest.mark.parametrize(
    ("nonce", "expected_hex"),
    [
        (0, "00000000"),
        (1, "01000000"),
        (0xFFFFFFFF, "ffffffff"),
        (0x12345678, "78563412"),
    ],
)
def test_nonce_is_unsigned_32_bit_little_endian(nonce: int, expected_hex: str) -> None:
    header = serialize_block_header(valid_job(), bytes(32), nonce)

    assert header[76:80] == bytes.fromhex(expected_hex)
    assert header[76:80] == nonce.to_bytes(4, byteorder="little", signed=False)


@pytest.mark.parametrize("value", [True, False, "1", 1.0, b"\x01", None])
def test_non_integer_nonce_is_rejected_without_coercion(value: object) -> None:
    with pytest.raises(BlockHeaderValidationError, match="nonce must be an integer"):
        serialize_block_header(valid_job(), bytes(32), value)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [-1, 0x100000000])
def test_nonce_outside_uint32_range_is_rejected(value: int) -> None:
    with pytest.raises(BlockHeaderValidationError, match="between 0 and 0xffffffff"):
        serialize_block_header(valid_job(), bytes(32), value)


def test_non_job_input_is_rejected() -> None:
    with pytest.raises(BlockHeaderValidationError, match="job must be a MiningJob"):
        serialize_block_header(object(), bytes(32), 0)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "value",
    ["00" * 32, bytearray(32), memoryview(bytes(32)), 1, None],
)
def test_invalid_merkle_root_type_is_rejected(value: object) -> None:
    with pytest.raises(BlockHeaderValidationError, match="merkle_root must be bytes"):
        serialize_block_header(valid_job(), value, 0)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [bytes(31), bytes(33)])
def test_invalid_merkle_root_length_is_rejected(value: bytes) -> None:
    with pytest.raises(BlockHeaderValidationError, match="exactly 32 bytes"):
        serialize_block_header(valid_job(), value, 0)


def test_previous_hash_is_converted_per_32_bit_word() -> None:
    header_previous_hash = serialize_block_header(valid_job(), bytes(32), 0)[4:36]
    stratum_bytes = bytes.fromhex(SYNTHETIC_STRATUM_PREVIOUS_HASH)

    assert header_previous_hash == bytes.fromhex(SYNTHETIC_HEADER_PREVIOUS_HASH)
    assert header_previous_hash != stratum_bytes
    assert header_previous_hash != bytes(reversed(stratum_bytes))


def test_raw_merkle_root_is_copied_without_transformation() -> None:
    merkle_root = bytes(range(32))

    header = serialize_block_header(valid_job(), merkle_root, 0)

    assert header[36:68] == merkle_root
    assert header[36:68] != bytes(reversed(merkle_root))


def test_stratum_numeric_fields_are_serialized_little_endian() -> None:
    header = serialize_block_header(valid_job(), bytes(32), 0)

    assert header[0:4] == bytes.fromhex("04030201")
    assert header[68:72] == bytes.fromhex("44332211")
    assert header[72:76] == bytes.fromhex("88776655")


@pytest.mark.parametrize(
    ("field", "value", "offset", "expected_hex"),
    [
        ("version", "80000000", 0, "00000080"),
        ("version", "ffffffff", 0, "ffffffff"),
        ("network_time", "80000000", 68, "00000080"),
        ("network_time", "ffffffff", 68, "ffffffff"),
        ("network_bits", "80000000", 72, "00000080"),
        ("network_bits", "ffffffff", 72, "ffffffff"),
    ],
)
def test_numeric_fields_preserve_all_uint32_bit_patterns(
    field: str,
    value: str,
    offset: int,
    expected_hex: str,
) -> None:
    values = {
        "version": "01020304",
        "network_time": "11223344",
        "network_bits": "55667788",
    }
    values[field] = value
    job = valid_job(**values)  # type: ignore[arg-type]

    header = serialize_block_header(job, bytes(32), 0)

    assert header[offset : offset + 4] == bytes.fromhex(expected_hex)


def test_serialization_is_deterministic_and_side_effect_free() -> None:
    job = valid_job(
        previous_block_hash=SYNTHETIC_STRATUM_PREVIOUS_HASH.upper(),
        version="a1B2c3D4",
        network_time="E5f60718",
        network_bits="19aBcDeF",
    )
    original_job = replace(job)
    merkle_root = bytes(range(32))
    original_merkle_root = merkle_root
    nonce = 0x12345678

    first = serialize_block_header(job, merkle_root, nonce)
    second = serialize_block_header(job, merkle_root, nonce)

    assert first == second
    assert job == original_job
    assert merkle_root == original_merkle_root
    assert nonce == 0x12345678


def test_explicit_little_endian_encoding_is_not_native_or_big_endian() -> None:
    nonce = 0x12345678
    header = serialize_block_header(valid_job(), bytes(32), nonce)

    assert header[76:80] == nonce.to_bytes(4, byteorder="little", signed=False)
    assert header[76:80] != nonce.to_bytes(4, byteorder="big", signed=False)


def test_returned_header_bytes_are_immutable() -> None:
    header = serialize_block_header(valid_job(), bytes(32), 0)

    with pytest.raises(TypeError):
        header[0] = 0  # type: ignore[index]


def _raw_digest_to_display_hex(raw_digest: bytes) -> str:
    """Convert a raw test-vector digest to conventional display order."""

    return bytes(reversed(raw_digest)).hex()


def test_bitcoin_mainnet_genesis_header_and_hash_vector() -> None:
    # Bitcoin Core fixes the genesis fields and known displayed block hash in:
    # https://github.com/bitcoin/bitcoin/blob/master/src/kernel/chainparams.cpp
    # Bitcoin's developer reference specifies the 80-byte field layout/order:
    # https://developer.bitcoin.org/reference/block_chain.html#block-headers
    job = valid_job(
        previous_block_hash="00" * 32,
        version="00000001",
        network_time="495fab29",
        network_bits="1d00ffff",
    )
    raw_merkle_root = bytes.fromhex(
        "3ba3edfd7a7b12b27ac72c3e67768f617fc81bc3888a51323a9fb8aa4b1e5e4a"
    )
    expected_header = bytes.fromhex(
        "01000000"
        + ("00" * 32)
        + "3ba3edfd7a7b12b27ac72c3e67768f61"
        + "7fc81bc3888a51323a9fb8aa4b1e5e4a"
        + "29ab5f49"
        + "ffff001d"
        + "1dac2b7c"
    )

    header = serialize_block_header(job, raw_merkle_root, 2083236893)
    displayed_hash = _raw_digest_to_display_hex(double_sha256(header))

    assert header == expected_header
    assert displayed_hash == ("000000000019d6689c085ae165831e934ff763ae46a2a6c172b3f1b60a8ce26f")
