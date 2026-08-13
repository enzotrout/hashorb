"""Focused tests for exact best-hash tracking across mining backends."""

from __future__ import annotations

import pytest

import hashorb.compute.native as native_module
import hashorb.mining.search as search_module
from hashorb.compute.native import NativeSequentialBackend
from hashorb.compute.parallel import NonceRangeAssignment, _reduce_worker_results
from hashorb.mining.search import (
    NonceSearchResult,
    NonceSearchValidationError,
    PreparedMiningWork,
    search_nonce_range,
)

_MAX_UINT256 = (1 << 256) - 1


def _work(*, network_target: int = 1, share_target: int = 1) -> PreparedMiningWork:
    return PreparedMiningWork(
        job_id="hash-quality",
        extra_nonce_2="0000",
        network_time="11223344",
        header_prefix=bytes(range(76)),
        network_target=network_target,
        share_target=share_target,
    )


def _install_hashes(
    monkeypatch: pytest.MonkeyPatch,
    values: dict[int, int],
    *,
    default: int = _MAX_UINT256,
) -> None:
    def fake_hash_block_header(header: bytes) -> bytes:
        nonce = int.from_bytes(header[-4:], byteorder="little", signed=False)
        return values.get(nonce, default).to_bytes(32, byteorder="little", signed=False)

    monkeypatch.setattr(search_module, "hash_block_header", fake_hash_block_header)


def test_python_search_reports_exact_lowest_hash_for_exhausted_range(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_hashes(monkeypatch, {10: 50, 11: 7, 12: 42, 13: 9})

    result = search_nonce_range(_work(), 10, 14)

    assert result.match is None
    assert result.hashes_checked == 4
    assert result.best_nonce == 11
    assert result.best_hash == (7).to_bytes(32, byteorder="little")
    assert result.best_hash_value == 7


def test_python_search_best_hash_covers_every_hash_before_early_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_hashes(monkeypatch, {20: 90, 21: 40, 22: 8, 23: 1})

    result = search_nonce_range(_work(network_target=5, share_target=10), 20, 24)

    assert result.match is not None
    assert result.match.nonce == 22
    assert result.hashes_checked == 3
    assert result.best_nonce == 22
    assert result.best_hash == (8).to_bytes(32, byteorder="little")
    assert result.best_hash_value == 8


def test_native_wrapper_rehashes_reported_best_nonce(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_searcher(*args: object) -> tuple[object, ...]:
        del args
        return (None, None, False, False, 4, 11)

    def fake_hash(header: bytes) -> bytes:
        nonce = int.from_bytes(header[-4:], byteorder="little", signed=False)
        assert nonce == 11
        return (7).to_bytes(32, byteorder="little", signed=False)

    monkeypatch.setattr(native_module, "hash_block_header", fake_hash)
    clock = iter((100, 200))
    backend = NativeSequentialBackend(fake_searcher, clock=lambda: next(clock))

    result = backend.search_nonce_range(_work(), 10, 14)

    assert result.match is None
    assert result.best_nonce == 11
    assert result.best_hash_value == 7


def test_parallel_reducer_selects_lowest_hash_across_worker_results() -> None:
    assignments = (NonceRangeAssignment(0, 2), NonceRangeAssignment(2, 4))
    results = (
        NonceSearchResult(
            0,
            2,
            2,
            10,
            None,
            best_nonce=0,
            best_hash=(50).to_bytes(32, byteorder="little"),
        ),
        NonceSearchResult(
            2,
            4,
            2,
            10,
            None,
            best_nonce=3,
            best_hash=(7).to_bytes(32, byteorder="little"),
        ),
    )

    reduced = _reduce_worker_results(0, 4, assignments, results, 12)

    assert reduced.match is None
    assert reduced.hashes_checked == 4
    assert reduced.best_nonce == 3
    assert reduced.best_hash_value == 7


def test_parallel_reducer_breaks_best_hash_ties_by_lower_nonce() -> None:
    digest = (7).to_bytes(32, byteorder="little")
    assignments = (NonceRangeAssignment(0, 2), NonceRangeAssignment(2, 4))
    results = (
        NonceSearchResult(0, 2, 2, 10, None, best_nonce=1, best_hash=digest),
        NonceSearchResult(2, 4, 2, 10, None, best_nonce=2, best_hash=digest),
    )

    reduced = _reduce_worker_results(0, 4, assignments, results, 12)

    assert reduced.best_nonce == 1
    assert reduced.best_hash == digest


def test_result_requires_best_nonce_and_hash_as_a_pair() -> None:
    with pytest.raises(NonceSearchValidationError, match="both be present or both be absent"):
        NonceSearchResult(0, 1, 1, 0, None, best_nonce=0)

    with pytest.raises(NonceSearchValidationError, match="both be present or both be absent"):
        NonceSearchResult(0, 1, 1, 0, None, best_hash=bytes(32))


def test_result_validates_best_hash_range_and_width() -> None:
    with pytest.raises(NonceSearchValidationError, match="best_nonce must be inside"):
        NonceSearchResult(0, 1, 1, 0, None, best_nonce=1, best_hash=bytes(32))

    with pytest.raises(NonceSearchValidationError, match="best_hash must contain exactly 32 bytes"):
        NonceSearchResult(0, 1, 1, 0, None, best_nonce=0, best_hash=bytes(31))
