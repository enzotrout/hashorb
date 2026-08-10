"""Deterministic performance-profile policy without hardware side effects."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

PROFILE_NAMES = frozenset({"lite", "auto", "max", "custom"})
CUDA_THREADS_PER_BLOCK_CHOICES = frozenset({64, 128, 256, 512})
DEFAULT_CUDA_THREADS_PER_BLOCK = 256
MAX_PROFILE_WORKERS = 256
MAX_PROFILE_CHUNK_SIZE = 1 << 32
MAX_INTER_RANGE_DELAY_SECONDS = 60.0

_BACKEND_NAMES = frozenset({"python", "native", "native-parallel", "cuda", "cuda-multi"})
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_-]*$")

_LITE_GPU_CHUNK_SIZE = 100_000_000
_LITE_CPU_CHUNK_SIZE = 250_000
_LITE_DELAY_SECONDS = 0.05
_AUTO_GPU_CHUNK_SIZE = 500_000_000
_AUTO_GPU_DELAY_SECONDS = 0.08
_AUTO_PARALLEL_CHUNK_SIZE = 5_000_000
_AUTO_SEQUENTIAL_CHUNK_SIZE = 1_000_000
_AUTO_PYTHON_CHUNK_SIZE = 100_000
_MAX_GPU_CHUNK_SIZE = 500_000_000
_MAX_PARALLEL_CHUNK_SIZE = 10_000_000
_MAX_SEQUENTIAL_CHUNK_SIZE = 2_000_000
_MAX_PYTHON_CHUNK_SIZE = 250_000


class ComputeProfileError(ValueError):
    """Base error for profile parsing and deterministic resolution."""


class ComputeProfileValidationError(ComputeProfileError):
    """Raised when profile inputs are structurally invalid or contradictory."""


class ComputeProfileResolutionError(ComputeProfileError):
    """Raised when no permitted operational backend can be resolved."""


@dataclass(frozen=True, slots=True)
class ComputeProfileOverrides:
    """Explicit profile-controlled settings from one precedence layer."""

    backend_name: str | None = None
    worker_count: int | None = None
    cuda_device: int | None = None
    cuda_devices: tuple[int, ...] | None = None
    cuda_threads_per_block: int | None = None
    chunk_size: int | None = None
    inter_range_delay_seconds: float | None = None

    def __post_init__(self) -> None:
        if self.backend_name is not None and (
            not isinstance(self.backend_name, str)
            or _IDENTIFIER.fullmatch(self.backend_name) is None
        ):
            raise ComputeProfileValidationError("backend_name must be a backend identifier")
        _optional_bounded_integer(self.worker_count, "worker_count", 1, MAX_PROFILE_WORKERS)
        _optional_bounded_integer(self.cuda_device, "cuda_device", 0, (1 << 31) - 1)
        if self.cuda_devices is not None:
            if not isinstance(self.cuda_devices, tuple) or not self.cuda_devices:
                raise ComputeProfileValidationError("cuda_devices must be a nonempty tuple")
            for ordinal in self.cuda_devices:
                _optional_bounded_integer(ordinal, "cuda_devices", 0, (1 << 31) - 1)
            if tuple(sorted(set(self.cuda_devices))) != self.cuda_devices:
                raise ComputeProfileValidationError(
                    "cuda_devices must contain unique ascending ordinals"
                )
        if (
            self.cuda_threads_per_block is not None
            and self.cuda_threads_per_block not in CUDA_THREADS_PER_BLOCK_CHOICES
        ):
            raise ComputeProfileValidationError("cuda_threads_per_block is unsupported")
        _optional_bounded_integer(
            self.chunk_size,
            "chunk_size",
            1,
            MAX_PROFILE_CHUNK_SIZE,
        )
        if self.inter_range_delay_seconds is not None:
            value = self.inter_range_delay_seconds
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or not 0 <= value <= MAX_INTER_RANGE_DELAY_SECONDS
            ):
                raise ComputeProfileValidationError(
                    "inter_range_delay_seconds must be finite and between 0 and 60"
                )

    def merged_over(self, fallback: ComputeProfileOverrides) -> ComputeProfileOverrides:
        """Return this layer over a lower-precedence layer."""

        if not isinstance(fallback, ComputeProfileOverrides):
            raise ComputeProfileValidationError("fallback must be ComputeProfileOverrides")
        values = {}
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            values[name] = getattr(fallback, name) if value is None else value
        return ComputeProfileOverrides(**values)


@dataclass(frozen=True, slots=True)
class ResolvedComputeProfile:
    """Sanitized immutable operational policy for one command invocation."""

    requested_profile: str
    effective_profile: str
    backend_name: str
    worker_count: int | None
    cuda_device: int | None
    cuda_devices: tuple[int, ...] | None
    cuda_threads_per_block: int | None
    chunk_size: int
    inter_range_delay_seconds: float
    resolution_reason: str

    def __post_init__(self) -> None:
        if self.requested_profile not in PROFILE_NAMES:
            raise ComputeProfileValidationError("requested_profile is invalid")
        if self.effective_profile not in PROFILE_NAMES:
            raise ComputeProfileValidationError("effective_profile is invalid")
        if self.backend_name not in _BACKEND_NAMES:
            raise ComputeProfileValidationError("backend_name is invalid")
        if not re.fullmatch(r"[A-Z][A-Za-z0-9]*", self.resolution_reason):
            raise ComputeProfileValidationError("resolution_reason is invalid")
        ComputeProfileOverrides(
            backend_name=self.backend_name,
            worker_count=self.worker_count,
            cuda_device=self.cuda_device,
            cuda_devices=self.cuda_devices,
            cuda_threads_per_block=self.cuda_threads_per_block,
            chunk_size=self.chunk_size,
            inter_range_delay_seconds=self.inter_range_delay_seconds,
        )

    @property
    def device_count(self) -> int | None:
        if self.cuda_devices is not None:
            return len(self.cuda_devices)
        if self.cuda_device is not None:
            return 1
        return None


@runtime_checkable
class ComputeProfileCapabilityProvider(Protocol):
    """Narrow command-time capability checks required by profile policy."""

    def logical_cpu_count(self) -> int | None:
        """Return sanitized logical CPU capacity or None when unavailable."""

    def native_available(self) -> bool:
        """Return whether the verified native extension is available."""

    def cuda_available(self, device_ordinal: int, threads_per_block: int) -> bool:
        """Probe one explicit CUDA ordinal without retaining the backend."""

    def cuda_multi_available(
        self,
        device_ordinals: tuple[int, ...],
        threads_per_block: int,
    ) -> bool:
        """Probe one explicit CUDA device set without device discovery."""


def parse_compute_profile(value: object) -> str:
    """Normalize profile case while rejecting padding and malformed names."""

    if not isinstance(value, str) or not value or value != value.strip():
        raise ComputeProfileValidationError("compute profile must be an unpadded profile name")
    normalized = value.lower()
    if normalized not in PROFILE_NAMES:
        raise ComputeProfileValidationError("compute profile must be lite, auto, max, or custom")
    return normalized


def resolve_compute_profile(
    requested_profile: object,
    overrides: ComputeProfileOverrides,
    capabilities: ComputeProfileCapabilityProvider,
) -> ResolvedComputeProfile:
    """Resolve one profile without constructing the selected execution backend."""

    profile = parse_compute_profile(requested_profile)
    if not isinstance(overrides, ComputeProfileOverrides):
        raise ComputeProfileValidationError("overrides must be ComputeProfileOverrides")
    if not isinstance(capabilities, ComputeProfileCapabilityProvider):
        raise ComputeProfileValidationError("capabilities must implement the profile provider")
    _validate_profile_conflicts(profile, overrides)
    if profile == "custom":
        return _resolve_custom(overrides, capabilities)
    if profile == "lite":
        return _resolve_lite(capabilities)
    return _resolve_automatic(profile, overrides, capabilities)


def _resolve_lite(capabilities: ComputeProfileCapabilityProvider) -> ResolvedComputeProfile:
    if capabilities.cuda_available(0, DEFAULT_CUDA_THREADS_PER_BLOCK):
        return _resolved(
            "lite",
            "cuda",
            cuda_device=0,
            threads=DEFAULT_CUDA_THREADS_PER_BLOCK,
            chunk_size=_LITE_GPU_CHUNK_SIZE,
            delay=_LITE_DELAY_SECONDS,
            reason="LiteCudaDevice",
        )
    if capabilities.native_available():
        return _resolved(
            "lite",
            "native",
            chunk_size=_LITE_CPU_CHUNK_SIZE,
            delay=_LITE_DELAY_SECONDS,
            reason="LiteNative",
        )
    return _resolved(
        "lite",
        "python",
        chunk_size=_LITE_CPU_CHUNK_SIZE,
        delay=_LITE_DELAY_SECONDS,
        reason="LitePython",
    )


def _resolve_automatic(
    profile: str,
    overrides: ComputeProfileOverrides,
    capabilities: ComputeProfileCapabilityProvider,
) -> ResolvedComputeProfile:
    explicit_devices = overrides.cuda_devices
    explicit_device = overrides.cuda_device
    reason_prefix = "Auto" if profile == "auto" else "Max"
    if explicit_devices is not None:
        if len(explicit_devices) == 1:
            return _require_cuda(
                profile,
                explicit_devices[0],
                capabilities,
                f"{reason_prefix}ExplicitCuda",
            )
        if not capabilities.cuda_multi_available(
            explicit_devices,
            DEFAULT_CUDA_THREADS_PER_BLOCK,
        ):
            raise ComputeProfileResolutionError("explicit CUDA device set is unavailable")
        return _resolved(
            profile,
            "cuda-multi",
            cuda_devices=explicit_devices,
            threads=DEFAULT_CUDA_THREADS_PER_BLOCK,
            chunk_size=_AUTO_GPU_CHUNK_SIZE if profile == "auto" else _MAX_GPU_CHUNK_SIZE,
            delay=_AUTO_GPU_DELAY_SECONDS if profile == "auto" else 0.0,
            reason=f"{reason_prefix}ExplicitCudaMulti",
        )
    if explicit_device is not None:
        return _require_cuda(
            profile,
            explicit_device,
            capabilities,
            f"{reason_prefix}ExplicitCuda",
        )
    if capabilities.cuda_available(0, DEFAULT_CUDA_THREADS_PER_BLOCK):
        return _resolved(
            profile,
            "cuda",
            cuda_device=0,
            threads=DEFAULT_CUDA_THREADS_PER_BLOCK,
            chunk_size=_AUTO_GPU_CHUNK_SIZE if profile == "auto" else _MAX_GPU_CHUNK_SIZE,
            delay=_AUTO_GPU_DELAY_SECONDS if profile == "auto" else 0.0,
            reason=f"{reason_prefix}CudaDevice",
        )
    if capabilities.native_available():
        cpu_count = _validated_cpu_count(capabilities.logical_cpu_count())
        if cpu_count >= 2:
            workers = min(max(1, cpu_count // 2), 8) if profile == "auto" else min(cpu_count, 32)
            return _resolved(
                profile,
                "native-parallel",
                workers=workers,
                chunk_size=(
                    _AUTO_PARALLEL_CHUNK_SIZE if profile == "auto" else _MAX_PARALLEL_CHUNK_SIZE
                ),
                delay=0.0,
                reason=f"{reason_prefix}NativeParallel",
            )
        return _resolved(
            profile,
            "native",
            chunk_size=(
                _AUTO_SEQUENTIAL_CHUNK_SIZE if profile == "auto" else _MAX_SEQUENTIAL_CHUNK_SIZE
            ),
            delay=0.0,
            reason=f"{reason_prefix}Native",
        )
    return _resolved(
        profile,
        "python",
        chunk_size=_AUTO_PYTHON_CHUNK_SIZE if profile == "auto" else _MAX_PYTHON_CHUNK_SIZE,
        delay=0.0,
        reason=f"{reason_prefix}Python",
    )


def _resolve_custom(
    overrides: ComputeProfileOverrides,
    capabilities: ComputeProfileCapabilityProvider,
) -> ResolvedComputeProfile:
    backend = overrides.backend_name
    if backend is None or backend not in _BACKEND_NAMES:
        raise ComputeProfileValidationError("custom profile requires an explicit backend")
    if overrides.chunk_size is None:
        raise ComputeProfileValidationError("custom profile requires an explicit chunk size")
    delay = overrides.inter_range_delay_seconds or 0.0
    workers: int | None = None
    device: int | None = None
    devices: tuple[int, ...] | None = None
    threads: int | None = None
    if backend in {"python", "native", "native-parallel"}:
        if any(
            item is not None
            for item in (
                overrides.cuda_device,
                overrides.cuda_devices,
                overrides.cuda_threads_per_block,
            )
        ):
            raise ComputeProfileValidationError("CPU custom backend rejects CUDA settings")
        if backend == "native-parallel":
            if overrides.worker_count is None:
                raise ComputeProfileValidationError(
                    "custom native-parallel requires an explicit worker count"
                )
            workers = overrides.worker_count
        elif overrides.worker_count is not None:
            raise ComputeProfileValidationError("sequential custom backend rejects worker count")
        if backend != "python" and not capabilities.native_available():
            raise ComputeProfileResolutionError("custom native backend is unavailable")
    else:
        if overrides.worker_count is not None:
            raise ComputeProfileValidationError("CUDA custom backend rejects CPU worker count")
        if overrides.cuda_threads_per_block is None:
            raise ComputeProfileValidationError("custom CUDA requires threads per block")
        threads = overrides.cuda_threads_per_block
        if backend == "cuda":
            if overrides.cuda_device is None or overrides.cuda_devices is not None:
                raise ComputeProfileValidationError(
                    "custom cuda requires one device and no device list"
                )
            device = overrides.cuda_device
            if not capabilities.cuda_available(device, threads):
                raise ComputeProfileResolutionError("custom CUDA device is unavailable")
        else:
            if overrides.cuda_devices is None or overrides.cuda_device is not None:
                raise ComputeProfileValidationError(
                    "custom cuda-multi requires a device list and no single device"
                )
            devices = overrides.cuda_devices
            if not capabilities.cuda_multi_available(devices, threads):
                raise ComputeProfileResolutionError("custom CUDA device set is unavailable")
    return _resolved(
        "custom",
        backend,
        workers=workers,
        cuda_device=device,
        cuda_devices=devices,
        threads=threads,
        chunk_size=overrides.chunk_size,
        delay=delay,
        reason="CustomExplicit",
    )


def _require_cuda(
    profile: str,
    ordinal: int,
    capabilities: ComputeProfileCapabilityProvider,
    reason: str,
) -> ResolvedComputeProfile:
    if not capabilities.cuda_available(ordinal, DEFAULT_CUDA_THREADS_PER_BLOCK):
        raise ComputeProfileResolutionError("explicit CUDA device is unavailable")
    return _resolved(
        profile,
        "cuda",
        cuda_device=ordinal,
        threads=DEFAULT_CUDA_THREADS_PER_BLOCK,
        chunk_size=_AUTO_GPU_CHUNK_SIZE if profile == "auto" else _MAX_GPU_CHUNK_SIZE,
        delay=_AUTO_GPU_DELAY_SECONDS if profile == "auto" else 0.0,
        reason=reason,
    )


def _resolved(
    profile: str,
    backend: str,
    *,
    workers: int | None = None,
    cuda_device: int | None = None,
    cuda_devices: tuple[int, ...] | None = None,
    threads: int | None = None,
    chunk_size: int,
    delay: float,
    reason: str,
) -> ResolvedComputeProfile:
    return ResolvedComputeProfile(
        requested_profile=profile,
        effective_profile=profile,
        backend_name=backend,
        worker_count=workers,
        cuda_device=cuda_device,
        cuda_devices=cuda_devices,
        cuda_threads_per_block=threads,
        chunk_size=chunk_size,
        inter_range_delay_seconds=delay,
        resolution_reason=reason,
    )


def _validate_profile_conflicts(profile: str, overrides: ComputeProfileOverrides) -> None:
    if overrides.cuda_device is not None and overrides.cuda_devices is not None:
        raise ComputeProfileValidationError("single CUDA device conflicts with device list")
    if profile == "lite":
        if any(getattr(overrides, name) is not None for name in overrides.__dataclass_fields__):
            raise ComputeProfileValidationError("lite profile rejects manual compute overrides")
        return
    if profile in {"auto", "max"}:
        forbidden = (
            overrides.backend_name,
            overrides.worker_count,
            overrides.cuda_threads_per_block,
            overrides.chunk_size,
            overrides.inter_range_delay_seconds,
        )
        if any(value is not None for value in forbidden):
            raise ComputeProfileValidationError(
                f"{profile} profile accepts only explicit CUDA device selection"
            )


def _validated_cpu_count(value: object) -> int:
    if value is None:
        return 1
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ComputeProfileResolutionError("logical CPU capability is invalid")
    return min(value, MAX_PROFILE_WORKERS)


def _optional_bounded_integer(
    value: object,
    name: str,
    minimum: int,
    maximum: int,
) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ComputeProfileValidationError(
            f"{name} must be an integer between {minimum} and {maximum}"
        )
