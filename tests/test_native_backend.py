"""Parity and verification tests for the portable native CPU backend."""

from __future__ import annotations

import random
from collections.abc import Iterator

import pytest

import hashphere.compute.native as native_module
from hashphere.compute import (
    ComputeBackendCapabilities,
    ComputeBackendExecutionError,
    ComputeBackendValidationError,
    NativeSequentialBackend,
    PythonSequentialBackend,
)
from hashphere.mining import (
    PreparedMiningWork,
    block_hash_to_int,
    hash_block_header,
)

_MAX_TARGET = 2**256 - 1
_PREFIX = bytes(range(76))


def prepared_work(
    *,
    header_prefix: bytes = _PREFIX,
    share_target: int = 1,
    network_target: int = 1,
) -> PreparedMiningWork:
    """Build synthetic immutable work with caller-selected target thresholds."""

    return PreparedMiningWork(
        job_id="synthetic-native-parity",
        extra_nonce_2="00000000",
        network_time="65f04abc",
        header_prefix=header_prefix,
        network_target=network_target,
        share_target=share_target,
    )


def digest_for_nonce(header_prefix: bytes, nonce: int) -> bytes:
    """Return the established Python digest for one synthetic candidate."""

    return hash_block_header(header_prefix + nonce.to_bytes(4, byteorder="little", signed=False))


def assert_search_parity(
    work: PreparedMiningWork,
    start_nonce: int,
    stop_nonce: int,
) -> None:
    """Compare all semantic result fields while allowing independent timing."""

    python_result = PythonSequentialBackend().search_nonce_range(
        work,
        start_nonce,
        stop_nonce,
    )
    native_result = NativeSequentialBackend().search_nonce_range(
        work,
        start_nonce,
        stop_nonce,
    )

    assert native_result.start_nonce == python_result.start_nonce
    assert native_result.stop_nonce == python_result.stop_nonce
    assert native_result.hashes_checked == python_result.hashes_checked
    assert native_result.match == python_result.match
    assert native_result.found is python_result.found
    assert native_result.exhausted is python_result.exhausted


def ticking_clock(values: tuple[int, int] = (100, 125)) -> Iterator[int]:
    """Return deterministic monotonic values for wrapper timing tests."""

    return iter(values)


def test_native_capabilities_are_exact_immutable_and_available() -> None:
    backend = NativeSequentialBackend()
    if not backend.capabilities.available:
        pytest.skip("optional native extension is not built")

    assert backend.capabilities == ComputeBackendCapabilities(
        backend_name="native",
        display_name="Portable native C sequential",
        backend_kind="cpu",
        implementation="c",
        supports_parallel_search=False,
        supports_cooperative_cancellation=False,
        supports_device_selection=False,
        deterministic_search_order=True,
        preferred_batch_size=None,
        available=True,
    )


def test_unavailable_backend_has_controlled_category_and_cannot_execute() -> None:
    backend = NativeSequentialBackend(None)

    assert backend.capabilities.available is False
    assert backend.capabilities.unavailable_reason == "ExtensionNotInstalled"
    with pytest.raises(ComputeBackendExecutionError, match="unavailable"):
        backend.search_nonce_range(prepared_work(), 0, 1)


@pytest.mark.parametrize(
    ("failure", "expected_category"),
    [
        (ImportError("private import path"), "ExtensionNotInstalled"),
        (RuntimeError("private loader detail"), "ExtensionImportFailed"),
    ],
)
def test_extension_load_failures_become_controlled_availability_categories(
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
    expected_category: str,
) -> None:
    def fail_import(name: str) -> object:
        del name
        raise failure

    monkeypatch.setattr(native_module, "import_module", fail_import)
    backend = NativeSequentialBackend()

    assert backend.capabilities.available is False
    assert backend.capabilities.unavailable_reason == expected_category
    assert "private" not in str(backend.capabilities)


def test_malformed_extension_module_is_controlled_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(native_module, "import_module", lambda name: object())

    backend = NativeSequentialBackend()

    assert backend.capabilities.available is False
    assert backend.capabilities.unavailable_reason == "ExtensionInvalid"


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
def test_native_backend_rejects_invalid_public_inputs(
    work: object,
    start_nonce: object,
    stop_nonce: object,
) -> None:
    with pytest.raises(ComputeBackendValidationError):
        NativeSequentialBackend().search_nonce_range(
            work,  # type: ignore[arg-type]
            start_nonce,  # type: ignore[arg-type]
            stop_nonce,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    ("share_target", "network_target", "start_nonce", "stop_nonce"),
    [
        (1, 1, 0, 4),
        (_MAX_TARGET, 1, 0, 1),
        (1, _MAX_TARGET, 0, 1),
        (_MAX_TARGET, _MAX_TARGET, 0, 1),
        (1, 1, 17, 18),
    ],
)
def test_native_backend_has_exact_reference_parity_for_core_outcomes(
    share_target: int,
    network_target: int,
    start_nonce: int,
    stop_nonce: int,
) -> None:
    assert_search_parity(
        prepared_work(
            share_target=share_target,
            network_target=network_target,
        ),
        start_nonce,
        stop_nonce,
    )


