"""Tests for deterministic Stratum session recovery."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import FrozenInstanceError, dataclass, field
from decimal import Decimal

import pytest

from hashphere.mining import (
    ReconnectPolicy,
    SessionRecoveryError,
    SessionRecoveryExhaustedError,
    SessionRecoveryValidationError,
    StopController,
    StratumMiningSession,
    StratumRecoveryStage,
    StratumSessionRecovery,
    is_recoverable_stratum_error,
    wait_for_reconnect_delay,
)
from hashphere.network.stratum import (
    MiningNotifyNotification,
    SetDifficultyNotification,
    StratumAuthorizationError,
    StratumClientError,
    StratumClientState,
    StratumConnectionError,
    StratumProtocolError,
    SubscribeResult,
)


def subscription(extra_nonce_2_size: int = 1) -> SubscribeResult:
    """Build one synthetic subscription result."""

    return SubscribeResult(
        subscriptions=(("mining.notify", "subscription-id"),),
        extra_nonce_1="08000002",
        extra_nonce_2_size=extra_nonce_2_size,
    )


def difficulty(value: int | float = 100) -> SetDifficultyNotification:
    """Build one parsed difficulty update."""

    return SetDifficultyNotification(difficulty=value)


def job(job_id: str = "job-a") -> MiningNotifyNotification:
    """Build one parsed synthetic mining job."""

    return MiningNotifyNotification(
        job_id=job_id,
        previous_block_hash="00" * 32,
        coinbase_part_1="01000000cafebabe",
        coinbase_part_2="ffffffffdeadbeef",
        merkle_branches=("11" * 32,),
        version="20000000",
        network_bits="170fffff",
        network_time="65f04abc",
        clean_jobs=True,
    )


class FakeClient:
    """Authorized-session fake without sockets or request sharing."""

    def __init__(
        self,
        *,
        subscribe_result: SubscribeResult | None = None,
        handshake_failure: BaseException | None = None,
        notifications: list[object | None] | None = None,
        close_failure: BaseException | None = None,
    ) -> None:
        self.subscribe_result = subscribe_result or subscription()
        self.handshake_failure = handshake_failure
        self.notifications = deque(
            notifications if notifications is not None else [difficulty(), job(), None]
        )
        self.close_failure = close_failure
        self.state = StratumClientState.DISCONNECTED
        self.handshake_calls = 0
        self.poll_timeouts: list[float] = []
        self.submit_calls: list[tuple[str, str, str, int]] = []
        self.close_calls = 0
        self.on_poll: Callable[[int], None] | None = None

    def handshake(self) -> SubscribeResult:
        self.handshake_calls += 1
        if self.handshake_failure is not None:
            raise self.handshake_failure
        self.state = StratumClientState.AUTHORIZED
        return self.subscribe_result

    def poll_notification(
        self,
        timeout_seconds: float = 0.0,
    ) -> SetDifficultyNotification | MiningNotifyNotification | None:
        self.poll_timeouts.append(timeout_seconds)
        if self.on_poll is not None:
            self.on_poll(len(self.poll_timeouts))
        if not self.notifications:
            return None
        value = self.notifications.popleft()
        if isinstance(value, BaseException):
            raise value
        return value  # type: ignore[return-value]

    def submit_share(
        self,
        job_id: str,
        extra_nonce_2: str,
        network_time: str,
        nonce: int,
    ) -> bool:
        self.submit_calls.append((job_id, extra_nonce_2, network_time, nonce))
        return True

    def close(self) -> None:
        self.close_calls += 1
        self.state = StratumClientState.DISCONNECTED
        if self.close_failure is not None:
            raise self.close_failure


@dataclass
class Harness:
    """Factory, seed, delay, and observer capture for recovery tests."""

    clients: deque[FakeClient | BaseException]
    controller: StopController = field(default_factory=StopController)
    seed_values: deque[str] = field(default_factory=deque)
    factory_calls: int = 0
    seed_sizes: list[int] = field(default_factory=list)
    waits: list[float] = field(default_factory=list)
    observations: list[tuple[object, ...]] = field(default_factory=list)
    stop_during_wait: bool = False
    stop_after_failed_attempt: bool = False

    def create_client(self) -> FakeClient:
        self.factory_calls += 1
        value = self.clients.popleft()
        if isinstance(value, BaseException):
            raise value
        return value

    def generate_seed(self, byte_size: int) -> str:
        self.seed_sizes.append(byte_size)
        if self.seed_values:
            return self.seed_values.popleft()
        return "ab" * byte_size

    def wait(self, delay_seconds: float, stop_token: object) -> bool:
        del stop_token
        self.waits.append(delay_seconds)
        if self.stop_during_wait:
            self.controller.request_stop()
            return False
        return True

    def notification_received(
        self,
        notification: SetDifficultyNotification | MiningNotifyNotification,
    ) -> None:
        kind = "difficulty" if isinstance(notification, SetDifficultyNotification) else "job"
        self.observations.append((kind,))

    def session_authorized(self, result: SubscribeResult) -> None:
        self.observations.append(("authorized", result.extra_nonce_2_size))

    def connection_lost(self, stage: StratumRecoveryStage, error_category: str) -> None:
        self.observations.append(("lost", stage.value, error_category))

    def reconnect_scheduled(
        self,
        attempt: int,
        maximum_attempts: int,
        delay_seconds: float,
        stage: StratumRecoveryStage,
    ) -> None:
        self.observations.append(
            ("scheduled", attempt, maximum_attempts, delay_seconds, stage.value)
        )

    def reconnect_attempted(
        self,
        attempt: int,
        maximum_attempts: int,
        stage: StratumRecoveryStage,
    ) -> None:
        self.observations.append(("attempted", attempt, maximum_attempts, stage.value))

    def reconnect_succeeded(
        self,
        attempt: int,
        successful_reconnect_count: int,
        session_index: int,
    ) -> None:
        self.observations.append(("succeeded", attempt, successful_reconnect_count, session_index))

    def reconnect_failed(
        self,
        attempt: int,
        maximum_attempts: int,
        stage: StratumRecoveryStage,
        error_category: str,
    ) -> None:
        self.observations.append(("failed", attempt, maximum_attempts, stage.value, error_category))
        if self.stop_after_failed_attempt:
            self.controller.request_stop()

    def reconnect_exhausted(
        self,
        attempts: int,
        maximum_attempts: int,
        stage: StratumRecoveryStage,
        error_category: str,
    ) -> None:
        self.observations.append(
            ("exhausted", attempts, maximum_attempts, stage.value, error_category)
        )

    def recovery(self, maximum_attempts: int = 5) -> StratumSessionRecovery:
        return StratumSessionRecovery(
            ReconnectPolicy(maximum_attempts=maximum_attempts),
            self.controller,
            client_factory=self.create_client,
            seed_factory=self.generate_seed,
            observer=self,
            backoff_waiter=self.wait,
        )


def test_default_policy_is_frozen_slotted_and_has_exact_delays() -> None:
    policy = ReconnectPolicy()

    assert policy.maximum_attempts == 5
    assert policy.base_delay_seconds == 1.0
    assert policy.maximum_delay_seconds == 30.0
    assert [policy.delay_for_attempt(attempt) for attempt in range(1, 6)] == [
        1.0,
        2.0,
        4.0,
        8.0,
        16.0,
    ]
    assert not hasattr(policy, "__dict__")
    with pytest.raises(FrozenInstanceError):
        policy.maximum_attempts = 2  # type: ignore[misc]


def test_policy_supports_zero_maximum_and_caps_exponential_delays() -> None:
    assert ReconnectPolicy(maximum_attempts=0).maximum_attempts == 0
    policy = ReconnectPolicy(maximum_attempts=8)
    assert [policy.delay_for_attempt(attempt) for attempt in range(5, 9)] == [
        16.0,
        30.0,
        30.0,
        30.0,
    ]
    assert ReconnectPolicy(maximum_attempts=100).maximum_attempts == 100


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("maximum_attempts", True),
        ("maximum_attempts", -1),
        ("maximum_attempts", 101),
        ("maximum_attempts", 1.0),
        ("base_delay_seconds", True),
        ("base_delay_seconds", 0),
        ("base_delay_seconds", float("nan")),
        ("maximum_delay_seconds", float("inf")),
        ("maximum_delay_seconds", Decimal("1")),
    ],
)
def test_policy_rejects_invalid_values(field_name: str, value: object) -> None:
    values: dict[str, object] = {
        "maximum_attempts": 5,
        "base_delay_seconds": 1.0,
        "maximum_delay_seconds": 30.0,
    }
    values[field_name] = value
    with pytest.raises(SessionRecoveryValidationError):
        ReconnectPolicy(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize("attempt", [False, 0, 6, 1.0, None])
def test_policy_rejects_invalid_attempt_index(attempt: object) -> None:
    with pytest.raises(SessionRecoveryValidationError):
        ReconnectPolicy().delay_for_attempt(attempt)  # type: ignore[arg-type]


def test_interruptible_wait_uses_injected_sleep_without_busy_spinning() -> None:
    controller = StopController()
    sleeps: list[float] = []

    assert wait_for_reconnect_delay(0.25, controller, sleep=sleeps.append) is True
    assert sleeps == [0.1, 0.1, pytest.approx(0.05)]


def test_interruptible_wait_stops_during_delay() -> None:
    controller = StopController()
    sleeps: list[float] = []

    def stop_after_first(interval: float) -> None:
        sleeps.append(interval)
        controller.request_stop()

    assert wait_for_reconnect_delay(2.0, controller, sleep=stop_after_first) is False
    assert sleeps == [0.1]


def test_only_connection_errors_are_recoverable() -> None:
    assert is_recoverable_stratum_error(StratumConnectionError("lost")) is True
    assert is_recoverable_stratum_error(StratumProtocolError("bad protocol")) is False
    assert is_recoverable_stratum_error(StratumClientError("bad client state")) is False
    assert is_recoverable_stratum_error(ValueError("bad input")) is False


def test_initial_session_requires_fresh_difficulty_then_newest_job() -> None:
    client = FakeClient(
        notifications=[
            job("too-early"),
            difficulty(200),
            job("first-usable"),
            difficulty(300),
            job("newest"),
            None,
        ]
    )
    harness = Harness(deque([client]))
    recovery = harness.recovery()

    session = recovery.establish_initial_session()

    assert session is not None
    assert session.initial_job.job_id == "newest"
    assert session.initial_job.difficulty == 300
    assert session.assembler.current_difficulty == 300
    assert harness.seed_sizes == [1]
    assert client.poll_timeouts == [0.25, 0.25, 0.25, 0.0, 0.0, 0.0]
    assert recovery.statistics.sessions_established == 1


def test_initial_factory_failure_reconnects_with_exact_delay() -> None:
    client = FakeClient()
    harness = Harness(deque([StratumConnectionError("unavailable"), client]))
    recovery = harness.recovery()

    session = recovery.establish_initial_session()

    assert session is not None
    assert harness.factory_calls == 2
    assert harness.waits == [1.0]
    assert harness.seed_sizes == [1]
    assert recovery.statistics.reconnect_attempts == 1
    assert recovery.statistics.successful_reconnects == 1
    assert recovery.statistics.failed_reconnect_attempts == 0
    assert recovery.statistics.sessions_established == 1
    assert harness.observations[0] == (
        "lost",
        "handshake",
        "StratumConnectionError",
    )
    assert harness.observations[-1] == ("succeeded", 1, 1, 1)


def test_multiple_failures_then_success_have_deterministic_attempts() -> None:
    client = FakeClient()
    harness = Harness(
        deque(
            [
                StratumConnectionError("initial"),
                StratumConnectionError("retry"),
                client,
            ]
        )
    )
    recovery = harness.recovery()

    assert recovery.establish_initial_session() is not None

    assert harness.waits == [1.0, 2.0]
    assert recovery.statistics.reconnect_attempts == 2
    assert recovery.statistics.failed_reconnect_attempts == 1
    assert recovery.statistics.successful_reconnects == 1


def test_handshake_connection_failure_closes_client_and_does_not_generate_seed() -> None:
    failed = FakeClient(handshake_failure=StratumConnectionError("handshake lost"))
    successful = FakeClient()
    harness = Harness(deque([failed, successful]))

    session = harness.recovery().establish_initial_session()

    assert session is not None
    assert failed.close_calls == 1
    assert harness.seed_sizes == [1]


def test_authorization_rejection_is_terminal_without_retry_or_seed() -> None:
    failed = FakeClient(handshake_failure=StratumAuthorizationError("rejected"))
    harness = Harness(deque([failed]))
    recovery = harness.recovery()

    with pytest.raises(StratumAuthorizationError):
        recovery.establish_initial_session()

    assert failed.close_calls == 1
    assert harness.waits == []
    assert harness.seed_sizes == []
    assert recovery.statistics.reconnect_attempts == 0


def test_protocol_failure_while_waiting_is_terminal_without_retry() -> None:
    failed = FakeClient(notifications=[StratumProtocolError("malformed")])
    harness = Harness(deque([failed]))

    with pytest.raises(StratumProtocolError):
        harness.recovery().establish_initial_session()

    assert failed.close_calls == 1
    assert harness.waits == []
    assert harness.seed_sizes == [1]


def test_connection_loss_while_waiting_creates_fresh_session_and_seed() -> None:
    failed = FakeClient(notifications=[StratumConnectionError("closed")])
    successful = FakeClient(
        subscribe_result=subscription(2),
        notifications=[difficulty(400), job("fresh"), None],
    )
    harness = Harness(
        deque([failed, successful]),
        seed_values=deque(["aa", "bbbb"]),
    )
    recovery = harness.recovery()

    session = recovery.establish_initial_session()

    assert session is not None
    assert session.initial_job.job_id == "fresh"
    assert session.initial_job.extra_nonce_2_size == 2
    assert session.extra_nonce_2_seed == "bbbb"
    assert harness.seed_sizes == [1, 2]
    assert failed.close_calls == 1


def test_active_session_recovery_closes_old_client_and_changes_size() -> None:
    old_client = FakeClient()
    new_client = FakeClient(
        subscribe_result=subscription(2),
        notifications=[difficulty(250), job("new-session"), None],
    )
    harness = Harness(
        deque([old_client, new_client]),
        seed_values=deque(["aa", "bbbb"]),
    )
    recovery = harness.recovery()
    old_session = recovery.establish_initial_session()
    assert old_session is not None

    new_session = recovery.recover_session(
        StratumConnectionError("poll lost"),
        StratumRecoveryStage.NOTIFICATION_POLL,
    )

    assert new_session is not None
    assert old_client.close_calls == 1
    assert new_session.client is new_client
    assert new_session.extra_nonce_2_seed == "bbbb"
    assert new_session.session_index == 2
    assert harness.seed_sizes == [1, 2]
    assert recovery.statistics.sessions_established == 2
    assert recovery.statistics.successful_reconnects == 1


def test_failed_client_close_does_not_hide_recovery() -> None:
    old_client = FakeClient(close_failure=OSError("close failed"))
    new_client = FakeClient()
    harness = Harness(deque([old_client, new_client]))
    recovery = harness.recovery()
    assert recovery.establish_initial_session() is not None

    assert (
        recovery.recover_session(
            StratumConnectionError("poll lost"),
            StratumRecoveryStage.NOTIFICATION_POLL,
        )
        is not None
    )
    assert old_client.close_calls == 1


def test_zero_attempts_exhausts_without_creating_retry_client() -> None:
    harness = Harness(deque([StratumConnectionError("initial")]))
    recovery = harness.recovery(maximum_attempts=0)

    with pytest.raises(SessionRecoveryExhaustedError) as captured:
        recovery.establish_initial_session()

    assert captured.value.attempts == 0
    assert captured.value.recovery_stage is StratumRecoveryStage.HANDSHAKE
    assert captured.value.error_category == "StratumConnectionError"
    assert harness.factory_calls == 1
    assert harness.waits == []
    assert harness.observations[-1] == (
        "exhausted",
        0,
        0,
        "handshake",
        "StratumConnectionError",
    )


def test_retry_exhaustion_stops_at_exact_maximum() -> None:
    harness = Harness(
        deque(
            [
                StratumConnectionError("initial"),
                StratumConnectionError("one"),
                StratumConnectionError("two"),
            ]
        )
    )
    recovery = harness.recovery(maximum_attempts=2)

    with pytest.raises(SessionRecoveryExhaustedError) as captured:
        recovery.establish_initial_session()

    assert captured.value.attempts == 2
    assert harness.factory_calls == 3
    assert harness.waits == [1.0, 2.0]
    assert recovery.statistics.reconnect_attempts == 2
    assert recovery.statistics.failed_reconnect_attempts == 2


def test_stop_before_initial_attempt_creates_no_client() -> None:
    harness = Harness(deque([]))
    harness.controller.request_stop()

    assert harness.recovery().establish_initial_session() is None
    assert harness.factory_calls == 0
    assert harness.waits == []


def test_stop_during_backoff_creates_no_retry_client() -> None:
    harness = Harness(
        deque([StratumConnectionError("initial")]),
        stop_during_wait=True,
    )

    assert harness.recovery().establish_initial_session() is None
    assert harness.factory_calls == 1
    assert harness.waits == [1.0]


def test_stop_after_failed_retry_prevents_another_delay_or_attempt() -> None:
    harness = Harness(
        deque(
            [
                StratumConnectionError("initial"),
                StratumConnectionError("retry"),
            ]
        ),
        stop_after_failed_attempt=True,
    )
    recovery = harness.recovery(maximum_attempts=5)

    assert recovery.establish_initial_session() is None
    assert harness.factory_calls == 2
    assert harness.waits == [1.0]
    assert recovery.statistics.reconnect_attempts == 1


def test_stop_after_authorization_before_work_closes_client() -> None:
    client = FakeClient(notifications=[None])
    harness = Harness(deque([client]))

    def request_stop(call_number: int) -> None:
        assert call_number == 1
        harness.controller.request_stop()

    client.on_poll = request_stop

    assert harness.recovery().establish_initial_session() is None
    assert client.close_calls == 1
    assert harness.seed_sizes == [1]
    assert harness.factory_calls == 1


def test_session_delegates_exact_submission_and_hides_seed_in_repr() -> None:
    client = FakeClient()
    harness = Harness(deque([client]), seed_values=deque(["aa"]))
    session = harness.recovery().establish_initial_session()
    assert isinstance(session, StratumMiningSession)

    assert session.submit_share("job-a", "ab", "65f04abc", 7) is True
    assert client.submit_calls == [("job-a", "ab", "65f04abc", 7)]
    assert "aa" not in repr(session)


def test_unsupported_parsed_notification_is_terminal() -> None:
    client = FakeClient(notifications=[object()])
    harness = Harness(deque([client]))

    with pytest.raises(SessionRecoveryError, match="unsupported"):
        harness.recovery().establish_initial_session()

    assert harness.waits == []
