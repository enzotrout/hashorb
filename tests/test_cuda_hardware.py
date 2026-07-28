"""Explicitly gated CUDA-device parity tests; excluded from normal test runs."""

from __future__ import annotations

import os
import random
from collections.abc import Iterator

import pytest

from hashphere.compute import CudaBackend, PythonSequentialBackend
from hashphere.mining import PreparedMiningWork, block_hash_to_int, hash_block_header

_CUDA_TEST_FLAG = "HASHPHERE_ENABLE_CUDA_TESTS"
_MAX_TARGET = (1 << 256) - 1
_PREFIX = bytes(range(76))

pytestmark = pytest.mark.skipif(
    os.getenv(_CUDA_TEST_FLAG) != "1",
    reason=f"set {_CUDA_TEST_FLAG}=1 to run CUDA hardware parity tests",
)


def prepared_work(
    *,
    header_prefix: bytes = _PREFIX,
    share_target: int = 1,
    network_target: int = 1,
) -> PreparedMiningWork:
    """Return deterministic synthetic work with caller-selected targets."""

    return PreparedMiningWork(
        job_id="synthetic-cuda-hardware-parity",
        extra_nonce_2="00000000",
        network_time="65f04abc",
        header_prefix=header_prefix,
        network_target=network_target,
        share_target=share_target,
    )


def digest_value(header_prefix: bytes, nonce: int) -> int:
    """Return the established little-endian proof-of-work integer."""

    header = header_prefix + nonce.to_bytes(4, "little")
    return block_hash_to_int(hash_block_header(header))


@pytest.fixture
def cuda_backend() -> Iterator[CudaBackend]:
    """Initialize the explicitly requested CUDA device and close it after each test."""

    device_text = os.getenv("HASHPHERE_CUDA_DEVICE", "0")
    if (
        not device_text
        or not device_text.isascii()
        or not device_text.isdecimal()
        or (len(device_text) > 1 and device_text.startswith("0"))
    ):
        pytest.fail("HASHPHERE_CUDA_DEVICE must be an unpadded ASCII decimal integer")
    backend = CudaBackend(int(device_text))
    if not backend.capabilities.available:
        pytest.skip("CUDA extension or requested CUDA device is unavailable")
    try:
        yield backend
    finally:
        backend.close()


def assert_cuda_python_match_parity(
    cuda_backend: CudaBackend,
    work: PreparedMiningWork,
    start_nonce: int,
    stop_nonce: int,
) -> None:
    """Compare candidate semantics while respecting full CUDA hash accounting."""

    python_result = PythonSequentialBackend().search_nonce_range(
        work,
        start_nonce,
        stop_nonce,
    )
    cuda_result = cuda_backend.search_nonce_range(work, start_nonce, stop_nonce)

    assert cuda_result.start_nonce == start_nonce
    assert cuda_result.stop_nonce == stop_nonce
    assert cuda_result.hashes_checked == stop_nonce - start_nonce
    assert cuda_result.match == python_result.match
    assert cuda_result.found is python_result.found
    assert cuda_result.exhausted is python_result.exhausted


@pytest.mark.parametrize(
    ("share_target", "network_target"),
    [
        (1, 1),
        (_MAX_TARGET, 1),
        (1, _MAX_TARGET),
        (_MAX_TARGET, _MAX_TARGET),
    ],
)
def test_cuda_has_exact_core_outcome_parity(
    cuda_backend: CudaBackend,
    share_target: int,
    network_target: int,
) -> None:
    assert_cuda_python_match_parity(
        cuda_backend,
        prepared_work(
            share_target=share_target,
            network_target=network_target,
        ),
        7,
        15,
    )


def test_cuda_finds_a_final_included_nonce(cuda_backend: CudaBackend) -> None:
    selected_prefix: bytes | None = None
    selected_target: int | None = None
    for prefix_marker in range(256):
        prefix = bytes([prefix_marker]) + _PREFIX[1:]
        values = tuple(digest_value(prefix, nonce) for nonce in range(7, 11))
        if values[-1] < min(values[:-1]):
            selected_prefix = prefix
            selected_target = values[-1]
            break
    if selected_prefix is None or selected_target is None:
        pytest.fail("deterministic final-nonce parity fixture could not be constructed")

    assert_cuda_python_match_parity(
        cuda_backend,
        prepared_work(
            header_prefix=selected_prefix,
            share_target=selected_target,
        ),
        7,
        11,
    )


def test_cuda_range_can_end_exactly_at_2_to_32(cuda_backend: CudaBackend) -> None:
    final_nonce = 0xFFFFFFFF
    assert_cuda_python_match_parity(
        cuda_backend,
        prepared_work(share_target=_MAX_TARGET),
        final_nonce,
        2**32,
    )


def test_cuda_has_fixed_seed_randomized_small_range_parity(
    cuda_backend: CudaBackend,
) -> None:
    generator = random.Random(0x4355444148415348)
    for _ in range(40):
        start_nonce = generator.randrange(0, 10_000)
        stop_nonce = start_nonce + generator.randrange(1, 17)
        assert_cuda_python_match_parity(
            cuda_backend,
            prepared_work(
                header_prefix=generator.randbytes(76),
                share_target=generator.randrange(1, _MAX_TARGET + 1),
                network_target=generator.randrange(1, _MAX_TARGET + 1),
            ),
            start_nonce,
            stop_nonce,
        )
