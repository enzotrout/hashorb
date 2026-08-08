from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

from scripts import dev_helper
from scripts.dev_helper import CommandResult, DevError, DevHelper

ROOT = Path(__file__).resolve().parents[1]


class RecordingRunner:
    def __init__(self, responses: dict[tuple[str, ...], CommandResult]) -> None:
        self.responses = responses
        self.calls: list[tuple[tuple[str, ...], Path]] = []

    def __call__(self, args: Sequence[str], cwd: Path) -> CommandResult:
        command = tuple(args)
        self.calls.append((command, cwd))
        try:
            return self.responses[command]
        except KeyError as exc:
            raise AssertionError(f"unexpected command: {command}") from exc


def result(args: tuple[str, ...], stdout: str = "", returncode: int = 0) -> CommandResult:
    return CommandResult(args=args, returncode=returncode, stdout=stdout)


def tools(name: str) -> str:
    return f"/tools/{name}"


def probe_json(
    *,
    python_version: str = "3.13.7",
    native_available: bool = True,
    cuda_available: bool = False,
) -> str:
    backends = [
        {"name": "python", "available": True, "reason": None},
        {
            "name": "native",
            "available": native_available,
            "reason": None if native_available else "ExtensionNotInstalled",
        },
        {
            "name": "native-parallel",
            "available": native_available,
            "reason": None if native_available else "ExtensionNotInstalled",
        },
        {
            "name": "cuda",
            "available": cuda_available,
            "reason": None if cuda_available else "ExtensionNotInstalled",
        },
        {
            "name": "cuda-multi",
            "available": False,
            "reason": "NotInitialized",
        },
    ]
    return json.dumps({"python_version": python_version, "backends": backends}, sort_keys=True)


def repository_responses(
    root: Path,
    *,
    branch: str = "local/dev-workflow-helper-v2",
    status: str = "",
) -> dict[tuple[str, ...], CommandResult]:
    commands = {
        ("git", "rev-parse", "--show-toplevel"): str(root),
        ("git", "branch", "--show-current"): branch,
        ("git", "status", "--short"): status,
    }
    return {command: result(command, stdout) for command, stdout in commands.items()}


