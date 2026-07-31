"""Deterministic checks for the portable distribution boundary."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path, PureWindowsPath

import pytest

from hashphere import __main__ as cli_module

_ROOT = Path(__file__).resolve().parents[1]
_UNIX_INSTALLER_SUPPORTED = sys.platform.startswith("linux") or sys.platform == "darwin"


def test_release_metadata_has_one_console_entry_and_honest_python_range() -> None:
    metadata = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = metadata["project"]

    assert project["version"] == "0.1.0"
    assert project["requires-python"] == ">=3.13,<3.14"
    assert project["readme"] == "README.md"
    assert project["scripts"] == {"hashsphere": "hashphere.__main__:main"}
    assert not hasattr(__import__("hashphere"), "__version__")


def test_console_entry_forwards_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_module.sys, "argv", ["hashsphere", "--help"])
    assert cli_module.main() == 0


def test_unix_installer_is_cpu_only_strict_and_platform_guarded() -> None:
    script = _ROOT / "scripts" / "install-unix.sh"
    text = script.read_text(encoding="utf-8")

    assert "set -euo pipefail" in text
    assert 'case "$(uname -s)"' in text
    assert "Linux|Darwin" in text
    assert "This installer supports Linux and macOS only." in text
    assert "--no-python-downloads" in text
    assert "uv tool install" in text
    assert "sudo" not in text
    assert "curl" not in text
    assert "HASHPHERE_BUILD_CUDA" not in text


@pytest.mark.skipif(
    not _UNIX_INSTALLER_SUPPORTED,
    reason="the Unix installer is supported only on Linux and macOS",
)
def test_unix_installer_dry_run_passes_on_supported_platform() -> None:
    script = _ROOT / "scripts" / "install-unix.sh"
    result = subprocess.run(
        ["bash", str(script), "install", "--dry-run"],
        cwd=_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0
    assert "uv tool install" in result.stdout
    assert "hashsphere doctor" in result.stdout
    assert "HASHPHERE_" not in result.stdout
    assert result.stderr == ""


def test_windows_installer_is_user_local_utf8_and_cpu_only() -> None:
    text = (_ROOT / "scripts" / "install-windows.ps1").read_text(encoding="utf-8")

    assert '$ErrorActionPreference = "Stop"' in text
    assert "UTF8Encoding" in text
    assert "uv tool install" in text.replace('", "', " ").replace('"', "")
    assert "--no-python-downloads" in text
    assert "Set-ExecutionPolicy" not in text
    assert "sudo" not in text
    assert "HASHPHERE_BUILD_CUDA" not in text


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="the PowerShell installer executes only on Windows",
)
def test_windows_powershell_installer_dry_run_passes_on_windows() -> None:
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if powershell is None:
        pytest.fail("Windows packaging CI requires PowerShell")
    script = _ROOT / "scripts" / "install-windows.ps1"

    result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-File",
            str(script),
            "-Action",
            "install",
            "-DryRun",
        ],
        cwd=_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert result.returncode == 0
    assert "uv tool install" in result.stdout
    assert "hashsphere doctor" in result.stdout
    assert "HASHPHERE_" not in result.stdout
    assert result.stderr == ""


@pytest.mark.parametrize("reported_platform", ["MINGW64_NT", "MSYS_NT", "CYGWIN_NT"])
@pytest.mark.skipif(
    not _UNIX_INSTALLER_SUPPORTED,
    reason="the Unix guard harness requires a supported POSIX host",
)
def test_unix_installer_rejects_windows_compatibility_shells_even_for_dry_run(
    reported_platform: str,
    tmp_path: Path,
) -> None:
    fake_uname = tmp_path / "uname"
    fake_uname.write_text(
        f"#!/usr/bin/env sh\nprintf '%s\\n' '{reported_platform}'\n",
        encoding="utf-8",
        newline="\n",
    )
    fake_uname.chmod(0o755)
    environment = os.environ.copy()
    environment["PATH"] = f"{tmp_path}{os.pathsep}{environment['PATH']}"

    result = subprocess.run(
        ["bash", str(_ROOT / "scripts" / "install-unix.sh"), "install", "--dry-run"],
        cwd=_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == "This installer supports Linux and macOS only.\n"


def test_installer_documentation_matches_platform_boundaries() -> None:
    linux = (_ROOT / "platform" / "linux" / "README.md").read_text(encoding="utf-8")
    macos = (_ROOT / "platform" / "macos" / "README.md").read_text(encoding="utf-8")
    windows = (_ROOT / "platform" / "windows" / "README.md").read_text(encoding="utf-8")
    installation = (_ROOT / "docs" / "13-installation-and-packaging.md").read_text(encoding="utf-8")

    assert "scripts/install-unix.sh" in linux
    assert "scripts/install-unix.sh" in macos
    assert "scripts/install-windows.ps1" in windows
    assert "scripts/install-unix.sh" not in windows
    assert "The same Unix script handles Darwin." in installation
    assert "Use a normal, non-administrator PowerShell session" in installation


def test_installers_never_start_mining_or_reference_configuration_values() -> None:
    installers = (
        _ROOT / "scripts" / "install-unix.sh",
        _ROOT / "scripts" / "install-windows.ps1",
    )

    for installer in installers:
        text = installer.read_text(encoding="utf-8")
        assert "stratum-" not in text
        assert "HASHPHERE_" not in text
        assert ".env" not in text


def test_windows_style_log_path_remains_a_single_cli_value() -> None:
    path = PureWindowsPath("D:/Hashsphere Data/Hashsphere Logs/events.jsonl")
    parsed = cli_module._parse_doctor_arguments(["--log-dir", str(path.parent)])

    assert os.fspath(parsed[1]) == str(path.parent)


def test_distribution_verifier_rejects_private_environment_and_cuda_binary(
    tmp_path: Path,
) -> None:
    verifier_path = _ROOT / "scripts" / "verify-distributions.py"
    archive = tmp_path / "synthetic.whl"
    with zipfile.ZipFile(archive, mode="w") as stream:
        stream.writestr("hashsphere-0.1.0/.env", "synthetic")
        stream.writestr(
            "hashsphere/compute/_cuda.cpython-313-aarch64-linux-gnu.so",
            b"synthetic",
        )

    result = subprocess.run(
        [sys.executable, str(verifier_path), str(archive)],
        cwd=_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert "private environment file" in result.stderr


def test_distribution_verifier_help_is_utf8_and_offline() -> None:
    result = subprocess.run(
        [sys.executable, str(_ROOT / "scripts" / "verify-distributions.py"), "--help"],
        cwd=_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert result.returncode == 0
    assert "Inspect CPU distribution archives" in result.stdout
    assert result.stderr == ""


def test_environment_template_uses_one_nonconflicting_configuration_vocabulary() -> None:
    text = (_ROOT / ".env.example").read_text(encoding="utf-8")
    active_names = {
        line.partition("=")[0]
        for line in text.splitlines()
        if line and not line.startswith("#") and "=" in line
    }

    assert active_names == {
        "HASHPHERE_BITCOIN_ADDRESS",
        "HASHPHERE_SEARCH_STRATEGY",
        "HASHPHERE_STRATUM_HOST",
        "HASHPHERE_STRATUM_PASSWORD",
        "HASHPHERE_STRATUM_PORT",
        "HASHPHERE_WORKER_NAME",
    }
    assert "HASHPHERE_COMPUTE_PROFILE=custom" in text
    assert "Lifecycle limits, liveness thresholds, reconnect policy" in text
    assert "YOUR_BITCOIN_ADDRESS" in text
    assert len(re.findall(r"^HASHPHERE_COMPUTE_PROFILE=", text, flags=re.MULTILINE)) == 0
    assert "# HASHPHERE_ENABLE_TRUE_SOLO_HASHING=1" in text
    assert len(re.findall(r"^HASHPHERE_ENABLE_TRUE_SOLO_HASHING=", text, flags=re.MULTILINE)) == 0


def test_bitcoin_core_documentation_preserves_three_command_boundaries() -> None:
    readme = (_ROOT / "README.md").read_text(encoding="utf-8")
    architecture = (_ROOT / "ARCHITECTURE.md").read_text(encoding="utf-8")
    true_solo = (_ROOT / "docs" / "14-bitcoin-core-true-solo.md").read_text(encoding="utf-8")

    for text in (readme, architecture, true_solo):
        assert "bitcoin-core-check" in text
        assert "solo-hash" in text
        assert "solo-mine" in text
    assert "cannot earn a reward" in readme
    assert "no proposal callable" in architecture
    assert "HASHPHERE_ENABLE_TRUE_SOLO_HASHING=1" in true_solo


def test_platform_directories_contain_no_miner_copy() -> None:
    files = [path for path in (_ROOT / "platform").rglob("*") if path.is_file()]

    assert files
    assert {path.suffix for path in files} == {".md"}


def test_distribution_guidance_and_scripts_contain_no_personal_paths() -> None:
    roots = (_ROOT / "docs", _ROOT / "platform", _ROOT / "scripts")
    markers = ("/" + "home" + "/", "\\" + "Users" + "\\", "C:/" + "Users" + "/")

    for root in roots:
        for path in root.rglob("*"):
            if path.is_file() and path.suffix in {".md", ".ps1", ".py", ".sh"}:
                text = path.read_text(encoding="utf-8")
                assert not any(marker in text for marker in markers), path.name
