"""Host-only tests for explicit deterministic multi-CUDA orchestration."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Executor, Future
from dataclasses import FrozenInstanceError
from threading import Barrier, Lock

import pytest

import hashorb.__main__ as cli_module
from hashorb.compute import (
    ComputeBackendCapabilities,
    ComputeBackendExecutionError,
    ComputeBackendValidationError,
    CudaBackend,
    CudaMultiBackend,
    MiningComputeBackend,
    compute_backend_device_ordinals,
    validate_cuda_device_ordinals,
)
from hashorb.mining import NonceSearchMatch, NonceSearchResult, PreparedMiningWork


def prepared_work() -> PreparedMiningWork:
    return PreparedMiningWork(
        job_id="synthetic-multi-cuda",
        extra_nonce_2="00000000",
        network_time="65f04abc",
        header_prefix=bytes(range(76)),
        share_target=1,
        network_target=1,
    )


class FakeDeviceBackend:
    """One device-shaped full-range CUDA result boundary."""

    def __init__(
        self,
        ordinal: int,
        *,
        matches: set[int] | None = None,
        barrier: Barrier | None = None,
        failure: Exception | None = None,
        available: bool = True,
    ) -> None:
        self.device_ordinal = ordinal
        self.matches = set() if matches is None else matches
        self.barrier = barrier
        self.failure = failure
        self.calls: list[tuple[int, int]] = []
        self.close_calls = 0
        self.capabilities = ComputeBackendCapabilities(
            backend_name="cuda",
            display_name="fake CUDA device",
            backend_kind="gpu",
            implementation="cuda",
            supports_parallel_search=True,
            supports_cooperative_cancellation=False,
            supports_device_selection=True,
            deterministic_search_order=True,
            preferred_batch_size=None,
            available=available,
            unavailable_reason=None if available else "DeviceUnavailable",
        )

    def search_nonce_range(
        self,
        work: PreparedMiningWork,
        start_nonce: int,
        stop_nonce: int,
    ) -> NonceSearchResult:
        del work
        self.calls.append((start_nonce, stop_nonce))
        if self.barrier is not None:
            self.barrier.wait(timeout=2)
        if self.failure is not None:
            raise self.failure
        matching = sorted(self.matches.intersection(range(start_nonce, stop_nonce)))
        match = (
            NonceSearchMatch(matching[0], bytes([self.device_ordinal]) * 32, True, False)
            if matching
            else None
        )
        return NonceSearchResult(
            start_nonce,
            stop_nonce,
            stop_nonce - start_nonce,
            self.device_ordinal,
            match,
        )

    def close(self) -> None:
        self.close_calls += 1


def test_multi_cuda_capabilities_and_device_metadata_are_exact() -> None:
    devices: list[FakeDeviceBackend] = []

    def create(ordinal: int) -> MiningComputeBackend:
        device = FakeDeviceBackend(ordinal)
        devices.append(device)
        return device

    backend = CudaMultiBackend((3, 1), backend_factory=create)

    assert isinstance(backend, MiningComputeBackend)
    assert backend.device_ordinals == (1, 3)
    assert backend.device_count == 2
    assert compute_backend_device_ordinals(backend) == (1, 3)
    assert backend.capabilities.backend_name == "cuda-multi"
    assert backend.capabilities.implementation == "cuda-multi"
    assert backend.capabilities.available is True
    assert backend.capabilities.supports_parallel_search is True
    assert backend.capabilities.supports_cooperative_cancellation is False
    with pytest.raises(FrozenInstanceError):
        backend.capabilities.available = False  # type: ignore[misc]
    backend.close()
    assert [device.device_ordinal for device in devices] == [1, 3]


def test_multi_cuda_selected_event_contains_only_safe_device_metadata() -> None:
    backend = CudaMultiBackend(
        (2, 0),
        backend_factory=lambda ordinal: FakeDeviceBackend(ordinal),
    )
    captured: list[tuple[str, dict[str, object]]] = []

    class Events:
        def emit(
            self,
            event: str,
            *,
            level: str = "INFO",
            fields: dict[str, object] | None = None,
        ) -> None:
            assert level == "INFO"
            captured.append((event, {} if fields is None else fields))

    cli_module._emit_compute_backend_selected(Events(), backend)  # type: ignore[arg-type]
    backend.close()

    assert captured == [
        (
            "compute_backend_selected",
            {
                "backend_name": "cuda-multi",
                "backend_kind": "gpu",
                "implementation": "cuda-multi",
                "supports_parallel_search": True,
                "supports_cooperative_cancellation": False,
                "supports_device_selection": True,
                "device_count": 2,
                "device_ordinals": [0, 2],
            },
        )
    ]


@pytest.mark.parametrize(
    "ordinals",
    [(), (True,), (-1,), (2**31,), (0, 0), ("0",), [0], None],
)
def test_multi_cuda_rejects_invalid_device_tuples(ordinals: object) -> None:
    with pytest.raises(ComputeBackendValidationError):
        validate_cuda_device_ordinals(ordinals)
    with pytest.raises(ComputeBackendValidationError):
        CudaMultiBackend(ordinals, initialize=False)  # type: ignore[arg-type]


def test_multi_cuda_rejects_excess_devices() -> None:
    with pytest.raises(ComputeBackendValidationError):
        CudaMultiBackend(tuple(range(257)), initialize=False)


def test_uninitialized_multi_cuda_never_constructs_a_device_or_executor() -> None:
    def forbidden(value: int) -> MiningComputeBackend:
        raise AssertionError(value)

    backend = CudaMultiBackend(
        (0,),
        backend_factory=forbidden,
        executor_factory=forbidden,  # type: ignore[arg-type]
        initialize=False,
    )

    assert backend.capabilities.available is False
    assert backend.capabilities.unavailable_reason == "NotInitialized"
    with pytest.raises(ComputeBackendExecutionError, match="unavailable"):
        backend.search_nonce_range(prepared_work(), 0, 1)
    backend.close()


def test_multi_cuda_balances_exact_ranges_and_runs_devices_concurrently() -> None:
    barrier = Barrier(3)
    devices: dict[int, FakeDeviceBackend] = {}
    calls_lock = Lock()

    def create(ordinal: int) -> MiningComputeBackend:
        with calls_lock:
            devices[ordinal] = FakeDeviceBackend(ordinal, barrier=barrier)
        return devices[ordinal]

    backend = CudaMultiBackend(
        (2, 0, 1),
        backend_factory=create,
        clock=iter((100, 175)).__next__,
    )

    result = backend.search_nonce_range(prepared_work(), 7, 17)
    backend.close()

    assert result == NonceSearchResult(7, 17, 10, 75, None)
    assert devices[0].calls == [(7, 11)]
    assert devices[1].calls == [(11, 14)]
    assert devices[2].calls == [(14, 17)]
    assert all(device.close_calls == 1 for device in devices.values())


def test_multi_cuda_uses_only_useful_devices_for_tiny_range() -> None:
    devices: dict[int, FakeDeviceBackend] = {}

    def create(ordinal: int) -> MiningComputeBackend:
        devices[ordinal] = FakeDeviceBackend(ordinal)
        return devices[ordinal]

    backend = CudaMultiBackend((0, 1, 2), backend_factory=create)
    result = backend.search_nonce_range(prepared_work(), 10, 12)
    backend.close()

    assert result.hashes_checked == 2
    assert devices[0].calls == [(10, 11)]
    assert devices[1].calls == [(11, 12)]
    assert devices[2].calls == []


def test_multi_cuda_preserves_the_exact_2_to_32_stop_boundary() -> None:
    devices: dict[int, FakeDeviceBackend] = {}

    def create(ordinal: int) -> MiningComputeBackend:
        devices[ordinal] = FakeDeviceBackend(ordinal)
        return devices[ordinal]

    backend = CudaMultiBackend((0, 1, 2), backend_factory=create)
    result = backend.search_nonce_range(prepared_work(), 2**32 - 10, 2**32)
    backend.close()

    assert result.hashes_checked == 10
    assert devices[0].calls == [(2**32 - 10, 2**32 - 6)]
    assert devices[1].calls == [(2**32 - 6, 2**32 - 3)]
    assert devices[2].calls == [(2**32 - 3, 2**32)]


def test_multi_cuda_global_candidate_is_minimum_not_completion_order() -> None:
    devices: dict[int, FakeDeviceBackend] = {}

    def create(ordinal: int) -> MiningComputeBackend:
        devices[ordinal] = FakeDeviceBackend(ordinal, matches={2, 8})
        return devices[ordinal]

    backend = CudaMultiBackend((0, 1), backend_factory=create)
    result = backend.search_nonce_range(prepared_work(), 0, 10)
    backend.close()

    assert result.hashes_checked == 10
    assert result.match is not None
    assert result.match.nonce == 2


def test_one_device_multi_cuda_is_a_valid_degenerate_execution_path() -> None:
    device = FakeDeviceBackend(0)
    backend = CudaMultiBackend((0,), backend_factory=lambda ordinal: device)

    result = backend.search_nonce_range(prepared_work(), 100, 107)
    backend.close()

    assert result == NonceSearchResult(100, 107, 7, result.elapsed_ns, None)
    assert device.calls == [(100, 107)]
    assert device.close_calls == 1


def test_partial_device_initialization_failure_closes_every_created_context() -> None:
    devices: list[FakeDeviceBackend] = []

    def create(ordinal: int) -> MiningComputeBackend:
        if ordinal == 2:
            raise RuntimeError("private device failure")
        device = FakeDeviceBackend(ordinal)
        devices.append(device)
        return device

    backend = CudaMultiBackend((0, 2), backend_factory=create)

    assert backend.capabilities.available is False
    assert backend.capabilities.unavailable_reason == "DeviceUnavailable"
    assert devices[0].close_calls == 1
    with pytest.raises(ComputeBackendExecutionError, match="unavailable"):
        backend.search_nonce_range(prepared_work(), 0, 2)
    backend.close()


def test_first_unavailable_device_prevents_later_initialization() -> None:
    created: list[FakeDeviceBackend] = []

    def create(ordinal: int) -> MiningComputeBackend:
        device = FakeDeviceBackend(ordinal, available=False)
        created.append(device)
        return device

    backend = CudaMultiBackend((0, 1), backend_factory=create)

    assert backend.capabilities.available is False
    assert backend.capabilities.unavailable_reason == "DeviceUnavailable"
    assert len(created) == 1
    assert created[0].close_calls == 1
    backend.close()


def test_device_failure_is_terminal_sanitized_and_closes_all_contexts() -> None:
    devices: dict[int, FakeDeviceBackend] = {}

    def create(ordinal: int) -> MiningComputeBackend:
        failure = RuntimeError("private kernel detail") if ordinal == 0 else None
        devices[ordinal] = FakeDeviceBackend(ordinal, failure=failure)
        return devices[ordinal]

    backend = CudaMultiBackend((0, 1), backend_factory=create)

    with pytest.raises(ComputeBackendExecutionError) as raised:
        backend.search_nonce_range(prepared_work(), 0, 4)

    assert "private kernel detail" not in str(raised.value)
    assert all(device.close_calls == 1 for device in devices.values())
    with pytest.raises(ComputeBackendExecutionError, match="closed"):
        backend.search_nonce_range(prepared_work(), 0, 1)
    backend.close()
    assert all(device.close_calls == 1 for device in devices.values())


class FailingExecutor(Executor):
    """Expose one failed child and later pending futures for cancellation."""

    def __init__(self, device_count: int) -> None:
        del device_count
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
            future.set_exception(RuntimeError("private device detail"))
        self.futures.append(future)
        return future

    def shutdown(self, wait: bool = True, *, cancel_futures: bool = False) -> None:
        assert wait is True
        assert cancel_futures is True
        self.shutdown_calls += 1


def test_child_failure_cancels_pending_futures_and_shuts_down_once() -> None:
    executor = FailingExecutor(3)
    devices: list[FakeDeviceBackend] = []

    def create(ordinal: int) -> MiningComputeBackend:
        device = FakeDeviceBackend(ordinal)
        devices.append(device)
        return device

    backend = CudaMultiBackend(
        (0, 1, 2),
        backend_factory=create,
        executor_factory=lambda count: executor,
    )

    with pytest.raises(ComputeBackendExecutionError) as raised:
        backend.search_nonce_range(prepared_work(), 0, 9)

    assert "private device detail" not in str(raised.value)
    assert executor.shutdown_calls == 1
    assert executor.futures[0].done()
    assert all(future.cancelled() for future in executor.futures[1:])
    assert all(device.close_calls == 1 for device in devices)
    backend.close()
    assert executor.shutdown_calls == 1


class IncompleteDeviceBackend(FakeDeviceBackend):
    def search_nonce_range(
        self,
        work: PreparedMiningWork,
        start_nonce: int,
        stop_nonce: int,
    ) -> NonceSearchResult:
        del work
        return NonceSearchResult(
            start_nonce,
            stop_nonce,
            1,
            0,
            NonceSearchMatch(start_nonce, bytes(32), True, False),
        )


def test_incomplete_child_accounting_is_terminal() -> None:
    devices: list[IncompleteDeviceBackend] = []

    def create(ordinal: int) -> MiningComputeBackend:
        device = IncompleteDeviceBackend(ordinal)
        devices.append(device)
        return device

    backend = CudaMultiBackend((0, 1), backend_factory=create)
    with pytest.raises(ComputeBackendExecutionError):
        backend.search_nonce_range(prepared_work(), 0, 6)
    assert all(device.close_calls == 1 for device in devices)


def test_child_cuda_python_candidate_verification_failure_is_terminal() -> None:
    class InvalidRuntime:
        def initialize_device(self, ordinal: int) -> None:
            del ordinal

        def search_nonce_range(
            self,
            header: bytes,
            share: bytes,
            network: bytes,
            start: int,
            stop: int,
        ) -> object:
            del header, share, network
            return start, False, True, stop - start

        def close_device(self) -> None:
            return None

    backend = CudaMultiBackend(
        (0, 1),
        backend_factory=lambda ordinal: CudaBackend(ordinal, InvalidRuntime()),
    )
    work = PreparedMiningWork(
        job_id="synthetic-verification-failure",
        extra_nonce_2="00000000",
        network_time="65f04abc",
        header_prefix=bytes(range(76)),
        share_target=(1 << 256) - 1,
        network_target=1,
    )

    with pytest.raises(ComputeBackendExecutionError):
        backend.search_nonce_range(work, 0, 4)
    with pytest.raises(ComputeBackendExecutionError, match="closed"):
        backend.search_nonce_range(work, 0, 1)


class TrackingExecutor(Executor):
    def __init__(self, device_count: int) -> None:
        from concurrent.futures import ThreadPoolExecutor

        self.delegate = ThreadPoolExecutor(max_workers=device_count)
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


def test_executor_is_persistent_then_closed_once_with_all_devices() -> None:
    executors: list[TrackingExecutor] = []
    devices: list[FakeDeviceBackend] = []

    def create_executor(count: int) -> Executor:
        executor = TrackingExecutor(count)
        executors.append(executor)
        return executor

    def create_device(ordinal: int) -> MiningComputeBackend:
        device = FakeDeviceBackend(ordinal)
        devices.append(device)
        return device

    backend = CudaMultiBackend(
        (0, 1),
        backend_factory=create_device,
        executor_factory=create_executor,
    )
    backend.search_nonce_range(prepared_work(), 0, 4)
    backend.search_nonce_range(prepared_work(), 4, 8)
    backend.close()
    backend.close()

    assert len(executors) == 1
    assert executors[0].shutdown_calls == 1
    assert all(device.close_calls == 1 for device in devices)
    assert all(not thread.is_alive() for thread in executors[0].delegate._threads)


@pytest.mark.parametrize("threads", [64, 128, 256, 512])
def test_multi_cuda_accepts_only_evaluated_shared_launch_sizes(threads: int) -> None:
    backend = CudaMultiBackend(
        (0,),
        backend_factory=lambda ordinal: FakeDeviceBackend(ordinal),
        threads_per_block=threads,
    )

    assert backend.threads_per_block == threads
    backend.close()


@pytest.mark.parametrize("threads", [0, 32, 1024, True])
def test_multi_cuda_rejects_unevaluated_launch_sizes(threads: int) -> None:
    with pytest.raises(ComputeBackendValidationError):
        CudaMultiBackend((0,), threads_per_block=threads)
