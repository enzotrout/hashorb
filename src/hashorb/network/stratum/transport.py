"""TCP transport for newline-delimited Stratum JSON messages."""

from __future__ import annotations

import json
import math
import socket
import time
from collections.abc import Mapping
from types import TracebackType
from typing import Any, Self


class StratumTransportError(RuntimeError):
    """Base error for Stratum transport failures."""


class StratumConnectionError(StratumTransportError):
    """Raised when the transport cannot connect or communicate."""


class StratumProtocolError(StratumTransportError):
    """Raised when a received line is not a valid JSON object."""


class StratumReceiveTimeoutError(StratumTransportError):
    """Raised when no complete Stratum message arrives before a receive timeout."""


class StratumTransport:
    """Blocking TCP transport for newline-delimited JSON messages."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        timeout: float = 10.0,
    ) -> None:
        if not host.strip():
            raise ValueError("host must not be empty")

        if not 1 <= port <= 65535:
            raise ValueError("port must be between 1 and 65535")

        if timeout <= 0:
            raise ValueError("timeout must be greater than zero")

        self.host = host.strip()
        self.port = port
        self.timeout = timeout

        self._socket: socket.socket | None = None
        self._receive_buffer = bytearray()

    @property
    def is_connected(self) -> bool:
        """Return whether the transport currently owns an open socket."""

        return self._socket is not None

    def connect(self) -> None:
        """Open the TCP connection."""

        if self.is_connected:
            return

        try:
            connection = socket.create_connection(
                (self.host, self.port),
                timeout=self.timeout,
            )
            try:
                connection.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            except OSError:
                pass
            connection.settimeout(self.timeout)
        except OSError as exc:
            raise StratumConnectionError(f"could not connect to {self.host}:{self.port}") from exc

        self._socket = connection

    def send_message(self, message: Mapping[str, Any]) -> None:
        """Encode and send one newline-delimited JSON object."""

        connection = self._require_socket()

        try:
            payload = (
                json.dumps(
                    dict(message),
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode("utf-8")
                + b"\n"
            )
        except (TypeError, ValueError) as exc:
            raise StratumProtocolError("message is not JSON serializable") from exc

        try:
            connection.sendall(payload)
        except OSError as exc:
            raise StratumConnectionError("failed to send Stratum message") from exc

    def receive_message(
        self,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        """Read one JSON message, optionally bounded by a temporary timeout."""

        if timeout_seconds is not None:
            _validate_receive_timeout(timeout_seconds)

        line = self._receive_line(timeout_seconds)

        try:
            decoded = line.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise StratumProtocolError("received Stratum message is not valid UTF-8") from exc

        try:
            message = json.loads(decoded)
        except json.JSONDecodeError as exc:
            raise StratumProtocolError("received malformed JSON") from exc

        if not isinstance(message, dict):
            raise StratumProtocolError("received JSON value must be an object")

        return message

    def close(self) -> None:
        """Close the socket safely and discard buffered session data."""

        connection = self._socket

        self._socket = None
        self._receive_buffer.clear()

        if connection is not None:
            try:
                connection.close()
            except OSError:
                pass

    def _require_socket(self) -> socket.socket:
        if self._socket is None:
            raise StratumConnectionError("Stratum transport is not connected")

        return self._socket

    def _receive_line(self, timeout_seconds: float | None) -> bytes:
        connection = self._require_socket()
        newline_index = self._receive_buffer.find(b"\n")
        if newline_index >= 0:
            return self._pop_buffered_line(newline_index)

        previous_timeout: float | None = None
        deadline: float | None = None
        if timeout_seconds is not None:
            try:
                previous_timeout = connection.gettimeout()
                connection.settimeout(timeout_seconds)
            except OSError as exc:
                raise StratumConnectionError("failed to configure Stratum receive timeout") from exc
            deadline = time.monotonic() + timeout_seconds

        try:
            while True:
                try:
                    chunk = connection.recv(4096)
                except (BlockingIOError, TimeoutError) as exc:
                    raise StratumReceiveTimeoutError(
                        "timed out waiting for a Stratum message"
                    ) from exc
                except OSError as exc:
                    raise StratumConnectionError("failed to read Stratum message") from exc

                if chunk == b"":
                    raise StratumConnectionError("Stratum server closed the connection")

                self._receive_buffer.extend(chunk)
                newline_index = self._receive_buffer.find(b"\n")
                if newline_index >= 0:
                    return self._pop_buffered_line(newline_index)

                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise StratumReceiveTimeoutError("timed out waiting for a Stratum message")
                    try:
                        connection.settimeout(remaining)
                    except OSError as exc:
                        raise StratumConnectionError(
                            "failed to configure Stratum receive timeout"
                        ) from exc
        finally:
            if timeout_seconds is not None:
                try:
                    connection.settimeout(previous_timeout)
                except OSError as exc:
                    raise StratumConnectionError(
                        "failed to restore Stratum receive timeout"
                    ) from exc

    def _pop_buffered_line(self, newline_index: int) -> bytes:
        line = bytes(self._receive_buffer[:newline_index])
        del self._receive_buffer[: newline_index + 1]
        return line

    def __enter__(self) -> Self:
        self.connect()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


def _validate_receive_timeout(timeout_seconds: float) -> None:
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
        raise TypeError("timeout_seconds must be an integer or float")
    if timeout_seconds < 0 or (
        isinstance(timeout_seconds, float) and not math.isfinite(timeout_seconds)
    ):
        raise ValueError("timeout_seconds must be finite and nonnegative")
