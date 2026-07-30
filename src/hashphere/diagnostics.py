"""Sanitized offline installation-readiness diagnostics."""

from __future__ import annotations

import os
import platform
import re
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from importlib import metadata
from importlib.util import find_spec
from pathlib import Path
from typing import Protocol

from hashphere.compute import LocalComputeProfileCapabilities, PythonSequentialBackend
from hashphere.config import ResolvedComputeProfile

_SAFE_ARCHITECTURE = re.compile(r"^[A-Za-z0-9_.-]+$")
_CONFIGURATION_NAMES = frozenset(
    {
        "HASHPHERE_BITCOIN_ADDRESS",
        "HASHPHERE_COMPUTE_BACKEND",
        "HASHPHERE_COMPUTE_PROFILE",
        "HASHPHERE_STRATUM_HOST",
        "HASHPHERE_STRATUM_PASSWORD",
        "HASHPHERE_STRATUM_PORT",
        "HASHPHERE_WORKER_NAME",
    }
)


class DoctorStatus(StrEnum):
    """Stable diagnostic severity categories."""

    READY = "ready"
    OPTIONAL_UNAVAILABLE = "optional unavailable"
    CONFIGURATION_NEEDED = "configuration needed"
    ERROR = "error"


class DoctorCapabilityProvider(Protocol):
    """Minimal local capability surface used by offline diagnostics."""

    def native_available(self) -> bool: ...

    def cuda_available(self, device_ordinal: int, threads_per_block: int) -> bool: ...


@dataclass(frozen=True, slots=True)
class DoctorCheck:
    """One sanitized low-cardinality readiness result."""

    name: str
    status: DoctorStatus
    detail: str


@dataclass(frozen=True, slots=True)
class DoctorReport:
    """Immutable ordered doctor output and its process status."""

    checks: tuple[DoctorCheck, ...]

    @property
    def exit_code(self) -> int:
        return 1 if any(check.status is DoctorStatus.ERROR for check in self.checks) else 0


def build_doctor_report(
    *,
    log_directory: Path,
    environment: Mapping[str, str] | None = None,
    environment_file_present: bool = False,
    resolved_profile: ResolvedComputeProfile | None = None,
    profile_requested: bool = False,
    profile_error: bool = False,
    probe_cuda_device: int | None = None,
    capabilities: DoctorCapabilityProvider | None = None,
) -> DoctorReport:
    """Run local checks without opening a network connection or exposing values."""

    selected_environment = os.environ if environment is None else environment
    selected_capabilities = capabilities or LocalComputeProfileCapabilities()
    checks: list[DoctorCheck] = [
        _version_check(),
        _python_check(),
        DoctorCheck("operating-system", DoctorStatus.READY, _operating_system_family()),
        DoctorCheck("architecture", DoctorStatus.READY, _safe_architecture()),
        DoctorCheck("installation", DoctorStatus.READY, _installation_type()),
        _python_backend_check(),
    ]
    checks.extend(_optional_compute_checks(selected_capabilities))
    checks.append(_cuda_extension_check())
    if probe_cuda_device is not None:
        checks.append(_cuda_probe_check(selected_capabilities, probe_cuda_device))
    checks.append(_profile_check(resolved_profile, profile_requested, profile_error))
    checks.append(_log_directory_check(log_directory))
    checks.extend(
        _configuration_checks(
            selected_environment,
            environment_file_present=environment_file_present,
        )
    )
    return DoctorReport(tuple(checks))


def _version_check() -> DoctorCheck:
    try:
        value = metadata.version("hashphere")
    except metadata.PackageNotFoundError:
        return DoctorCheck("hashphere-version", DoctorStatus.ERROR, "package metadata unavailable")
    if not value or value != value.strip():
        return DoctorCheck("hashphere-version", DoctorStatus.ERROR, "package metadata invalid")
    return DoctorCheck("hashphere-version", DoctorStatus.READY, value)


def _python_check() -> DoctorCheck:
    version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    status = DoctorStatus.READY if sys.version_info[:2] == (3, 13) else DoctorStatus.ERROR
    detail = f"CPython {version}" if platform.python_implementation() == "CPython" else version
    return DoctorCheck("python", status, detail)


def _operating_system_family() -> str:
    family = platform.system()
    return {"Darwin": "macOS", "Linux": "Linux", "Windows": "Windows"}.get(family, "Other")


def _safe_architecture() -> str:
    architecture = platform.machine()
    return (
        architecture if architecture and _SAFE_ARCHITECTURE.fullmatch(architecture) else "unknown"
    )


