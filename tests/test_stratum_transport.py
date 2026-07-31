"""Tests for the Stratum TCP transport."""

from __future__ import annotations

import io

import pytest

import hashorb.network.stratum.transport as transport_module
from hashorb.network.stratum import (
    StratumConnectionError,
    StratumProtocolError,
    StratumReceiveTimeoutError,
    StratumTransport,
)


class FakeSocket:
    """Small socket test double."""

    def __init__(
        self,
        incoming: bytes = b"",
        *,
        recv_effects: list[bytes | BaseException] | None = None,
    ) -> None:
        self.reader = io.BytesIO(incoming)
        self.recv_effects = recv_effects or []
        self.sent = bytearray()
        self.closed = False
        self.timeout: float | None = None
        self.timeout_history: list[float | None] = []
        self.recv_calls = 0
        self.socket_options: list[tuple[int, int, int]] = []

    def setsockopt(self, level: int, option: int, value: int) -> None:
        self.socket_options.append((level, option, value))

    def settimeout(self, timeout: float | None) -> None:
        self.timeout = timeout
        self.timeout_history.append(timeout)

    def gettimeout(self) -> float | None:
        return self.timeout

    def makefile(self, mode: str) -> io.BytesIO:
        assert mode == "rb"
        return self.reader

    def sendall(self, payload: bytes) -> None:
        self.sent.extend(payload)

    def recv(self, size: int) -> bytes:
        self.recv_calls += 1
        if self.recv_effects:
            effect = self.recv_effects.pop(0)
            if isinstance(effect, BaseException):
                raise effect
            return effect
        return self.reader.read(size)

    def close(self) -> None:
        self.closed = True


def connect_fake_socket(
    monkeypatch: pytest.MonkeyPatch,
    fake_socket: FakeSocket,
) -> StratumTransport:
    def fake_create_connection(
        address: tuple[str, int],
        timeout: float,
    ) -> FakeSocket:
        assert address == ("pool.example.com", 3333)
        assert timeout == 5.0
        return fake_socket

    monkeypatch.setattr(
        transport_module.socket,
        "create_connection",
        fake_create_connection,
    )

    transport = StratumTransport(
        "pool.example.com",
        3333,
        timeout=5.0,
    )
    transport.connect()
    return transport


