"""Tests for compute-backend contracts, registry, and Python parity."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, dataclass

import pytest

import hashphere.mining.search as search_module
from hashphere.compute import (
    ComputeBackendCapabilities,
    ComputeBackendExecutionError,
    ComputeBackendRegistry,
    ComputeBackendSelectionError,
    ComputeBackendValidationError,
    MiningComputeBackend,
    NativeParallelBackend,
    NativeSequentialBackend,
    PythonSequentialBackend,
    builtin_compute_backend_registry,
    close_compute_backend,
    compute_backend_worker_count,
    list_compute_backends,
    select_compute_backend,
)
from hashphere.mining import (
    NonceSearchResult,
    PreparedMiningWork,
    search_nonce_range,
)

_MAX_UINT256 = (1 << 256) - 1


def prepared_work(
    *,
    network_target: int = 1,
    share_target: int = 1,
) -> PreparedMiningWork:
    """Return small synthetic immutable work without assembling a raw job."""

    return PreparedMiningWork(
        job_id="synthetic-job",
        extra_nonce_2="00000000",
        network_time="65f04abc",
        header_prefix=bytes(range(76)),
        network_target=network_target,
        share_target=share_target,
    )


def capabilities(
    name: str = "fake",
    *,
    available: bool = True,
) -> ComputeBackendCapabilities:
    """Build controlled fake backend capabilities."""

    return ComputeBackendCapabilities(
        backend_name=name,
        display_name=f"{name} test backend",
        backend_kind="cpu",
        implementation="test",
        supports_parallel_search=False,
        supports_cooperative_cancellation=False,
        supports_device_selection=False,
        deterministic_search_order=True,
        preferred_batch_size=None,
        available=available,
        unavailable_reason=None if available else "NotInstalled",
    )


@dataclass(frozen=True, slots=True)
class FakeBackend:
    """Minimal deterministic backend for isolated registry tests."""

    capabilities: ComputeBackendCapabilities

    def search_nonce_range(
        self,
        work: PreparedMiningWork,
        start_nonce: int,
        stop_nonce: int,
    ) -> NonceSearchResult:
        del work
        return NonceSearchResult(
            start_nonce=start_nonce,
            stop_nonce=stop_nonce,
            hashes_checked=stop_nonce - start_nonce,
            elapsed_ns=0,
            match=None,
        )


def test_python_capabilities_are_exact_immutable_and_slotted() -> None:
    backend = PythonSequentialBackend()
    metadata = backend.capabilities

    assert metadata == ComputeBackendCapabilities(
        backend_name="python",
        display_name="Python sequential reference",
        backend_kind="cpu",
        implementation="python",
        supports_parallel_search=False,
        supports_cooperative_cancellation=False,
        supports_device_selection=False,
        deterministic_search_order=True,
        preferred_batch_size=None,
        available=True,
    )
    assert isinstance(backend, MiningComputeBackend)
    assert not hasattr(metadata, "__dict__")
    with pytest.raises(FrozenInstanceError):
        metadata.available = False  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("backend_name", "Python"),
        ("backend_name", ""),
        ("display_name", " "),
        ("backend_kind", "CPU"),
        ("implementation", "python 3"),
        ("supports_parallel_search", 1),
        ("supports_cooperative_cancellation", None),
        ("supports_device_selection", "false"),
        ("deterministic_search_order", 1),
        ("preferred_batch_size", True),
        ("preferred_batch_size", 0),
        ("available", 1),
    ],
)
def test_capabilities_reject_invalid_fields(field_name: str, value: object) -> None:
    values: dict[str, object] = {
        "backend_name": "python",
        "display_name": "Python sequential reference",
        "backend_kind": "cpu",
        "implementation": "python",
        "supports_parallel_search": False,
        "supports_cooperative_cancellation": False,
        "supports_device_selection": False,
        "deterministic_search_order": True,
        "preferred_batch_size": None,
        "available": True,
        "unavailable_reason": None,
    }
    values[field_name] = value
    with pytest.raises(ComputeBackendValidationError):
        ComputeBackendCapabilities(**values)  # type: ignore[arg-type]


def test_unavailable_reason_is_controlled_and_consistent() -> None:
    with pytest.raises(ComputeBackendValidationError):
        capabilities(available=True).__class__(
            **{
                **{
                    field: getattr(capabilities(), field)
                    for field in capabilities().__dataclass_fields__
                },
                "unavailable_reason": "raw detail with spaces",
            }
        )
    with pytest.raises(ComputeBackendValidationError):
        ComputeBackendCapabilities(
            backend_name="native",
            display_name="Native",
            backend_kind="cpu",
            implementation="native",
            supports_parallel_search=False,
            supports_cooperative_cancellation=False,
            supports_device_selection=False,
            deterministic_search_order=True,
            preferred_batch_size=None,
            available=False,
            unavailable_reason=None,
        )


def test_python_backend_delegates_exactly_once_and_returns_same_result() -> None:
    work = prepared_work()
    expected = NonceSearchResult(7, 10, 3, 25, None)
    calls: list[tuple[PreparedMiningWork, int, int]] = []

    def searcher(
        received_work: PreparedMiningWork,
        start_nonce: int,
        stop_nonce: int,
    ) -> NonceSearchResult:
        calls.append((received_work, start_nonce, stop_nonce))
        return expected

    backend = PythonSequentialBackend(searcher)
    result = backend.search_nonce_range(work, 7, 10)

    assert result is expected
    assert calls == [(work, 7, 10)]
    assert work == prepared_work()
    assert not hasattr(backend, "work")


@pytest.mark.parametrize(
    ("start_nonce", "stop_nonce"),
    [(True, 1), (0, False), (-1, 1), (1, 1), (0, 2**32 + 1)],
)
def test_python_backend_translates_reference_validation_failures(
    start_nonce: object,
    stop_nonce: object,
) -> None:
    backend = PythonSequentialBackend()
    with pytest.raises(ComputeBackendValidationError):
        backend.search_nonce_range(
            prepared_work(),
            start_nonce,  # type: ignore[arg-type]
            stop_nonce,  # type: ignore[arg-type]
        )


def test_python_backend_translates_execution_failure_without_raw_detail() -> None:
    def fail(
        work: PreparedMiningWork,
        start_nonce: int,
        stop_nonce: int,
    ) -> NonceSearchResult:
        del work, start_nonce, stop_nonce
        raise RuntimeError("sensitive backend detail")

    with pytest.raises(ComputeBackendExecutionError) as raised:
        PythonSequentialBackend(fail).search_nonce_range(prepared_work(), 0, 1)

    assert "sensitive backend detail" not in str(raised.value)


def test_python_backend_rejects_invalid_result_and_mismatched_range() -> None:
    def invalid_result(
        work: PreparedMiningWork,
        start_nonce: int,
        stop_nonce: int,
    ) -> NonceSearchResult:
        del work, start_nonce, stop_nonce
        return object()  # type: ignore[return-value]

    def wrong_range(
        work: PreparedMiningWork,
        start_nonce: int,
        stop_nonce: int,
    ) -> NonceSearchResult:
        del work, start_nonce, stop_nonce
        return NonceSearchResult(1, 2, 1, 0, None)

    with pytest.raises(ComputeBackendExecutionError):
        PythonSequentialBackend(invalid_result).search_nonce_range(prepared_work(), 0, 1)
    with pytest.raises(ComputeBackendExecutionError):
        PythonSequentialBackend(wrong_range).search_nonce_range(prepared_work(), 0, 1)


@pytest.mark.parametrize(
    ("hash_value", "network_target", "share_target"),
    [
        (_MAX_UINT256, 1, 1),
        (2, 1, _MAX_UINT256),
        (2, _MAX_UINT256, 1),
    ],
)
def test_python_backend_has_exact_reference_parity(
    monkeypatch: pytest.MonkeyPatch,
    hash_value: int,
    network_target: int,
    share_target: int,
) -> None:
    ticks = iter((100, 125, 100, 125))
    monkeypatch.setattr(search_module, "perf_counter_ns", lambda: next(ticks))
    monkeypatch.setattr(
        search_module,
        "hash_block_header",
        lambda header: hash_value.to_bytes(32, byteorder="little", signed=False),
    )
    work = prepared_work(
        network_target=network_target,
        share_target=share_target,
    )

    direct = search_nonce_range(work, 3, 5)
    through_backend = PythonSequentialBackend().search_nonce_range(work, 3, 5)

    assert through_backend == direct
    assert through_backend.hashes_checked == direct.hashes_checked
    assert through_backend.elapsed_ns == direct.elapsed_ns == 25
    assert through_backend.match == direct.match
    if direct.match is not None:
        assert through_backend.match is not None
        assert through_backend.match.nonce == direct.match.nonce
        assert through_backend.match.block_hash == direct.match.block_hash
        assert through_backend.match.meets_share_target == direct.match.meets_share_target
        assert through_backend.match.meets_network_target == direct.match.meets_network_target


def test_registry_lists_builtins_deterministically_and_is_isolated() -> None:
    first = builtin_compute_backend_registry()
    second = builtin_compute_backend_registry()

    assert first is not second
    assert first.list_capabilities() == second.list_capabilities()
    assert tuple(item.backend_name for item in first.list_capabilities()) == (
        "native",
        "native-parallel",
        "python",
    )
    assert first.list_capabilities()[2] == PythonSequentialBackend().capabilities
    assert list_compute_backends(first) == first.list_capabilities()


def test_registry_sorts_names_and_rejects_duplicates() -> None:
    registry = ComputeBackendRegistry(
        (FakeBackend(capabilities("zeta")), FakeBackend(capabilities("alpha")))
    )
    assert tuple(item.backend_name for item in registry.list_capabilities()) == (
        "alpha",
        "zeta",
    )
    with pytest.raises(ComputeBackendValidationError, match="duplicate"):
        ComputeBackendRegistry((PythonSequentialBackend(), PythonSequentialBackend()))


def test_registry_selection_supports_exact_python_auto_and_legacy_cpu() -> None:
    registry = builtin_compute_backend_registry()

    assert registry.select("python").capabilities.backend_name == "python"
    assert registry.select("auto").capabilities.backend_name == "python"
    assert registry.select("cpu").capabilities.backend_name == "python"
    assert select_compute_backend("python", registry).capabilities.backend_name == "python"


def test_registry_exposes_controlled_unavailable_native_without_affecting_python() -> None:
    registry = builtin_compute_backend_registry(
        native_backend=NativeSequentialBackend(None),
    )

    native, native_parallel, python = registry.list_capabilities()
    assert native.backend_name == "native"
    assert native.available is False
    assert native.unavailable_reason == "ExtensionNotInstalled"
    assert native_parallel.backend_name == "native-parallel"
    assert native_parallel.available is False
    assert native_parallel.unavailable_reason == "ExtensionNotInstalled"
    assert python.backend_name == "python"
    assert python.available is True
    assert registry.select("auto").capabilities.backend_name == "python"
    assert registry.select("cpu").capabilities.backend_name == "python"
    with pytest.raises(ComputeBackendSelectionError, match="unavailable"):
        registry.select("native")
    with pytest.raises(ComputeBackendSelectionError, match="unavailable"):
        registry.select("native-parallel")


def test_registry_selects_parallel_explicitly_without_changing_logical_aliases() -> None:
    native = NativeSequentialBackend(lambda *args: (None, None, False, False, 1))
    parallel = NativeParallelBackend(4, native)
    registry = builtin_compute_backend_registry(
        native_backend=native,
        native_parallel_backend=parallel,
    )

    assert registry.select("native-parallel") is parallel
    assert registry.select("auto").capabilities.backend_name == "python"
    assert registry.select("cpu").capabilities.backend_name == "python"
    assert compute_backend_worker_count(parallel) == 4
    assert compute_backend_worker_count(registry.select("python")) is None
    close_compute_backend(parallel)


def test_registry_rejects_unknown_unavailable_and_invalid_selection_safely() -> None:
    unavailable = ComputeBackendRegistry((FakeBackend(capabilities("native", available=False)),))
    with pytest.raises(ComputeBackendSelectionError, match="unavailable"):
        unavailable.select("native")
    with pytest.raises(ComputeBackendSelectionError) as raised:
        builtin_compute_backend_registry().select("secret-looking-selector")
    assert "secret-looking-selector" not in str(raised.value)
    for value in (None, 1, True, ""):
        with pytest.raises(ComputeBackendSelectionError):
            builtin_compute_backend_registry().select(value)  # type: ignore[arg-type]


def test_registry_rejects_non_backend_and_non_registry_inputs() -> None:
    with pytest.raises(ComputeBackendValidationError):
        ComputeBackendRegistry((object(),))  # type: ignore[arg-type]
    with pytest.raises(ComputeBackendValidationError):
        select_compute_backend("python", object())  # type: ignore[arg-type]
    with pytest.raises(ComputeBackendValidationError):
        list_compute_backends(object())  # type: ignore[arg-type]


def test_close_compute_backend_is_harmless_for_sequential_backends() -> None:
    close_compute_backend(PythonSequentialBackend())
    close_compute_backend(NativeSequentialBackend(None))


def test_close_compute_backend_closes_resources_once_and_sanitizes_failure() -> None:
    close_calls: list[None] = []

    class ClosableBackend(FakeBackend):
        def close(self) -> None:
            close_calls.append(None)

    backend = ClosableBackend(capabilities())

    close_compute_backend(backend)

    assert close_calls == [None]

    class FailingCloseBackend(FakeBackend):
        def close(self) -> None:
            raise RuntimeError("private cleanup detail")

    with pytest.raises(ComputeBackendExecutionError) as raised:
        close_compute_backend(FailingCloseBackend(capabilities()))
    assert "private cleanup detail" not in str(raised.value)


def test_close_compute_backend_rejects_invalid_boundary() -> None:
    with pytest.raises(ComputeBackendValidationError):
        close_compute_backend(object())  # type: ignore[arg-type]
