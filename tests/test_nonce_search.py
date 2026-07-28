"""Tests for prepared mining work and bounded sequential nonce search."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from typing import Final

import pytest

import hashphere.mining.search as search_module
from hashphere.mining import (
    CoinbaseValidationError,
    MiningJob,
    NonceSearchMatch,
    NonceSearchResult,
    NonceSearchValidationError,
    PreparedMiningWork,
    block_hash_to_int,
    build_coinbase_transaction,
    calculate_merkle_root,
    decode_compact_target,
    difficulty_to_share_target,
    hash_coinbase_transaction,
    prepare_mining_work,
    search_nonce_range,
    serialize_block_header,
)

MAX_NONCE: Final = 0xFFFFFFFF
NONCE_STOP_LIMIT: Final = 1 << 32
MAX_UINT256: Final = (1 << 256) - 1
EXPECTED_COINBASE_HEX: Final = "01000000a1b200ffdeadbeef"
EXPECTED_COINBASE_HASH_HEX: Final = (
    "6076ce780d08b217095e8e701e855b881b33e94b147a4aa5a6b911cfbce87be8"
)
EXPECTED_MERKLE_ROOT_HEX: Final = "e0cf40466ad6f1b4f3c6cfb496e8724462dccd3ce16f155e614e58c62c67889c"
EXPECTED_HEADER_PREFIX_HEX: Final = (
    "04030201"
    "03020100070605040b0a09080f0e0d0c13121110171615141b1a19181f1e1d1c"
    "e0cf40466ad6f1b4f3c6cfb496e8724462dccd3ce16f155e614e58c62c67889c"
    "44332211"
    "ffff001d"
)
EXPECTED_NETWORK_TARGET: Final = int(
    "00000000ffff0000000000000000000000000000000000000000000000000000",
    16,
)
EXPECTED_SHARE_TARGET: Final = int(
    "0000000000068db22d0e5604189374bc6a7ef9db22d0e5604189374bc6a7ef9d",
    16,
)


def valid_job(
    *,
    network_time: str = "11223344",
) -> MiningJob:
    """Return deterministic synthetic job data for preparation tests."""

    return MiningJob(
        job_id="job-search",
        previous_block_hash=("000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f"),
        coinbase_part_1="01000000",
        coinbase_part_2="DEADBEEF",
        merkle_branches=("11" * 32, "22" * 32),
        version="01020304",
        network_bits="1d00ffff",
        network_time=network_time,
        clean_jobs=True,
        extra_nonce_1="A1B2",
        extra_nonce_2_size=2,
        difficulty=10_000,
    )


def prepared_work(
    *,
    header_prefix: bytes = bytes(range(76)),
    network_target: int = 5,
    share_target: int = 10,
) -> PreparedMiningWork:
    """Return directly validated work for deterministic search tests."""

    return PreparedMiningWork(
        job_id="job-search",
        extra_nonce_2="00fF",
        network_time="11223344",
        header_prefix=header_prefix,
        network_target=network_target,
        share_target=share_target,
    )


def install_fake_hashing(
    monkeypatch: pytest.MonkeyPatch,
    values: dict[int, int],
    *,
    default: int = MAX_UINT256,
) -> list[bytes]:
    """Install deterministic raw hashes keyed by serialized nonce."""

    headers: list[bytes] = []

    def fake_hash_block_header(header: bytes) -> bytes:
        headers.append(header)
        nonce = int.from_bytes(header[-4:], byteorder="little", signed=False)
        return values.get(nonce, default).to_bytes(
            32,
            byteorder="little",
            signed=False,
        )

    monkeypatch.setattr(search_module, "hash_block_header", fake_hash_block_header)
    return headers


def install_clock(monkeypatch: pytest.MonkeyPatch, *readings: int) -> None:
    """Install deterministic high-resolution monotonic-clock readings."""

    clock = iter(readings)
    monkeypatch.setattr(search_module, "perf_counter_ns", lambda: next(clock))


def test_preparation_known_pipeline_vector() -> None:
    job = valid_job()
    work = prepare_mining_work(job, "00fF")
    coinbase = build_coinbase_transaction(job, "00fF")
    coinbase_hash = hash_coinbase_transaction(coinbase)
    merkle_root = calculate_merkle_root(coinbase_hash, job.merkle_branches)
    nonce_zero_header = serialize_block_header(job, merkle_root, nonce=0)

    assert coinbase.hex() == EXPECTED_COINBASE_HEX
    assert coinbase_hash.hex() == EXPECTED_COINBASE_HASH_HEX
    assert merkle_root.hex() == EXPECTED_MERKLE_ROOT_HEX
    assert nonce_zero_header[:76].hex() == EXPECTED_HEADER_PREFIX_HEX
    assert nonce_zero_header[76:] == bytes(4)
    assert work == PreparedMiningWork(
        job_id="job-search",
        extra_nonce_2="00fF",
        network_time="11223344",
        header_prefix=bytes.fromhex(EXPECTED_HEADER_PREFIX_HEX),
        network_target=EXPECTED_NETWORK_TARGET,
        share_target=EXPECTED_SHARE_TARGET,
    )


def test_preparation_calls_each_fixed_operation_once_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = valid_job()
    calls: list[str] = []
    original_build = search_module.build_coinbase_transaction
    original_coinbase_hash = search_module.hash_coinbase_transaction
    original_merkle = search_module.calculate_merkle_root
    original_serialize = search_module.serialize_block_header
    original_network_target = search_module.decode_compact_target
    original_share_target = search_module.difficulty_to_share_target

    def build(current_job: MiningJob, extra_nonce_2: str) -> bytes:
        calls.append("coinbase")
        return original_build(current_job, extra_nonce_2)

    def coinbase_hash(transaction: bytes) -> bytes:
        calls.append("coinbase_hash")
        return original_coinbase_hash(transaction)

    def merkle_root(coinbase_digest: bytes, branches: tuple[str, ...]) -> bytes:
        calls.append("merkle")
        return original_merkle(coinbase_digest, branches)

    def serialize(current_job: MiningJob, root: bytes, nonce: int) -> bytes:
        calls.append("header")
        return original_serialize(current_job, root, nonce)

    def network_target(network_bits: str) -> int:
        calls.append("network_target")
        return original_network_target(network_bits)

    def share_target(difficulty: int | float) -> int:
        calls.append("share_target")
        return original_share_target(difficulty)

    monkeypatch.setattr(search_module, "build_coinbase_transaction", build)
    monkeypatch.setattr(search_module, "hash_coinbase_transaction", coinbase_hash)
    monkeypatch.setattr(search_module, "calculate_merkle_root", merkle_root)
    monkeypatch.setattr(search_module, "serialize_block_header", serialize)
    monkeypatch.setattr(search_module, "decode_compact_target", network_target)
    monkeypatch.setattr(search_module, "difficulty_to_share_target", share_target)

    prepare_mining_work(job, "00ff")

    assert calls == [
        "coinbase",
        "coinbase_hash",
        "merkle",
        "header",
        "network_target",
        "share_target",
    ]


def test_preparation_delegates_extra_nonce_validation() -> None:
    with pytest.raises(CoinbaseValidationError, match="extra_nonce_2"):
        prepare_mining_work(valid_job(), "0")


def test_preparation_does_not_mutate_or_normalize_inputs() -> None:
    job = valid_job(network_time="a1B2c3D4")
    original_job = replace(job)
    extra_nonce_2 = "aBfF"

    work = prepare_mining_work(job, extra_nonce_2)

    assert job == original_job
    assert extra_nonce_2 == "aBfF"
    assert work.extra_nonce_2 == "aBfF"
    assert work.network_time == "a1B2c3D4"


def test_prepared_work_is_frozen_and_uses_immutable_prefix() -> None:
    work = prepared_work()

    assert isinstance(work.header_prefix, bytes)
    with pytest.raises(FrozenInstanceError):
        work.share_target = 1  # type: ignore[misc]
    with pytest.raises(TypeError):
        work.header_prefix[0] = 0  # type: ignore[index]


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("job_id", "", "must not be empty"),
        ("job_id", "   ", "must not be empty"),
        ("job_id", 1, "must be a string"),
        ("extra_nonce_2", "", "must not be empty"),
        ("extra_nonce_2", "0", "whole number of bytes"),
        ("extra_nonce_2", "zz", "only hexadecimal"),
        ("extra_nonce_2", 1, "hexadecimal string"),
        ("network_time", "1234567", "exactly 8 hexadecimal"),
        ("network_time", "123456789", "exactly 8 hexadecimal"),
        ("network_time", "1234zzzz", "only hexadecimal"),
        ("network_time", 1, "hexadecimal string"),
        ("header_prefix", bytes(75), "exactly 76 bytes"),
        ("header_prefix", bytes(77), "exactly 76 bytes"),
        ("header_prefix", bytearray(76), "must be bytes"),
        ("network_target", True, "must be an integer"),
        ("network_target", 0, r"between 1 and 2\*\*256 - 1"),
        ("network_target", MAX_UINT256 + 1, r"between 1 and 2\*\*256 - 1"),
        ("share_target", False, "must be an integer"),
        ("share_target", -1, r"between 1 and 2\*\*256 - 1"),
        ("share_target", 1.0, "must be an integer"),
    ],
)
def test_direct_prepared_work_construction_validates_invariants(
    field: str,
    value: object,
    message: str,
) -> None:
    values: dict[str, object] = {
        "job_id": "job-search",
        "extra_nonce_2": "00ff",
        "network_time": "11223344",
        "header_prefix": bytes(76),
        "network_target": 1,
        "share_target": 1,
    }
    values[field] = value

    with pytest.raises(NonceSearchValidationError, match=message):
        PreparedMiningWork(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("start_nonce", "stop_nonce", "winning_nonce", "expected_hashes"),
    [
        (0, 3, 0, 1),
        (10, 15, 12, 3),
        (20, 23, 22, 3),
    ],
)
def test_search_stops_at_first_qualifying_position(
    monkeypatch: pytest.MonkeyPatch,
    start_nonce: int,
    stop_nonce: int,
    winning_nonce: int,
    expected_hashes: int,
) -> None:
    headers = install_fake_hashing(monkeypatch, {winning_nonce: 10})

    result = search_nonce_range(prepared_work(), start_nonce, stop_nonce)

    assert result.found is True
    assert result.exhausted is False
    assert result.hashes_checked == expected_hashes
    assert result.match is not None
    assert result.match.nonce == winning_nonce
    assert [int.from_bytes(header[-4:], "little") for header in headers] == list(
        range(start_nonce, winning_nonce + 1)
    )


def test_search_returns_first_of_multiple_qualifying_nonces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_hashing(monkeypatch, {2: 10, 3: 1})

    result = search_nonce_range(prepared_work(), 0, 5)

    assert result.match is not None
    assert result.match.nonce == 2
    assert result.hashes_checked == 3


def test_exhausted_search_has_exact_count(monkeypatch: pytest.MonkeyPatch) -> None:
    headers = install_fake_hashing(monkeypatch, {})

    result = search_nonce_range(prepared_work(), 7, 11)

    assert result.exhausted is True
    assert result.found is False
    assert result.match is None
    assert result.hashes_checked == 4
    assert len(headers) == 4


@pytest.mark.parametrize(
    ("network_target", "share_target", "hash_value", "share", "network"),
    [
        (5, 10, 10, True, False),
        (10, 5, 10, False, True),
        (10, 10, 10, True, True),
    ],
)
def test_targets_are_evaluated_independently_and_inclusively(
    monkeypatch: pytest.MonkeyPatch,
    network_target: int,
    share_target: int,
    hash_value: int,
    share: bool,
    network: bool,
) -> None:
    install_fake_hashing(monkeypatch, {4: hash_value})
    work = prepared_work(
        network_target=network_target,
        share_target=share_target,
    )

    result = search_nonce_range(work, 4, 5)

    assert result.match == NonceSearchMatch(
        nonce=4,
        block_hash=hash_value.to_bytes(32, "little"),
        meets_share_target=share,
        meets_network_target=network,
    )


def test_raw_hash_order_is_returned_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    raw_hash = bytes(range(32))
    monkeypatch.setattr(search_module, "hash_block_header", lambda header: raw_hash)
    work = prepared_work(network_target=MAX_UINT256, share_target=MAX_UINT256)

    result = search_nonce_range(work, 0, 1)

    assert result.match is not None
    assert result.match.block_hash == raw_hash
    assert result.match.block_hash != bytes(reversed(raw_hash))


def test_candidate_headers_are_80_bytes_with_little_endian_nonces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers = install_fake_hashing(monkeypatch, {})
    work = prepared_work()

    search_nonce_range(work, 0x01020304, 0x01020307)

    assert all(len(header) == 80 for header in headers)
    assert all(header[:76] == work.header_prefix for header in headers)
    assert [header[76:].hex() for header in headers] == [
        "04030201",
        "05030201",
        "06030201",
    ]


def test_range_can_include_maximum_nonce(monkeypatch: pytest.MonkeyPatch) -> None:
    headers = install_fake_hashing(monkeypatch, {})

    result = search_nonce_range(prepared_work(), MAX_NONCE, NONCE_STOP_LIMIT)

    assert result.hashes_checked == 1
    assert result.exhausted is True
    assert headers[0][-4:] == b"\xff" * 4


@pytest.mark.parametrize(
    ("start_nonce", "stop_nonce", "message"),
    [
        (1, 1, "less than"),
        (2, 1, "less than"),
        (-1, 1, "start_nonce must be between"),
        (MAX_NONCE + 1, NONCE_STOP_LIMIT, "start_nonce must be between"),
        (0, 0, "stop_nonce must be between"),
        (0, NONCE_STOP_LIMIT + 1, "stop_nonce must be between"),
    ],
)
def test_invalid_nonce_range_is_rejected(
    start_nonce: int,
    stop_nonce: int,
    message: str,
) -> None:
    with pytest.raises(NonceSearchValidationError, match=message):
        search_nonce_range(prepared_work(), start_nonce, stop_nonce)


@pytest.mark.parametrize(
    ("start_nonce", "stop_nonce", "message"),
    [
        (True, 1, "start_nonce must be an integer"),
        (False, 1, "start_nonce must be an integer"),
        (0, True, "stop_nonce must be an integer"),
        (0, False, "stop_nonce must be an integer"),
        (0.0, 1, "start_nonce must be an integer"),
        ("0", 1, "start_nonce must be an integer"),
        (0, 1.0, "stop_nonce must be an integer"),
        (0, None, "stop_nonce must be an integer"),
    ],
)
def test_nonce_range_rejects_nonintegers_and_bool(
    start_nonce: object,
    stop_nonce: object,
    message: str,
) -> None:
    with pytest.raises(NonceSearchValidationError, match=message):
        search_nonce_range(
            prepared_work(),
            start_nonce,  # type: ignore[arg-type]
            stop_nonce,  # type: ignore[arg-type]
        )


def test_search_rejects_non_prepared_work() -> None:
    with pytest.raises(NonceSearchValidationError, match="PreparedMiningWork"):
        search_nonce_range(object(), 0, 1)  # type: ignore[arg-type]


def test_hash_is_converted_to_an_integer_once_per_nonce(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_hashing(monkeypatch, {})
    received: list[bytes] = []
    original = search_module.block_hash_to_int

    def recording_conversion(block_hash: bytes) -> int:
        received.append(block_hash)
        return original(block_hash)

    monkeypatch.setattr(search_module, "block_hash_to_int", recording_conversion)

    result = search_nonce_range(prepared_work(), 3, 6)

    assert result.hashes_checked == 3
    assert len(received) == 3


def test_search_does_not_recalculate_fixed_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    work = prepare_mining_work(valid_job(), "00ff")
    install_fake_hashing(monkeypatch, {})

    def fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("fixed preparation operation called during search")

    monkeypatch.setattr(search_module, "build_coinbase_transaction", fail)
    monkeypatch.setattr(search_module, "hash_coinbase_transaction", fail)
    monkeypatch.setattr(search_module, "calculate_merkle_root", fail)
    monkeypatch.setattr(search_module, "serialize_block_header", fail)
    monkeypatch.setattr(search_module, "decode_compact_target", fail)
    monkeypatch.setattr(search_module, "difficulty_to_share_target", fail)

    result = search_nonce_range(work, 0, 3)

    assert result.hashes_checked == 3
    assert result.exhausted is True


def test_search_is_deterministic_and_does_not_mutate_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_hashing(monkeypatch, {2: 10})
    install_clock(monkeypatch, 100, 200, 100, 200)
    work = prepared_work()
    original_work = replace(work)

    first = search_nonce_range(work, 0, 4)
    second = search_nonce_range(work, 0, 4)

    assert first == second
    assert work == original_work


def test_elapsed_time_and_hashrate_are_exact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_hashing(monkeypatch, {})
    install_clock(monkeypatch, 1_000_000_000, 1_500_000_000)

    result = search_nonce_range(prepared_work(), 0, 2)

    assert result.elapsed_ns == 500_000_000
    assert result.hashes_checked == 2
    assert result.hashes_per_second == 4.0


def test_zero_elapsed_time_has_no_hashrate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_hashing(monkeypatch, {})
    install_clock(monkeypatch, 100, 100)

    result = search_nonce_range(prepared_work(), 0, 1)

    assert result.elapsed_ns == 0
    assert result.hashes_per_second is None


def test_elapsed_time_is_never_negative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_hashing(monkeypatch, {})
    install_clock(monkeypatch, 200, 100)

    result = search_nonce_range(prepared_work(), 0, 1)

    assert result.elapsed_ns == 0
    assert result.hashes_per_second is None


def test_match_and_result_are_frozen() -> None:
    match = NonceSearchMatch(
        nonce=3,
        block_hash=bytes(32),
        meets_share_target=True,
        meets_network_target=False,
    )
    result = NonceSearchResult(
        start_nonce=2,
        stop_nonce=5,
        hashes_checked=2,
        elapsed_ns=10,
        match=match,
    )

    with pytest.raises(FrozenInstanceError):
        match.nonce = 4  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        result.hashes_checked = 3  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("nonce", True, "nonce must be an integer"),
        ("nonce", -1, "nonce must be between"),
        ("nonce", NONCE_STOP_LIMIT, "nonce must be between"),
        ("block_hash", bytearray(32), "block_hash must be bytes"),
        ("block_hash", bytes(31), "exactly 32 bytes"),
        ("meets_share_target", 1, "must be a boolean"),
        ("meets_network_target", None, "must be a boolean"),
    ],
)
def test_direct_match_construction_validates_fields(
    field: str,
    value: object,
    message: str,
) -> None:
    values: dict[str, object] = {
        "nonce": 0,
        "block_hash": bytes(32),
        "meets_share_target": True,
        "meets_network_target": False,
    }
    values[field] = value

    with pytest.raises(NonceSearchValidationError, match=message):
        NonceSearchMatch(**values)  # type: ignore[arg-type]


def test_direct_match_must_meet_at_least_one_target() -> None:
    with pytest.raises(NonceSearchValidationError, match="at least one target"):
        NonceSearchMatch(
            nonce=0,
            block_hash=bytes(32),
            meets_share_target=False,
            meets_network_target=False,
        )


def test_direct_exhausted_result_requires_full_range_count() -> None:
    with pytest.raises(NonceSearchValidationError, match="count every nonce"):
        NonceSearchResult(
            start_nonce=2,
            stop_nonce=5,
            hashes_checked=2,
            elapsed_ns=0,
            match=None,
        )


@pytest.mark.parametrize("hashes_checked", [0, 4])
def test_direct_matched_result_requires_actual_count_inside_parent_range(
    hashes_checked: int,
) -> None:
    match = NonceSearchMatch(3, bytes(32), True, False)

    with pytest.raises(NonceSearchValidationError, match="between one hash and the range size"):
        NonceSearchResult(2, 5, hashes_checked, 0, match)


def test_direct_matched_result_accepts_parallel_actual_hash_count() -> None:
    match = NonceSearchMatch(3, bytes(32), True, False)

    result = NonceSearchResult(2, 5, 3, 0, match)

    assert result.hashes_checked == 3


def test_direct_result_rejects_match_outside_range() -> None:
    match = NonceSearchMatch(5, bytes(32), True, False)

    with pytest.raises(NonceSearchValidationError, match="inside the searched range"):
        NonceSearchResult(2, 5, 4, 0, match)


@pytest.mark.parametrize(
    ("hashes_checked", "elapsed_ns", "message"),
    [
        (True, 0, "hashes_checked must be an integer"),
        (3, True, "elapsed_ns must be an integer"),
        (3, -1, "elapsed_ns must be nonnegative"),
    ],
)
def test_direct_result_validates_counts_and_timing_types(
    hashes_checked: object,
    elapsed_ns: object,
    message: str,
) -> None:
    with pytest.raises(NonceSearchValidationError, match=message):
        NonceSearchResult(
            start_nonce=0,
            stop_nonce=3,
            hashes_checked=hashes_checked,  # type: ignore[arg-type]
            elapsed_ns=elapsed_ns,  # type: ignore[arg-type]
            match=None,
        )


def test_small_real_hashing_pipeline_search() -> None:
    work = prepare_mining_work(valid_job(), "00ff")

    result = search_nonce_range(work, 0, 2)

    assert result.match is None
    assert result.exhausted is True
    assert result.hashes_checked == 2
    assert result.elapsed_ns >= 0
    assert decode_compact_target(valid_job().network_bits) == work.network_target
    assert difficulty_to_share_target(valid_job().difficulty) == work.share_target
    assert block_hash_to_int(bytes(32)) == 0
