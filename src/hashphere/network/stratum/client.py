"""Synchronous stateful client for the Stratum V1 handshake."""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Mapping
from enum import Enum, auto
from typing import Protocol

from hashphere.config import Settings
from hashphere.network.stratum.messages import (
    MiningNotifyNotification,
    SetDifficultyNotification,
    StratumError,
    SubscribeResult,
    build_authorize_request,
    build_submit_request,
    build_subscribe_request,
    parse_authorize_result,
    parse_mining_notify,
    parse_set_difficulty,
    parse_stratum_error,
    parse_submit_result,
    parse_subscribe_result,
)
from hashphere.network.stratum.transport import (
    StratumReceiveTimeoutError,
    StratumTransport,
)

_StratumNotification = SetDifficultyNotification | MiningNotifyNotification


class StratumClientState(Enum):
    """Connection and handshake states for a Stratum client."""

    DISCONNECTED = auto()
    CONNECTED = auto()
    SUBSCRIBED = auto()
    AUTHORIZED = auto()


class StratumClientError(RuntimeError):
    """Base error for Stratum client failures."""


class StratumClientStateError(StratumClientError):
    """Raised when a client operation is invalid in the current state."""


class StratumRequestError(StratumClientError):
    """Raised when a response cannot satisfy the pending request."""

    def __init__(
        self,
        message: str,
        *,
        request_id: int | None = None,
        error: StratumError | None = None,
    ) -> None:
        super().__init__(message)
        self.request_id = request_id
        self.error = error


class StratumAuthorizationError(StratumRequestError):
    """Raised when a pool explicitly rejects authorization."""


class _StratumTransport(Protocol):
    """Operations required from an injected synchronous transport."""

    def connect(self) -> None:
        """Open the transport."""

    def send_message(self, message: Mapping[str, object]) -> None:
        """Send one JSON-compatible Stratum message."""

    def receive_message(
        self,
        timeout_seconds: float | None = None,
    ) -> Mapping[str, object]:
        """Receive one decoded Stratum message with an optional timeout."""

    def close(self) -> None:
        """Close the transport."""


