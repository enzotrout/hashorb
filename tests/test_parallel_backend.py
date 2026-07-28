"""Tests for portable native parallel compute execution."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Executor, Future, ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from threading import Barrier, Event, Lock

import pytest

import hashphere.compute.native as native_module
from hashphere.compute import (
    ComputeBackendExecutionError,
    ComputeBackendValidationError,
    MiningComputeBackend,
    NativeParallelBackend,
    NativeSequentialBackend,
    PythonSequentialBackend,
)
from hashphere.mining import NonceSearchResult, PreparedMiningWork, hash_block_header

_MAX_UINT256 = (1 << 256) - 1

type NativeResult = tuple[int | None, bytes | None, bool, bool, int]
type NativeSearcher = Callable[[bytes, bytes, bytes, int, int], NativeResult]


def prepared_work(
    *,
    share_target: int = 1,
    network_target: int = 1,
) -> PreparedMiningWork:
    """Return immutable synthetic prepared work."""

    return PreparedMiningWork(
        job_id="synthetic-parallel-job",
        extra_nonce_2="00000000",
        network_time="65f04abc",
        header_prefix=bytes(range(76)),
        share_target=share_target,
        network_target=network_target,
    )


def reference_native_searcher(
    calls: list[tuple[int, int]] | None = None,
    *,
    completion_order: list[int] | None = None,
) -> NativeSearcher:
    """Return a narrow extension-shaped searcher using production Python hashing."""

    call_lock = Lock()
    completion_barrier = Barrier(3)
    final_completed = Event()
    middle_completed = Event()

    def search(
        header_prefix: bytes,
        share_target_bytes: bytes,
        network_target_bytes: bytes,
        start_nonce: int,
        stop_nonce: int,
    ) -> NativeResult:
        if calls is not None:
            with call_lock:
                calls.append((start_nonce, stop_nonce))
        share_target = int.from_bytes(share_target_bytes, byteorder="little", signed=False)
        network_target = int.from_bytes(network_target_bytes, byteorder="little", signed=False)
        hashes_checked = 0
        result: NativeResult = (None, None, False, False, stop_nonce - start_nonce)
        for nonce in range(start_nonce, stop_nonce):
            digest = hash_block_header(
                header_prefix + nonce.to_bytes(4, byteorder="little", signed=False)
            )
            hashes_checked += 1
            value = int.from_bytes(digest, byteorder="little", signed=False)
            meets_share = value <= share_target
            meets_network = value <= network_target
            if meets_share or meets_network:
                result = (nonce, digest, meets_share, meets_network, hashes_checked)
                break
        if completion_order is not None:
            completion_barrier.wait()
            if start_nonce == 6:
                with call_lock:
                    completion_order.append(start_nonce)
                final_completed.set()
            elif start_nonce == 3:
                final_completed.wait()
                with call_lock:
                    completion_order.append(start_nonce)
                middle_completed.set()
            else:
                middle_completed.wait()
                with call_lock:
                    completion_order.append(start_nonce)
        return result

    return search


def test_parallel_capabilities_are_exact_and_native_availability_is_inherited() -> None:
    available = NativeParallelBackend(
        4,
        NativeSequentialBackend(reference_native_searcher()),
    )
    unavailable = NativeParallelBackend(4, NativeSequentialBackend(None))

    assert isinstance(available, MiningComputeBackend)
    assert available.worker_count == 4
    assert available.capabilities.backend_name == "native-parallel"
    assert available.capabilities.backend_kind == "cpu"
    assert available.capabilities.implementation == "c-threadpool"
    assert available.capabilities.supports_parallel_search is True
    assert available.capabilities.supports_cooperative_cancellation is False
    assert available.capabilities.supports_device_selection is False
    assert available.capabilities.deterministic_search_order is True
    assert available.capabilities.preferred_batch_size is None
    assert available.capabilities.available is True
    assert unavailable.capabilities.available is False
    assert unavailable.capabilities.unavailable_reason == "ExtensionNotInstalled"
    with pytest.raises(FrozenInstanceError):
        available.capabilities.available = False  # type: ignore[misc]


@pytest.mark.parametrize("worker_count", [True, 0, 257, 1.0, "2", None])
def test_parallel_backend_rejects_invalid_worker_configuration(worker_count: object) -> None:
    with pytest.raises(ComputeBackendValidationError):
        NativeParallelBackend(worker_count)  # type: ignore[arg-type]


def test_parallel_exhaustion_searches_every_assignment_once_and_uses_wall_clock() -> None:
    calls: list[tuple[int, int]] = []
    ticks = iter((100, 160))
    backend = NativeParallelBackend(
        4,
        NativeSequentialBackend(reference_native_searcher(calls)),
        clock=lambda: next(ticks),
    )
    work = prepared_work()

    result = backend.search_nonce_range(work, 7, 17)
    backend.close()

    assert result == NonceSearchResult(7, 17, 10, 60, None)
    assert sorted(calls) == [(7, 10), (10, 13), (13, 15), (15, 17)]
    assert len(calls) == len(set(calls)) == 4
    assert work == prepared_work()


def test_parallel_result_is_independent_of_worker_completion_order() -> None:
    completion_order: list[int] = []
    backend = NativeParallelBackend(
        3,
        NativeSequentialBackend(reference_native_searcher(completion_order=completion_order)),
        clock=iter((1, 2)).__next__,
    )
    work = prepared_work(
        share_target=_MAX_UINT256,
        network_target=_MAX_UINT256,
    )

    result = backend.search_nonce_range(work, 0, 9)
    backend.close()

    assert completion_order != sorted(completion_order)
    assert result.match is not None
    assert result.match.nonce == 0
    assert result.match.block_hash == hash_block_header(work.header_prefix + bytes(4))
    assert result.match.meets_share_target is True
    assert result.match.meets_network_target is True
    assert result.hashes_checked == 3
    assert result.elapsed_ns == 1


@pytest.mark.parametrize(
    ("share_matches", "network_matches", "expected_nonce", "expected_flags"),
    [
        ({0}, set(), 0, (True, False)),
        ({4}, set(), 4, (True, False)),
        (set(), {8}, 8, (False, True)),
        ({4, 8}, set(), 4, (True, False)),
        ({4}, {4}, 4, (True, True)),
    ],
)
def test_parallel_candidate_selection_and_flags_are_deterministic(
    monkeypatch: pytest.MonkeyPatch,
    share_matches: set[int],
    network_matches: set[int],
    expected_nonce: int,
    expected_flags: tuple[bool, bool],
) -> None:
    share_target = 101
    network_target = 202

    def digest_for_header(header: bytes) -> bytes:
        nonce = int.from_bytes(header[-4:], byteorder="little", signed=False)
        return (nonce + 1).to_bytes(32, byteorder="little", signed=False)

    def meets_target(digest: bytes, target: int) -> bool:
        nonce = int.from_bytes(digest, byteorder="little", signed=False) - 1
        return nonce in (share_matches if target == share_target else network_matches)

    def searcher(
        header_prefix: bytes,
        share_bytes: bytes,
        network_bytes: bytes,
        start_nonce: int,
        stop_nonce: int,
    ) -> NativeResult:
        assert int.from_bytes(share_bytes, "little") == share_target
        assert int.from_bytes(network_bytes, "little") == network_target
        for count, nonce in enumerate(range(start_nonce, stop_nonce), start=1):
            digest = digest_for_header(header_prefix + nonce.to_bytes(4, "little"))
            share = nonce in share_matches
            network = nonce in network_matches
            if share or network:
                return nonce, digest, share, network, count
        return None, None, False, False, stop_nonce - start_nonce

    monkeypatch.setattr(native_module, "hash_block_header", digest_for_header)
    monkeypatch.setattr(native_module, "hash_meets_target", meets_target)
    backend = NativeParallelBackend(
        3,
        NativeSequentialBackend(searcher),
        clock=iter((5, 10)).__next__,
    )

    result = backend.search_nonce_range(
        prepared_work(share_target=share_target, network_target=network_target),
        0,
        9,
    )
    backend.close()

    assert result.match is not None
    assert result.match.nonce == expected_nonce
    assert result.match.block_hash == (expected_nonce + 1).to_bytes(32, "little")
    assert (
        result.match.meets_share_target,
        result.match.meets_network_target,
    ) == expected_flags
    assert result.hashes_checked == sum(
        min(
            [
                nonce - start + 1
                for nonce in share_matches | network_matches
                if start <= nonce < stop
            ]
            or [stop - start]
        )
        for start, stop in ((0, 3), (3, 6), (6, 9))
    )


def test_parallel_one_worker_matches_python_oracle_exactly_except_wall_clock() -> None:
    work = prepared_work()
    python_result = PythonSequentialBackend().search_nonce_range(work, 2, 7)
    backend = NativeParallelBackend(
        1,
        NativeSequentialBackend(reference_native_searcher()),
        clock=iter((50, 75)).__next__,
    )

    parallel_result = backend.search_nonce_range(work, 2, 7)
    backend.close()

    assert parallel_result.start_nonce == python_result.start_nonce
    assert parallel_result.stop_nonce == python_result.stop_nonce
    assert parallel_result.hashes_checked == python_result.hashes_checked
    assert parallel_result.match == python_result.match
    assert parallel_result.elapsed_ns == 25


class TrackingExecutor(Executor):
    """Delegating executor that exposes deterministic lifecycle observations."""

    def __init__(self, worker_count: int) -> None:
        self.delegate = ThreadPoolExecutor(max_workers=worker_count)
        self.shutdown_calls = 0

    def submit(  # type: ignore[override]
        self,
        fn: Callable[..., NonceSearchResult],
        /,
        *args: object,
        **kwargs: object,
    ) -> Future[NonceSearchResult]:
        return self.delegate.submit(fn, *args, **kwargs)

    def shutdown(self, wait: bool = True, *, cancel_futures: bool = False) -> None:
        self.shutdown_calls += 1
        self.delegate.shutdown(wait=wait, cancel_futures=cancel_futures)


def test_executor_is_created_once_reused_closed_once_and_threads_terminate() -> None:
    executors: list[TrackingExecutor] = []

    def create(worker_count: int) -> Executor:
        executor = TrackingExecutor(worker_count)
        executors.append(executor)
        return executor

    backend = NativeParallelBackend(
        2,
        NativeSequentialBackend(reference_native_searcher()),
        executor_factory=create,
    )

    backend.search_nonce_range(prepared_work(), 0, 4)
    backend.search_nonce_range(prepared_work(), 4, 8)
    backend.close()
    backend.close()

    assert len(executors) == 1
    assert executors[0].shutdown_calls == 1
    assert all(not thread.is_alive() for thread in executors[0].delegate._threads)
    with pytest.raises(ComputeBackendExecutionError, match="closed"):
        backend.search_nonce_range(prepared_work(), 0, 1)


class FailingExecutor(Executor):
    """Executor with one failing future and later cancellable pending futures."""

    def __init__(self, worker_count: int) -> None:
        del worker_count
        self.futures: list[Future[NonceSearchResult]] = []
        self.shutdown_calls = 0

    def submit(  # type: ignore[override]
        self,
        fn: Callable[..., NonceSearchResult],
        /,
        *args: object,
        **kwargs: object,
    ) -> Future[NonceSearchResult]:
        del fn, args, kwargs
        future: Future[NonceSearchResult] = Future()
        if not self.futures:
            future.set_exception(RuntimeError("private worker detail"))
        self.futures.append(future)
        return future

    def shutdown(self, wait: bool = True, *, cancel_futures: bool = False) -> None:
        del wait, cancel_futures
        self.shutdown_calls += 1


def test_worker_failure_cancels_pending_work_breaks_backend_and_never_falls_back() -> None:
    executor = FailingExecutor(3)
    backend = NativeParallelBackend(
        3,
        NativeSequentialBackend(reference_native_searcher()),
        executor_factory=lambda count: executor,
    )

    with pytest.raises(ComputeBackendExecutionError) as raised:
        backend.search_nonce_range(prepared_work(), 0, 9)

    assert "private worker detail" not in str(raised.value)
    assert executor.shutdown_calls == 1
    assert executor.futures[0].done()
    assert all(future.cancelled() for future in executor.futures[1:])
    with pytest.raises(ComputeBackendExecutionError, match="unavailable"):
        backend.search_nonce_range(prepared_work(), 0, 1)
    backend.close()
    assert executor.shutdown_calls == 1


def test_native_candidate_verification_failure_is_terminal_and_sanitized() -> None:
    def invalid_candidate(
        header_prefix: bytes,
        share_target: bytes,
        network_target: bytes,
        start_nonce: int,
        stop_nonce: int,
    ) -> NativeResult:
        del header_prefix, share_target, network_target, stop_nonce
        return start_nonce, bytes.fromhex("ab" * 32), True, False, 1

    backend = NativeParallelBackend(
        2,
        NativeSequentialBackend(invalid_candidate),
    )

    with pytest.raises(ComputeBackendExecutionError) as raised:
        backend.search_nonce_range(
            prepared_work(share_target=_MAX_UINT256),
            0,
            4,
        )

    assert "ab" * 32 not in str(raised.value)
    with pytest.raises(ComputeBackendExecutionError, match="unavailable"):
        backend.search_nonce_range(prepared_work(), 0, 1)
    backend.close()


def test_unavailable_parallel_backend_never_constructs_an_executor() -> None:
    executor_calls = 0

    def forbidden(worker_count: int) -> Executor:
        nonlocal executor_calls
        executor_calls += 1
        raise AssertionError(worker_count)

    backend = NativeParallelBackend(
        2,
        NativeSequentialBackend(None),
        executor_factory=forbidden,
    )

    with pytest.raises(ComputeBackendExecutionError, match="unavailable"):
        backend.search_nonce_range(prepared_work(), 0, 2)
    backend.close()

    assert executor_calls == 0
