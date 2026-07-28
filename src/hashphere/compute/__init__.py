"""Compute-backend contracts, registry, and Python reference implementation."""

from hashphere.compute.backend import (
    ComputeBackendCapabilities,
    ComputeBackendError,
    ComputeBackendExecutionError,
    ComputeBackendSelectionError,
    ComputeBackendValidationError,
    MiningComputeBackend,
    close_compute_backend,
    compute_backend_worker_count,
)
from hashphere.compute.benchmark import deterministic_benchmark_work
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
from hashphere.config import DEFAULT_COMPUTE_WORKERS, MAX_COMPUTE_WORKERS

__all__ = [
    "ComputeBackendCapabilities",
    "ComputeBackendError",
    "ComputeBackendExecutionError",
    "ComputeBackendRegistry",
    "ComputeBackendSelectionError",
    "ComputeBackendValidationError",
    "DEFAULT_COMPUTE_WORKERS",
    "MAX_COMPUTE_WORKERS",
    "MiningComputeBackend",
    "NativeParallelBackend",
    "NativeSequentialBackend",
    "NonceRangeAssignment",
    "PythonSequentialBackend",
    "builtin_compute_backend_registry",
    "close_compute_backend",
    "compute_backend_worker_count",
    "deterministic_benchmark_work",
    "list_compute_backends",
    "partition_nonce_range",
    "select_compute_backend",
]
