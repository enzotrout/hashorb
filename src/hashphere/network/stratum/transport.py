"""TCP transport for newline-delimited Stratum JSON messages."""

from __future__ import annotations

import json
import socket
from collections.abc import Mapping
from types import TracebackType
from typing import Any, Self


class StratumTransportError(RuntimeError):
    """Base error for Stratum transport failures."""


class StratumConnectionError(StratumTransportError):
    """Raised when the transport cannot connect or communicate."""


class StratumProtocolError(StratumTransportError):
    """Raised when a received line is not a valid JSON object."""


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
        self._reader: Any | None = None

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
            connection.settimeout(self.timeout)
            reader = connection.makefile("rb")
        except OSError as exc:
            raise StratumConnectionError(
                f"could not connect to {self.host}:{self.port}"
            ) from exc

        self._socket = connection
        self._reader = reader

    def send_message(self, message: Mapping[str, Any]) -> None:
        """Encode and send one newline-delimited JSON object."""

        connection = self._require_socket()

        try:
            payload = json.dumps(
                dict(message),
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8") + b"\n"
        except (TypeError, ValueError) as exc:
            raise StratumProtocolError(
                "message is not JSON serializable"
            ) from exc

        try:
            connection.sendall(payload)
        except OSError as exc:
            raise StratumConnectionError(
                "failed to send Stratum message"
            ) from exc

    def receive_message(self) -> dict[str, Any]:
        """Read and decode one newline-delimited JSON object."""

        reader = self._require_reader()

        try:
            line = reader.readline()
        except OSError as exc:
            raise StratumConnectionError(
                "failed to read Stratum message"
            ) from exc

        if line == b"":
            raise StratumConnectionError(
                "Stratum server closed the connection"
            )

        try:
            decoded = line.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise StratumProtocolError(
                "received Stratum message is not valid UTF-8"
            ) from exc

        try:
            message = json.loads(decoded)
        except json.JSONDecodeError as exc:
            raise StratumProtocolError(
                "received malformed JSON"
            ) from exc

        if not isinstance(message, dict):
            raise StratumProtocolError(
                "received JSON value must be an object"
            )

        return message

    def close(self) -> None:
        """Close the reader and socket safely."""

        reader = self._reader
        connection = self._socket

        self._reader = None
        self._socket = None

        if reader is not None:
            try:
                reader.close()
            except OSError:
                pass

        if connection is not None:
            try:
                connection.close()
            except OSError:
                pass

    def _require_socket(self) -> socket.socket:
        if self._socket is None:
            raise StratumConnectionError(
                "Stratum transport is not connected"
            )

        return self._socket

    def _require_reader(self) -> Any:
        if self._reader is None:
            raise StratumConnectionError(
                "Stratum transport is not connected"
            )

        return self._reader

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
