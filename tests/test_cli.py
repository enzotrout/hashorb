"""Tests for the opt-in Hashphere command-line handshake."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable

import pytest

import hashphere.__main__ as cli_module
from hashphere.config import Settings
from hashphere.network.stratum import (
    MiningNotifyNotification,
    SetDifficultyNotification,
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

    def __init__(
        self,
        failure: BaseException | None = None,
        *,
        notifications: list[SetDifficultyNotification | MiningNotifyNotification] | None = None,
        receive_failure: BaseException | None = None,
    ) -> None:
        self.failure = failure
        self.notifications = deque(notifications or [])
        self.receive_failure = receive_failure
        self.state = StratumClientState.DISCONNECTED
        self.close_calls = 0
        self.receive_calls = 0

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

    def receive_notification(
        self,
    ) -> SetDifficultyNotification | MiningNotifyNotification:
        self.receive_calls += 1
        if self.receive_failure is not None:
            raise self.receive_failure
        if not self.notifications:
            raise AssertionError("fake client has no queued notification")
        return self.notifications.popleft()


def difficulty_notification(difficulty: int | float = 2048) -> SetDifficultyNotification:
    """Return a parsed difficulty notification for observer tests."""

    return SetDifficultyNotification(difficulty=difficulty)


def mining_notification(job_id: str = "job-123") -> MiningNotifyNotification:
    """Return a parsed mining notification containing conspicuous test data."""

    return MiningNotifyNotification(
        job_id=job_id,
        previous_block_hash=("0000000000000000000000000000000000000000000000001234567890abcdef"),
        coinbase_part_1="01000000abcdef01",
        coinbase_part_2="ffffffff12345678",
        merkle_branches=("11223344", "aabbccdd"),
        version="20000000",
        network_bits="170fffff",
        network_time="65f04abc",
        clean_jobs=True,
    )


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


def test_live_observer_requires_explicit_environment_flag(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeClient()
    configure_command(monkeypatch, client)
    monkeypatch.delenv("HASHPHERE_ENABLE_LIVE_STRATUM")

    assert cli_module.main(["stratum-observe"]) == 2

    captured = capsys.readouterr()
    assert "HASHPHERE_ENABLE_LIVE_STRATUM=1" in captured.err
    assert client.close_calls == 0


@pytest.mark.parametrize(
    ("notifications", "expected_order"),
    [
        (
            [difficulty_notification(), mining_notification()],
            "mining.set_difficulty -> mining.notify",
        ),
        (
            [mining_notification(), difficulty_notification()],
            "mining.notify -> mining.set_difficulty",
        ),
    ],
)
def test_observer_handles_either_notification_order_and_queued_values(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    notifications: list[SetDifficultyNotification | MiningNotifyNotification],
    expected_order: str,
) -> None:
    client = FakeClient(notifications=notifications)
    configure_command(monkeypatch, client)

    assert cli_module.main(["stratum-observe"]) == 0

    captured = capsys.readouterr()
    assert f"Arrival order: {expected_order}" in captured.out
    assert client.receive_calls == 2
    assert client.close_calls == 1


def test_observer_reports_sanitized_notification_fields(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    difficulty = difficulty_notification(4096.5)
    job = mining_notification("job-sanitized")
    client = FakeClient(notifications=[difficulty, job])
    configure_command(monkeypatch, client)
    settings = make_settings()

    assert cli_module.main(["stratum-observe"]) == 0

    captured = capsys.readouterr()
    assert captured.err == ""
    assert "Stratum notification observation succeeded" in captured.out
    assert "Endpoint: pool.example.com:3333" in captured.out
    assert "Username: bc1q…r-01" in captured.out
    assert "Difficulty: 4096.5" in captured.out
    assert "Job ID: job-sanitized" in captured.out
    assert "Previous block hash: 00000000…90abcdef" in captured.out
    assert "Coinbase part 1 hex characters: 16" in captured.out
    assert "Coinbase part 2 hex characters: 16" in captured.out
    assert "Merkle branch count: 2" in captured.out
    assert "Version: 20000000" in captured.out
    assert "Network bits: 170fffff" in captured.out
    assert "Network time: 65f04abc" in captured.out
    assert "Clean jobs: true" in captured.out
    assert "Extra nonce 1: 08000002" in captured.out
    assert "Extra nonce 2 size: 4" in captured.out
    assert "State: AUTHORIZED" in captured.out
    assert settings.stratum_password not in captured.out
    assert settings.bitcoin_address not in captured.out
    assert settings.stratum_username not in captured.out
    assert job.coinbase_part_1 not in captured.out
    assert job.coinbase_part_2 not in captured.out
    assert job.previous_block_hash not in captured.out
    assert client.close_calls == 1


def test_observer_keeps_repeated_difficulties_in_arrival_order(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeClient(
        notifications=[
            difficulty_notification(1024),
            difficulty_notification(2048),
            mining_notification(),
        ]
    )
    configure_command(monkeypatch, client)

    assert cli_module.main(["stratum-observe"]) == 0

    captured = capsys.readouterr()
    assert (
        "Arrival order: mining.set_difficulty -> mining.set_difficulty -> mining.notify"
        in captured.out
    )
    assert "Difficulty: 1024" in captured.out
    assert client.receive_calls == 3


def test_observer_keeps_repeated_jobs_in_arrival_order(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeClient(
        notifications=[
            mining_notification("job-first"),
            mining_notification("job-second"),
            difficulty_notification(),
        ]
    )
    configure_command(monkeypatch, client)

    assert cli_module.main(["stratum-observe"]) == 0

    captured = capsys.readouterr()
    assert "Arrival order: mining.notify -> mining.notify -> mining.set_difficulty" in captured.out
    assert "Job ID: job-first" in captured.out
    assert "Job ID: job-second" not in captured.out
    assert client.receive_calls == 3


def test_observer_closes_after_receive_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeClient(receive_failure=StratumClientError("sensitive receive detail"))
    configure_command(monkeypatch, client)

    assert cli_module.main(["stratum-observe"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "notification observation failed" in captured.err
    assert "sensitive" not in captured.err
    assert client.close_calls == 1


@pytest.mark.parametrize(
    ("failure_factory", "expected_message"),
    [
        (lambda: StratumConnectionError("sensitive connection detail"), "Could not connect"),
        (lambda: StratumProtocolError("sensitive protocol detail"), "observation failed"),
        (lambda: StratumMessageError("sensitive notification detail"), "observation failed"),
        (
            lambda: StratumAuthorizationError("sensitive authorization detail"),
            "authorization failed",
        ),
    ],
)
def test_observer_expected_handshake_failures_are_nonzero_and_sanitized(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure_factory: Callable[[], BaseException],
    expected_message: str,
) -> None:
    client = FakeClient(failure_factory())
    configure_command(monkeypatch, client)

    assert cli_module.main(["stratum-observe"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert expected_message in captured.err
    assert "sensitive" not in captured.err
    assert client.close_calls == 1


def test_observer_configuration_failure_is_nonzero(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        cli_module.Settings,
        "from_env",
        classmethod(lambda cls: (_ for _ in ()).throw(ValueError("address is required"))),
    )

    assert cli_module.main(["stratum-observe"]) == 2

    captured = capsys.readouterr()
    assert "Configuration error: address is required" in captured.err


@pytest.mark.parametrize(
    "arguments",
    [[], ["unknown"], ["stratum-handshake", "extra"], ["stratum-observe", "extra"]],
)
def test_unknown_command_prints_usage(
    arguments: list[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli_module.main(arguments) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.strip() == (
        "Usage: python -m hashphere {stratum-handshake,stratum-observe,stratum-mine-once} [options]"
    )


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
