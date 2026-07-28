"""Deterministic partitioning and portable native parallel CPU execution."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Executor, Future, ThreadPoolExecutor
from dataclasses import dataclass
from threading import Lock
from time import perf_counter_ns

from hashphere.compute.backend import (
    ComputeBackendCapabilities,
    ComputeBackendExecutionError,
    ComputeBackendValidationError,
)
from hashphere.compute.native import NativeSequentialBackend
from hashphere.mining.search import NonceSearchResult, PreparedMiningWork, _validate_nonce_range

DEFAULT_COMPUTE_WORKERS = 2
MAX_COMPUTE_WORKERS = 256

type ExecutorFactory = Callable[[int], Executor]
type MonotonicClock = Callable[[], int]


@dataclass(frozen=True, slots=True)
class NonceRangeAssignment:
    """One immutable nonempty half-open worker nonce range."""

    start_nonce: int
    stop_nonce: int

    def __post_init__(self) -> None:
        """Validate direct construction through the established nonce boundary."""

        _validate_range(self.start_nonce, self.stop_nonce)

    @property
    def size(self) -> int:
        """Return the exact number of nonces in this assignment."""

        return self.stop_nonce - self.start_nonce


def partition_nonce_range(
    start_nonce: int,
    stop_nonce: int,
    worker_count: int,
) -> tuple[NonceRangeAssignment, ...]:
    """Partition one half-open nonce range into balanced ascending assignments."""

    _validate_range(start_nonce, stop_nonce)
    _validate_worker_count(worker_count)

    range_size = stop_nonce - start_nonce
    assignment_count = min(worker_count, range_size)
    base_size, larger_assignments = divmod(range_size, assignment_count)
    assignments: list[NonceRangeAssignment] = []
    assignment_start = start_nonce
    for index in range(assignment_count):
        assignment_size = base_size + (1 if index < larger_assignments else 0)
        assignment_stop = assignment_start + assignment_size
        assignments.append(NonceRangeAssignment(assignment_start, assignment_stop))
        assignment_start = assignment_stop

    return tuple(assignments)


class NativeParallelBackend:
    """Search deterministic assignments concurrently through verified native workers."""

    __slots__ = (
        "_broken",
        "_clock",
        "_closed",
        "_executor",
        "_executor_factory",
        "_operation_lock",
        "_worker_backend",
        "capabilities",
        "worker_count",
    )

    def __init__(
        self,
        worker_count: int = DEFAULT_COMPUTE_WORKERS,
        native_backend: NativeSequentialBackend | None = None,
        *,
        executor_factory: ExecutorFactory | None = None,
        clock: MonotonicClock = perf_counter_ns,
    ) -> None:
        """Create one lazily started worker pool for an invocation-local backend."""

        _validate_worker_count(worker_count)
        selected_native = NativeSequentialBackend() if native_backend is None else native_backend
        if not isinstance(selected_native, NativeSequentialBackend):
            raise ComputeBackendValidationError("native_backend must be NativeSequentialBackend")
        if executor_factory is not None and not callable(executor_factory):
            raise ComputeBackendValidationError("executor_factory must be callable or None")
        if not callable(clock):
            raise ComputeBackendValidationError("clock must be callable")

        self.worker_count = worker_count
        self._worker_backend = selected_native
        self._executor_factory = executor_factory or _create_executor
        self._clock = clock
        self._executor: Executor | None = None
        self._operation_lock = Lock()
        self._closed = False
        self._broken = False
        native_capabilities = selected_native.capabilities
        self.capabilities = ComputeBackendCapabilities(
            backend_name="native-parallel",
            display_name="Portable native C thread-pool",
            backend_kind="cpu",
            implementation="c-threadpool",
            supports_parallel_search=True,
            supports_cooperative_cancellation=False,
            supports_device_selection=False,
            deterministic_search_order=True,
            preferred_batch_size=None,
            available=native_capabilities.available,
            unavailable_reason=native_capabilities.unavailable_reason,
        )

    def search_nonce_range(
        self,
        work: PreparedMiningWork,
        start_nonce: int,
        stop_nonce: int,
    ) -> NonceSearchResult:
        """Search one parent range and reduce every completed assignment deterministically."""

        if not isinstance(work, PreparedMiningWork):
            raise ComputeBackendValidationError("work must be PreparedMiningWork")
        assignments = partition_nonce_range(start_nonce, stop_nonce, self.worker_count)

        with self._operation_lock:
            if self._closed:
                raise ComputeBackendExecutionError("parallel compute backend is closed")
            if self._broken:
                raise ComputeBackendExecutionError("parallel compute backend is unavailable")
            if not self.capabilities.available:
                raise ComputeBackendExecutionError("parallel compute backend is unavailable")

            executor = self._executor
            futures: tuple[Future[NonceSearchResult], ...] = ()
            try:
                if executor is None:
                    executor = self._executor_factory(self.worker_count)
                    if not isinstance(executor, Executor):
                        raise TypeError("executor factory returned invalid data")
                    self._executor = executor
                started_ns = self._clock()
                futures = self._submit_assignments(executor, work, assignments)
                results = tuple(future.result() for future in futures)
                finished_ns = self._clock()
                elapsed_ns = _elapsed_nanoseconds(started_ns, finished_ns)
                return _reduce_worker_results(
                    start_nonce,
                    stop_nonce,
                    assignments,
                    results,
                    elapsed_ns,
                )
            except BaseException as exc:
                self._break_executor(futures)
                if isinstance(exc, Exception):
                    raise ComputeBackendExecutionError(
                        "parallel compute backend search failed"
                    ) from exc
                raise

    def _submit_assignments(
        self,
        executor: Executor,
        work: PreparedMiningWork,
        assignments: tuple[NonceRangeAssignment, ...],
    ) -> tuple[Future[NonceSearchResult], ...]:
        futures: list[Future[NonceSearchResult]] = []
        try:
            for assignment in assignments:
                futures.append(
                    executor.submit(
                        self._worker_backend.search_nonce_range,
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

    def _break_executor(self, futures: tuple[Future[NonceSearchResult], ...]) -> None:
        for future in futures:
            future.cancel()
        self._broken = True
        executor = self._executor
        self._executor = None
        if executor is not None:
            try:
                executor.shutdown(wait=True, cancel_futures=True)
            except BaseException:
                pass

    def close(self) -> None:
        """Shut down the persistent executor exactly once; repeated calls are safe."""

        with self._operation_lock:
            if self._closed:
                return
            self._closed = True
            executor = self._executor
            self._executor = None
            if executor is None:
                return
            try:
                executor.shutdown(wait=True, cancel_futures=True)
            except Exception as exc:
                raise ComputeBackendExecutionError(
                    "parallel compute backend cleanup failed"
                ) from exc


def _create_executor(worker_count: int) -> Executor:
    return ThreadPoolExecutor(
        max_workers=worker_count,
        thread_name_prefix="hashphere-native",
    )


def _elapsed_nanoseconds(started_ns: object, finished_ns: object) -> int:
    if (
        isinstance(started_ns, bool)
        or not isinstance(started_ns, int)
        or isinstance(finished_ns, bool)
        or not isinstance(finished_ns, int)
    ):
        raise ComputeBackendExecutionError("parallel compute clock returned invalid data")
    return max(0, finished_ns - started_ns)


def _reduce_worker_results(
    start_nonce: int,
    stop_nonce: int,
    assignments: tuple[NonceRangeAssignment, ...],
    results: tuple[object, ...],
    elapsed_ns: int,
) -> NonceSearchResult:
    if len(results) != len(assignments):
        raise ComputeBackendExecutionError("parallel compute result count is invalid")

    hashes_checked = 0
    matches = []
    for assignment, result in zip(assignments, results, strict=True):
        if not isinstance(result, NonceSearchResult):
            raise ComputeBackendExecutionError("parallel worker returned invalid data")
        if (
            result.start_nonce != assignment.start_nonce
            or result.stop_nonce != assignment.stop_nonce
        ):
            raise ComputeBackendExecutionError("parallel worker returned a different range")
        hashes_checked += result.hashes_checked
        if result.match is not None:
            matches.append(result.match)

    range_size = stop_nonce - start_nonce
    if not 1 <= hashes_checked <= range_size:
        raise ComputeBackendExecutionError("parallel hash count is invalid")
    match = min(matches, key=lambda item: item.nonce) if matches else None
    if match is None and hashes_checked != range_size:
        raise ComputeBackendExecutionError("parallel exhausted result is incomplete")
    return NonceSearchResult(
        start_nonce=start_nonce,
        stop_nonce=stop_nonce,
        hashes_checked=hashes_checked,
        elapsed_ns=elapsed_ns,
        match=match,
    )


def _validate_range(start_nonce: object, stop_nonce: object) -> None:
    try:
        _validate_nonce_range(start_nonce, stop_nonce)
    except (TypeError, ValueError) as exc:
        raise ComputeBackendValidationError("parallel nonce range is invalid") from exc


def _validate_worker_count(worker_count: object) -> None:
    if isinstance(worker_count, bool) or not isinstance(worker_count, int):
        raise ComputeBackendValidationError("worker_count must be an integer")
    if not 1 <= worker_count <= MAX_COMPUTE_WORKERS:
        raise ComputeBackendValidationError(
            f"worker_count must be between 1 and {MAX_COMPUTE_WORKERS}"
        )
