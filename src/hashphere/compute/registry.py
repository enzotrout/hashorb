"""Deterministic isolated registry and selection for compute backends."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from hashphere.compute.backend import (
    ComputeBackendCapabilities,
    ComputeBackendSelectionError,
    ComputeBackendValidationError,
    MiningComputeBackend,
)
from hashphere.compute.cuda import CudaBackend
from hashphere.compute.cuda_multi import CudaMultiBackend
from hashphere.compute.native import NativeSequentialBackend
from hashphere.compute.parallel import NativeParallelBackend
from hashphere.compute.python import PythonSequentialBackend, RangeSearcher
from hashphere.config import (
    DEFAULT_COMPUTE_WORKERS,
    DEFAULT_CUDA_DEVICE,
    DEFAULT_CUDA_DEVICES,
)

_AUTO_SELECTOR = "auto"
_LEGACY_CPU_SELECTOR = "cpu"
_PYTHON_BACKEND_NAME = "python"


@dataclass(frozen=True, slots=True, init=False)
class ComputeBackendRegistry:
    """Immutable per-instance registry with deterministic listing and selection."""

    _backends: tuple[MiningComputeBackend, ...]

    def __init__(self, backends: Iterable[MiningComputeBackend]) -> None:
        """Snapshot validated backends and reject duplicate exact names."""

        try:
            registered = tuple(backends)
        except TypeError as exc:
            raise ComputeBackendValidationError("backends must be iterable") from exc

        names: set[str] = set()
        for backend in registered:
            if not isinstance(backend, MiningComputeBackend):
                raise ComputeBackendValidationError(
                    "every registered backend must implement MiningComputeBackend"
                )
            name = backend.capabilities.backend_name
            if name in names:
                raise ComputeBackendValidationError("duplicate compute backend registration")
            names.add(name)
        object.__setattr__(
            self,
            "_backends",
            tuple(sorted(registered, key=lambda backend: backend.capabilities.backend_name)),
        )

    def list_capabilities(self) -> tuple[ComputeBackendCapabilities, ...]:
        """Return immutable capabilities in stable backend-name order."""

        return tuple(backend.capabilities for backend in self._backends)

    def select(self, backend_name: str) -> MiningComputeBackend:
        """Select one available exact backend or supported logical selector."""

        if not isinstance(backend_name, str) or not backend_name:
            raise ComputeBackendSelectionError("compute backend selector must be a string")
        selected_name = (
            _PYTHON_BACKEND_NAME
            if backend_name in {_AUTO_SELECTOR, _LEGACY_CPU_SELECTOR}
            else backend_name
        )
        for backend in self._backends:
            capabilities = backend.capabilities
            if capabilities.backend_name != selected_name:
                continue
            if not capabilities.available:
                raise ComputeBackendSelectionError("configured compute backend is unavailable")
            return backend
        raise ComputeBackendSelectionError("configured compute backend is unknown")


def builtin_compute_backend_registry(
    *,
    python_searcher: RangeSearcher | None = None,
    native_backend: NativeSequentialBackend | None = None,
    native_parallel_backend: NativeParallelBackend | None = None,
    cuda_backend: CudaBackend | None = None,
    cuda_multi_backend: CudaMultiBackend | None = None,
    worker_count: int = DEFAULT_COMPUTE_WORKERS,
    cuda_device: int = DEFAULT_CUDA_DEVICE,
    cuda_devices: tuple[int, ...] = DEFAULT_CUDA_DEVICES,
    initialize_cuda: bool = False,
    initialize_cuda_multi: bool = False,
) -> ComputeBackendRegistry:
    """Create a registry containing Python and optional native and CUDA modes."""

    python_backend = (
        PythonSequentialBackend()
        if python_searcher is None
        else PythonSequentialBackend(python_searcher)
    )
    selected_native = NativeSequentialBackend() if native_backend is None else native_backend
    if not isinstance(selected_native, NativeSequentialBackend):
        raise ComputeBackendValidationError("native_backend must be NativeSequentialBackend")
    selected_parallel = (
        NativeParallelBackend(worker_count, selected_native)
        if native_parallel_backend is None
        else native_parallel_backend
    )
    if not isinstance(selected_parallel, NativeParallelBackend):
        raise ComputeBackendValidationError("native_parallel_backend must be NativeParallelBackend")
    selected_cuda = (
        CudaBackend(cuda_device, initialize=initialize_cuda)
        if cuda_backend is None
        else cuda_backend
    )
    if not isinstance(selected_cuda, CudaBackend):
        raise ComputeBackendValidationError("cuda_backend must be CudaBackend")
    selected_cuda_multi = (
        CudaMultiBackend(cuda_devices, initialize=initialize_cuda_multi)
        if cuda_multi_backend is None
        else cuda_multi_backend
    )
    if not isinstance(selected_cuda_multi, CudaMultiBackend):
        raise ComputeBackendValidationError("cuda_multi_backend must be CudaMultiBackend")
    return ComputeBackendRegistry(
        (
            python_backend,
            selected_cuda,
            selected_cuda_multi,
            selected_native,
            selected_parallel,
        )
    )


def select_compute_backend(
    backend_name: str,
    registry: ComputeBackendRegistry | None = None,
    *,
    cuda_device: int = DEFAULT_CUDA_DEVICE,
    cuda_devices: tuple[int, ...] = DEFAULT_CUDA_DEVICES,
) -> MiningComputeBackend:
    """Select an operational backend from a caller registry or fresh built-ins."""

    selected_registry = (
        builtin_compute_backend_registry(
            cuda_device=cuda_device,
            cuda_devices=cuda_devices,
            initialize_cuda=backend_name == "cuda",
            initialize_cuda_multi=backend_name == "cuda-multi",
        )
        if registry is None
        else registry
    )
    if not isinstance(selected_registry, ComputeBackendRegistry):
        raise ComputeBackendValidationError("registry must be a ComputeBackendRegistry")
    return selected_registry.select(backend_name)


def list_compute_backends(
    registry: ComputeBackendRegistry | None = None,
) -> tuple[ComputeBackendCapabilities, ...]:
    """List deterministic capabilities, probing CUDA availability when built in."""

    selected_registry = (
        builtin_compute_backend_registry(initialize_cuda=True) if registry is None else registry
    )
    if not isinstance(selected_registry, ComputeBackendRegistry):
        raise ComputeBackendValidationError("registry must be a ComputeBackendRegistry")
    return selected_registry.list_capabilities()