@pytest.mark.parametrize(
    ("host", "port", "timeout", "message"),
    [
        ("", 3333, 10.0, "host must not be empty"),
        ("pool.example.com", 0, 10.0, "port must be between"),
        ("pool.example.com", 65536, 10.0, "port must be between"),
        ("pool.example.com", 3333, 0.0, "timeout must be greater"),
    ],
)
def test_invalid_constructor_values_are_rejected(
    host: str,
    port: int,
    timeout: float,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        StratumTransport(host, port, timeout=timeout)


def test_connect_opens_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_socket = FakeSocket()

    transport = connect_fake_socket(monkeypatch, fake_socket)

    assert transport.is_connected is True
    assert fake_socket.timeout == 5.0
    assert fake_socket.socket_options == [
        (transport_module.socket.SOL_SOCKET, transport_module.socket.SO_KEEPALIVE, 1)
    ]


def test_unsupported_keepalive_degrades_to_connected_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class KeepaliveUnsupportedSocket(FakeSocket):
        def setsockopt(self, level: int, option: int, value: int) -> None:
            del level, option, value
            raise OSError("unsupported")

    fake_socket = KeepaliveUnsupportedSocket()

    transport = connect_fake_socket(monkeypatch, fake_socket)

    assert transport.is_connected is True
    assert fake_socket.timeout == 5.0


def test_connect_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_socket = FakeSocket()
    calls = 0

    def fake_create_connection(
        address: tuple[str, int],
        timeout: float,
    ) -> FakeSocket:
        nonlocal calls
        calls += 1
        return fake_socket

    monkeypatch.setattr(
        transport_module.socket,
        "create_connection",
        fake_create_connection,
    )

    transport = StratumTransport("pool.example.com", 3333)
    transport.connect()
    transport.connect()

    assert calls == 1


def test_connect_wraps_socket_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_create_connection(
        address: tuple[str, int],
        timeout: float,
    ) -> FakeSocket:
        raise OSError("network unavailable")

    monkeypatch.setattr(
        transport_module.socket,
        "create_connection",
        fake_create_connection,
    )

    transport = StratumTransport("pool.example.com", 3333)

    with pytest.raises(
        StratumConnectionError,
        match="could not connect",
    ):
        transport.connect()


def test_send_message_writes_compact_json_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_socket = FakeSocket()
    transport = connect_fake_socket(monkeypatch, fake_socket)

    transport.send_message(
        {
            "id": 1,
            "method": "mining.subscribe",
            "params": [],
        }
    )

    assert bytes(fake_socket.sent) == (b'{"id":1,"method":"mining.subscribe","params":[]}\n')


def test_send_requires_connection() -> None:
    transport = StratumTransport("pool.example.com", 3333)

    with pytest.raises(
        StratumConnectionError,
        match="not connected",
    ):
        transport.send_message({"id": 1})


def test_send_rejects_non_serializable_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_socket = FakeSocket()
    transport = connect_fake_socket(monkeypatch, fake_socket)

    with pytest.raises(
        StratumProtocolError,
        match="not JSON serializable",
    ):
        transport.send_message({"invalid": object()})


def test_receive_message_decodes_json_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    incoming = b'{"id":1,"result":true,"error":null}\n'
    fake_socket = FakeSocket(incoming)
    transport = connect_fake_socket(monkeypatch, fake_socket)

    assert transport.receive_message() == {
        "id": 1,
        "result": True,
        "error": None,
    }


def test_ordinary_receive_retains_configured_socket_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_socket = FakeSocket(b'{"id":1}\n')
    transport = connect_fake_socket(monkeypatch, fake_socket)
    timeout_history = list(fake_socket.timeout_history)

    assert transport.receive_message(None) == {"id": 1}
    assert fake_socket.timeout == 5.0
    assert fake_socket.timeout_history == timeout_history


@pytest.mark.parametrize(
    ("incoming", "message"),
    [
        (b"not-json\n", "malformed JSON"),
        (b"[1,2,3]\n", "must be an object"),
        (b"\xff\n", "not valid UTF-8"),
    ],
)
def test_receive_rejects_invalid_messages(
    monkeypatch: pytest.MonkeyPatch,
    incoming: bytes,
    message: str,
) -> None:
    fake_socket = FakeSocket(incoming)
    transport = connect_fake_socket(monkeypatch, fake_socket)

    with pytest.raises(StratumProtocolError, match=message):
        transport.receive_message()


def test_receive_detects_closed_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_socket = FakeSocket(b"")
    transport = connect_fake_socket(monkeypatch, fake_socket)

    with pytest.raises(
        StratumConnectionError,
        match="closed the connection",
    ):
        transport.receive_message()


def test_receive_requires_connection() -> None:
    transport = StratumTransport("pool.example.com", 3333)

    with pytest.raises(
        StratumConnectionError,
        match="not connected",
    ):
        transport.receive_message()


def test_temporary_receive_timeout_is_restored_after_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_socket = FakeSocket(b'{"id":1}\n')
    transport = connect_fake_socket(monkeypatch, fake_socket)

    assert transport.receive_message(0.25) == {"id": 1}
    assert fake_socket.timeout == 5.0
    assert fake_socket.timeout_history[-1] == 5.0


@pytest.mark.parametrize(
    "timeout_error",
    [BlockingIOError(), TimeoutError()],
)
def test_temporary_receive_timeout_is_distinct_and_restored(
    monkeypatch: pytest.MonkeyPatch,
    timeout_error: BaseException,
) -> None:
    fake_socket = FakeSocket(recv_effects=[timeout_error])
    transport = connect_fake_socket(monkeypatch, fake_socket)

    with pytest.raises(StratumReceiveTimeoutError, match="timed out"):
        transport.receive_message(0.0)

    assert fake_socket.timeout == 5.0
    assert fake_socket.timeout_history[-1] == 5.0


def test_temporary_receive_timeout_is_restored_after_connection_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_socket = FakeSocket(recv_effects=[OSError("read failed")])
    transport = connect_fake_socket(monkeypatch, fake_socket)

    with pytest.raises(StratumConnectionError, match="failed to read"):
        transport.receive_message(1.0)

    assert fake_socket.timeout == 5.0
    assert fake_socket.timeout_history[-1] == 5.0


def test_temporary_receive_timeout_is_restored_after_connection_closure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_socket = FakeSocket()
    transport = connect_fake_socket(monkeypatch, fake_socket)

    with pytest.raises(StratumConnectionError, match="closed the connection"):
        transport.receive_message(1.0)

    assert fake_socket.timeout == 5.0
    assert fake_socket.timeout_history[-1] == 5.0


def test_temporary_receive_timeout_is_restored_before_parsing_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_socket = FakeSocket(b"not-json\n")
    transport = connect_fake_socket(monkeypatch, fake_socket)

    with pytest.raises(StratumProtocolError, match="malformed JSON"):
        transport.receive_message(1.0)

    assert fake_socket.timeout == 5.0
    assert fake_socket.timeout_history[-1] == 5.0


def test_partial_message_is_preserved_across_receive_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_socket = FakeSocket(
        recv_effects=[b'{"id":', TimeoutError(), b"1}\n"],
    )
    transport = connect_fake_socket(monkeypatch, fake_socket)

    with pytest.raises(StratumReceiveTimeoutError):
        transport.receive_message(1.0)

    assert transport.receive_message(1.0) == {"id": 1}


def test_multiple_framed_messages_are_preserved_in_arrival_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_socket = FakeSocket(b'{"id":1}\n{"id":2}\n')
    transport = connect_fake_socket(monkeypatch, fake_socket)

    assert transport.receive_message(0.0) == {"id": 1}
    recv_calls = fake_socket.recv_calls
    assert transport.receive_message(0.0) == {"id": 2}
    assert fake_socket.recv_calls == recv_calls


def test_close_is_safe_and_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_socket = FakeSocket()
    transport = connect_fake_socket(monkeypatch, fake_socket)

    transport.close()
    transport.close()

    assert transport.is_connected is False
    assert fake_socket.closed is True


def test_context_manager_connects_and_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_socket = FakeSocket()

    monkeypatch.setattr(
        transport_module.socket,
        "create_connection",
        lambda address, timeout: fake_socket,
    )

    with StratumTransport("pool.example.com", 3333) as transport:
        assert transport.is_connected is True

    assert transport.is_connected is False
    assert fake_socket.closed is True
