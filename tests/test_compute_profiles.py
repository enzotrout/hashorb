"""Deterministic policy tests for user-facing compute profiles."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, dataclass, field

import pytest

from hashphere.config import (
    ComputeProfileOverrides,
    ComputeProfileResolutionError,
    ComputeProfileValidationError,
    parse_compute_profile,
    resolve_compute_profile,
)


@dataclass
class FakeCapabilities:
    """Narrow fake with explicit availability and an auditable probe order."""

    cpus: int | None = 8
    native: bool = True
    cuda_devices: set[int] = field(default_factory=set)
    cuda_sets: set[tuple[int, ...]] = field(default_factory=set)
    calls: list[tuple[object, ...]] = field(default_factory=list)

    def logical_cpu_count(self) -> int | None:
        self.calls.append(("cpu",))
        return self.cpus

    def native_available(self) -> bool:
        self.calls.append(("native",))
        return self.native

    def cuda_available(self, device_ordinal: int, threads_per_block: int) -> bool:
        self.calls.append(("cuda", device_ordinal, threads_per_block))
        return device_ordinal in self.cuda_devices

    def cuda_multi_available(
        self, device_ordinals: tuple[int, ...], threads_per_block: int
    ) -> bool:
        self.calls.append(("cuda-multi", device_ordinals, threads_per_block))
        return device_ordinals in self.cuda_sets


@pytest.mark.parametrize(
    ("value", "expected"),
    [("lite", "lite"), ("AUTO", "auto"), ("Max", "max"), ("custom", "custom")],
)
def test_profile_names_are_case_normalized_without_padding(value: str, expected: str) -> None:
    assert parse_compute_profile(value) == expected


@pytest.mark.parametrize("value", [None, "", " lite", "lite ", "li te", "unknown", 1])
def test_malformed_profile_names_fail_clearly(value: object) -> None:
    with pytest.raises(ComputeProfileValidationError):
        parse_compute_profile(value)


def test_lite_uses_at_most_device_zero_and_real_pacing() -> None:
    capabilities = FakeCapabilities(cuda_devices={0, 1}, cuda_sets={(0, 1)})

    resolved = resolve_compute_profile("lite", ComputeProfileOverrides(), capabilities)

    assert resolved.backend_name == "cuda"
    assert resolved.cuda_device == 0
    assert resolved.cuda_devices is None
    assert resolved.chunk_size == 100_000_000
    assert resolved.inter_range_delay_seconds == 0.05
    assert capabilities.calls == [("cuda", 0, 256)]


def test_lite_cpu_resolution_is_sequential_and_conservative() -> None:
    native = resolve_compute_profile(
        "lite", ComputeProfileOverrides(), FakeCapabilities(native=True)
    )
    python = resolve_compute_profile(
        "lite", ComputeProfileOverrides(), FakeCapabilities(native=False)
    )

    assert (native.backend_name, native.worker_count) == ("native", None)
    assert (python.backend_name, python.worker_count) == ("python", None)


def test_auto_prefers_one_cuda_device_without_implicit_inventory() -> None:
    capabilities = FakeCapabilities(cuda_devices={0, 1}, cuda_sets={(0, 1)})

    resolved = resolve_compute_profile("auto", ComputeProfileOverrides(), capabilities)

    assert resolved.backend_name == "cuda"
    assert resolved.cuda_device == 0
    assert ("cuda-multi", (0, 1), 256) not in capabilities.calls


def test_auto_explicit_multi_device_set_is_exact() -> None:
    overrides = ComputeProfileOverrides(cuda_devices=(0, 2))
    capabilities = FakeCapabilities(cuda_sets={(0, 2)})

    resolved = resolve_compute_profile("auto", overrides, capabilities)

    assert resolved.backend_name == "cuda-multi"
    assert resolved.cuda_devices == (0, 2)
    assert capabilities.calls == [("cuda-multi", (0, 2), 256)]


def test_auto_cpu_fallback_is_resolution_only_and_bounded() -> None:
    resolved = resolve_compute_profile(
        "auto", ComputeProfileOverrides(), FakeCapabilities(cpus=64, native=True)
    )

    assert resolved.backend_name == "native-parallel"
    assert resolved.worker_count == 8


def test_max_never_implicitly_selects_multiple_devices_and_bounds_cpu() -> None:
    gpu = resolve_compute_profile(
        "max",
        ComputeProfileOverrides(),
        FakeCapabilities(cpus=256, cuda_devices={0, 1}, cuda_sets={(0, 1)}),
    )
    cpu = resolve_compute_profile(
        "max", ComputeProfileOverrides(), FakeCapabilities(cpus=256, native=True)
    )

    assert (gpu.backend_name, gpu.cuda_device, gpu.cuda_devices) == ("cuda", 0, None)
    assert (cpu.backend_name, cpu.worker_count) == ("native-parallel", 32)
    assert gpu.inter_range_delay_seconds == 0


@pytest.mark.parametrize(
    "overrides",
    [
        ComputeProfileOverrides(cuda_devices=(0, 1)),
        ComputeProfileOverrides(worker_count=64),
        ComputeProfileOverrides(chunk_size=1),
    ],
)
def test_lite_rejects_manual_compute_controls(overrides: ComputeProfileOverrides) -> None:
    with pytest.raises(ComputeProfileValidationError):
        resolve_compute_profile("lite", overrides, FakeCapabilities())


def test_auto_and_max_reject_non_device_manual_controls() -> None:
    for profile, overrides in (
        ("auto", ComputeProfileOverrides(backend_name="native")),
        ("max", ComputeProfileOverrides(inter_range_delay_seconds=0.1)),
    ):
        with pytest.raises(ComputeProfileValidationError):
            resolve_compute_profile(profile, overrides, FakeCapabilities())


def test_custom_cuda_is_explicit_reproducible_and_frozen() -> None:
    overrides = ComputeProfileOverrides(
        backend_name="cuda",
        cuda_device=0,
        cuda_threads_per_block=128,
        chunk_size=123_456,
        inter_range_delay_seconds=0.25,
    )
    capabilities = FakeCapabilities(cuda_devices={0})

    first = resolve_compute_profile("custom", overrides, capabilities)
    second = resolve_compute_profile("custom", overrides, FakeCapabilities(cuda_devices={0}))

    assert first == second
    assert first.cuda_threads_per_block == 128
    assert first.chunk_size == 123_456
    with pytest.raises(FrozenInstanceError):
        first.chunk_size = 1  # type: ignore[misc]


@pytest.mark.parametrize(
    "overrides",
    [
        ComputeProfileOverrides(chunk_size=1),
        ComputeProfileOverrides(backend_name="cuda", chunk_size=1),
        ComputeProfileOverrides(
            backend_name="cuda",
            cuda_device=0,
            cuda_devices=(0,),
            cuda_threads_per_block=256,
            chunk_size=1,
        ),
        ComputeProfileOverrides(backend_name="native", cuda_device=0, chunk_size=1),
        ComputeProfileOverrides(
            backend_name="cuda-multi", cuda_threads_per_block=256, chunk_size=1
        ),
    ],
)
def test_custom_rejects_missing_or_contradictory_controls(
    overrides: ComputeProfileOverrides,
) -> None:
    with pytest.raises(ComputeProfileValidationError):
        resolve_compute_profile("custom", overrides, FakeCapabilities(cuda_devices={0}))


def test_explicit_unusable_cuda_fails_without_execution_fallback() -> None:
    with pytest.raises(ComputeProfileResolutionError):
        resolve_compute_profile(
            "auto", ComputeProfileOverrides(cuda_device=7), FakeCapabilities(native=True)
        )


def test_profile_override_layers_use_cli_shaped_values_over_environment_shaped_values() -> None:
    environment = ComputeProfileOverrides(cuda_device=0, chunk_size=10)
    cli = ComputeProfileOverrides(cuda_device=2)

    assert cli.merged_over(environment) == ComputeProfileOverrides(
        cuda_device=2,
        chunk_size=10,
    )
