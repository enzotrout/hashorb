"""Host-only correctness tests for the optional verified CUDA backend."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

import hashphere.compute.cuda as cuda_module
from hashphere.compute import (
    ComputeBackendCapabilities,
    ComputeBackendExecutionError,
    ComputeBackendValidationError,
    CudaBackend,
    cuda_grid_stride_offsets,
)
from hashphere.mining import PreparedMiningWork, block_hash_to_int, hash_block_header

_MAX_TARGET = (1 << 256) - 1
_PREFIX = bytes(range(76))


def prepared_work(
    *,
    header_prefix: bytes = _PREFIX,
    share_target: int = 1,
    network_target: int = 1,
) -> PreparedMiningWork:
    """Return deterministic immutable synthetic work."""

    return PreparedMiningWork(
        job_id="synthetic-cuda-correctness",
        extra_nonce_2="00000000",
        network_time="65f04abc",
        header_prefix=header_prefix,
        network_target=network_target,
        share_target=share_target,
    )


def digest_for_nonce(header_prefix: bytes, nonce: int) -> bytes:
    """Return the established Python digest for one synthetic nonce."""

    return hash_block_header(header_prefix + nonce.to_bytes(4, "little"))


class FakeCudaRuntime:
    """Deterministic extension-shaped boundary without CUDA initialization."""

    def __init__(
        self,
        result: object = (None, False, False, 1),
        *,
        initialize_failure: Exception | None = None,
        search_failure: Exception | None = None,
        close_failure: Exception | None = None,
    ) -> None:
        self.result = result
        self.initialize_failure = initialize_failure
        self.search_failure = search_failure
        self.close_failure = close_failure
        self.initialize_calls: list[int] = []
        self.search_calls: list[tuple[bytes, bytes, bytes, int, int]] = []
        self.close_calls = 0

    def initialize_device(self, device_ordinal: int) -> None:
        self.initialize_calls.append(device_ordinal)
        if self.initialize_failure is not None:
            raise self.initialize_failure

    def search_nonce_range(
        self,
        header_prefix: bytes,
        share_target: bytes,
        network_target: bytes,
        start_nonce: int,
        stop_nonce: int,
    ) -> object:
        self.search_calls.append(
            (header_prefix, share_target, network_target, start_nonce, stop_nonce)
        )
        if self.search_failure is not None:
            raise self.search_failure
        return self.result

    def close_device(self) -> None:
        self.close_calls += 1
        if self.close_failure is not None:
            raise self.close_failure


class ReferenceCudaRuntime(FakeCudaRuntime):
    """Small host fake that models full-range deterministic CUDA reduction."""

    def search_nonce_range(
        self,
        header_prefix: bytes,
        share_target: bytes,
        network_target: bytes,
        start_nonce: int,
        stop_nonce: int,
    ) -> object:
        self.search_calls.append(
            (header_prefix, share_target, network_target, start_nonce, stop_nonce)
        )
        share_value = int.from_bytes(share_target, "little")
        network_value = int.from_bytes(network_target, "little")
        candidate: tuple[int, bool, bool] | None = None
        for nonce in range(start_nonce, stop_nonce):
            value = block_hash_to_int(digest_for_nonce(header_prefix, nonce))
            meets_share = value <= share_value
            meets_network = value <= network_value
            if candidate is None and (meets_share or meets_network):
                candidate = (nonce, meets_share, meets_network)
        if candidate is None:
            return (None, False, False, stop_nonce - start_nonce)
        return (*candidate, stop_nonce - start_nonce)


def ticking_clock(values: tuple[object, object] = (100, 125)) -> Iterator[object]:
    """Yield two deterministic clock values."""

    return iter(values)


def test_cuda_capabilities_are_exact_immutable_and_device_is_safe() -> None:
    runtime = FakeCudaRuntime()
    backend = CudaBackend(3, runtime)

    assert backend.device_ordinal == 3
    assert backend.capabilities == ComputeBackendCapabilities(
        backend_name="cuda",
        display_name="Optional CUDA correctness backend",
        backend_kind="gpu",
        implementation="cuda",
        supports_parallel_search=True,
        supports_cooperative_cancellation=False,
        supports_device_selection=True,
        deterministic_search_order=True,
        preferred_batch_size=None,
        available=True,
    )
    assert runtime.initialize_calls == [3]
    with pytest.raises(FrozenInstanceError):
        backend.capabilities.available = False  # type: ignore[misc]


def test_uninitialized_cuda_backend_never_imports_or_initializes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cuda_module,
        "import_module",
        lambda name: pytest.fail(f"unexpected import: {name}"),
    )

    backend = CudaBackend(initialize=False)

    assert backend.capabilities.available is False
    assert backend.capabilities.unavailable_reason == "NotInitialized"


@pytest.mark.parametrize(
    ("runtime", "expected_reason"),
    [
        (None, "ExtensionNotInstalled"),
        (object(), "ExtensionInvalid"),
        (
            FakeCudaRuntime(initialize_failure=RuntimeError("private device detail")),
            "DeviceUnavailable",
        ),
    ],
)
def test_cuda_unavailability_is_controlled(
    runtime: object,
    expected_reason: str,
) -> None:
    backend = CudaBackend(runtime=runtime)

    assert backend.capabilities.available is False
    assert backend.capabilities.unavailable_reason == expected_reason
    assert "private" not in str(backend.capabilities)
    with pytest.raises(ComputeBackendExecutionError, match="unavailable"):
        backend.search_nonce_range(prepared_work(), 0, 1)


@pytest.mark.parametrize(
    ("failure", "expected_reason"),
    [
        (ImportError("private path"), "ExtensionNotInstalled"),
        (RuntimeError("private loader detail"), "ExtensionImportFailed"),
    ],
)
def test_cuda_import_failures_are_sanitized(
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
    expected_reason: str,
) -> None:
    def fail_import(name: str) -> object:
        del name
        raise failure

    monkeypatch.setattr(cuda_module, "import_module", fail_import)
    assert CudaBackend().capabilities.unavailable_reason == expected_reason


@pytest.mark.parametrize(
    "device",
    [True, False, -1, 2**31, "0", 0.0, Decimal("0"), None],
)
def test_cuda_device_rejects_invalid_values(device: object) -> None:
    with pytest.raises(ComputeBackendValidationError):
        CudaBackend(device, FakeCudaRuntime())  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("work", "start_nonce", "stop_nonce"),
    [
        (object(), 0, 1),
        (prepared_work(), True, 1),
        (prepared_work(), 0, False),
        (prepared_work(), -1, 1),
        (prepared_work(), 1, 1),
        (prepared_work(), 0, 2**32 + 1),
    ],
)
def test_cuda_backend_rejects_invalid_search_inputs(
    work: object,
    start_nonce: object,
    stop_nonce: object,
) -> None:
    with pytest.raises(ComputeBackendValidationError):
        CudaBackend(runtime=FakeCudaRuntime()).search_nonce_range(
            work,  # type: ignore[arg-type]
            start_nonce,  # type: ignore[arg-type]
            stop_nonce,  # type: ignore[arg-type]
        )


def test_cuda_passes_exact_half_open_range_targets_and_header() -> None:
    runtime = FakeCudaRuntime((None, False, False, 3))
    work = prepared_work(share_target=0x1234, network_target=0x5678)
    result = CudaBackend(2, runtime).search_nonce_range(work, 7, 10)

    assert result == result.__class__(7, 10, 3, result.elapsed_ns, None)
    assert runtime.search_calls == [
        (
            work.header_prefix,
            work.share_target.to_bytes(32, "little"),
            work.network_target.to_bytes(32, "little"),
            7,
            10,
        )
    ]


@pytest.mark.parametrize(
    ("start_nonce", "stop_nonce"),
    [(0, 1), (17, 18), (0xFFFFFFFF, 2**32)],
)
def test_cuda_exact_boundaries_and_full_hash_accounting(
    start_nonce: int,
    stop_nonce: int,
) -> None:
    runtime = FakeCudaRuntime((None, False, False, stop_nonce - start_nonce))

    result = CudaBackend(runtime=runtime).search_nonce_range(
        prepared_work(),
        start_nonce,
        stop_nonce,
    )

    assert result.hashes_checked == stop_nonce - start_nonce
    assert result.exhausted is True


@pytest.mark.parametrize(
    ("share_target", "network_target", "expected_flags"),
    [
        (_MAX_TARGET, 1, (True, False)),
        (1, _MAX_TARGET, (False, True)),
        (_MAX_TARGET, _MAX_TARGET, (True, True)),
    ],
)
def test_cuda_candidate_is_reconstructed_and_verified_in_python(
    share_target: int,
    network_target: int,
    expected_flags: tuple[bool, bool],
) -> None:
    runtime = FakeCudaRuntime((7, *expected_flags, 3))
    work = prepared_work(share_target=share_target, network_target=network_target)

    result = CudaBackend(runtime=runtime).search_nonce_range(work, 7, 10)

    assert result.hashes_checked == 3
    assert result.match is not None
    assert result.match.nonce == 7
    assert result.match.block_hash == digest_for_nonce(work.header_prefix, 7)
    assert (result.match.meets_share_target, result.match.meets_network_target) == (expected_flags)


def test_reference_cuda_reduction_returns_smallest_of_multiple_candidates() -> None:
    result = CudaBackend(runtime=ReferenceCudaRuntime()).search_nonce_range(
        prepared_work(share_target=_MAX_TARGET, network_target=_MAX_TARGET),
        20,
        28,
    )

    assert result.match is not None
    assert result.match.nonce == 20
    assert result.hashes_checked == 8


@pytest.mark.parametrize(
    "cuda_result",
    [
        None,
        [],
        (None,),
        (None, False, False, 0),
        (None, True, False, 1),
        (None, 0, False, 1),
        (None, False, False, True),
        (True, True, False, 1),
        (-1, True, False, 1),
        (1, True, False, 1),
        (0, False, False, 1),
    ],
)
def test_cuda_backend_rejects_malformed_extension_results(cuda_result: object) -> None:
    with pytest.raises(ComputeBackendExecutionError):
        CudaBackend(runtime=FakeCudaRuntime(cuda_result)).search_nonce_range(
            prepared_work(share_target=_MAX_TARGET),
            0,
            1,
        )


def test_cuda_backend_rejects_target_flag_mismatch() -> None:
    runtime = FakeCudaRuntime((0, False, True, 1))

    with pytest.raises(ComputeBackendExecutionError, match="target verification"):
        CudaBackend(runtime=runtime).search_nonce_range(
            prepared_work(share_target=_MAX_TARGET, network_target=1),
            0,
            1,
        )


def test_cuda_extension_exception_is_sanitized_and_not_exhaustion() -> None:
    runtime = FakeCudaRuntime(search_failure=RuntimeError("private kernel failure"))

    with pytest.raises(ComputeBackendExecutionError) as raised:
        CudaBackend(runtime=runtime).search_nonce_range(prepared_work(), 0, 1)

    assert "private kernel failure" not in str(raised.value)


def test_cuda_clock_is_complete_clamped_and_strict() -> None:
    ticks = ticking_clock((125, 100))
    result = CudaBackend(
        runtime=FakeCudaRuntime((None, False, False, 1)),
        clock=lambda: next(ticks),
    ).search_nonce_range(prepared_work(), 0, 1)
    assert result.elapsed_ns == 0

    invalid_ticks = ticking_clock((True, 1))
    with pytest.raises(ComputeBackendExecutionError, match="clock"):
        CudaBackend(
            runtime=FakeCudaRuntime((None, False, False, 1)),
            clock=lambda: next(invalid_ticks),
        ).search_nonce_range(prepared_work(), 0, 1)


def test_cuda_close_is_idempotent_and_search_after_close_fails() -> None:
    runtime = FakeCudaRuntime()
    backend = CudaBackend(runtime=runtime)

    backend.close()
    backend.close()

    assert runtime.close_calls == 1
    with pytest.raises(ComputeBackendExecutionError, match="closed"):
        backend.search_nonce_range(prepared_work(), 0, 1)


def test_cuda_cleanup_failure_is_sanitized() -> None:
    backend = CudaBackend(
        runtime=FakeCudaRuntime(close_failure=RuntimeError("private CUDA detail"))
    )

    with pytest.raises(ComputeBackendExecutionError) as raised:
        backend.close()

    assert "private CUDA detail" not in str(raised.value)
    backend.close()


@pytest.mark.parametrize(
    "arguments",
    [
        (-1, 1, 1),
        (2**32 + 1, 1, 1),
        (1, 0, 1),
        (1, 1, 0),
        (True, 1, 1),
        (1, False, 1),
        (1, 1, "1"),
    ],
)
def test_grid_stride_mapping_rejects_invalid_inputs(
    arguments: tuple[object, object, object],
) -> None:
    with pytest.raises(ComputeBackendValidationError):
        cuda_grid_stride_offsets(*arguments)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("range_size", "block_count", "threads_per_block"),
    [(0, 1, 1), (1, 1, 1), (7, 1, 4), (17, 2, 3), (64, 4, 8)],
)
def test_grid_stride_mapping_is_complete_unique_bounded_and_deterministic(
    range_size: int,
    block_count: int,
    threads_per_block: int,
) -> None:
    first = cuda_grid_stride_offsets(range_size, block_count, threads_per_block)
    second = cuda_grid_stride_offsets(range_size, block_count, threads_per_block)
    flattened = tuple(offset for lane in first for offset in lane)

    assert first == second
    assert len(flattened) == range_size
    assert len(set(flattened)) == range_size
    assert set(flattened) == set(range(range_size))
    assert all(0 <= offset < range_size for offset in flattened)
    stride = block_count * threads_per_block
    assert all(
        all(
            next_offset - offset == stride
            for offset, next_offset in zip(lane, lane[1:], strict=False)
        )
        for lane in first
    )


def test_cuda_backend_does_not_mutate_or_retain_work() -> None:
    work = prepared_work()
    before = prepared_work()
    backend = CudaBackend(runtime=FakeCudaRuntime((None, False, False, 2)))

    backend.search_nonce_range(work, 0, 2)

    assert work == before
    assert not hasattr(backend, "work")
