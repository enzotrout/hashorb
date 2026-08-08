"""Cross-platform development workflow helper for HashOrb."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SUPPORTED_PYTHON = (3, 13)

_BACKEND_PROBE = """\
import json
import platform
from hashorb.compute.registry import list_compute_backends

payload = {
    "python_version": platform.python_version(),
    "backends": [
        {
            "name": item.backend_name,
            "available": item.available,
            "reason": item.unavailable_reason,
        }
        for item in list_compute_backends()
    ],
}
print(json.dumps(payload, sort_keys=True))
"""

_SECRET_NAME_MARKERS = (
    ".env",
    ".pem",
    ".key",
    "credential",
    "credentials",
    "secret",
    "secrets",
    "wallet",
    "seed",
    "private_key",
    "private-key",
    "rpc_cookie",
    "rpc-cookie",
    "id_rsa",
)


class DevError(RuntimeError):
    """Raised when a development-helper operation cannot complete safely."""


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Captured subprocess result used by the helper and its tests."""

    args: tuple[str, ...]
    returncode: int
    stdout: str = ""
    stderr: str = ""


type Runner = Callable[[Sequence[str], Path], CommandResult]
type Which = Callable[[str], str | None]


def subprocess_runner(args: Sequence[str], cwd: Path) -> CommandResult:
    """Run one explicit command without a shell and capture its result."""

    command = tuple(str(part) for part in args)
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            shell=False,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise DevError(f"unable to execute {command[0]}") from exc
    return CommandResult(
        args=command,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


class DevHelper:
    """Implementation of HashOrb's repository-local development commands."""

    def __init__(
        self,
        *,
        root: Path = REPOSITORY_ROOT,
        runner: Runner = subprocess_runner,
        which: Which = shutil.which,
    ) -> None:
        self.root = root.resolve()
        self.runner = runner
        self.which = which

    def _require_tool(self, name: str) -> None:
        if self.which(name) is None:
            raise DevError(f"{name} is required but was not found on PATH")

    def _run_checked(self, args: Sequence[str], operation: str) -> str:
        result = self.runner(tuple(args), self.root)
        if result.returncode != 0:
            raise DevError(f"{operation} failed with exit code {result.returncode}")
        return result.stdout.strip()

    def _git(self, *args: str, operation: str) -> str:
        self._require_tool("git")
        return self._run_checked(("git", *args), operation)

    def _uv(self, *args: str, operation: str) -> str:
        self._require_tool("uv")
        return self._run_checked(("uv", *args), operation)

    def _repository_state(self) -> tuple[Path, str, tuple[str, ...]]:
        root_text = self._git("rev-parse", "--show-toplevel", operation="detect repository root")
        if not root_text:
            raise DevError("detect repository root returned no path")
        repository = Path(root_text).resolve()
        branch = self._git("branch", "--show-current", operation="detect current branch")
        if not branch:
            raise DevError("detached HEAD is not supported by the development helper")
        status_text = self._git("status", "--short", operation="read working tree")
        status_lines = tuple(line for line in status_text.splitlines() if line)
        return repository, branch, status_lines

    def _probe_backends(self, *, no_sync: bool) -> dict[str, Any]:
        args = ["run"]
        if no_sync:
            args.append("--no-sync")
        args.extend(("python", "-c", _BACKEND_PROBE))
        output = self._uv(*args, operation="probe HashOrb development environment")
        try:
            value = json.loads(output)
        except json.JSONDecodeError as exc:
            raise DevError("backend probe returned invalid JSON") from exc
        if not isinstance(value, dict):
            raise DevError("backend probe returned an invalid payload")
        python_version = value.get("python_version")
        backends = value.get("backends")
        if not isinstance(python_version, str) or not isinstance(backends, list):
            raise DevError("backend probe returned an invalid payload")
        for backend in backends:
            if not isinstance(backend, dict):
                raise DevError("backend probe returned an invalid backend entry")
            if not isinstance(backend.get("name"), str) or not isinstance(
                backend.get("available"), bool
            ):
                raise DevError("backend probe returned an invalid backend entry")
            reason = backend.get("reason")
            if reason is not None and not isinstance(reason, str):
                raise DevError("backend probe returned an invalid backend entry")
        return value

    @staticmethod
    def _print_backends(payload: dict[str, Any]) -> None:
        backends = payload["backends"]
        for backend in sorted(backends, key=lambda item: str(item["name"])):
            state = "available" if backend["available"] else "unavailable"
            reason = backend["reason"]
            suffix = "" if reason is None else f" ({reason})"
            print(f"backend {backend['name']}: {state}{suffix}")

    def status(self) -> int:
        """Report development state without modifying the repository or uv environment."""

        repository, branch, status_lines = self._repository_state()
        print("HashOrb development status")
        print(f"repository: {repository}")
        print(f"branch: {branch}")
        print(f"working tree: {'clean' if not status_lines else 'dirty'}")
        print(f"launcher python: {sys.version.split()[0]}")
        self._require_tool("uv")
        uv_version = self._uv("--version", operation="read uv version")
        print(f"uv: {uv_version}")
        payload = self._probe_backends(no_sync=True)
        print(f"uv python: {payload['python_version']}")
        self._print_backends(payload)
        return 0

    def sync(self) -> int:
        """Safely update local main from origin without rewriting local work."""

        _repository, _branch, status_lines = self._repository_state()
        if status_lines:
            raise DevError("sync requires a clean working tree")
        self._git("fetch", "--prune", "origin", operation="fetch origin")
        self._git("switch", "main", operation="switch to main")
        self._git("pull", "--ff-only", "origin", "main", operation="fast-forward main")
        print("sync: main is up to date")
        return 0

    def start(self) -> int:
        """Synchronize the locked development environment through uv."""

        self._uv(
            "sync",
            "--locked",
            "--no-python-downloads",
            operation="synchronize locked development environment",
        )
        print("start: development environment is ready")
        return 0

    def _validation_step(self, args: Sequence[str], label: str) -> None:
        print(f"check: {label}")
        self._uv(*args, operation=label)

    def check(self) -> int:
        """Run the fast everyday development validation gate."""

        steps: tuple[tuple[tuple[str, ...], str], ...] = (
            (("run", "ruff", "format", "--check", "."), "format"),
            (("run", "ruff", "check", "."), "lint"),
            (("run", "mypy", "src"), "typing"),
            (("run", "pytest", "-q", "tests/test_dev_helper.py"), "developer helper tests"),
        )
        for args, label in steps:
            self._validation_step(args, label)
        self._probe_backends(no_sync=False)
        print("check: backend/import probe")
        print("check: passed")
        return 0

    @staticmethod
    def _parse_python_minor(version: str) -> tuple[int, int] | None:
        parts = version.split(".")
        if len(parts) < 2:
            return None
        try:
            return int(parts[0]), int(parts[1])
        except ValueError:
            return None

    def doctor(self) -> int:
        """Diagnose the repository and locked development environment."""

        repository, branch, status_lines = self._repository_state()
        self._require_tool("uv")
        uv_version = self._uv("--version", operation="read uv version")
        self._uv("lock", "--check", operation="verify uv lock")
        payload = self._probe_backends(no_sync=True)
        python_minor = self._parse_python_minor(str(payload["python_version"]))
        if python_minor != SUPPORTED_PYTHON:
            raise DevError(
                "uv development environment must use Python "
                f"{SUPPORTED_PYTHON[0]}.{SUPPORTED_PYTHON[1]}"
            )
        backends = {str(item["name"]): item for item in payload["backends"]}
        python_backend = backends.get("python")
        if python_backend is None or not python_backend["available"]:
            raise DevError("required Python compute backend is unavailable")
        if "native" not in backends:
            raise DevError("native compute backend was not reported")

        print("HashOrb development doctor")
        print(f"repository: {repository}")
        print(f"branch: {branch}")
        print(f"working tree: {'clean' if not status_lines else 'dirty'}")
        print(f"uv: {uv_version}")
        print(f"uv python: {payload['python_version']}")
        self._print_backends(payload)
        print("doctor: healthy")
        return 0

    def full(self) -> int:
        """Run the complete pre-review development validation gate."""

        steps: tuple[tuple[tuple[str, ...], str], ...] = (
            (("run", "ruff", "format", "--check", "."), "format"),
            (("run", "ruff", "check", "."), "lint"),
            (("run", "mypy", "src"), "typing"),
            (("run", "pytest", "-q"), "full test suite"),
        )
        for args, label in steps:
            self._validation_step(args, label)
        self._probe_backends(no_sync=False)
        print("check: backend/import probe")
        self.doctor()
        print("full: passed")
        return 0

    @staticmethod
    def _display_path(path_text: str) -> str:
        return path_text.replace("\\", "/")

    @classmethod
    def _is_suspicious_filename(cls, path_text: str) -> bool:
        name = Path(cls._display_path(path_text)).name.lower()
        if name == ".env.example":
            return False
        return any(marker in name for marker in _SECRET_NAME_MARKERS)

    def review(self) -> int:
        """Report the review surface without reading changed-file contents."""

        repository, branch, status_lines = self._repository_state()
        commits = self._git(
            "log",
            "--oneline",
            "main..HEAD",
            operation="read commits against main",
        )
        diff_stat = self._git(
            "diff",
            "--stat",
            "main...HEAD",
            operation="read diff summary against main",
        )
        changed_text = self._git(
            "diff",
            "--name-only",
            "main...HEAD",
            operation="read changed filenames against main",
        )
        changed_files = tuple(line.strip() for line in changed_text.splitlines() if line.strip())
        suspicious = tuple(path for path in changed_files if self._is_suspicious_filename(path))

        print("HashOrb development review")
        print(f"repository: {repository}")
        print(f"branch: {branch}")
        print(f"working tree: {'clean' if not status_lines else 'dirty'}")
        print("commits against main:")
        print(commits or "(none)")
        print("changed files:")
        for path in changed_files:
            print(f"- {self._display_path(path)}")
        if not changed_files:
            print("- (none)")
        print("diff summary:")
        print(diff_stat or "(none)")
        print("suspicious filenames:")
        for path in suspicious:
            print(f"- {self._display_path(path)}")
        if not suspicious:
            print("- none")
        return 0


def build_parser() -> argparse.ArgumentParser:
    """Create the stable command-line parser."""

    parser = argparse.ArgumentParser(
        prog="dev",
        description="HashOrb development workflow helper",
    )
    subparsers = parser.add_subparsers(dest="command")
    for name, help_text in (
        ("help", "show this help"),
        ("status", "report repository and development-environment state"),
        ("sync", "safely fast-forward local main from origin"),
        ("start", "synchronize the locked uv development environment"),
        ("check", "run the fast development validation gate"),
        ("doctor", "diagnose the local development environment"),
        ("full", "run the complete pre-review validation gate"),
        ("review", "summarize the branch review surface"),
    ):
        subparsers.add_parser(name, help=help_text)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run one development-helper command."""

    parser = build_parser()
    arguments = parser.parse_args(argv)
    if arguments.command in {None, "help"}:
        parser.print_help()
        return 0

    helper = DevHelper()
    command = getattr(helper, str(arguments.command))
    try:
        return int(command())
    except DevError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
