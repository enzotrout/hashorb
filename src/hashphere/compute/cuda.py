"""Verified wrapper and host utilities for the optional CUDA extension."""

from __future__ import annotations

from collections.abc import Callable
from importlib import import_module
from time import perf_counter_ns
from typing import cast

from hashphere.compute.backend import (
    ComputeBackendCapabilities,
    ComputeBackendExecutionError,
    ComputeBackendValidationError,
)
from hashphere.config import DEFAULT_CUDA_DEVICE, MAX_CUDA_DEVICE
from hashphere.mining.header import hash_block_header
from hashphere.mining.search import (
    NonceSearchMatch,
    NonceSearchResult,
    PreparedMiningWork,
    _validate_nonce_range,
)
from hashphere.mining.target import hash_meets_target

type CudaInitializer = Callable[[int], object]
type CudaSearcher = Callable[[bytes, bytes, bytes, int, int], object]
type CudaCloser = Callable[[], object]
type MonotonicClock = Callable[[], int]

_AUTO_LOAD = object()
_TARGET_BYTE_LENGTH = 32
_NONCE_BYTE_LENGTH = 4
_NONCE_LIMIT = 1 << 32


def cuda_grid_stride_offsets(
    range_size: int,
    block_count: int,
    threads_per_block: int,
) -> tuple[tuple[int, ...], ...]:
    """Return deterministic logical offsets assigned to each synthetic CUDA lane."""

    parsed_size = _validate_nonnegative_integer(range_size, "range_size")
    parsed_blocks = _validate_positive_integer(block_count, "block_count")
    parsed_threads = _validate_positive_integer(threads_per_block, "threads_per_block")
    if parsed_size > _NONCE_LIMIT:
        raise ComputeBackendValidationError("range_size must not exceed 2**32")
    lane_count = parsed_blocks * parsed_threads
    if lane_count > _NONCE_LIMIT:
        raise ComputeBackendValidationError("CUDA lane count must not exceed 2**32")
    return tuple(
        tuple(range(lane_index, parsed_size, lane_count)) for lane_index in range(lane_count)
    )


class CudaBackend:
    """Search through optional CUDA and verify every reported candidate in Python."""

    __slots__ = (
        "_clock",
        "_closed",
        "_closer",
        "_device_ordinal",
        "_searcher",
        "capabilities",
    )

    def __init__(
        self,
        device_ordinal: int = DEFAULT_CUDA_DEVICE,
        runtime: object = _AUTO_LOAD,
        *,
        initialize: bool = True,
        clock: MonotonicClock = perf_counter_ns,
    ) -> None:
        """Load and initialize one device, or create a controlled unavailable backend."""

        self._device_ordinal = _validate_cuda_device(device_ordinal)
        if not isinstance(initialize, bool):
            raise ComputeBackendValidationError("initialize must be a Boolean")
        if not callable(clock):
            raise ComputeBackendValidationError("clock must be callable")
        self._clock = clock
        self._closed = False
        self._searcher: CudaSearcher | None = None
        self._closer: CudaCloser | None = None

        unavailable_reason: str | None
        if not initialize:
            unavailable_reason = "NotInitialized"
        else:
            unavailable_reason = self._initialize_runtime(runtime)
        self.capabilities = _capabilities(unavailable_reason)

    @property
    def device_ordinal(self) -> int:
        """Return the configured nonnegative CUDA device ordinal."""

        return self._device_ordinal

    def search_nonce_range(
        self,
        work: PreparedMiningWork,
        start_nonce: int,
        stop_nonce: int,
    ) -> NonceSearchResult:
        """Search one exact parent range and return only a Python-verified result."""

        if not isinstance(work, PreparedMiningWork):
            raise ComputeBackendValidationError("work must be PreparedMiningWork")
        try:
            _validate_nonce_range(start_nonce, stop_nonce)
        except (TypeError, ValueError) as exc:
            raise ComputeBackendValidationError("CUDA search range is invalid") from exc
        if self._closed:
            raise ComputeBackendExecutionError("CUDA compute backend is closed")
        if self._searcher is None:
            raise ComputeBackendExecutionError("CUDA compute backend is unavailable")

        share_target = work.share_target.to_bytes(
            _TARGET_BYTE_LENGTH,
            byteorder="little",
            signed=False,
        )
        network_target = work.network_target.to_bytes(
            _TARGET_BYTE_LENGTH,
            byteorder="little",
            signed=False,
        )
        try:
            started_ns = self._clock()
            cuda_result = self._searcher(
                work.header_prefix,
                share_target,
                network_target,
                start_nonce,
                stop_nonce,
            )
            finished_ns = self._clock()
        except Exception as exc:
            raise ComputeBackendExecutionError("CUDA compute backend search failed") from exc

        return _validated_cuda_result(
            work,
            start_nonce,
            stop_nonce,
            _elapsed_nanoseconds(started_ns, finished_ns),
            cuda_result,
        )

    def close(self) -> None:
        """Synchronize backend-owned work once without resetting unrelated CUDA state."""

        if self._closed:
            return
        self._closed = True
        closer = self._closer
        self._closer = None
        self._searcher = None
        if closer is None:
            return
        try:
            closer()
        except Exception as exc:
            raise ComputeBackendExecutionError("CUDA compute backend cleanup failed") from exc

    def _initialize_runtime(self, runtime: object) -> str | None:
        selected_runtime = runtime
        if selected_runtime is _AUTO_LOAD:
            try:
                selected_runtime = import_module("hashphere.compute._cuda")
            except ImportError:
                return "ExtensionNotInstalled"
            except Exception:
                return "ExtensionImportFailed"
        elif selected_runtime is None:
            return "ExtensionNotInstalled"

        initializer = getattr(selected_runtime, "initialize_device", None)
        searcher = getattr(selected_runtime, "search_nonce_range", None)
        closer = getattr(selected_runtime, "close_device", None)
        if not callable(initializer) or not callable(searcher) or not callable(closer):
            return "ExtensionInvalid"
        selected_closer = cast(CudaCloser, closer)
        try:
            initialized = cast(CudaInitializer, initializer)(self.device_ordinal)
        except Exception:
            try:
                selected_closer()
            except Exception:
                pass
            return "DeviceUnavailable"
        if initialized is not None:
            try:
                selected_closer()
            except Exception:
                pass
            return "ExtensionInvalid"
        self._searcher = cast(CudaSearcher, searcher)
        self._closer = selected_closer
        return None


