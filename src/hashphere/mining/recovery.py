"""Deterministic single-endpoint Stratum mining-session recovery."""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, runtime_checkable

from hashphere.mining.job import MiningJob, MiningJobAssembler
from hashphere.network.stratum.client import StratumClientState
from hashphere.network.stratum.messages import (
    MiningNotifyNotification,
    SetDifficultyNotification,
    SubscribeResult,
)
from hashphere.network.stratum.transport import StratumConnectionError

type MiningNotification = SetDifficultyNotification | MiningNotifyNotification
type ExtraNonceSeedFactory = Callable[[int], str]

_DEFAULT_MAX_RECONNECT_ATTEMPTS = 5
MAX_RECONNECT_ATTEMPTS = 100
_DEFAULT_BASE_DELAY_SECONDS = 1.0
_DEFAULT_MAX_DELAY_SECONDS = 30.0
_DEFAULT_NOTIFICATION_TIMEOUT_SECONDS = 0.25
_BACKOFF_QUANTUM_SECONDS = 0.1
_LOWER_HEX_DIGITS = frozenset("0123456789abcdef")


@runtime_checkable
class _StopToken(Protocol):
    @property
    def stop_requested(self) -> bool:
        """Return whether graceful shutdown has been requested."""


type BackoffWaiter = Callable[[float, _StopToken], bool]


class SessionRecoveryError(RuntimeError):
    """Base error for Stratum mining-session establishment and recovery."""


class SessionRecoveryValidationError(SessionRecoveryError, ValueError):
    """Raised when recovery configuration or callback data is invalid."""


class SessionRecoveryExhaustedError(SessionRecoveryError):
    """Raised after every permitted reconnect attempt fails."""

    def __init__(
        self,
        *,
        attempts: int,
        recovery_stage: StratumRecoveryStage,
        error_category: str,
    ) -> None:
        super().__init__("Stratum session recovery attempts were exhausted")
        self.attempts = attempts
        self.recovery_stage = recovery_stage
        self.error_category = error_category


class StratumRecoveryStage(StrEnum):
    """Stable stages at which recoverable connection loss can occur."""

    HANDSHAKE = "handshake"
    SESSION_WORK = "session_work"
    NOTIFICATION_POLL = "notification_poll"
    REPLACEMENT_WAIT = "replacement_wait"
    LIVENESS = "liveness"


@dataclass(frozen=True, slots=True)
class ReconnectPolicy:
    """Validated deterministic reconnect-attempt and delay policy."""

    maximum_attempts: int = _DEFAULT_MAX_RECONNECT_ATTEMPTS
    base_delay_seconds: int | float = _DEFAULT_BASE_DELAY_SECONDS
    maximum_delay_seconds: int | float = _DEFAULT_MAX_DELAY_SECONDS

    def __post_init__(self) -> None:
        """Validate bounded attempts and finite positive delays."""

        if isinstance(self.maximum_attempts, bool) or not isinstance(self.maximum_attempts, int):
            raise SessionRecoveryValidationError("maximum_attempts must be an integer")
        if not 0 <= self.maximum_attempts <= MAX_RECONNECT_ATTEMPTS:
            raise SessionRecoveryValidationError(
                f"maximum_attempts must be between 0 and {MAX_RECONNECT_ATTEMPTS}"
            )
        _validate_positive_number(self.base_delay_seconds, "base_delay_seconds")
        _validate_positive_number(self.maximum_delay_seconds, "maximum_delay_seconds")

    def delay_for_attempt(self, attempt: int) -> float:
        """Return the capped exponential delay for a one-based retry attempt."""

        if isinstance(attempt, bool) or not isinstance(attempt, int):
            raise SessionRecoveryValidationError("attempt must be an integer")
        if not 1 <= attempt <= self.maximum_attempts:
            raise SessionRecoveryValidationError(
                "attempt must be within the configured reconnect-attempt range"
            )
        base_delay = float(self.base_delay_seconds)
        maximum_delay = float(self.maximum_delay_seconds)
        exponential_delay: float = base_delay * (2.0 ** (attempt - 1))
        return min(exponential_delay, maximum_delay)


