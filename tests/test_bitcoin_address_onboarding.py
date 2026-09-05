"""Tests for explicit interactive Bitcoin payout onboarding."""

from __future__ import annotations

import builtins

import pytest

import hashorb.config.settings as settings_module
from hashorb.config.settings import HASHORB_SUPPORT_BITCOIN_ADDRESS, Settings


class _TTY:
    """Minimal interactive stdin stand-in."""

    def isatty(self) -> bool:
        return True


class _NonTTY:
    """Minimal noninteractive stdin stand-in."""

    def isatty(self) -> bool:
        return False


def _prepare(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings_module, "load_hashorb_environment", lambda: None)
    monkeypatch.delenv("HASHORB_BITCOIN_ADDRESS", raising=False)


def _inputs(monkeypatch: pytest.MonkeyPatch, *values: str) -> None:
    answers = iter(values)
    monkeypatch.setattr(builtins, "input", lambda prompt="": next(answers))


def test_noninteractive_missing_address_keeps_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare(monkeypatch)
    monkeypatch.setattr(settings_module.sys, "stdin", _NonTTY())

    with pytest.raises(ValueError, match="HASHORB_BITCOIN_ADDRESS is required"):
        Settings.from_env()


def test_interactive_user_address_is_used_for_current_session(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _prepare(monkeypatch)
    monkeypatch.setattr(settings_module.sys, "stdin", _TTY())
    _inputs(monkeypatch, "1", "bc1quseraddress")

    settings = Settings.from_env()

    assert settings.bitcoin_address == "bc1quseraddress"
    output = capsys.readouterr().out
    assert "No Bitcoin payout address is configured." in output
    assert "1. Enter my Bitcoin address" in output
    assert "2. Mine temporarily to the HashOrb support address" in output
    assert "3. Cancel" in output
    assert "Add HASHORB_BITCOIN_ADDRESS to .env" in output


def test_interactive_support_choice_is_explicit_and_temporary(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _prepare(monkeypatch)
    monkeypatch.setattr(settings_module.sys, "stdin", _TTY())
    _inputs(monkeypatch, "2")

    settings = Settings.from_env()

    assert settings.bitcoin_address == HASHORB_SUPPORT_BITCOIN_ADDRESS
    assert "HASHORB_BITCOIN_ADDRESS" not in settings_module.os.environ
    output = capsys.readouterr().out
    assert "Using the HashOrb support address for this mining session only" in output
    assert HASHORB_SUPPORT_BITCOIN_ADDRESS in output


def test_interactive_cancel_preserves_missing_address_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _prepare(monkeypatch)
    monkeypatch.setattr(settings_module.sys, "stdin", _TTY())
    _inputs(monkeypatch, "3")

    with pytest.raises(ValueError, match="HASHORB_BITCOIN_ADDRESS is required"):
        Settings.from_env()

    assert "Mining cancelled." in capsys.readouterr().out


def test_interactive_invalid_choice_reprompts(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _prepare(monkeypatch)
    monkeypatch.setattr(settings_module.sys, "stdin", _TTY())
    _inputs(monkeypatch, "9", "2")

    settings = Settings.from_env()

    assert settings.bitcoin_address == HASHORB_SUPPORT_BITCOIN_ADDRESS
    assert "Choose 1, 2, or 3." in capsys.readouterr().out
