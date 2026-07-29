"""Hashphere configuration."""

from hashphere.config.settings import (
    DEFAULT_COMPUTE_WORKERS,
    DEFAULT_CUDA_DEVICE,
    DEFAULT_CUDA_DEVICES,
    DEFAULT_SEARCH_STRATEGY,
    MAX_COMPUTE_WORKERS,
    MAX_CUDA_DEVICE,
    MAX_CUDA_DEVICES,
    Settings,
    parse_cuda_devices,
)

__all__ = [
    "DEFAULT_COMPUTE_WORKERS",
    "DEFAULT_CUDA_DEVICE",
    "DEFAULT_CUDA_DEVICES",
    "DEFAULT_SEARCH_STRATEGY",
    "MAX_COMPUTE_WORKERS",
    "MAX_CUDA_DEVICE",
    "MAX_CUDA_DEVICES",
    "Settings",
    "parse_cuda_devices",
]