@pytest.mark.parametrize("help_args", [("--help",), ("help",)])
def test_wrapper_help_is_cross_platform(help_args: tuple[str, ...]) -> None:
    completed = subprocess.run(
        [sys.executable, str(ROOT / "dev"), *help_args],
        cwd=ROOT,
        shell=False,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert "HashOrb development workflow helper" in completed.stdout
    assert "doctor" in completed.stdout
    assert "review" in completed.stdout


def test_status_reports_environment_without_mutating_it(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "repo with spaces"
    responses = repository_responses(root)
    responses[("uv", "--version")] = result(("uv", "--version"), "uv 0.12.0")
    probe = ("uv", "run", "--no-sync", "python", "-c", dev_helper._BACKEND_PROBE)
    responses[probe] = result(probe, probe_json())
    runner = RecordingRunner(responses)

    exit_code = DevHelper(root=root, runner=runner, which=tools).status()

    output = capsys.readouterr().out
    assert exit_code == 0
    assert f"repository: {root.resolve()}" in output
    assert "working tree: clean" in output
    assert "backend python: available" in output
    assert "backend cuda: unavailable (ExtensionNotInstalled)" in output
    assert all("sync" not in command for command, _cwd in runner.calls)


def test_status_reports_dirty_tree(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "repo"
    responses = repository_responses(root, status=" M README.md")
    responses[("uv", "--version")] = result(("uv", "--version"), "uv 0.12.0")
    probe = ("uv", "run", "--no-sync", "python", "-c", dev_helper._BACKEND_PROBE)
    responses[probe] = result(probe, probe_json())
    runner = RecordingRunner(responses)

    assert DevHelper(root=root, runner=runner, which=tools).status() == 0

    assert "working tree: dirty" in capsys.readouterr().out


def test_status_propagates_git_failure(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    command = ("git", "rev-parse", "--show-toplevel")
    runner = RecordingRunner({command: result(command, returncode=2)})

    with pytest.raises(DevError, match="detect repository root failed with exit code 2"):
        DevHelper(root=root, runner=runner, which=tools).status()


def test_sync_rejects_dirty_tree_before_fetch(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    runner = RecordingRunner(repository_responses(root, status="?? notes.txt"))

    with pytest.raises(DevError, match="sync requires a clean working tree"):
        DevHelper(root=root, runner=runner, which=tools).sync()

    commands = [command for command, _cwd in runner.calls]
    assert ("git", "fetch", "--prune", "origin") not in commands


@pytest.mark.parametrize(
    ("failing_command", "expected_operation"),
    [
        (("git", "fetch", "--prune", "origin"), "fetch origin"),
        (("git", "switch", "main"), "switch to main"),
        (("git", "pull", "--ff-only", "origin", "main"), "fast-forward main"),
    ],
)
def test_sync_propagates_each_git_failure(
    tmp_path: Path,
    failing_command: tuple[str, ...],
    expected_operation: str,
) -> None:
    root = tmp_path / "repo"
    responses = repository_responses(root)
    ordered = [
        ("git", "fetch", "--prune", "origin"),
        ("git", "switch", "main"),
        ("git", "pull", "--ff-only", "origin", "main"),
    ]
    for command in ordered:
        responses[command] = result(command, returncode=7 if command == failing_command else 0)
    runner = RecordingRunner(responses)

    with pytest.raises(DevError, match=f"{expected_operation} failed with exit code 7"):
        DevHelper(root=root, runner=runner, which=tools).sync()

    called = [command for command, _cwd in runner.calls]
    failing_index = called.index(failing_command)
    for command in ordered[ordered.index(failing_command) + 1 :]:
        assert command not in called[failing_index + 1 :]


def test_missing_uv_is_clear_and_nonzero(tmp_path: Path) -> None:
    def missing_uv(name: str) -> str | None:
        return None if name == "uv" else f"/tools/{name}"

    runner = RecordingRunner({})

    with pytest.raises(DevError, match="uv is required but was not found on PATH"):
        DevHelper(root=tmp_path, runner=runner, which=missing_uv).start()

    assert runner.calls == []


def test_start_uses_locked_uv_sync(tmp_path: Path) -> None:
    command = ("uv", "sync", "--locked", "--no-python-downloads")
    runner = RecordingRunner({command: result(command)})

    assert DevHelper(root=tmp_path, runner=runner, which=tools).start() == 0

    assert [item[0] for item in runner.calls] == [command]


def test_check_runs_fast_gate_in_order(tmp_path: Path) -> None:
    commands = [
        ("uv", "run", "ruff", "format", "--check", "."),
        ("uv", "run", "ruff", "check", "."),
        ("uv", "run", "mypy", "src"),
        ("uv", "run", "pytest", "-q", "tests/test_dev_helper.py"),
        ("uv", "run", "python", "-c", dev_helper._BACKEND_PROBE),
    ]
    responses = {command: result(command) for command in commands}
    responses[commands[-1]] = result(commands[-1], probe_json())
    runner = RecordingRunner(responses)

    assert DevHelper(root=tmp_path, runner=runner, which=tools).check() == 0

    assert [command for command, _cwd in runner.calls] == commands


def test_check_stops_on_first_failure(tmp_path: Path) -> None:
    format_command = ("uv", "run", "ruff", "format", "--check", ".")
    lint_command = ("uv", "run", "ruff", "check", ".")
    runner = RecordingRunner(
        {
            format_command: result(format_command),
            lint_command: result(lint_command, returncode=4),
        }
    )

    with pytest.raises(DevError, match="lint failed with exit code 4"):
        DevHelper(root=tmp_path, runner=runner, which=tools).check()

    assert [command for command, _cwd in runner.calls] == [format_command, lint_command]


def test_doctor_is_repeatable_and_allows_optional_cuda(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "repo"
    responses = repository_responses(root)
    responses[("uv", "--version")] = result(("uv", "--version"), "uv 0.12.0")
    responses[("uv", "lock", "--check")] = result(("uv", "lock", "--check"))
    probe = ("uv", "run", "--no-sync", "python", "-c", dev_helper._BACKEND_PROBE)
    responses[probe] = result(probe, probe_json(cuda_available=False))
    runner = RecordingRunner(responses)
    helper = DevHelper(root=root, runner=runner, which=tools)

    assert helper.doctor() == 0
    first_output = capsys.readouterr().out
    assert helper.doctor() == 0
    second_output = capsys.readouterr().out

    assert first_output == second_output
    assert "backend native: available" in first_output
    assert "backend cuda: unavailable (ExtensionNotInstalled)" in first_output
    assert "doctor: healthy" in first_output


def test_doctor_requires_native_backend_to_be_reported(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    payload = json.loads(probe_json())
    payload["backends"] = [item for item in payload["backends"] if item["name"] != "native"]
    responses = repository_responses(root)
    responses[("uv", "--version")] = result(("uv", "--version"), "uv 0.12.0")
    responses[("uv", "lock", "--check")] = result(("uv", "lock", "--check"))
    probe = ("uv", "run", "--no-sync", "python", "-c", dev_helper._BACKEND_PROBE)
    responses[probe] = result(probe, json.dumps(payload))
    runner = RecordingRunner(responses)

    with pytest.raises(DevError, match="native compute backend was not reported"):
        DevHelper(root=root, runner=runner, which=tools).doctor()


def test_full_runs_complete_gate_then_doctor(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    validation = [
        ("uv", "run", "ruff", "format", "--check", "."),
        ("uv", "run", "ruff", "check", "."),
        ("uv", "run", "mypy", "src"),
        ("uv", "run", "pytest", "-q"),
        ("uv", "run", "python", "-c", dev_helper._BACKEND_PROBE),
    ]
    responses = repository_responses(root)
    responses.update({command: result(command) for command in validation})
    responses[validation[-1]] = result(validation[-1], probe_json())
    responses[("uv", "--version")] = result(("uv", "--version"), "uv 0.12.0")
    responses[("uv", "lock", "--check")] = result(("uv", "lock", "--check"))
    doctor_probe = ("uv", "run", "--no-sync", "python", "-c", dev_helper._BACKEND_PROBE)
    responses[doctor_probe] = result(doctor_probe, probe_json())
    runner = RecordingRunner(responses)

    assert DevHelper(root=root, runner=runner, which=tools).full() == 0

    calls = [command for command, _cwd in runner.calls]
    assert calls[:5] == validation
    assert calls[5:8] == [
        ("git", "rev-parse", "--show-toplevel"),
        ("git", "branch", "--show-current"),
        ("git", "status", "--short"),
    ]
    assert calls[8:] == [
        ("uv", "--version"),
        ("uv", "lock", "--check"),
        doctor_probe,
    ]


def test_review_normalizes_paths_and_only_inspects_names(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "repo with spaces"
    responses = repository_responses(root)
    log_command = ("git", "log", "--oneline", "main..HEAD")
    stat_command = ("git", "diff", "--stat", "main...HEAD")
    names_command = ("git", "diff", "--name-only", "main...HEAD")
    responses[log_command] = result(log_command, "abc123 feat(dev): add helper")
    responses[stat_command] = result(stat_command, "2 files changed, 10 insertions(+)")
    responses[names_command] = result(
        names_command,
        "scripts\\dev_helper.py\nprivate\\deployment.key\n",
    )
    runner = RecordingRunner(responses)

    assert DevHelper(root=root, runner=runner, which=tools).review() == 0

    output = capsys.readouterr().out
    assert "scripts/dev_helper.py" in output
    assert "private/deployment.key" in output
    assert "suspicious filenames:\n- private/deployment.key" in output
    commands = [command for command, _cwd in runner.calls]
    assert all(command[:2] != ("git", "show") for command in commands)
    assert all("cat" not in command for command in commands)