class StratumClient:
    """Perform a synchronous Stratum handshake and authenticated requests."""

    def __init__(
        self,
        settings: Settings,
        user_agent: str,
        *,
        transport: _StratumTransport | None = None,
    ) -> None:
        if not isinstance(user_agent, str):
            raise TypeError("user_agent must be a string")
        if not user_agent.strip():
            raise ValueError("user_agent must not be empty")

        self._settings = settings
        self._user_agent = user_agent
        self._transport: _StratumTransport = (
            transport
            if transport is not None
            else StratumTransport(settings.stratum_host, settings.stratum_port)
        )
        self._state = StratumClientState.DISCONNECTED
        self._next_request_id = 1
        self._subscribe_result: SubscribeResult | None = None
        self._notifications: deque[_StratumNotification] = deque()

    @property
    def state(self) -> StratumClientState:
        """Return the client's current connection and handshake state."""

        return self._state

    @property
    def subscribe_result(self) -> SubscribeResult | None:
        """Return the active subscription result, if subscribed."""

        return self._subscribe_result

    def connect(self) -> None:
        """Connect the transport from the disconnected state."""

        self._require_state(StratumClientState.DISCONNECTED, "connect")
        self._transport.connect()
        self._state = StratumClientState.CONNECTED

    def subscribe(self) -> SubscribeResult:
        """Subscribe to pool jobs from the connected state."""

        self._require_state(StratumClientState.CONNECTED, "subscribe")
        request_id = self._allocate_request_id()
        self._transport.send_message(build_subscribe_request(request_id, self._user_agent))
        response = self._wait_for_response(request_id)
        result = parse_subscribe_result(response)

        self._subscribe_result = result
        self._state = StratumClientState.SUBSCRIBED
        return result

    def authorize(self) -> None:
        """Authorize the configured worker from the subscribed state."""

        self._require_state(StratumClientState.SUBSCRIBED, "authorize")
        request_id = self._allocate_request_id()
        self._transport.send_message(
            build_authorize_request(
                request_id,
                self._settings.stratum_username,
                self._settings.stratum_password,
            )
        )
        response = self._wait_for_response(request_id)
        if not parse_authorize_result(response):
            raise StratumAuthorizationError(
                f"Stratum authorization was rejected for request {request_id}",
                request_id=request_id,
            )

        self._state = StratumClientState.AUTHORIZED

    def submit_share(
        self,
        job_id: str,
        extra_nonce_2: str,
        network_time: str,
        nonce: int,
    ) -> bool:
        """Submit one share while authorized and return the pool's Boolean result."""

        self._require_state(StratumClientState.AUTHORIZED, "submit_share")
        request_id = self._allocate_request_id()
        request = build_submit_request(
            request_id,
            self._settings.stratum_username,
            job_id,
            extra_nonce_2,
            network_time,
            nonce,
        )
        self._transport.send_message(request)
        response = self._wait_for_response(request_id)
        return parse_submit_result(response)

    def handshake(self) -> SubscribeResult:
        """Connect, subscribe, and authorize; clean up fully on failure."""

        try:
            self.connect()
            result = self.subscribe()
            self.authorize()
        except BaseException:
            try:
                self.close()
            except BaseException:
                pass
            raise

        return result

    def receive_notification(
        self,
    ) -> SetDifficultyNotification | MiningNotifyNotification:
        """Return the next supported notification in stream arrival order."""

        self._require_state(StratumClientState.AUTHORIZED, "receive_notification")
        if self._notifications:
            return self._notifications.popleft()

        message = self._transport.receive_message()
        return self._route_notification_message(message)

    def poll_notification(
        self,
        timeout_seconds: float = 0.0,
    ) -> SetDifficultyNotification | MiningNotifyNotification | None:
        """Return the next notification, or ``None`` after a bounded timeout."""

        self._require_state(StratumClientState.AUTHORIZED, "poll_notification")
        _validate_poll_timeout(timeout_seconds)
        if self._notifications:
            return self._notifications.popleft()

        try:
            message = self._transport.receive_message(timeout_seconds=timeout_seconds)
        except StratumReceiveTimeoutError:
            return None

        return self._route_notification_message(message)

    def _route_notification_message(
        self,
        message: Mapping[str, object],
    ) -> _StratumNotification:
        if "method" not in message:
            response_id = self._parse_response_id(message)
            raise StratumRequestError(
                f"unexpected response ID {response_id}; no request is pending",
                request_id=response_id,
            )

        return self._parse_notification(message)

    def close(self) -> None:
        """Close the transport and reset session state; repeated calls are safe."""

        try:
            self._transport.close()
        finally:
            self._state = StratumClientState.DISCONNECTED
            self._subscribe_result = None
            self._notifications.clear()

    def _allocate_request_id(self) -> int:
        request_id = self._next_request_id
        self._next_request_id += 1
        return request_id

    def _wait_for_response(self, expected_request_id: int) -> Mapping[str, object]:
        while True:
            message = self._transport.receive_message()
            if "method" in message:
                self._notifications.append(self._parse_notification(message))
                continue

            response_id = self._parse_response_id(message)
            if response_id != expected_request_id:
                raise StratumRequestError(
                    f"unexpected response ID {response_id}; expected {expected_request_id}",
                    request_id=response_id,
                )

            if "error" not in message:
                raise StratumRequestError(
                    f"response {response_id} is missing the error field",
                    request_id=response_id,
                )

            error = parse_stratum_error(message["error"])
            if error is not None:
                raise StratumRequestError(
                    f"Stratum request {response_id} failed ({error.code}): {error.message}",
                    request_id=response_id,
                    error=error,
                )

            return message

    def _parse_response_id(self, message: Mapping[str, object]) -> int:
        if "id" not in message:
            raise StratumRequestError("response is missing the id field")

        request_id = message["id"]
        if isinstance(request_id, bool) or not isinstance(request_id, int):
            raise StratumRequestError("response id must be a nonnegative integer")
        if request_id < 0:
            raise StratumRequestError("response id must be a nonnegative integer")
        return request_id

    def _parse_notification(
        self,
        message: Mapping[str, object],
    ) -> _StratumNotification:
        if "id" in message and message["id"] is not None:
            raise StratumClientError("notification id must be null when present")

        method = message.get("method")
        if not isinstance(method, str):
            raise StratumClientError("notification method must be a string")
        if method == "mining.set_difficulty":
            return parse_set_difficulty(message)
        if method == "mining.notify":
            return parse_mining_notify(message)
        raise StratumClientError(f"unsupported Stratum notification method: {method}")

    def _require_state(self, required: StratumClientState, operation: str) -> None:
        if self._state is not required:
            raise StratumClientStateError(
                f"{operation} requires state {required.name}; current state is {self._state.name}"
            )


def _validate_poll_timeout(timeout_seconds: float) -> None:
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
        raise TypeError("timeout_seconds must be an integer or float")
    if timeout_seconds < 0 or (
        isinstance(timeout_seconds, float) and not math.isfinite(timeout_seconds)
    ):
        raise ValueError("timeout_seconds must be finite and nonnegative")