def _installation_type() -> str:
    try:
        distribution = metadata.distribution("hashphere")
    except metadata.PackageNotFoundError:
        return "source import"
    files = distribution.files or ()
    return (
        "editable installation"
        if any(str(item).startswith("__editable__") for item in files)
        else "installed distribution"
    )


def _python_backend_check() -> DoctorCheck:
    try:
        available = PythonSequentialBackend().capabilities.available
    except Exception:
        available = False
    return DoctorCheck(
        "python-backend",
        DoctorStatus.READY if available else DoctorStatus.ERROR,
        "available" if available else "unavailable",
    )


def _optional_compute_checks(
    capabilities: DoctorCapabilityProvider,
) -> tuple[DoctorCheck, DoctorCheck]:
    try:
        native_available = capabilities.native_available()
    except Exception:
        native_available = False
    status = DoctorStatus.READY if native_available else DoctorStatus.OPTIONAL_UNAVAILABLE
    detail = "available" if native_available else "optional extension unavailable"
    return (
        DoctorCheck("native-backend", status, detail),
        DoctorCheck("native-parallel-backend", status, detail),
    )


def _cuda_extension_check() -> DoctorCheck:
    try:
        available = find_spec("hashphere.compute._cuda") is not None
    except (ImportError, ValueError):
        available = False
    return DoctorCheck(
        "cuda-extension",
        DoctorStatus.READY if available else DoctorStatus.OPTIONAL_UNAVAILABLE,
        "installed; device not probed" if available else "optional extension unavailable",
    )


def _cuda_probe_check(
    capabilities: DoctorCapabilityProvider,
    ordinal: int,
) -> DoctorCheck:
    try:
        available = capabilities.cuda_available(ordinal, 256)
    except Exception:
        available = False
    return DoctorCheck(
        "explicit-cuda-device",
        DoctorStatus.READY if available else DoctorStatus.ERROR,
        f"usable ordinal count: {1 if available else 0}",
    )


def _profile_check(
    resolved_profile: ResolvedComputeProfile | None,
    requested: bool,
    failed: bool,
) -> DoctorCheck:
    if failed:
        return DoctorCheck("profile-resolution", DoctorStatus.ERROR, "configuration is invalid")
    if resolved_profile is not None:
        return DoctorCheck(
            "profile-resolution",
            DoctorStatus.READY,
            (
                f"{resolved_profile.effective_profile} -> {resolved_profile.backend_name} "
                f"({resolved_profile.resolution_reason})"
            ),
        )
    if requested:
        return DoctorCheck("profile-resolution", DoctorStatus.ERROR, "profile did not resolve")
    return DoctorCheck(
        "profile-resolution",
        DoctorStatus.READY,
        "policy available; no profile selected",
    )


def _log_directory_check(log_directory: Path) -> DoctorCheck:
    try:
        log_directory.mkdir(parents=True, exist_ok=True)
        if not log_directory.is_dir():
            raise OSError
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix=".hashsphere-doctor-",
            dir=log_directory,
        ) as stream:
            stream.write("ready\n")
            stream.flush()
    except (OSError, UnicodeError, ValueError):
        return DoctorCheck("log-directory", DoctorStatus.ERROR, "not writable")
    return DoctorCheck("log-directory", DoctorStatus.READY, "writable")


def _configuration_checks(
    environment: Mapping[str, str],
    *,
    environment_file_present: bool,
) -> tuple[DoctorCheck, DoctorCheck]:
    configured_names = {name for name in _CONFIGURATION_NAMES if environment.get(name, "").strip()}
    source_present = environment_file_present or bool(configured_names)
    address_present = bool(environment.get("HASHPHERE_BITCOIN_ADDRESS", "").strip())
    return (
        DoctorCheck(
            "configuration-source",
            DoctorStatus.READY if source_present else DoctorStatus.CONFIGURATION_NEEDED,
            "present; values hidden" if source_present else "no environment configuration detected",
        ),
        DoctorCheck(
            "stratum-configuration",
            DoctorStatus.READY if address_present else DoctorStatus.CONFIGURATION_NEEDED,
            "complete enough to validate at runtime; values hidden"
            if address_present
            else "payout configuration is required only for live commands",
        ),
    )


def format_doctor_report(report: DoctorReport) -> str:
    """Render stable plain UTF-8 diagnostics without paths or configuration values."""

    lines = ["Hashphere doctor."]
    counts = {status: 0 for status in DoctorStatus}
    for check in report.checks:
        counts[check.status] += 1
        lines.append(f"[{check.status.value}] {check.name}: {check.detail}")
    lines.append(
        "Summary: " + ", ".join(f"{status.value}={counts[status]}" for status in DoctorStatus)
    )
    return "\n".join(lines)
