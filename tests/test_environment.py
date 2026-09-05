import os
import platform
import sys
from pathlib import Path

import pytest

from hashorb.config.environment import load_hashorb_environment


def test_python_version() -> None:
    assert sys.version_info >= (3, 13)
    assert sys.version_info < (3, 14)


def test_python_implementation() -> None:
    assert platform.python_implementation() == "CPython"


def test_basic_arithmetic() -> None:
    assert 2 + 2 == 4


def test_load_hashorb_environment_searches_from_working_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_dir = tmp_path / "project"
    nested_dir = project_dir / "nested"
    nested_dir.mkdir(parents=True)
    (project_dir / ".env").write_text(
        "HASHORB_BITCOIN_ADDRESS=bc1qfromdotenv\n",
        encoding="utf-8",
    )

    monkeypatch.chdir(nested_dir)
    monkeypatch.delenv("HASHORB_BITCOIN_ADDRESS", raising=False)

    load_hashorb_environment()

    assert os.environ["HASHORB_BITCOIN_ADDRESS"] == "bc1qfromdotenv"


def test_load_hashorb_environment_does_not_override_process_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / ".env").write_text(
        "HASHORB_BITCOIN_ADDRESS=bc1qfromdotenv\n",
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HASHORB_BITCOIN_ADDRESS", "bc1qfromprocess")

    load_hashorb_environment()

    assert os.environ["HASHORB_BITCOIN_ADDRESS"] == "bc1qfromprocess"
