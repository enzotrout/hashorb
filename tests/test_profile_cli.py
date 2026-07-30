"""Offline CLI integration tests for compute profiles."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

import hashphere.__main__ as cli_module
from hashphere.compute import ComputeBackendCapabilities
from hashphere.config import ComputeProfileOverrides
from hashphere.mining import NonceSearchResult, PreparedMiningWork


@dataclass
class FakeCapabilities:
    cpus: int | None = 8
    native: bool = True
    cuda: bool = False

    def logical_cpu_count(self) -> int | None:
        return self.cpus

    def native_available(self) -> bool:
        return self.native

    def cuda_available(self, device_ordinal: int, threads_per_block: int) -> bool:
        return self.cuda and device_ordinal == 0 and threads_per_block in {64, 128, 256, 512}

    def cuda_multi_available(
        self, device_ordinals: tuple[int, ...], threads_per_block: int
    ) -> bool:
        return False


class FakeBackend:
    capabilities = ComputeBackendCapabilities(
        backend_name="python",
        display_name="Python fake",
        backend_kind="cpu",
        implementation="python",
        supports_parallel_search=False,
        supports_cooperative_cancellation=False,
        supports_device_selection=False,
        deterministic_search_order=True,
        preferred_batch_size=None,
        available=True,
    )

    def __init__(self) -> None:
        self.closed = False

    def search_nonce_range(
        self, work: PreparedMiningWork, start_nonce: int, stop_nonce: int
    ) -> NonceSearchResult:
        del work
        return NonceSearchResult(start_nonce, stop_nonce, stop_nonce - start_nonce, 10, None)

    def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def isolated_profile_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_module, "load_dotenv", lambda: False)
    for name in (
        "HASHPHERE_COMPUTE_PROFILE",
        "HASHPHERE_COMPUTE_BACKEND",
        "HASHPHERE_COMPUTE_WORKERS",
        "HASHPHERE_CUDA_DEVICE",
        "HASHPHERE_CUDA_DEVICES",
        "HASHPHERE_CUDA_THREADS_PER_BLOCK",
        "HASHPHERE_CHUNK_SIZE",
        "HASHPHERE_INTER_RANGE_DELAY_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)


def test_profile_info_is_offline_and_prints_only_sanitized_resolution(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli_module, "LocalComputeProfileCapabilities", FakeCapabilities)
    monkeypatch.setattr(
        cli_module.Settings,
        "from_env",
        classmethod(lambda cls: pytest.fail("profile-info must not load Stratum settings")),
    )

    assert cli_module.main(["profile-info", "--profile", "AUTO"]) == 0

    output = capsys.readouterr()
    assert output.err == ""
    assert "Requested profile: auto" in output.out
    assert "Backend: native-parallel" in output.out
    assert "CPU workers: 4" in output.out
    assert "Resolution reason: AutoNativeParallel" in output.out
    assert "password" not in output.out.lower()


def test_cli_profile_takes_precedence_over_environment_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HASHPHERE_COMPUTE_PROFILE", "lite")
    monkeypatch.setattr(cli_module, "LocalComputeProfileCapabilities", FakeCapabilities)

    resolved = cli_module._resolve_command_profile(
        cli_module._ProfileSelection(
            profile_name="max",
            overrides=ComputeProfileOverrides(),
            use_environment_profile=True,
        ),
        require_profile=True,
    )

    assert resolved is not None
    assert resolved.requested_profile == "max"
    assert resolved.backend_name == "native-parallel"
    assert resolved.worker_count == 8


def test_environment_profile_is_used_when_cli_profile_is_omitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HASHPHERE_COMPUTE_PROFILE", "auto")
    monkeypatch.setattr(cli_module, "LocalComputeProfileCapabilities", FakeCapabilities)

    resolved = cli_module._resolve_command_profile(
        cli_module._ProfileSelection(use_environment_profile=True),
        require_profile=True,
    )

    assert resolved is not None
    assert resolved.requested_profile == "auto"


def test_profile_benchmark_labels_raw_rate_and_does_not_apply_pacing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    backend = FakeBackend()
    monkeypatch.setattr(cli_module, "LocalComputeProfileCapabilities", FakeCapabilities)

    def select(
        name: str,
        workers: int | None,
        device: int | None,
        devices: tuple[int, ...] | None = None,
        *,
        threads_per_block: int = 256,
    ) -> FakeBackend:
        assert (name, workers, device, devices, threads_per_block) == (
            "python",
            None,
            None,
            None,
            256,
        )
        return backend

    monkeypatch.setattr(cli_module, "_select_benchmark_compute_backend", select)

    assert (
        cli_module.main(
            [
                "compute-benchmark",
                "--profile",
                "custom",
                "--backend",
                "python",
                "--hash-count",
                "3",
                "--inter-range-delay-seconds",
                "0.5",
            ]
        )
        == 0
    )

    output = capsys.readouterr()
    assert output.err == ""
    assert "Requested profile: custom" in output.out
    assert "Profile chunk size: 3" in output.out
    assert "raw compute rate; profile pacing is not applied" in output.out
    assert backend.closed


def test_legacy_continuous_parser_remains_unchanged_when_profile_is_omitted() -> None:
    plan, _, _, selection = cli_module._parse_profiled_continuous_mining_arguments(
        ["--chunk-size", "7", "--max-chunks", "2"]
    )

    assert plan.chunk_size == 7
    assert plan.inter_range_delay_seconds == 0
    assert selection == cli_module._ProfileSelection()
