"""Compute-backend contracts, registry, and Python reference implementation."""

from hashorb.compute.backend import (
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
from hashorb.compute.benchmark import deterministic_benchmark_work
from hashorb.compute.cuda import CudaBackend, cuda_grid_stride_offsets
from hashorb.compute.cuda_multi import CudaMultiBackend, validate_cuda_device_ordinals
from hashorb.compute.native import NativeSequentialBackend
from hashorb.compute.parallel import (
    NativeParallelBackend,
    NonceRangeAssignment,
    partition_nonce_range,
)
from hashorb.compute.profile import LocalComputeProfileCapabilities
from hashorb.compute.python import PythonSequentialBackend
from hashorb.compute.registry import (
    ComputeBackendRegistry,
    builtin_compute_backend_registry,
    list_compute_backends,
    select_compute_backend,
)
from hashorb.config import (
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
    "LocalComputeProfileCapabilities",
    "partition_nonce_range",
    "select_compute_backend",
    "validate_cuda_device_ordinals",
]
