"""Pre-release identity migration and operator-tool regression tests."""

from __future__ import annotations

import importlib.util
import os
import re
import stat
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

import hashorb.config.environment as environment_module
from hashorb.config import TRUE_SOLO_FLAG, load_hashorb_environment, require_exact_opt_in

_ROOT = Path(__file__).resolve().parents[1]
_LEGACY_PATTERN = "Hashsphere|hashsphere|HASHSPHERE_|Hashphere|hashphere|HASHPHERE_"
_ALLOWED_LEGACY_FILES = {
    "docs/15-security-audit.md",
    "docs/16-hashorb-migration.md",
    "scripts/migrate-hashorb-env.py",
    "scripts/smoke-installed-distribution.py",
    "scripts/verify-distributions.py",
    "src/hashorb/config/environment.py",
    "tests/test_hashorb_identity.py",
}


def _run_migration(path: Path, *options: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_ROOT / "scripts" / "migrate-hashorb-env.py"), *options, str(path)],
        cwd=path.parent,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def test_canonical_distribution_package_and_entry_point() -> None:
    metadata = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert metadata["project"]["name"] == "hashorb"
    assert metadata["project"]["version"] == "0.1.0"
    assert metadata["project"]["description"] == (
        "HashOrb: Distributed hashing as a coordinated swarm."
    )
    assert metadata["project"]["scripts"] == {"hashorb": "hashorb.__main__:main"}
    assert (_ROOT / "src" / "hashorb" / "__init__.py").is_file()
    assert not (_ROOT / "src" / "hashphere").exists()
    assert not (_ROOT / "src" / "hashsphere").exists()


def test_current_environment_imports_only_hashorb() -> None:
    assert importlib.util.find_spec("hashorb") is not None
    assert importlib.util.find_spec("hashphere") is None
    assert importlib.util.find_spec("hashsphere") is None


def test_native_and_cuda_extensions_use_hashorb_package_boundary() -> None:
    setup = (_ROOT / "setup.py").read_text(encoding="utf-8")

    assert '"hashorb.compute._native"' in setup
    assert '"hashorb.compute._cuda"' in setup
    assert 'sources=["src/hashorb/compute/_native.c"]' in setup
    assert 'sources=["src/hashorb/compute/_cuda.cu"]' in setup
    assert "HASHORB_BUILD_CUDA" in setup
    assert "HASHORB_CUDA_ARCH" in setup


def test_legacy_environment_is_rejected_without_value_reflection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(environment_module, "load_dotenv", lambda: False)
    synthetic_value = "synthetic-value-must-not-appear"
    monkeypatch.setenv("HASHPHERE_ENABLE_TRUE_SOLO", synthetic_value)

    with pytest.raises(ValueError) as raised:
        load_hashorb_environment()

    assert str(raised.value) == "Legacy project configuration detected; rename keys to HASHORB_."
    assert synthetic_value not in str(raised.value)


def test_legacy_opt_in_cannot_grant_true_solo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(TRUE_SOLO_FLAG, raising=False)
    monkeypatch.setenv("HASHSPHERE_ENABLE_TRUE_SOLO", "1")

    with pytest.raises(ValueError, match="HASHORB_ENABLE_TRUE_SOLO=1 is required"):
        require_exact_opt_in(TRUE_SOLO_FLAG)


def test_tracked_legacy_names_are_confined_to_reviewed_migration_contexts() -> None:
    paths = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=_ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    pattern = re.compile(_LEGACY_PATTERN)
    matched: set[str] = set()
    for relative in paths.stdout.splitlines():
        try:
            text = (_ROOT / relative).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if pattern.search(text) is not None:
            matched.add(relative)

    assert matched == _ALLOWED_LEGACY_FILES


def test_env_migration_preserves_bytes_permissions_and_creates_backup(tmp_path: Path) -> None:
    dotenv = tmp_path / ".env"
    original = (
        b"# unchanged comment\n"
        b"HASHPHERE_ALPHA=value with spaces # preserved\n"
        b"HASHSPHERE_BETA=a=b=c\n"
        b"UNRELATED=unchanged\n"
        b"NO_NEWLINE=preserved"
    )
    dotenv.write_bytes(original)
    dotenv.chmod(0o600)

    result = _run_migration(dotenv)

    assert result.returncode == 0
    assert "Migrated 2 legacy configuration key(s)" in result.stdout
    assert "value with spaces" not in result.stdout
    assert result.stderr == ""
    assert dotenv.read_bytes() == original.replace(b"HASHPHERE_", b"HASHORB_").replace(
        b"HASHSPHERE_", b"HASHORB_"
    )
    backup = tmp_path / ".env.pre-hashorb"
    assert backup.read_bytes() == original
    assert stat.S_IMODE(dotenv.stat().st_mode) == 0o600
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600


def test_env_migration_refuses_colliding_legacy_prefixes(tmp_path: Path) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_bytes(b"HASHPHERE_FLAG=one\nHASHSPHERE_FLAG=two\n")
    dotenv.chmod(0o600)

    result = _run_migration(dotenv)

    assert result.returncode == 1
    assert result.stdout == ""
    assert "one" not in result.stderr
    assert "two" not in result.stderr
    assert not (tmp_path / ".env.pre-hashorb").exists()


def test_env_migration_refuses_existing_hashorb_key_or_backup(tmp_path: Path) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_bytes(b"HASHPHERE_FLAG=old\nHASHORB_FLAG=current\n")
    dotenv.chmod(0o600)

    collision = _run_migration(dotenv)

    assert collision.returncode == 1
    assert dotenv.read_bytes() == b"HASHPHERE_FLAG=old\nHASHORB_FLAG=current\n"

    dotenv.write_bytes(b"HASHPHERE_FLAG=old\n")
    backup = tmp_path / ".env.pre-hashorb"
    backup.write_bytes(b"existing")
    backup.chmod(0o600)
    existing_backup = _run_migration(dotenv)

    assert existing_backup.returncode == 1
    assert dotenv.read_bytes() == b"HASHPHERE_FLAG=old\n"
    assert backup.read_bytes() == b"existing"


def test_env_migration_verify_prints_names_only(tmp_path: Path) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_bytes(b"HASHPHERE_ALPHA=hidden-one\nHASHSPHERE_BETA=hidden-two\n")
    dotenv.chmod(0o600)

    result = _run_migration(dotenv, "--verify")

    assert result.returncode == 1
    assert result.stdout.splitlines() == ["HASHPHERE_ALPHA", "HASHSPHERE_BETA"]
    assert "hidden" not in result.stdout
    assert result.stderr == ""


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode enforcement is Linux operator tooling")
def test_env_migration_refuses_nonprivate_permissions(tmp_path: Path) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_bytes(b"HASHPHERE_FLAG=hidden\n")
    dotenv.chmod(0o640)

    result = _run_migration(dotenv)

    assert result.returncode == 1
    assert result.stdout == ""
    assert "hidden" not in result.stderr
    assert not (tmp_path / ".env.pre-hashorb").exists()
