"""Static security contracts for automation, packaging, and operator boundaries."""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_FULL_ACTION_SHA = re.compile(r"^\s*-?\s*uses:\s*[^\s@]+@[0-9a-f]{40}(?:\s+#.*)?$")


def _workflow_text(name: str) -> str:
    return (_ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")


def test_every_external_action_is_pinned_and_checkout_drops_credentials() -> None:
    workflows = tuple((_ROOT / ".github" / "workflows").glob("*.yml"))
    assert workflows
    for workflow in workflows:
        lines = workflow.read_text(encoding="utf-8").splitlines()
        action_lines = [line for line in lines if "uses:" in line]
        assert action_lines
        assert all(_FULL_ACTION_SHA.fullmatch(line) for line in action_lines)
        text = "\n".join(lines)
        assert text.count("persist-credentials: false") == text.count("actions/checkout@")


def test_workflows_are_read_only_and_avoid_privileged_triggers_and_runners() -> None:
    for workflow in (_ROOT / ".github" / "workflows").glob("*.yml"):
        text = workflow.read_text(encoding="utf-8")
        assert "permissions:\n  contents: read" in text
        assert "pull_request_target" not in text
        assert "workflow_run" not in text
        assert "self-hosted" not in text
        assert not re.search(
            r"\b(?:contents|actions|checks|packages|security-events): write\b", text
        )


def test_security_workflow_is_scheduled_bounded_and_submission_free() -> None:
    text = _workflow_text("security.yml")
    for trigger in ("pull_request:", "push:", "schedule:", "workflow_dispatch:"):
        assert trigger in text
    for scanner in ("run-security-audit.sh source", "run-security-audit.sh image"):
        assert scanner in text
    for forbidden in (
        "HASHORB_BITCOIN_RPC",
        "HASHORB_STRATUM",
        "HASHORB_ENABLE_TRUE_SOLO",
        "solo-mine",
        "submitblock",
        "proposal",
        "HASHORB_BUILD_CUDA=1",
    ):
        assert forbidden not in text
    assert text.count("timeout-minutes:") == 2
    assert "upload-artifact" not in text


def test_security_scanners_are_version_and_checksum_pinned_with_redaction() -> None:
    text = (_ROOT / "scripts" / "run-security-audit.sh").read_text(encoding="utf-8")
    for version in ("bandit==1.9.4", "pip-audit==2.10.1", "zizmor==1.28.0"):
        assert version in text
    for version in ("actionlint_1.7.12", "gitleaks_8.30.1", "trivy_0.69.3"):
        assert version in text
    assert "sha256sum --check --status" in text
    assert 'gitleaks" git --redact' in text
    assert "report contents suppressed" in text
    assert "releases/latest" not in text
    assert ":latest" not in text


def test_dependabot_uses_supported_scoped_ecosystems_without_credentials() -> None:
    text = (_ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
    assert text.count("interval: weekly") == 3
    assert text.count("target-branch: main") == 3
    assert {match.group(1) for match in re.finditer(r"package-ecosystem: ([\w-]+)", text)} == {
        "github-actions",
        "docker",
        "uv",
    }
    assert "open-pull-requests-limit:" in text
    assert "registries:" not in text
    assert "ignore:" not in text


def test_docker_bases_are_digest_pinned_and_runtime_is_nonroot() -> None:
    text = (_ROOT / "Dockerfile").read_text(encoding="utf-8")
    from_lines = [line for line in text.splitlines() if line.startswith("FROM ")]
    assert len(from_lines) == 2
    assert all(re.search(r"@sha256:[0-9a-f]{64}\s+AS\s+", line) for line in from_lines)
    assert "USER hashorb" in text
    assert "HEALTHCHECK" in text
    for forbidden in ("HASHORB_BITCOIN_RPC_PASSWORD", "HASHORB_STRATUM_PASSWORD"):
        assert forbidden not in text


def test_container_vulnerability_exceptions_are_narrow_reviewed_and_expiring() -> None:
    ignore = (_ROOT / "security" / "trivy-image-ignore.yaml").read_text(encoding="utf-8")
    entries = ignore.split("  - id: ")[1:]
    assert entries
    assert all(re.match(r"CVE-\d{4}-\d+\n", entry) for entry in entries)
    assert all("expired_at: 2026-10-31" in entry for entry in entries)
    assert all("statement: HashOrb does not" in entry for entry in entries)
    assert "secrets:" not in ignore
    script = (_ROOT / "scripts" / "run-security-audit.sh").read_text(encoding="utf-8")
    assert script.count("--ignorefile") == 1
    assert "security/trivy-image-ignore.yaml" in script


def test_installers_remain_platform_scoped_dry_run_only_and_do_not_mine() -> None:
    unix = (_ROOT / "scripts" / "install-unix.sh").read_text(encoding="utf-8")
    windows = (_ROOT / "scripts" / "install-windows.ps1").read_text(encoding="utf-8")
    assert "Linux|Darwin" in unix
    assert "supports Linux and macOS only" in unix
    assert "--dry-run" in unix
    assert "[switch]$DryRun" in windows
    for text in (unix, windows):
        assert "solo-mine" not in text
        assert "stratum-mine" not in text
        assert "HASHORB_BITCOIN_RPC_PASSWORD" not in text
        assert "HASHORB_STRATUM_PASSWORD" not in text
