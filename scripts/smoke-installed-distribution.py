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


def _run(command: list[str], *, cwd: Path, environment: dict[str, str]) -> None:
    result = subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise SmokeError(f"offline command failed: {command[1] if len(command) > 1 else 'cli'}")


def smoke_distribution(distribution_directory: Path) -> None:
    """Install the one wheel in a temporary venv and run only offline commands."""

    wheels = sorted(distribution_directory.glob("hashphere-*.whl"))
    if len(wheels) != 1:
        raise SmokeError("expected exactly one Hashsphere wheel")
    uv = shutil.which("uv")
    if uv is None:
        raise SmokeError("uv is required")

    with tempfile.TemporaryDirectory(prefix="hashsphere-installed-smoke-") as temporary:
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
        command = scripts_directory / ("hashsphere.exe" if os.name == "nt" else "hashsphere")
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
            name: value for name, value in os.environ.items() if not name.startswith("HASHPHERE_")
        }
        environment["PYTHON_DOTENV_DISABLED"] = "1"
        commands = [
            [str(command), "--help"],
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
