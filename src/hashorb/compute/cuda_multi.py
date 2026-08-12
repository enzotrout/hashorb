"""Deterministic concurrent orchestration across explicitly selected CUDA devices."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import FIRST_EXCEPTION, Executor, Future, ThreadPoolExecutor, wait
from threading import Lock
from time import perf_counter_ns

from hashorb.compute.backend import (
    ComputeBackendCapabilities,
    ComputeBackendExecutionError,
    ComputeBackendValidationError,
    MiningComputeBackend,
    close_compute_backend,
)
from hashorb.compute.cuda import CudaBackend
from hashorb.compute.parallel import NonceRangeAssignment, partition_nonce_range
from hashorb.config import MAX_CUDA_DEVICE, MAX_CUDA_DEVICES
from hashorb.config.profile import (
    CUDA_THREADS_PER_BLOCK_CHOICES,
    DEFAULT_CUDA_THREADS_PER_BLOCK,
)
from hashorb.mining.search import NonceSearchResult, PreparedMiningWork
from hashorb.mining.target import block_hash_to_int

type DeviceBackendFactory = Callable[[int], MiningComputeBackend]
type ExecutorFactory = Callable[[int], Executor]
type MonotonicClock = Callable[[], int]


class CudaMultiBackend:
    """Own one CUDA context per explicit ordinal and reduce exact child ranges."""

    __slots__ = (
        "_backends",
        "_broken",
        "_clock",
        "_closed",
        "_device_ordinals",
        "_executor",
        "_executor_factory",
        "_operation_lock",
        "_threads_per_block",
        "capabilities",
    )

    def __init__(
        self,
        device_ordinals: tuple[int, ...],
        *,
        backend_factory: DeviceBackendFactory | None = None,
        executor_factory: ExecutorFactory | None = None,
        initialize: bool = True,
        threads_per_block: int = DEFAULT_CUDA_THREADS_PER_BLOCK,
        clock: MonotonicClock = perf_counter_ns,
    ) -> None:
        """Create isolated device backends without performing device discovery."""

        self._device_ordinals = validate_cuda_device_ordinals(device_ordinals)
        if backend_factory is not None and not callable(backend_factory):
            raise ComputeBackendValidationError("backend_factory must be callable or None")
        if executor_factory is not None and not callable(executor_factory):
            raise ComputeBackendValidationError("executor_factory must be callable or None")
        if not isinstance(initialize, bool):
            raise ComputeBackendValidationError("initialize must be a Boolean")
        if not callable(clock):
            raise ComputeBackendValidationError("clock must be callable")
        if threads_per_block not in CUDA_THREADS_PER_BLOCK_CHOICES:
            raise ComputeBackendValidationError("threads_per_block is unsupported")

        self._threads_per_block = threads_per_block
        self._executor_factory = executor_factory or _create_executor
        self._clock = clock
        self._executor: Executor | None = None
        self._operation_lock = Lock()
        self._closed = False
        self._broken = False
        self._backends: tuple[MiningComputeBackend, ...] = ()

        unavailable_reason: str | None = "NotInitialized"
        if initialize:
            unavailable_reason = self._initialize_backends(backend_factory)
        self.capabilities = ComputeBackendCapabilities(
            backend_name="cuda-multi",
            display_name="Explicit deterministic multi-CUDA orchestrator",
            backend_kind="gpu",
            implementation="cuda-multi",
            supports_parallel_search=True,
            supports_cooperative_cancellation=False,
            supports_device_selection=True,
            deterministic_search_order=True,
            preferred_batch_size=None,
            available=unavailable_reason is None,
            unavailable_reason=unavailable_reason,
        )

    @property
    def device_ordinals(self) -> tuple[int, ...]:
        """Return canonical ascending explicit ordinals."""

        return self._device_ordinals

    @property
    def device_count(self) -> int:
        """Return the configured number of independently owned CUDA contexts."""

        return len(self._device_ordinals)

    @property
    def threads_per_block(self) -> int:
        """Return the shared validated launch size for every selected device."""

        return self._threads_per_block

    def search_nonce_range(
        self,
        work: PreparedMiningWork,
        start_nonce: int,
        stop_nonce: int,
    ) -> NonceSearchResult:
        """Partition, concurrently search, and deterministically reduce one range."""

        if not isinstance(work, PreparedMiningWork):
            raise ComputeBackendValidationError("work must be PreparedMiningWork")
        assignments = partition_nonce_range(start_nonce, stop_nonce, self.device_count)

        with self._operation_lock:
            if self._closed:
                raise ComputeBackendExecutionError("multi-CUDA compute backend is closed")
            if self._broken or not self.capabilities.available:
                raise ComputeBackendExecutionError("multi-CUDA compute backend is unavailable")

            executor = self._executor
            futures: tuple[Future[NonceSearchResult], ...] = ()
            try:
                if executor is None:
                    executor = self._executor_factory(self.device_count)
                    if not isinstance(executor, Executor):
                        raise TypeError("executor factory returned invalid data")
                    self._executor = executor
                started_ns = self._clock()
                futures = self._submit_assignments(executor, work, assignments)
                completed, _ = wait(futures, return_when=FIRST_EXCEPTION)
                for future in completed:
                    failure = future.exception()
                    if failure is not None:
                        raise failure
                results = tuple(future.result() for future in futures)
                finished_ns = self._clock()
                return _reduce_device_results(
                    start_nonce,
                    stop_nonce,
                    assignments,
                    results,
                    _elapsed_nanoseconds(started_ns, finished_ns),
                )
            except BaseException as exc:
                self._terminate(futures)
                if isinstance(exc, Exception):
                    raise ComputeBackendExecutionError(
                        "multi-CUDA compute backend search failed"
                    ) from exc
                raise

    def close(self) -> None:
        """Wait for active work and release every executor and device context once."""

        with self._operation_lock:
            if self._closed:
                return
            self._closed = True
            cleanup_failed = self._release_resources()
            if cleanup_failed:
                raise ComputeBackendExecutionError("multi-CUDA compute backend cleanup failed")

    def _initialize_backends(self, factory: DeviceBackendFactory | None) -> str | None:
        initialized: list[MiningComputeBackend] = []
        try:
            for ordinal in self.device_ordinals:
                backend = (
                    CudaBackend(ordinal, threads_per_block=self.threads_per_block)
                    if factory is None
                    else factory(ordinal)
                )
                if not isinstance(backend, MiningComputeBackend):
                    raise TypeError("device backend does not implement the compute contract")
                if getattr(backend, "device_ordinal", None) != ordinal:
                    raise TypeError("device backend ordinal is inconsistent")
                if not backend.capabilities.available:
                    reason = backend.capabilities.unavailable_reason or "DeviceUnavailable"
                    initialized.append(backend)
                    self._backends = tuple(initialized)
                    self._release_resources()
                    return reason
                initialized.append(backend)
        except Exception:
            self._backends = tuple(initialized)
            self._release_resources()
            return "DeviceUnavailable"
        self._backends = tuple(initialized)
        return None

    def _submit_assignments(
        self,
        executor: Executor,
        work: PreparedMiningWork,
        assignments: tuple[NonceRangeAssignment, ...],
    ) -> tuple[Future[NonceSearchResult], ...]:
        futures: list[Future[NonceSearchResult]] = []
        try:
            for backend, assignment in zip(self._backends, assignments, strict=False):
                futures.append(
                    executor.submit(
                        backend.search_nonce_range,
                        work,
                        assignment.start_nonce,
                        assignment.stop_nonce,
                    )
                )
        except BaseException:
            for future in futures:
                future.cancel()
            raise
        return tuple(futures)

    def _terminate(self, futures: tuple[Future[NonceSearchResult], ...]) -> None:
        for future in futures:
            future.cancel()
        self._broken = True
        self._closed = True
        self._release_resources()

    def _release_resources(self) -> bool:
        cleanup_failed = False
        executor = self._executor
        self._executor = None
        if executor is not None:
            try:
                executor.shutdown(wait=True, cancel_futures=True)
            except BaseException:
                cleanup_failed = True
        backends = self._backends
        self._backends = ()
        for backend in backends:
            try:
                close_compute_backend(backend)
            except BaseException:
                cleanup_failed = True
        return cleanup_failed


def validate_cuda_device_ordinals(device_ordinals: object) -> tuple[int, ...]:
    """Validate and canonicalize one explicit nonempty device tuple."""

    if not isinstance(device_ordinals, tuple) or not device_ordinals:
        raise ComputeBackendValidationError("device_ordinals must be a nonempty tuple")
    if len(device_ordinals) > MAX_CUDA_DEVICES:
        raise ComputeBackendValidationError(
            f"device_ordinals must contain at most {MAX_CUDA_DEVICES} devices"
        )
    parsed: list[int] = []
    for ordinal in device_ordinals:
        if isinstance(ordinal, bool) or not isinstance(ordinal, int):
            raise ComputeBackendValidationError("CUDA device ordinals must be integers")
        if not 0 <= ordinal <= MAX_CUDA_DEVICE:
            raise ComputeBackendValidationError(
                f"CUDA device ordinals must be between 0 and {MAX_CUDA_DEVICE}"
            )
        parsed.append(ordinal)
    if len(set(parsed)) != len(parsed):
        raise ComputeBackendValidationError("CUDA device ordinals must be unique")
    return tuple(sorted(parsed))


def _create_executor(device_count: int) -> Executor:
    return ThreadPoolExecutor(
        max_workers=device_count,
        thread_name_prefix="hashorb-cuda",
    )


def _elapsed_nanoseconds(started_ns: object, finished_ns: object) -> int:
    if (
        isinstance(started_ns, bool)
        or not isinstance(started_ns, int)
        or isinstance(finished_ns, bool)
        or not isinstance(finished_ns, int)
    ):
        raise ComputeBackendExecutionError("multi-CUDA compute clock returned invalid data")
    return max(0, finished_ns - started_ns)


def _reduce_device_results(
    start_nonce: int,
    stop_nonce: int,
    assignments: tuple[NonceRangeAssignment, ...],
    results: tuple[object, ...],
    elapsed_ns: int,
) -> NonceSearchResult:
    if len(results) != len(assignments):
        raise ComputeBackendExecutionError("multi-CUDA result count is invalid")

    hashes_checked = 0
    matches = []
    best_candidates: list[tuple[int, int, bytes]] = []
    all_best_hashes_present = True

    for assignment, result in zip(assignments, results, strict=True):
        if not isinstance(result, NonceSearchResult):
            raise ComputeBackendExecutionError("multi-CUDA device returned invalid data")
        if (
            result.start_nonce != assignment.start_nonce
            or result.stop_nonce != assignment.stop_nonce
        ):
            raise ComputeBackendExecutionError("multi-CUDA device returned a different range")
        if result.hashes_checked != assignment.size:
            raise ComputeBackendExecutionError("multi-CUDA device hash count is incomplete")

        hashes_checked += result.hashes_checked
        if result.match is not None:
            matches.append(result.match)

        if result.best_nonce is None or result.best_hash is None:
            all_best_hashes_present = False
        else:
            best_candidates.append(
                (
                    block_hash_to_int(result.best_hash),
                    result.best_nonce,
                    result.best_hash,
                )
            )

    if hashes_checked != stop_nonce - start_nonce:
        raise ComputeBackendExecutionError("multi-CUDA aggregate hash count is inconsistent")

    best_candidate = (
        min(best_candidates, key=lambda item: (item[0], item[1]))
        if all_best_hashes_present and best_candidates
        else None
    )

    return NonceSearchResult(
        start_nonce=start_nonce,
        stop_nonce=stop_nonce,
        hashes_checked=hashes_checked,
        elapsed_ns=elapsed_ns,
        match=min(matches, key=lambda item: item.nonce) if matches else None,
        best_nonce=None if best_candidate is None else best_candidate[1],
        best_hash=None if best_candidate is None else best_candidate[2],
    )
