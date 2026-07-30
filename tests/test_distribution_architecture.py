"""Deterministic checks for the portable distribution boundary."""

from __future__ import annotations

import os
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path, PureWindowsPath

import pytest

from hashphere import __main__ as cli_module

_ROOT = Path(__file__).resolve().parents[1]


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


def test_unix_installer_is_cpu_only_strict_and_has_a_dry_run() -> None:
    script = _ROOT / "scripts" / "install-unix.sh"
    text = script.read_text(encoding="utf-8")

    assert "set -euo pipefail" in text
    assert "--no-python-downloads" in text
    assert "uv tool install" in text
    assert "sudo" not in text
    assert "curl" not in text
    assert "HASHPHERE_BUILD_CUDA" not in text
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
