#!/usr/bin/env python3
"""Create a clean environment and smoke-test an installed CPU wheel."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


class SmokeError(RuntimeError):
    """A clean-installed distribution failed an offline smoke check."""


def _capture(command: list[str], *, cwd: Path, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _run(command: list[str], *, cwd: Path, environment: dict[str, str]) -> None:
    result = _capture(command, cwd=cwd, environment=environment)
    if result.returncode != 0:
        raise SmokeError(f"offline command failed: {command[1] if len(command) > 1 else 'cli'}")


def _assert_installed_dotenv_loading(
    *,
    command: Path,
    python: Path,
    root: Path,
    base_environment: dict[str, str],
) -> None:
    """Exercise real installed-package .env discovery from a nested working directory."""

    configured_root = root / "configured"
    nested_directory = configured_root / "nested"
    nested_directory.mkdir(parents=True)
    (configured_root / ".env").write_text(
        "\n".join(
            [
                "HASHORB_BITCOIN_ADDRESS=bc1qinstalledsmokeaddress",
                "HASHORB_STRATUM_HOST=stratum.ckpool.org",
                "HASHORB_STRATUM_PORT=3333",
                "HASHORB_WORKER_NAME=auto",
                "HASHORB_STRATUM_PASSWORD=x",
                "",
            ]
        ),
        encoding="utf-8",
        newline="",
    )

    dotenv_environment = dict(base_environment)
    dotenv_environment.pop("PYTHON_DOTENV_DISABLED", None)

    doctor = _capture(
        [str(command), "doctor", "--log-dir", "logs"],
        cwd=nested_directory,
        environment=dotenv_environment,
    )
    if doctor.returncode != 0:
        raise SmokeError("installed CLI doctor failed while loading parent .env")
    if "[ready] configuration-source: present; values hidden" not in doctor.stdout:
        raise SmokeError("installed CLI did not discover parent .env from working directory")
    if "[ready] stratum-configuration: complete enough to validate at runtime; values hidden" not in doctor.stdout:
        raise SmokeError("installed CLI did not load HASHORB_BITCOIN_ADDRESS from .env")

    _run(
        [
            str(python),
            "-I",
            "-c",
            (
                "from hashorb.config.settings import Settings; "
                "assert Settings.from_env().bitcoin_address == 'bc1qinstalledsmokeaddress'"
            ),
        ],
        cwd=nested_directory,
        environment=dotenv_environment,
    )

    overridden_environment = dict(dotenv_environment)
    overridden_environment["HASHORB_BITCOIN_ADDRESS"] = "bc1qprocessoverride"
    _run(
        [
            str(python),
            "-I",
            "-c",
            (
                "from hashorb.config.settings import Settings; "
                "assert Settings.from_env().bitcoin_address == 'bc1qprocessoverride'"
            ),
        ],
        cwd=nested_directory,
        environment=overridden_environment,
    )

    unrelated_directory = root / "unrelated"
    unrelated_directory.mkdir()
    unrelated = _capture(
        [
            str(python),
            "-I",
            "-c",
            (
                "from hashorb.config.settings import Settings; "
                "Settings.from_env()"
            ),
        ],
        cwd=unrelated_directory,
        environment=dotenv_environment,
    )
    if unrelated.returncode == 0:
        raise SmokeError("installed package loaded .env from an unrelated directory")
    if "HASHORB_BITCOIN_ADDRESS is required" not in unrelated.stderr:
        raise SmokeError("unrelated-directory .env negative smoke failed unexpectedly")


def smoke_distribution(distribution_directory: Path) -> None:
    """Install the one wheel in a temporary venv and run only offline commands."""

    wheels = sorted(distribution_directory.glob("hashorb-*.whl"))
    if len(wheels) != 1:
        raise SmokeError("expected exactly one HashOrb wheel")
    uv = shutil.which("uv")
    if uv is None:
        raise SmokeError("uv is required")

    with tempfile.TemporaryDirectory(prefix="hashorb-installed-smoke-") as temporary:
        root = Path(temporary)
        environment_directory = root / "venv"
        subprocess.run(
            [uv, "venv", "--python", "3.13", "--no-python-downloads", str(environment_directory)],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        scripts_directory = environment_directory / ("Scripts" if os.name == "nt" else "bin")
        python = scripts_directory / ("python.exe" if os.name == "nt" else "python")
        command = scripts_directory / ("hashorb.exe" if os.name == "nt" else "hashorb")
        legacy_commands = [
            scripts_directory / (f"{name}.exe" if os.name == "nt" else name)
            for name in ("hashphere", "hashsphere")
        ]
        subprocess.run(
            [uv, "pip", "install", "--python", str(python), str(wheels[0])],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

        run_directory = root / "run"
        run_directory.mkdir()
        empty_log = run_directory / "empty.jsonl"
        empty_log.write_text("", encoding="utf-8", newline="")
        environment = {
            name: value
            for name, value in os.environ.items()
            if not name.startswith(("HASHORB_", "HASHSPHERE_", "HASHPHERE_"))
        }
        environment["PYTHON_DOTENV_DISABLED"] = "1"
        commands = [
            [str(command), "--help"],
            [str(python), "-I", "-m", "hashorb", "--help"],
            [str(command), "bitcoin-core-check", "--help"],
            [str(command), "solo-hash", "--help"],
            [str(command), "solo-mine", "--help"],
            [str(command), "doctor", "--log-dir", "logs"],
            *[
                [str(command), "profile-info", "--profile", profile]
                for profile in ("lite", "auto", "max")
            ],
            [
                str(command),
                "profile-info",
                "--profile",
                "custom",
                "--backend",
                "python",
                "--chunk-size",
                "1000",
            ],
            [
                str(command),
                "compute-benchmark",
                "--backend",
                "python",
                "--hash-count",
                "16",
            ],
            [str(command), "logs-summary", "--log-file", str(empty_log)],
        ]
        for smoke_command in commands:
            _run(smoke_command, cwd=run_directory, environment=environment)
        _run(
            [
                str(python),
                "-I",
                "-c",
                (
                    "import importlib.metadata, importlib.util; "
                    "assert importlib.metadata.version('hashorb') == '0.1.0'; "
                    "assert importlib.util.find_spec('hashorb') is not None; "
                    "assert importlib.util.find_spec('hashphere') is None; "
                    "assert importlib.util.find_spec('hashsphere') is None"
                ),
            ],
            cwd=run_directory,
            environment=environment,
        )
        _assert_installed_dotenv_loading(
            command=command,
            python=python,
            root=root,
            base_environment=environment,
        )
        if any(path.exists() for path in legacy_commands):
            raise SmokeError("a legacy console command is installed")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("distribution_directory", type=Path)
    arguments = parser.parse_args(argv)
    try:
        smoke_distribution(arguments.distribution_directory)
    except SmokeError as exc:
        print(f"installed distribution smoke failed: {exc}", file=sys.stderr)
        return 1
    except (OSError, subprocess.SubprocessError):
        print("installed distribution smoke failed: local installation error", file=sys.stderr)
        return 1
    print("installed distribution smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
