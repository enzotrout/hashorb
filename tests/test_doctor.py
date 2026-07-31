"""Offline and privacy tests for installation readiness diagnostics."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

import hashorb.__main__ as cli_module
import hashorb.diagnostics as diagnostics
from hashorb.config import ResolvedComputeProfile
from hashorb.diagnostics import DoctorStatus, build_doctor_report, format_doctor_report


class FakeCapabilities:
    def __init__(self, *, native: bool = False, cuda: bool = False) -> None:
        self.native = native
        self.cuda = cuda
        self.cuda_calls: list[tuple[int, int]] = []

    def logical_cpu_count(self) -> int | None:
        return 8

    def native_available(self) -> bool:
        return self.native

    def cuda_available(self, device_ordinal: int, threads_per_block: int) -> bool:
        self.cuda_calls.append((device_ordinal, threads_per_block))
        return self.cuda

    def cuda_multi_available(
        self, device_ordinals: tuple[int, ...], threads_per_block: int
    ) -> bool:
        return False


@pytest.fixture
def stable_package_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(diagnostics.metadata, "version", lambda name: "0.1.0")

    class Distribution:
        files = ()

    monkeypatch.setattr(diagnostics.metadata, "distribution", lambda name: Distribution())


def test_cpu_only_doctor_is_successful_private_and_does_not_probe_cuda(
    stable_package_metadata: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    capabilities = FakeCapabilities(native=False, cuda=False)
    secret = "synthetic-secret-must-not-appear"
    monkeypatch.setattr(diagnostics, "find_spec", lambda name: None)

    report = build_doctor_report(
        log_directory=tmp_path / "logs",
        environment={
            "HASHORB_BITCOIN_ADDRESS": secret,
            "HASHORB_STRATUM_PASSWORD": secret,
        },
        environment_file_present=True,
        capabilities=capabilities,
    )
    output = format_doctor_report(report)

    assert report.exit_code == 0
    assert "[ready] python-backend: available" in output
    assert "[optional unavailable] native-backend" in output
    assert "[optional unavailable] cuda-extension" in output
    assert "values hidden" in output
    assert secret not in output
    assert capabilities.cuda_calls == []
    assert (tmp_path / "logs").is_dir()
    assert list((tmp_path / "logs").iterdir()) == []


def test_explicit_cuda_probe_reports_only_usable_ordinal_count(
    stable_package_metadata: None,
    tmp_path,
) -> None:
    capabilities = FakeCapabilities(cuda=True)

    report = build_doctor_report(
        log_directory=tmp_path / "logs",
        probe_cuda_device=7,
        capabilities=capabilities,
    )

    check = next(item for item in report.checks if item.name == "explicit-cuda-device")
    assert check.status is DoctorStatus.READY
    assert check.detail == "usable ordinal count: 1"
    assert capabilities.cuda_calls == [(7, 256)]


def test_profile_readiness_uses_only_sanitized_resolution(
    stable_package_metadata: None,
    tmp_path,
) -> None:
    profile = ResolvedComputeProfile(
        requested_profile="auto",
        effective_profile="auto",
        backend_name="native-parallel",
        worker_count=4,
        cuda_device=None,
        cuda_devices=None,
        cuda_threads_per_block=None,
        chunk_size=5_000_000,
        inter_range_delay_seconds=0,
        resolution_reason="AutoNativeParallel",
    )

    report = build_doctor_report(
        log_directory=tmp_path / "logs",
        resolved_profile=profile,
        profile_requested=True,
        capabilities=FakeCapabilities(),
    )

    check = next(item for item in report.checks if item.name == "profile-resolution")
    assert check.status is DoctorStatus.READY
    assert check.detail == "auto -> native-parallel (AutoNativeParallel)"


def test_failed_required_log_check_returns_nonzero_without_raw_error(
    stable_package_metadata: None,
    tmp_path,
) -> None:
    blocking_file = tmp_path / "not-a-directory"
    blocking_file.write_text("synthetic", encoding="utf-8")

    report = build_doctor_report(
        log_directory=blocking_file,
        capabilities=FakeCapabilities(),
    )

    assert report.exit_code == 1
    assert "[error] log-directory: not writable" in format_doctor_report(report)


def test_doctor_cli_is_offline_and_uses_no_stratum_settings(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path,
) -> None:
    monkeypatch.setattr(cli_module, "load_hashorb_environment", lambda: False)
    monkeypatch.delenv("HASHORB_COMPUTE_PROFILE", raising=False)
    monkeypatch.setattr(
        cli_module.Settings,
        "from_env",
        classmethod(lambda cls: pytest.fail("doctor must not load Stratum settings")),
    )

    assert cli_module.main(["doctor", "--log-dir", str(tmp_path / "logs")]) == 0

    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out.startswith("HashOrb doctor.\n")
    assert "stratum-configuration" in captured.out


def test_doctor_profile_error_is_sanitized_and_nonzero(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path,
) -> None:
    monkeypatch.setattr(cli_module, "load_hashorb_environment", lambda: False)
    monkeypatch.delenv("HASHORB_COMPUTE_PROFILE", raising=False)

    assert (
        cli_module.main(
            [
                "doctor",
                "--profile",
                "lite",
                "--workers",
                "99",
                "--log-dir",
                str(tmp_path / "logs"),
            ]
        )
        == 1
    )

    captured = capsys.readouterr()
    assert captured.err == ""
    assert "[error] profile-resolution: configuration is invalid" in captured.out


def test_doctor_report_is_immutable(stable_package_metadata: None, tmp_path) -> None:
    report = build_doctor_report(
        log_directory=tmp_path / "logs",
        capabilities=FakeCapabilities(),
    )

    with pytest.raises(FrozenInstanceError):
        report.checks[0].detail = "changed"  # type: ignore[misc]
