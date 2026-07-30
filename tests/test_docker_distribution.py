"""Static contracts for the production CPU container boundary."""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def test_dockerfile_builds_cpu_wheel_and_has_a_minimal_nonroot_runtime() -> None:
    text = (_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "ARG PYTHON_VERSION=3.13.14" in text
    assert "-bookworm AS builder" in text
    assert "-slim-bookworm AS runtime" in text
    assert "HASHPHERE_BUILD_CUDA=0" in text
    assert "python -m pip wheel" in text
    assert "USER hashphere" in text
    assert 'ENTRYPOINT ["hashsphere"]' in text
    assert 'CMD ["doctor", "--log-dir", "/app/logs"]' in text
    assert "uv run" not in text
    assert "stratum-mine" not in text
    assert "HASHPHERE_BITCOIN_ADDRESS" not in text
    assert "HASHPHERE_STRATUM_PASSWORD" not in text


def test_docker_exec_entrypoint_forwards_arguments_and_owns_signals() -> None:
    lines = (_ROOT / "Dockerfile").read_text(encoding="utf-8").splitlines()
    entrypoint = next(line for line in lines if line.startswith("ENTRYPOINT"))

    assert entrypoint == 'ENTRYPOINT ["hashsphere"]'
    assert "sh -c" not in entrypoint
    assert "bash -c" not in entrypoint


def test_docker_context_is_an_allowlist_with_explicit_private_exclusions() -> None:
    lines = (_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()

    for required in (
        ".env",
        ".git",
        ".venv",
        "credentials",
        "secrets",
        "logs",
        "*.so",
        "*.pyd",
        "build",
        "dist",
        "**",
        "!src/**",
    ):
        assert required in lines
    assert lines.index("**") < lines.index("!src/**")
