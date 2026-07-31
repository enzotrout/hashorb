"""Tests for raw Bitcoin Merkle-root calculation."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from types import GeneratorType

import pytest

import hashorb.mining.merkle as merkle_module
from hashorb.mining import (
    MerkleValidationError,
    MiningJob,
    build_coinbase_transaction,
    calculate_merkle_root,
    hash_coinbase_transaction,
)


def manual_double_sha256(data: bytes) -> bytes:
    """Calculate a test-only independent double-SHA256 digest."""

    return hashlib.sha256(hashlib.sha256(data).digest()).digest()


def valid_job(*, merkle_branches: tuple[str, ...] = ()) -> MiningJob:
    """Return a valid compact job for end-to-end Merkle tests."""

    return MiningJob(
        job_id="job-merkle",
        previous_block_hash="00" * 32,
        coinbase_part_1="01000000",
        coinbase_part_2="DEADBEEF",
        merkle_branches=merkle_branches,
        version="20000000",
        network_bits="170fffff",
        network_time="65f04abc",
        clean_jobs=True,
        extra_nonce_1="A1B2",
        extra_nonce_2_size=2,
        difficulty=2048,
    )


def test_empty_branches_return_original_coinbase_hash() -> None:
    coinbase_hash = bytes(range(32))

    root = calculate_merkle_root(coinbase_hash, ())

    assert root is coinbase_hash
    assert isinstance(root, bytes)
    assert len(root) == 32


def test_one_branch_matches_independent_hashlib_calculation() -> None:
    coinbase_hash = bytes(range(32))
    branch = "20" * 32

    root = calculate_merkle_root(coinbase_hash, (branch,))

    assert root == manual_double_sha256(coinbase_hash + bytes.fromhex(branch))


def test_multiple_branches_match_independent_iterative_calculation() -> None:
    coinbase_hash = bytes.fromhex("ab" * 32)
    branches = ("01" * 32, "23" * 32, "45" * 32)
    expected = coinbase_hash
    for branch in branches:
        expected = manual_double_sha256(expected + bytes.fromhex(branch))

    root = calculate_merkle_root(coinbase_hash, branches)

    assert root == expected
    assert isinstance(root, bytes)
    assert len(root) == 32


def test_branch_order_affects_result() -> None:
    coinbase_hash = bytes.fromhex("ab" * 32)
    first = "01" * 32
    second = "02" * 32

    assert calculate_merkle_root(coinbase_hash, (first, second)) != calculate_merkle_root(
        coinbase_hash, (second, first)
    )


def test_repeated_calls_are_deterministic() -> None:
    coinbase_hash = bytes.fromhex("12" * 32)
    branches = ("34" * 32, "56" * 32)

    assert calculate_merkle_root(coinbase_hash, branches) == calculate_merkle_root(
        coinbase_hash, branches
    )


@pytest.mark.parametrize(
    ("coinbase_hash", "branch"),
    [
        (b"\x00" * 32, "00" * 32),
        (b"\xff" * 32, "ff" * 32),
    ],
)
def test_zero_and_all_ff_values(coinbase_hash: bytes, branch: str) -> None:
    expected = manual_double_sha256(coinbase_hash + bytes.fromhex(branch))

    assert calculate_merkle_root(coinbase_hash, (branch,)) == expected


@pytest.mark.parametrize("branch", ["AB" * 32, "ab" * 32, "Ab" * 32])
def test_upper_lower_and_mixed_case_branches_are_accepted(branch: str) -> None:
    coinbase_hash = bytes.fromhex("10" * 32)

    assert calculate_merkle_root(coinbase_hash, (branch,)) == manual_double_sha256(
        coinbase_hash + bytes.fromhex(branch)
    )


def test_equivalent_upper_and_lowercase_branches_have_same_result() -> None:
    coinbase_hash = bytes.fromhex("10" * 32)

    assert calculate_merkle_root(coinbase_hash, ("AB" * 32,)) == calculate_merkle_root(
        coinbase_hash, ("ab" * 32,)
    )


@pytest.mark.parametrize(
    "value",
    ["00" * 32, bytearray(32), memoryview(bytes(32)), 1, None],
)
def test_invalid_coinbase_hash_type_is_rejected(value: object) -> None:
    with pytest.raises(MerkleValidationError, match="coinbase_hash must be bytes"):
        calculate_merkle_root(value, ())  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [b"\x00" * 31, b"\x00" * 33])
def test_invalid_coinbase_hash_length_is_rejected(value: bytes) -> None:
    with pytest.raises(MerkleValidationError, match="exactly 32 bytes"):
        calculate_merkle_root(value, ())


def branch_generator() -> GeneratorType:
    """Return a generator to verify mutable/streamed containers are rejected."""

    return (branch for branch in ("00" * 32,))


@pytest.mark.parametrize(
    "value",
    [["00" * 32], "00" * 32, {"00" * 32}, branch_generator()],
)
def test_invalid_branch_collection_type_is_rejected(value: object) -> None:
    with pytest.raises(MerkleValidationError, match="merkle_branches must be a tuple"):
        calculate_merkle_root(bytes(32), value)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [None, 0, True, b"00" * 32, bytearray(32)])
def test_non_string_branch_is_rejected(value: object) -> None:
    with pytest.raises(MerkleValidationError, match=r"merkle_branches\[0\].*string"):
        calculate_merkle_root(bytes(32), (value,))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("branch", "message"),
    [
        ("", "must not be empty"),
        ("00" * 31, "exactly 64 hexadecimal characters"),
        ("00" * 33, "exactly 64 hexadecimal characters"),
        ("0" * 63, "whole number of bytes"),
        ("gg" * 32, "only hexadecimal characters"),
        (("00" * 31) + "  ", "only hexadecimal characters"),
    ],
)
def test_malformed_branch_is_rejected(branch: str, message: str) -> None:
    with pytest.raises(MerkleValidationError, match=message):
        calculate_merkle_root(bytes(32), (branch,))


def test_inputs_remain_unchanged() -> None:
    coinbase_hash = bytes(range(32))
    branches = ("Ab" * 32, "cD" * 32)
    original_coinbase_hash = coinbase_hash
    original_branches = branches

    calculate_merkle_root(coinbase_hash, branches)

    assert coinbase_hash == original_coinbase_hash
    assert branches == original_branches


def test_calculation_delegates_to_double_sha256_without_reversal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coinbase_hash = bytes(range(32))
    branches = ("20" * 32, "30" * 32)
    calls: list[bytes] = []

    def fake_double_sha256(data: bytes) -> bytes:
        calls.append(data)
        return bytes([len(calls)]) * 32

    monkeypatch.setattr(merkle_module, "double_sha256", fake_double_sha256)

    root = calculate_merkle_root(coinbase_hash, branches)

    assert calls == [
        coinbase_hash + bytes.fromhex(branches[0]),
        (b"\x01" * 32) + bytes.fromhex(branches[1]),
    ]
    assert root == b"\x02" * 32


def test_raw_root_is_not_reversed() -> None:
    coinbase_hash = bytes(range(32))
    branch = bytes(range(32, 64))
    expected_raw = manual_double_sha256(coinbase_hash + branch)

    root = calculate_merkle_root(coinbase_hash, (branch.hex(),))

    assert root == expected_raw
    assert root != expected_raw[::-1]


def test_returned_root_bytes_are_immutable() -> None:
    root = calculate_merkle_root(bytes(32), ("01" * 32,))

    with pytest.raises(TypeError):
        root[0] = 0  # type: ignore[index]


def test_end_to_end_public_mining_boundaries() -> None:
    branches = ("11" * 32, "22" * 32)
    job = valid_job(merkle_branches=branches)
    original_job = replace(job)

    transaction = build_coinbase_transaction(job, "00ff")
    coinbase_hash = hash_coinbase_transaction(transaction)
    root = calculate_merkle_root(coinbase_hash, job.merkle_branches)

    expected = manual_double_sha256(coinbase_hash + bytes.fromhex(branches[0]))
    expected = manual_double_sha256(expected + bytes.fromhex(branches[1]))
    assert root == expected
    assert job == original_job
