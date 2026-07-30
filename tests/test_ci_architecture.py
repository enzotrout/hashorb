"""Static safety and breadth checks for packaging CI."""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def test_cpu_ci_covers_three_os_families_and_release_gates() -> None:
    text = (_ROOT / ".github" / "workflows" / "packaging.yml").read_text(encoding="utf-8")

    for runner in ("ubuntu-latest", "macos-latest", "windows-latest"):
        assert runner in text
    for command in (
        "uv sync --locked",
        "uv run pytest -q",
        "uv run ruff check .",
        "uv run ruff format --check .",
        "uv run mypy src",
        "uv lock --check",
        "uv build --out-dir dist",
        "verify-distributions.py dist",
        "smoke-installed-distribution.py dist",
    ):
        assert command in text
    assert "contents: read" in text


def test_ci_is_cpu_only_private_and_never_runs_live_commands() -> None:
    text = (_ROOT / ".github" / "workflows" / "packaging.yml").read_text(encoding="utf-8")

    assert 'HASHPHERE_BUILD_CUDA: "0"' in text
    assert "stratum-mine" not in text
    assert "stratum-handshake" not in text
    assert "HASHPHERE_BITCOIN_ADDRESS:" not in text
    assert "HASHPHERE_STRATUM_PASSWORD:" not in text
    assert "secrets." not in text


def test_docker_ci_validates_default_doctor_argument_forwarding_and_nonroot() -> None:
    text = (_ROOT / ".github" / "workflows" / "packaging.yml").read_text(encoding="utf-8")

    assert "docker build --tag hashphere:ci ." in text
    assert "docker run --rm hashphere:ci\n" in text
    assert "docker run --rm hashphere:ci --help" in text
    assert "--entrypoint id" in text
