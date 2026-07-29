"""Compute-backend contracts, registry, and Python reference implementation."""

from hashphere.compute.backend import (
    ComputeBackendCapabilities,
    ComputeBackendError,
    ComputeBackendExecutionError,
    ComputeBackendSelectionError,
    ComputeBackendValidationError,
    MiningComputeBackend,
    close_compute_backend,
    compute_backend_device_ordinal,
    compute_backend_device_ordinals,
    compute_backend_worker_count,
)
from hashphere.compute.benchmark import deterministic_benchmark_work
from hashphere.compute.cuda import CudaBackend, cuda_grid_stride_offsets
from hashphere.compute.cuda_multi import CudaMultiBackend, validate_cuda_device_ordinals
from hashphere.compute.native import NativeSequentialBackend
from hashphere.compute.parallel import (
    NativeParallelBackend,
    NonceRangeAssignment,
    partition_nonce_range,
)
from hashphere.compute.python import PythonSequentialBackend
from hashphere.compute.registry import (
    ComputeBackendRegistry,
    builtin_compute_backend_registry,
    list_compute_backends,
    select_compute_backend,
)
from hashphere.config import (
    DEFAULT_COMPUTE_WORKERS,
    DEFAULT_CUDA_DEVICE,
    DEFAULT_CUDA_DEVICES,
    MAX_COMPUTE_WORKERS,
    MAX_CUDA_DEVICE,
    MAX_CUDA_DEVICES,
)

__all__ = [
    "ComputeBackendCapabilities",
    "ComputeBackendError",
    "ComputeBackendExecutionError",
    "ComputeBackendRegistry",
    "ComputeBackendSelectionError",
    "ComputeBackendValidationError",
    "CudaBackend",
    "CudaMultiBackend",
    "DEFAULT_CUDA_DEVICE",
    "DEFAULT_CUDA_DEVICES",
    "DEFAULT_COMPUTE_WORKERS",
    "MAX_COMPUTE_WORKERS",
    "MAX_CUDA_DEVICE",
    "MAX_CUDA_DEVICES",
    "MiningComputeBackend",
    "NativeParallelBackend",
    "NativeSequentialBackend",
    "NonceRangeAssignment",
    "PythonSequentialBackend",
    "builtin_compute_backend_registry",
    "close_compute_backend",
    "compute_backend_device_ordinal",
    "compute_backend_device_ordinals",
    "compute_backend_worker_count",
    "cuda_grid_stride_offsets",
    "deterministic_benchmark_work",
    "list_compute_backends",
    "partition_nonce_range",
    "select_compute_backend",
    "validate_cuda_device_ordinals",
]