def _capabilities(unavailable_reason: str | None) -> ComputeBackendCapabilities:
    return ComputeBackendCapabilities(
        backend_name="cuda",
        display_name="Optional CUDA correctness backend",
        backend_kind="gpu",
        implementation="cuda",
        supports_parallel_search=True,
        supports_cooperative_cancellation=False,
        supports_device_selection=True,
        deterministic_search_order=True,
        preferred_batch_size=None,
        available=unavailable_reason is None,
        unavailable_reason=unavailable_reason,
    )


def _validated_cuda_result(
    work: PreparedMiningWork,
    start_nonce: int,
    stop_nonce: int,
    elapsed_ns: int,
    cuda_result: object,
) -> NonceSearchResult:
    if not isinstance(cuda_result, tuple) or len(cuda_result) != 4:
        raise ComputeBackendExecutionError("CUDA compute backend returned invalid data")
    nonce, meets_share, meets_network, hashes_checked = cuda_result
    if not isinstance(meets_share, bool) or not isinstance(meets_network, bool):
        raise ComputeBackendExecutionError("CUDA compute backend returned invalid flags")
    if isinstance(hashes_checked, bool) or not isinstance(hashes_checked, int):
        raise ComputeBackendExecutionError("CUDA compute backend returned an invalid count")
    if hashes_checked != stop_nonce - start_nonce:
        raise ComputeBackendExecutionError("CUDA compute hash count is inconsistent")

    if nonce is None:
        if meets_share or meets_network:
            raise ComputeBackendExecutionError("CUDA exhausted result is inconsistent")
        return NonceSearchResult(
            start_nonce=start_nonce,
            stop_nonce=stop_nonce,
            hashes_checked=hashes_checked,
            elapsed_ns=elapsed_ns,
            match=None,
        )

    if isinstance(nonce, bool) or not isinstance(nonce, int):
        raise ComputeBackendExecutionError("CUDA compute backend returned an invalid nonce")
    if not start_nonce <= nonce < stop_nonce:
        raise ComputeBackendExecutionError("CUDA candidate nonce is outside the range")
    if not meets_share and not meets_network:
        raise ComputeBackendExecutionError("CUDA candidate has no matching target")

    header = work.header_prefix + nonce.to_bytes(
        _NONCE_BYTE_LENGTH,
        byteorder="little",
        signed=False,
    )
    verified_digest = hash_block_header(header)
    verified_share = hash_meets_target(verified_digest, work.share_target)
    verified_network = hash_meets_target(verified_digest, work.network_target)
    if meets_share is not verified_share or meets_network is not verified_network:
        raise ComputeBackendExecutionError("CUDA candidate target verification failed")
    if not verified_share and not verified_network:
        raise ComputeBackendExecutionError("CUDA candidate verification failed")

    return NonceSearchResult(
        start_nonce=start_nonce,
        stop_nonce=stop_nonce,
        hashes_checked=hashes_checked,
        elapsed_ns=elapsed_ns,
        match=NonceSearchMatch(
            nonce=nonce,
            block_hash=verified_digest,
            meets_share_target=verified_share,
            meets_network_target=verified_network,
        ),
    )


def _elapsed_nanoseconds(started_ns: object, finished_ns: object) -> int:
    if (
        isinstance(started_ns, bool)
        or not isinstance(started_ns, int)
        or isinstance(finished_ns, bool)
        or not isinstance(finished_ns, int)
    ):
        raise ComputeBackendExecutionError("CUDA compute clock returned invalid data")
    return max(0, finished_ns - started_ns)


def _validate_cuda_device(value: object) -> int:
    parsed = _validate_nonnegative_integer(value, "device_ordinal")
    if parsed > MAX_CUDA_DEVICE:
        raise ComputeBackendValidationError(f"device_ordinal must not exceed {MAX_CUDA_DEVICE}")
    return parsed


def _validate_nonnegative_integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ComputeBackendValidationError(f"{name} must be an integer")
    if value < 0:
        raise ComputeBackendValidationError(f"{name} must be nonnegative")
    return value


def _validate_positive_integer(value: object, name: str) -> int:
    parsed = _validate_nonnegative_integer(value, name)
    if parsed == 0:
        raise ComputeBackendValidationError(f"{name} must be positive")
    return parsed