def test_native_backend_first_match_inside_and_at_final_included_nonce() -> None:
    target = block_hash_to_int(digest_for_nonce(_PREFIX, 2))
    work = prepared_work(share_target=target)

    assert_search_parity(work, 0, 3)
    result = NativeSequentialBackend().search_nonce_range(work, 0, 3)
    assert result.match is not None
    assert result.match.nonce == 2
    assert result.hashes_checked == 3


def test_native_backend_range_can_end_exactly_at_nonce_limit() -> None:
    final_nonce = 0xFFFFFFFF
    target = block_hash_to_int(digest_for_nonce(_PREFIX, final_nonce))
    work = prepared_work(network_target=target)

    assert_search_parity(work, final_nonce - 1, 2**32)
    result = NativeSequentialBackend().search_nonce_range(work, final_nonce - 1, 2**32)
    assert result.match is not None
    assert result.match.nonce == final_nonce


def test_native_backend_has_fixed_seed_randomized_small_range_parity() -> None:
    generator = random.Random(0x4841534850484552)

    for _ in range(40):
        header_prefix = generator.randbytes(76)
        start_nonce = generator.randrange(0, 10_000)
        stop_nonce = start_nonce + generator.randrange(1, 9)
        share_target = generator.randrange(1, _MAX_TARGET + 1)
        network_target = generator.randrange(1, _MAX_TARGET + 1)
        assert_search_parity(
            prepared_work(
                header_prefix=header_prefix,
                share_target=share_target,
                network_target=network_target,
            ),
            start_nonce,
            stop_nonce,
        )


def test_native_backend_does_not_mutate_or_retain_prepared_work() -> None:
    work = prepared_work()
    before = prepared_work()
    backend = NativeSequentialBackend()

    backend.search_nonce_range(work, 0, 2)

    assert work == before
    assert not hasattr(backend, "work")


def test_wrapper_measures_only_native_invocation_and_clamps_negative_time() -> None:
    calls: list[tuple[bytes, bytes, bytes, int, int]] = []

    def exhausted(*arguments: object) -> object:
        header, share, network, start, stop = arguments
        assert isinstance(header, bytes)
        assert isinstance(share, bytes)
        assert isinstance(network, bytes)
        assert isinstance(start, int)
        assert isinstance(stop, int)
        calls.append((header, share, network, start, stop))
        return (None, None, False, False, stop - start)

    ticks = ticking_clock((125, 100))
    work = prepared_work()
    result = NativeSequentialBackend(exhausted, clock=lambda: next(ticks)).search_nonce_range(
        work,
        7,
        10,
    )

    assert result.elapsed_ns == 0
    assert calls == [
        (
            work.header_prefix,
            work.share_target.to_bytes(32, "little"),
            work.network_target.to_bytes(32, "little"),
            7,
            10,
        )
    ]


@pytest.mark.parametrize(
    "native_result",
    [
        None,
        [],
        (None,),
        (None, b"", False, False, 1),
        (None, None, 0, False, 1),
        (None, None, False, False, True),
        (None, None, False, False, 0),
        (0, b"short", True, False, 1),
        (0, bytes(32), False, False, 1),
        (2, bytes(32), True, False, 1),
    ],
)
def test_native_backend_rejects_malformed_native_results(native_result: object) -> None:
    def malformed(
        header: bytes,
        share: bytes,
        network: bytes,
        start: int,
        stop: int,
    ) -> object:
        del header, share, network, start, stop
        return native_result

    with pytest.raises(ComputeBackendExecutionError):
        NativeSequentialBackend(malformed).search_nonce_range(prepared_work(), 0, 1)


def test_native_backend_rejects_digest_and_target_flag_mismatches() -> None:
    expected_digest = digest_for_nonce(_PREFIX, 0)

    def bad_digest(*arguments: object) -> object:
        del arguments
        return (0, bytes(32), True, False, 1)

    def bad_flags(*arguments: object) -> object:
        del arguments
        return (0, expected_digest, True, True, 1)

    work = prepared_work(share_target=_MAX_TARGET, network_target=1)
    with pytest.raises(ComputeBackendExecutionError, match="digest verification"):
        NativeSequentialBackend(bad_digest).search_nonce_range(work, 0, 1)
    with pytest.raises(ComputeBackendExecutionError, match="target verification"):
        NativeSequentialBackend(bad_flags).search_nonce_range(work, 0, 1)


def test_native_exception_is_sanitized_and_never_becomes_exhaustion() -> None:
    def fail(*arguments: object) -> object:
        del arguments
        raise RuntimeError("private native failure details")

    with pytest.raises(ComputeBackendExecutionError) as raised:
        NativeSequentialBackend(fail).search_nonce_range(prepared_work(), 0, 1)

    assert "private native failure details" not in str(raised.value)


def test_native_backend_rejects_invalid_clock_data() -> None:
    def exhausted(*arguments: object) -> object:
        del arguments
        return (None, None, False, False, 1)

    ticks: Iterator[object] = iter((True, 1))
    with pytest.raises(ComputeBackendExecutionError, match="clock"):
        NativeSequentialBackend(exhausted, clock=lambda: next(ticks)).search_nonce_range(
            prepared_work(),
            0,
            1,
        )