@dataclass(frozen=True, slots=True)
class StratumRecoveryStatistics:
    """Immutable cumulative recovery counters for one command invocation."""

    reconnect_attempts: int
    successful_reconnects: int
    failed_reconnect_attempts: int
    sessions_established: int

    def __post_init__(self) -> None:
        """Validate cumulative counters."""

        for name, value in (
            ("reconnect_attempts", self.reconnect_attempts),
            ("successful_reconnects", self.successful_reconnects),
            ("failed_reconnect_attempts", self.failed_reconnect_attempts),
            ("sessions_established", self.sessions_established),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise SessionRecoveryValidationError(f"{name} must be nonnegative")
        if self.successful_reconnects > self.reconnect_attempts:
            raise SessionRecoveryValidationError(
                "successful_reconnects cannot exceed reconnect_attempts"
            )
        if self.failed_reconnect_attempts > self.reconnect_attempts:
            raise SessionRecoveryValidationError(
                "failed_reconnect_attempts cannot exceed reconnect_attempts"
            )


@runtime_checkable
class StratumSessionClient(Protocol):
    """Client operations required by mining-session recovery."""

    @property
    def state(self) -> StratumClientState:
        """Return the client's current Stratum state."""

    def handshake(self) -> SubscribeResult:
        """Establish an authorized Stratum session."""

    def poll_notification(
        self,
        timeout_seconds: float = 0.0,
    ) -> MiningNotification | None:
        """Poll for one parsed mining notification."""

    def submit_share(
        self,
        job_id: str,
        extra_nonce_2: str,
        network_time: str,
        nonce: int,
    ) -> bool:
        """Submit one candidate through this exact session."""

    def close(self) -> None:
        """Close the client safely."""


type StratumClientFactory = Callable[[], StratumSessionClient]


@dataclass(frozen=True, slots=True)
class StratumMiningSession:
    """One authorized session with fresh assembler, seed, and usable work."""

    client: StratumSessionClient = field(repr=False)
    subscription: SubscribeResult
    assembler: MiningJobAssembler = field(repr=False)
    initial_job: MiningJob
    extra_nonce_2_seed: str = field(repr=False)
    session_index: int

    def __post_init__(self) -> None:
        """Validate session ownership and negotiated seed invariants."""

        if not isinstance(self.client, StratumSessionClient):
            raise SessionRecoveryValidationError("client must implement StratumSessionClient")
        if not isinstance(self.subscription, SubscribeResult):
            raise SessionRecoveryValidationError("subscription must be a SubscribeResult")
        if not isinstance(self.assembler, MiningJobAssembler):
            raise SessionRecoveryValidationError("assembler must be a MiningJobAssembler")
        if not isinstance(self.initial_job, MiningJob):
            raise SessionRecoveryValidationError("initial_job must be a MiningJob")
        _validate_seed(self.extra_nonce_2_seed, self.subscription.extra_nonce_2_size)
        if isinstance(self.session_index, bool) or not isinstance(self.session_index, int):
            raise SessionRecoveryValidationError("session_index must be an integer")
        if self.session_index <= 0:
            raise SessionRecoveryValidationError("session_index must be positive")

    def receive_notification(self, timeout_seconds: float) -> MiningNotification | None:
        """Poll this session's client for one parsed notification."""

        return self.client.poll_notification(timeout_seconds=timeout_seconds)

    def submit_share(
        self,
        job_id: str,
        extra_nonce_2: str,
        network_time: str,
        nonce: int,
    ) -> bool:
        """Submit exact prepared-work metadata through this session."""

        return self.client.submit_share(
            job_id,
            extra_nonce_2,
            network_time,
            nonce,
        )


class StratumSessionRecoveryObserver(Protocol):
    """Passive safe-event boundary for session establishment and recovery."""

    def notification_received(self, notification: MiningNotification) -> None:
        """Observe one parsed notification in arrival order."""

    def session_authorized(self, subscription: SubscribeResult) -> None:
        """Observe successful authorization before session work arrives."""

    def connection_lost(self, stage: StratumRecoveryStage, error_category: str) -> None:
        """Observe one recoverable connection-availability failure."""

    def reconnect_scheduled(
        self,
        attempt: int,
        maximum_attempts: int,
        delay_seconds: float,
        stage: StratumRecoveryStage,
    ) -> None:
        """Observe one scheduled retry and deterministic delay."""

    def reconnect_attempted(
        self,
        attempt: int,
        maximum_attempts: int,
        stage: StratumRecoveryStage,
    ) -> None:
        """Observe creation of one reconnect attempt."""

    def reconnect_succeeded(
        self,
        attempt: int,
        successful_reconnect_count: int,
        session_index: int,
    ) -> None:
        """Observe recovery only after fresh usable work exists."""

    def reconnect_failed(
        self,
        attempt: int,
        maximum_attempts: int,
        stage: StratumRecoveryStage,
        error_category: str,
    ) -> None:
        """Observe one failed retry without raw exception text."""

    def reconnect_exhausted(
        self,
        attempts: int,
        maximum_attempts: int,
        stage: StratumRecoveryStage,
        error_category: str,
    ) -> None:
        """Observe terminal recovery exhaustion."""


class NullStratumSessionRecoveryObserver:
    """No-op observer for callers that do not persist recovery events."""

    def notification_received(self, notification: MiningNotification) -> None:
        """Discard a parsed notification."""

    def session_authorized(self, subscription: SubscribeResult) -> None:
        """Discard an authorization observation."""

    def connection_lost(self, stage: StratumRecoveryStage, error_category: str) -> None:
        """Discard a connection-loss observation."""

    def reconnect_scheduled(
        self,
        attempt: int,
        maximum_attempts: int,
        delay_seconds: float,
        stage: StratumRecoveryStage,
    ) -> None:
        """Discard a scheduled-reconnect observation."""

    def reconnect_attempted(
        self,
        attempt: int,
        maximum_attempts: int,
        stage: StratumRecoveryStage,
    ) -> None:
        """Discard a reconnect-attempt observation."""

    def reconnect_succeeded(
        self,
        attempt: int,
        successful_reconnect_count: int,
        session_index: int,
    ) -> None:
        """Discard a reconnect-success observation."""

    def reconnect_failed(
        self,
        attempt: int,
        maximum_attempts: int,
        stage: StratumRecoveryStage,
        error_category: str,
    ) -> None:
        """Discard a reconnect-failure observation."""

    def reconnect_exhausted(
        self,
        attempts: int,
        maximum_attempts: int,
        stage: StratumRecoveryStage,
        error_category: str,
    ) -> None:
        """Discard a reconnect-exhaustion observation."""


@dataclass(frozen=True, slots=True)
class _RecoverableSessionFailure(Exception):
    error: StratumConnectionError = field(repr=False)
    stage: StratumRecoveryStage


class StratumSessionRecovery:
    """Own the current client and establish fresh usable mining sessions."""

    __slots__ = (
        "_backoff_waiter",
        "_client_factory",
        "_current_session",
        "_failed_reconnect_attempts",
        "_notification_timeout_seconds",
        "_server_silence_seconds",
        "_clock",
        "_observer",
        "_policy",
        "_reconnect_attempts",
        "_seed_factory",
        "_sessions_established",
        "_stop_token",
        "_successful_reconnects",
    )

    def __init__(
        self,
        policy: ReconnectPolicy,
        stop_token: _StopToken,
        *,
        client_factory: StratumClientFactory,
        seed_factory: ExtraNonceSeedFactory,
        observer: StratumSessionRecoveryObserver | None = None,
        backoff_waiter: BackoffWaiter = lambda delay, stop: wait_for_reconnect_delay(delay, stop),
        notification_timeout_seconds: float = _DEFAULT_NOTIFICATION_TIMEOUT_SECONDS,
        server_silence_seconds: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not isinstance(policy, ReconnectPolicy):
            raise SessionRecoveryValidationError("policy must be a ReconnectPolicy")
        if not isinstance(stop_token, _StopToken):
            raise SessionRecoveryValidationError("stop_token must expose stop_requested")
        for callback, name in (
            (client_factory, "client_factory"),
            (seed_factory, "seed_factory"),
            (backoff_waiter, "backoff_waiter"),
        ):
            if not callable(callback):
                raise SessionRecoveryValidationError(f"{name} must be callable")
        _validate_nonnegative_number(
            notification_timeout_seconds,
            "notification_timeout_seconds",
        )
        if server_silence_seconds is not None:
            _validate_positive_number(server_silence_seconds, "server_silence_seconds")
        if not callable(clock):
            raise SessionRecoveryValidationError("clock must be callable")
        self._policy = policy
        self._stop_token = stop_token
        self._client_factory = client_factory
        self._seed_factory = seed_factory
        self._observer = NullStratumSessionRecoveryObserver() if observer is None else observer
        self._backoff_waiter = backoff_waiter
        self._notification_timeout_seconds = notification_timeout_seconds
        self._server_silence_seconds = server_silence_seconds
        self._clock = clock
        self._current_session: StratumMiningSession | None = None
        self._reconnect_attempts = 0
        self._successful_reconnects = 0
        self._failed_reconnect_attempts = 0
        self._sessions_established = 0

    @property
    def current_session(self) -> StratumMiningSession | None:
        """Return the current usable session, if one is installed."""

        return self._current_session

    @property
    def statistics(self) -> StratumRecoveryStatistics:
        """Return an immutable snapshot of cumulative recovery counters."""

        return StratumRecoveryStatistics(
            reconnect_attempts=self._reconnect_attempts,
            successful_reconnects=self._successful_reconnects,
            failed_reconnect_attempts=self._failed_reconnect_attempts,
            sessions_established=self._sessions_established,
        )

    def establish_initial_session(self) -> StratumMiningSession | None:
        """Establish initial usable work, retrying recoverable failures by policy."""

        if self._current_session is not None:
            raise SessionRecoveryValidationError("a Stratum session is already established")
        if self._stop_token.stop_requested:
            return None
        try:
            session = self._establish_once()
        except _RecoverableSessionFailure as failure:
            self._observer.connection_lost(
                failure.stage,
                _recoverable_error_category(failure.error),
            )
            return self._retry(failure)
        if session is None:
            return None
        return self._install_session(session)

    def recover_session(
        self,
        error: BaseException,
        stage: StratumRecoveryStage,
    ) -> StratumMiningSession | None:
        """Discard the failed client and recover fresh usable session work."""

        if not isinstance(error, StratumConnectionError):
            raise SessionRecoveryValidationError("error is not recoverable")
        if not isinstance(stage, StratumRecoveryStage):
            raise SessionRecoveryValidationError("stage must be a StratumRecoveryStage")
        self._close_current_best_effort()
        self._observer.connection_lost(stage, _recoverable_error_category(error))
        if self._stop_token.stop_requested:
            return None
        return self._retry(_RecoverableSessionFailure(error=error, stage=stage))

    def recover_stale_session(self) -> StratumMiningSession | None:
        """Replace a configured-stale session through the existing retry path."""

        self._close_current_best_effort()
        if self._stop_token.stop_requested:
            return None
        error = StratumConnectionError("configured Stratum liveness threshold exceeded")
        return self._retry(
            _RecoverableSessionFailure(
                error=error,
                stage=StratumRecoveryStage.LIVENESS,
            )
        )

    def close(self) -> None:
        """Close the current client and clear ownership; repeated calls are safe."""

        session = self._current_session
        self._current_session = None
        if session is not None:
            session.client.close()

    def _retry(
        self,
        initial_failure: _RecoverableSessionFailure,
    ) -> StratumMiningSession | None:
        last_failure = initial_failure
        for attempt in range(1, self._policy.maximum_attempts + 1):
            if self._stop_token.stop_requested:
                return None
            delay = self._policy.delay_for_attempt(attempt)
            self._observer.reconnect_scheduled(
                attempt,
                self._policy.maximum_attempts,
                delay,
                last_failure.stage,
            )
            if not self._backoff_waiter(delay, self._stop_token):
                return None
            if self._stop_token.stop_requested:
                return None

            self._reconnect_attempts += 1
            self._observer.reconnect_attempted(
                attempt,
                self._policy.maximum_attempts,
                last_failure.stage,
            )
            try:
                session = self._establish_once()
            except _RecoverableSessionFailure as failure:
                self._failed_reconnect_attempts += 1
                last_failure = failure
                self._observer.reconnect_failed(
                    attempt,
                    self._policy.maximum_attempts,
                    failure.stage,
                    _recoverable_error_category(failure.error),
                )
                continue
            if session is None:
                return None

            installed = self._install_session(session)
            self._successful_reconnects += 1
            self._observer.reconnect_succeeded(
                attempt,
                self._successful_reconnects,
                installed.session_index,
            )
            return installed

        category = _recoverable_error_category(last_failure.error)
        self._observer.reconnect_exhausted(
            self._policy.maximum_attempts,
            self._policy.maximum_attempts,
            last_failure.stage,
            category,
        )
        raise SessionRecoveryExhaustedError(
            attempts=self._policy.maximum_attempts,
            recovery_stage=last_failure.stage,
            error_category=category,
        ) from last_failure.error

    def _establish_once(self) -> StratumMiningSession | None:
        client: StratumSessionClient | None = None
        keep_client = False
        try:
            try:
                client = self._client_factory()
                subscription = client.handshake()
            except StratumConnectionError as error:
                raise _RecoverableSessionFailure(
                    error=error,
                    stage=StratumRecoveryStage.HANDSHAKE,
                ) from error

            self._observer.session_authorized(subscription)
            seed = self._seed_factory(subscription.extra_nonce_2_size)
            _validate_seed(seed, subscription.extra_nonce_2_size)
            assembler = MiningJobAssembler(subscription)
            try:
                initial_job = self._wait_for_usable_work(client, assembler)
            except StratumConnectionError as error:
                raise _RecoverableSessionFailure(
                    error=error,
                    stage=StratumRecoveryStage.SESSION_WORK,
                ) from error
            if initial_job is None:
                return None

            keep_client = True
            return StratumMiningSession(
                client=client,
                subscription=subscription,
                assembler=assembler,
                initial_job=initial_job,
                extra_nonce_2_seed=seed,
                session_index=self._sessions_established + 1,
            )
        finally:
            if client is not None and not keep_client:
                _close_client_best_effort(client)

    def _wait_for_usable_work(
        self,
        client: StratumSessionClient,
        assembler: MiningJobAssembler,
    ) -> MiningJob | None:
        selected_job: MiningJob | None = None
        last_server_activity = _read_finite_clock(self._clock)
        while not self._stop_token.stop_requested:
            timeout = 0.0 if selected_job is not None else float(self._notification_timeout_seconds)
            notification = client.poll_notification(timeout_seconds=timeout)
            if notification is None:
                if selected_job is not None:
                    return selected_job
                if self._server_silence_seconds is not None:
                    elapsed = _read_finite_clock(self._clock) - last_server_activity
                    if elapsed >= self._server_silence_seconds:
                        raise StratumConnectionError(
                            "configured server-silence threshold exceeded before usable work"
                        )
                continue
            if not isinstance(
                notification,
                (SetDifficultyNotification, MiningNotifyNotification),
            ):
                raise SessionRecoveryError("unsupported parsed Stratum notification")
            self._observer.notification_received(notification)
            last_server_activity = _read_finite_clock(self._clock)
            if isinstance(notification, SetDifficultyNotification):
                assembler.apply_difficulty(notification)
            elif assembler.current_difficulty is not None:
                selected_job = assembler.build_job(notification)
        return None

    def _install_session(self, session: StratumMiningSession) -> StratumMiningSession:
        self._sessions_established += 1
        self._current_session = session
        return session

    def _close_current_best_effort(self) -> None:
        session = self._current_session
        self._current_session = None
        if session is not None:
            _close_client_best_effort(session.client)


def is_recoverable_stratum_error(error: object) -> bool:
    """Return whether an error is a genuine retryable connection failure."""

    return isinstance(error, StratumConnectionError)


def wait_for_reconnect_delay(
    delay_seconds: float,
    stop_token: _StopToken,
    *,
    sleep: Callable[[float], None] = time.sleep,
    quantum_seconds: float = _BACKOFF_QUANTUM_SECONDS,
) -> bool:
    """Wait without busy spinning, returning false when stop interrupts delay."""

    _validate_nonnegative_number(delay_seconds, "delay_seconds")
    _validate_positive_number(quantum_seconds, "quantum_seconds")
    if not isinstance(stop_token, _StopToken):
        raise SessionRecoveryValidationError("stop_token must expose stop_requested")
    if not callable(sleep):
        raise SessionRecoveryValidationError("sleep must be callable")

    remaining = float(delay_seconds)
    while remaining > 0:
        if stop_token.stop_requested:
            return False
        interval = min(float(quantum_seconds), remaining)
        sleep(interval)
        remaining -= interval
    return not stop_token.stop_requested


def _close_client_best_effort(client: StratumSessionClient) -> None:
    try:
        client.close()
    except BaseException:
        pass


def _recoverable_error_category(error: object) -> str:
    if isinstance(error, StratumConnectionError):
        return "StratumConnectionError"
    raise SessionRecoveryValidationError("error is not recoverable")


def _validate_seed(value: object, byte_size: int) -> str:
    if not isinstance(value, str):
        raise SessionRecoveryValidationError("extra nonce seed must be a string")
    if len(value) != byte_size * 2:
        raise SessionRecoveryValidationError(
            "extra nonce seed must match the negotiated fixed width"
        )
    if any(character not in _LOWER_HEX_DIGITS for character in value):
        raise SessionRecoveryValidationError(
            "extra nonce seed must contain lowercase hexadecimal characters"
        )
    return value


def _read_finite_clock(clock: Callable[[], float]) -> float:
    value = clock()
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SessionRecoveryValidationError("clock must return a number")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise SessionRecoveryValidationError("clock must return a finite number")
    return parsed


def _validate_positive_number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SessionRecoveryValidationError(f"{name} must be an integer or float")
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise SessionRecoveryValidationError(f"{name} must be finite and positive")
    return parsed


def _validate_nonnegative_number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SessionRecoveryValidationError(f"{name} must be an integer or float")
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0:
        raise SessionRecoveryValidationError(f"{name} must be finite and nonnegative")
    return parsed
