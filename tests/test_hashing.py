"""Tests for Bitcoin double-SHA256 and coinbase transaction hashing."""

from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest

import hashorb.mining.coinbase as coinbase_module
from hashorb.crypto import HashingValidationError, double_sha256
from hashorb.mining import (
    CoinbaseValidationError,
    MiningJob,
    build_coinbase_transaction,
    hash_coinbase_transaction,
)


def valid_job(
    *,
    coinbase_part_1: str = "01000000",
    coinbase_part_2: str = "DEADBEEF",
    extra_nonce_1: str = "A1B2",
) -> MiningJob:
    """Return a valid compact job for hashing boundary tests."""

    return MiningJob(
        job_id="job-hashing",
        previous_block_hash="00" * 32,
        coinbase_part_1=coinbase_part_1,
        coinbase_part_2=coinbase_part_2,
        merkle_branches=(),
        version="20000000",
        network_bits="170fffff",
        network_time="65f04abc",
        clean_jobs=True,
        extra_nonce_1=extra_nonce_1,
        extra_nonce_2_size=2,
        difficulty=2048,
    )


@pytest.mark.parametrize(
    ("data", "expected_hex"),
    [
        (
            b"",
            "5df6e0e2761359d30a8275058e299fcc0381534545f55cf43e41983f5d4c9456",
        ),
        (
            b"hello",
            "9595c9df90075148eb06860365df33584b75bff782a510c6cd4883a419833d50",
        ),
    ],
)
def test_double_sha256_known_vectors(data: bytes, expected_hex: str) -> None:
    digest = double_sha256(data)

    assert isinstance(digest, bytes)
    assert len(digest) == 32
    assert digest.hex() == expected_hex


def test_double_sha256_matches_manual_two_stage_hashlib() -> None:
    data = b"HashOrb deterministic hashing"

    digest = double_sha256(data)
    manual = hashlib.sha256(hashlib.sha256(data).digest()).digest()

    assert digest == manual


def test_double_sha256_is_deterministic_and_does_not_mutate_input() -> None:
    data = b"unchanged input"
    original = data

    first = double_sha256(data)
    second = double_sha256(data)

    assert first == second
    assert data == original


@pytest.mark.parametrize(
    "value",
    ["hello", bytearray(b"hello"), memoryview(b"hello"), 1, None],
)
def test_double_sha256_rejects_non_bytes(value: object) -> None:
    with pytest.raises(HashingValidationError, match="data must be bytes"):
        double_sha256(value)  # type: ignore[arg-type]


def test_generic_double_sha256_accepts_empty_bytes() -> None:
    assert len(double_sha256(b"")) == 32


def test_coinbase_hashing_rejects_empty_bytes() -> None:
    with pytest.raises(CoinbaseValidationError, match="must not be empty"):
        hash_coinbase_transaction(b"")


@pytest.mark.parametrize(
    "value",
    ["transaction", bytearray(b"transaction"), memoryview(b"transaction"), 1, None],
)
def test_coinbase_hashing_rejects_non_bytes(value: object) -> None:
    with pytest.raises(CoinbaseValidationError, match="transaction must be bytes"):
        hash_coinbase_transaction(value)  # type: ignore[arg-type]


def test_coinbase_hash_matches_double_sha256_for_assembled_transaction() -> None:
    job = valid_job()
    transaction = build_coinbase_transaction(job, "00ff")

    digest = hash_coinbase_transaction(transaction)

    assert digest == double_sha256(transaction)
    assert len(digest) == 32


def test_source_hex_case_has_no_effect_after_transaction_decoding() -> None:
    uppercase = valid_job(
        coinbase_part_1="AABBCCDD",
        coinbase_part_2="EEFF0011",
        extra_nonce_1="A1B2",
    )
    lowercase = valid_job(
        coinbase_part_1="aabbccdd",
        coinbase_part_2="eeff0011",
        extra_nonce_1="a1b2",
    )
    mixed_case = valid_job(
        coinbase_part_1="AaBbCcDd",
        coinbase_part_2="EeFf0011",
        extra_nonce_1="A1b2",
    )

    transactions = (
        build_coinbase_transaction(uppercase, "C3D4"),
        build_coinbase_transaction(lowercase, "c3d4"),
        build_coinbase_transaction(mixed_case, "C3d4"),
    )

    assert transactions[0] == transactions[1] == transactions[2]
    assert len({hash_coinbase_transaction(value) for value in transactions}) == 1


def test_coinbase_hashing_delegates_to_double_sha256(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transaction = b"coinbase transaction"
    expected = b"\x42" * 32
    received: list[bytes] = []

    def fake_double_sha256(data: bytes) -> bytes:
        received.append(data)
        return expected

    monkeypatch.setattr(coinbase_module, "double_sha256", fake_double_sha256)

    assert hash_coinbase_transaction(transaction) == expected
    assert received == [transaction]


def test_raw_digest_is_not_reversed_or_formatted_as_txid() -> None:
    transaction = build_coinbase_transaction(valid_job(), "1020")
    expected_raw = hashlib.sha256(hashlib.sha256(transaction).digest()).digest()

    digest = hash_coinbase_transaction(transaction)

    assert digest == expected_raw
    assert digest != expected_raw[::-1]


def test_hashing_inputs_are_unchanged() -> None:
    job = valid_job()
    original_job = replace(job)
    transaction = build_coinbase_transaction(job, "00ff")
    original_transaction = transaction

    hash_coinbase_transaction(transaction)

    assert job == original_job
    assert transaction == original_transaction


def test_returned_digest_bytes_are_immutable() -> None:
    digest = hash_coinbase_transaction(b"coinbase transaction")

    with pytest.raises(TypeError):
        digest[0] = 0  # type: ignore[index]
