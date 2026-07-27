"""Tests for the opt-in Hashphere command-line handshake."""

from __future__ import annotations

from collections.abc import Callable

import pytest

import hashphere.__main__ as cli_module
from hashphere.config import Settings
from hashphere.network.stratum import (
    StratumAuthorizationError,
    StratumClientError,
    StratumClientState,
    StratumConnectionError,
    StratumMessageError,
    StratumProtocolError,
    SubscribeResult,
)


def make_settings() -> Settings:
    """Return non-secret settings for CLI tests."""

    return Settings(
        stratum_host="pool.example.com",
        stratum_port=3333,
        bitcoin_address="bc1qexampleaddress",
        worker_name="worker-01",
        stratum_password="test-password-not-real",
        compute_backend="cpu",
        compute_profile="lite",
    )


class FakeClient:
    """Configurable synchronous client test double."""

    def __init__(self, failure: BaseException | None = None) -> None:
        self.failure = failure
        self.state = StratumClientState.DISCONNECTED
        self.close_calls = 0

    def handshake(self) -> SubscribeResult:
        if self.failure is not None:
            raise self.failure
        self.state = StratumClientState.AUTHORIZED
        return SubscribeResult(
            subscriptions=(("mining.notify", "subscription-id"),),
            extra_nonce_1="08000002",
            extra_nonce_2_size=4,
        )

    def close(self) -> None:
        self.close_calls += 1
        self.state = StratumClientState.DISCONNECTED


def configure_command(
    monkeypatch: pytest.MonkeyPatch,
    client: FakeClient,
) -> list[tuple[Settings, str]]:
    """Install deterministic settings and client factories."""

    settings = make_settings()
    created_with: list[tuple[Settings, str]] = []

    monkeypatch.setattr(
        cli_module.Settings,
        "from_env",
        classmethod(lambda cls: settings),
    )

    def client_factory(received_settings: Settings, user_agent: str) -> FakeClient:
        created_with.append((received_settings, user_agent))
        return client

    monkeypatch.setattr(cli_module, "StratumClient", client_factory)
    monkeypatch.setenv("HASHPHERE_ENABLE_LIVE_STRATUM", "1")
    return created_with


def test_success_prints_sanitized_summary_and_closes(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeClient()
    created_with = configure_command(monkeypatch, client)
    settings = make_settings()

    assert cli_module.main(["stratum-handshake"]) == 0

    captured = capsys.readouterr()
    assert captured.err == ""
    assert "Stratum handshake succeeded" in captured.out
    assert "pool.example.com:3333" in captured.out
    assert "bc1q…r-01" in captured.out
    assert "08000002" in captured.out
    assert "Extra nonce 2 size: 4" in captured.out
    assert "State: AUTHORIZED" in captured.out
    assert settings.bitcoin_address not in captured.out
    assert settings.stratum_username not in captured.out
    assert settings.stratum_password not in captured.out
    assert created_with == [(settings, "Hashphere/0.1")]
    assert client.close_calls == 1


@pytest.mark.parametrize(
    ("failure_factory", "expected_message"),
    [
        (lambda: StratumConnectionError("sensitive connection detail"), "Could not connect"),
        (lambda: StratumProtocolError("sensitive protocol detail"), "protocol handshake failed"),
        (lambda: StratumMessageError("sensitive message detail"), "protocol handshake failed"),
        (lambda: StratumClientError("sensitive client detail"), "protocol handshake failed"),
        (
            lambda: StratumAuthorizationError("sensitive authorization detail"),
            "authorization failed",
        ),
    ],
)
def test_expected_handshake_failures_are_sanitized_and_close_client(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure_factory: Callable[[], BaseException],
    expected_message: str,
) -> None:
    client = FakeClient(failure_factory())
    configure_command(monkeypatch, client)

    assert cli_module.main(["stratum-handshake"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert expected_message in captured.err
    assert "sensitive" not in captured.err
    assert client.close_calls == 1


def test_invalid_configuration_returns_nonzero_without_creating_client(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        cli_module.Settings,
        "from_env",
        classmethod(lambda cls: (_ for _ in ()).throw(ValueError("address is required"))),
    )
    client_created = False

    def client_factory(settings: Settings, user_agent: str) -> FakeClient:
        nonlocal client_created
        client_created = True
        return FakeClient()

    monkeypatch.setattr(cli_module, "StratumClient", client_factory)

    assert cli_module.main(["stratum-handshake"]) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Configuration error: address is required" in captured.err
    assert client_created is False


def test_live_handshake_requires_explicit_environment_flag(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeClient()
    configure_command(monkeypatch, client)
    monkeypatch.delenv("HASHPHERE_ENABLE_LIVE_STRATUM")

    assert cli_module.main(["stratum-handshake"]) == 2

    captured = capsys.readouterr()
    assert "HASHPHERE_ENABLE_LIVE_STRATUM=1" in captured.err
    assert client.close_calls == 0


@pytest.mark.parametrize("arguments", [[], ["unknown"], ["stratum-handshake", "extra"]])
def test_unknown_command_prints_usage(
    arguments: list[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli_module.main(arguments) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.strip() == "Usage: python -m hashphere stratum-handshake"


@pytest.mark.parametrize(
    ("username", "masked"),
    [
        ("", ""),
        ("a", "*"),
        ("abc", "a*c"),
        ("abcdefgh", "a******h"),
        ("bc1qexampleaddress.worker-01", "bc1q…r-01"),
    ],
)
def test_mask_username_never_returns_complete_nontrivial_value(
    username: str,
    masked: str,
) -> None:
    assert cli_module._mask_username(username) == masked
    if len(username) > 1:
        assert masked != username
