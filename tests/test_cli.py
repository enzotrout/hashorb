"""Tests for the opt-in Hashphere command-line handshake."""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Callable
from pathlib import Path

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
from hashphere.observability import EventLogError


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


def read_event_log(path: Path) -> list[dict[str, object]]:
    """Read independently parseable JSONL event records."""

    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


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


def test_handshake_jsonl_events_are_ordered_and_sanitized(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    path = tmp_path / "logs" / "handshake.jsonl"
    client = FakeClient()
    configure_command(monkeypatch, client)
    settings = make_settings()

    assert cli_module.main(["stratum-handshake", "--log-file", str(path)]) == 0

    captured = capsys.readouterr()
    assert captured.err == ""
    assert "Stratum handshake succeeded." in captured.out
    records = read_event_log(path)
    assert [record["event"] for record in records] == [
        "command_started",
        "stratum_authorized",
        "command_completed",
    ]
    assert [record["sequence"] for record in records] == [1, 2, 3]
    assert {record["command"] for record in records} == {"stratum-handshake"}
    assert len({record["run_id"] for record in records}) == 1
    assert records[1]["endpoint"] == "pool.example.com:3333"
    assert records[1]["extra_nonce_2_size"] == 4
    assert records[2]["outcome"] == "handshake_succeeded"

    log_text = path.read_text(encoding="utf-8")
    assert settings.stratum_password not in log_text
    assert settings.bitcoin_address not in log_text
    assert settings.stratum_username not in log_text
    assert "08000002" not in log_text
    assert client.close_calls == 1


@pytest.mark.parametrize(
    "arguments",
    [
        ["stratum-handshake", "--log-file"],
        ["stratum-handshake", "--log-file", ""],
        ["stratum-handshake", "--log-file", "   "],
        ["stratum-handshake", "--log-file", "one", "--log-file", "two"],
        ["stratum-observe", "--log-file"],
        ["stratum-observe", "--log-file", ""],
        ["stratum-observe", "--log-file", "one", "--log-file", "two"],
    ],
)
def test_non_mining_log_file_argument_errors_are_rejected(
    arguments: list[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli_module.main(arguments) == 2
    assert capsys.readouterr().err == (
        "Usage: python -m hashphere "
        "{stratum-handshake,stratum-observe,stratum-mine-once} [options]\n"
    )


def test_explicit_log_initialization_failure_is_visible_without_network_client(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    blocking_file = tmp_path / "not-a-directory"
    blocking_file.write_text("content", encoding="utf-8")
    client = FakeClient()
    configure_command(monkeypatch, client)

    status = cli_module.main(
        ["stratum-handshake", "--log-file", str(blocking_file / "events.jsonl")]
    )

    assert status == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "Could not initialize structured event logging.\n"
    assert client.state is StratumClientState.DISCONNECTED
    assert client.close_calls == 0


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


def test_configuration_failure_event_omits_arbitrary_error_text(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "configuration-failed.jsonl"
    sensitive_detail = "bc1qexampleaddress.test-password-not-real"
    monkeypatch.setattr(
        cli_module.Settings,
        "from_env",
        classmethod(lambda cls: (_ for _ in ()).throw(ValueError(f"invalid {sensitive_detail}"))),
    )

    assert cli_module.main(["stratum-handshake", "--log-file", str(path)]) == 2

    records = read_event_log(path)
    assert [record["event"] for record in records] == ["command_started", "command_failed"]
    assert records[-1]["stage"] == "configuration"
    assert records[-1]["error_category"] == "ConfigurationOrOptInError"
    assert sensitive_detail not in path.read_text(encoding="utf-8")


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


@pytest.mark.parametrize(
    ("notifications", "expected_events"),
    [
        (
            [difficulty_notification(), mining_notification()],
            ["difficulty_received", "mining_job_received"],
        ),
        (
            [mining_notification(), difficulty_notification()],
            ["mining_job_received", "difficulty_received"],
        ),
    ],
)
def test_observer_jsonl_events_preserve_notification_order(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    notifications: list[SetDifficultyNotification | MiningNotifyNotification],
    expected_events: list[str],
) -> None:
    path = tmp_path / "observer.jsonl"
    client = FakeClient(notifications=notifications)
    configure_command(monkeypatch, client)

    assert cli_module.main(["stratum-observe", "--log-file", str(path)]) == 0

    records = read_event_log(path)
    assert [record["event"] for record in records] == [
        "command_started",
        "stratum_authorized",
        *expected_events,
        "notification_observation_completed",
        "command_completed",
    ]
    assert records[-1]["outcome"] == "observation_succeeded"
    assert client.close_calls == 1


def test_failed_command_writes_sanitized_failure_event_and_closes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "failed.jsonl"
    sensitive_detail = "test-password-not-real.bc1qexampleaddress"
    client = FakeClient(StratumClientError(sensitive_detail))
    configure_command(monkeypatch, client)

    assert cli_module.main(["stratum-handshake", "--log-file", str(path)]) == 1

    records = read_event_log(path)
    assert [record["event"] for record in records] == ["command_started", "command_failed"]
    assert records[-1]["level"] == "ERROR"
    assert records[-1]["stage"] == "handshake"
    assert records[-1]["error_category"] == "StratumClientError"
    assert sensitive_detail not in path.read_text(encoding="utf-8")
    assert client.close_calls == 1


class FailingEventSink:
    """CLI sink double that fails after command startup."""

    def __init__(self) -> None:
        self.events: list[str] = []
        self.close_calls = 0

    def emit(
        self,
        event: str,
        *,
        level: str = "INFO",
        fields: object = None,
    ) -> None:
        self.events.append(event)
        if event == "stratum_authorized":
            raise EventLogError("sensitive write failure")

    def close(self) -> None:
        self.close_calls += 1


def test_logging_write_failure_is_visible_and_client_still_closes(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeClient()
    configure_command(monkeypatch, client)
    sink = FailingEventSink()
    monkeypatch.setattr(cli_module, "JsonlEventSink", lambda path, command: sink)

    assert cli_module.main(["stratum-handshake", "--log-file", "events.jsonl"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "Structured event logging failed.\n"
    assert sink.events == ["command_started", "stratum_authorized"]
    assert sink.close_calls == 1
    assert client.close_calls == 1


class CloseFailingEventSink:
    """CLI sink double whose deterministic close fails."""

    def __init__(self) -> None:
        self.events: list[str] = []
        self.close_calls = 0

    def emit(
        self,
        event: str,
        *,
        level: str = "INFO",
        fields: object = None,
    ) -> None:
        self.events.append(event)

    def close(self) -> None:
        self.close_calls += 1
        raise EventLogError("sensitive close failure")


def test_log_close_failure_after_success_is_visible_and_nonzero(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeClient()
    configure_command(monkeypatch, client)
    sink = CloseFailingEventSink()
    monkeypatch.setattr(cli_module, "JsonlEventSink", lambda path, command: sink)

    assert cli_module.main(["stratum-handshake", "--log-file", "events.jsonl"]) == 1

    captured = capsys.readouterr()
    assert "Stratum handshake succeeded." in captured.out
    assert captured.err == "Could not close structured event logging cleanly.\n"
    assert sink.events == ["command_started", "stratum_authorized", "command_completed"]
    assert sink.close_calls == 1
    assert client.close_calls == 1


def test_log_close_failure_does_not_hide_earlier_runtime_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeClient(StratumClientError("sensitive original failure"))
    configure_command(monkeypatch, client)
    sink = CloseFailingEventSink()
    monkeypatch.setattr(cli_module, "JsonlEventSink", lambda path, command: sink)

    assert cli_module.main(["stratum-handshake", "--log-file", "events.jsonl"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "Stratum protocol handshake failed.\n"
    assert sink.events == ["command_started", "command_failed"]
    assert sink.close_calls == 1
    assert client.close_calls == 1


def test_logging_disabled_does_not_initialize_jsonl_sink(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeClient()
    configure_command(monkeypatch, client)

    def unexpected_jsonl_sink(path: str, command: str) -> None:
        raise AssertionError("JSONL sink must remain disabled")

    monkeypatch.setattr(cli_module, "JsonlEventSink", unexpected_jsonl_sink)

    assert cli_module.main(["stratum-handshake"]) == 0
    assert "Stratum handshake succeeded." in capsys.readouterr().out
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
