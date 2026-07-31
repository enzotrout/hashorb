"""Explicitly gated parity gate requiring at least two real CUDA devices."""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from hashorb.compute import CudaMultiBackend, PythonSequentialBackend
from hashorb.config import parse_cuda_devices
from hashorb.mining import PreparedMiningWork

_MULTI_CUDA_TEST_FLAG = "HASHORB_ENABLE_MULTI_CUDA_TESTS"
_MAX_TARGET = (1 << 256) - 1

pytestmark = pytest.mark.skipif(
    os.getenv(_MULTI_CUDA_TEST_FLAG) != "1",
    reason=f"set {_MULTI_CUDA_TEST_FLAG}=1 on a host with at least two CUDA devices",
)


def prepared_work(*, share_target: int = 1, network_target: int = 1) -> PreparedMiningWork:
    return PreparedMiningWork(
        job_id="synthetic-multi-cuda-hardware-parity",
        extra_nonce_2="00000000",
        network_time="65f04abc",
        header_prefix=bytes(range(76)),
        network_target=network_target,
        share_target=share_target,
    )


@pytest.fixture
def multi_cuda_backend() -> Iterator[CudaMultiBackend]:
    try:
        ordinals = parse_cuda_devices(os.getenv("HASHORB_CUDA_DEVICES", ""))
    except ValueError as exc:
        pytest.fail(str(exc))
    if len(ordinals) < 2:
        pytest.fail("the real multi-CUDA gate requires at least two unique devices")
    backend = CudaMultiBackend(ordinals)
    if not backend.capabilities.available:
        pytest.fail("every explicitly selected CUDA device must initialize")
    try:
        yield backend
    finally:
        backend.close()


@pytest.mark.parametrize(
    ("share_target", "network_target"),
    [(1, 1), (_MAX_TARGET, 1), (1, _MAX_TARGET), (_MAX_TARGET, _MAX_TARGET)],
)
def test_real_multi_cuda_matches_python_across_distinct_child_ranges(
    multi_cuda_backend: CudaMultiBackend,
    share_target: int,
    network_target: int,
) -> None:
    work = prepared_work(share_target=share_target, network_target=network_target)
    python_result = PythonSequentialBackend().search_nonce_range(work, 101, 149)
    cuda_result = multi_cuda_backend.search_nonce_range(work, 101, 149)

    assert multi_cuda_backend.device_count >= 2
    assert cuda_result.start_nonce == 101
    assert cuda_result.stop_nonce == 149
    assert cuda_result.hashes_checked == 48
    assert cuda_result.match == python_result.match


def test_real_multi_cuda_preserves_the_unsigned_nonce_boundary(
    multi_cuda_backend: CudaMultiBackend,
) -> None:
    work = prepared_work()
    python_result = PythonSequentialBackend().search_nonce_range(work, 2**32 - 17, 2**32)
    cuda_result = multi_cuda_backend.search_nonce_range(work, 2**32 - 17, 2**32)

    assert cuda_result.hashes_checked == 17
    assert cuda_result.match == python_result.match
