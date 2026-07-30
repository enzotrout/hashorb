"""Command-time capability probes for deterministic compute-profile policy."""

from __future__ import annotations

import os

from hashphere.compute.backend import ComputeBackendError, close_compute_backend
from hashphere.compute.cuda import CudaBackend
from hashphere.compute.cuda_multi import CudaMultiBackend
from hashphere.compute.native import NativeSequentialBackend


class LocalComputeProfileCapabilities:
    """Perform only narrow, sanitized probes explicitly requested by resolution."""

    def logical_cpu_count(self) -> int | None:
        return os.cpu_count()

    def native_available(self) -> bool:
        return NativeSequentialBackend().capabilities.available

    def cuda_available(self, device_ordinal: int, threads_per_block: int) -> bool:
        try:
            backend = CudaBackend(device_ordinal, threads_per_block=threads_per_block)
        except ComputeBackendError:
            return False
        available = backend.capabilities.available
        try:
            close_compute_backend(backend)
        except ComputeBackendError:
            return False
        return available

    def cuda_multi_available(
        self,
        device_ordinals: tuple[int, ...],
        threads_per_block: int,
    ) -> bool:
        try:
            backend = CudaMultiBackend(
                device_ordinals,
                threads_per_block=threads_per_block,
            )
        except ComputeBackendError:
            return False
        available = backend.capabilities.available
        try:
            close_compute_backend(backend)
        except ComputeBackendError:
            return False
        return available
