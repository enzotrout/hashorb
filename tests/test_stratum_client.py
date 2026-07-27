"""Tests for the synchronous Stratum client state machine."""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping

import pytest

import hashphere.network.stratum.client as client_module
from hashphere.config import Settings
from hashphere.network.stratum import (
    MiningNotifyNotification,
    SetDifficultyNotification,
    StratumAuthorizationError,
    StratumClient,
    StratumClientError,
    StratumClientState,
    StratumClientStateError,
    StratumRequestError,
    SubscribeResult,
)


class FakeTransport:
    """Deterministic in-memory transport for client tests."""

    def __init__(
        self,
        incoming: list[dict[str, object] | BaseException] | None = None,
        *,
        connect_error: BaseException | None = None,
        send_error: BaseException | None = None,
    ) -> None:
        self.incoming = deque(incoming or [])
        self.connect_error = connect_error
        self.send_error = send_error
        self.sent: list[dict[str, object]] = []
        self.connect_calls = 0
        self.close_calls = 0
        self.connected = False

    def connect(self) -> None:
        self.connect_calls += 1
        if self.connect_error is not None:
            raise self.connect_error
        self.connected = True

    def send_message(self, message: Mapping[str, object]) -> None:
        if self.send_error is not None:
            raise self.send_error
        self.sent.append(dict(message))

    def receive_message(self) -> dict[str, object]:
        if not self.incoming:
            raise AssertionError("fake transport has no incoming messages")
        message = self.incoming.popleft()
        if isinstance(message, BaseException):
            raise message
        return message

    def close(self) -> None:
        self.close_calls += 1
        self.connected = False


def make_settings() -> Settings:
    """Return deterministic settings for a client test."""

    return Settings(
        stratum_host="pool.example.com",
        stratum_port=3333,
        bitcoin_address="bc1qtestaddress",
        worker_name="rig-01",
        stratum_password="secret",
        compute_backend="cpu",
        compute_profile="lite",
    )


def subscribe_response(request_id: object = 1) -> dict[str, object]:
    """Build a valid subscription response."""

    return {
        "id": request_id,
        "result": [
            [["mining.notify", "subscription-id"]],
            "08000002",
            4,
        ],
        "error": None,
    }


def authorize_response(result: object = True, request_id: object = 2) -> dict[str, object]:
    """Build an authorization response."""

    return {"id": request_id, "result": result, "error": None}


def difficulty_notification(difficulty: object = 1024) -> dict[str, object]:
    """Build a difficulty notification."""

    return {
        "id": None,
        "method": "mining.set_difficulty",
        "params": [difficulty],
    }


def mining_notification(job_id: object = "job-1") -> dict[str, object]:
    """Build a mining job notification."""

    return {
        "id": None,
        "method": "mining.notify",
        "params": [
            job_id,
            "00aabbcc",
            "01000000",
            "ffffffff",
            ["11223344"],
            "20000000",
            "170fffff",
            "65f04abc",
            True,
        ],
    }


def make_client(
    transport: FakeTransport,
    *,
    user_agent: str = "Hashphere/0.1",
) -> StratumClient:
    """Create a client backed by a fake transport."""

    return StratumClient(make_settings(), user_agent, transport=transport)


def test_client_constructs_normal_transport_from_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created_with: list[tuple[str, int]] = []
    fake = FakeTransport()

    def fake_transport(host: str, port: int) -> FakeTransport:
        created_with.append((host, port))
        return fake

    monkeypatch.setattr(client_module, "StratumTransport", fake_transport)

    client = StratumClient(make_settings(), "Hashphere/0.1")

    assert client.state is StratumClientState.DISCONNECTED
    assert created_with == [("pool.example.com", 3333)]


@pytest.mark.parametrize("user_agent", ["", "   "])
def test_client_rejects_empty_user_agent(user_agent: str) -> None:
    with pytest.raises(ValueError, match="user_agent"):
        make_client(FakeTransport(), user_agent=user_agent)


def test_valid_state_transitions_and_request_contents() -> None:
    transport = FakeTransport([subscribe_response(), authorize_response()])
    client = make_client(transport)

    assert client.state is StratumClientState.DISCONNECTED
    client.connect()
    assert client.state is StratumClientState.CONNECTED

    result = client.subscribe()
    assert client.state is StratumClientState.SUBSCRIBED
    assert client.subscribe_result is result
    assert transport.sent[0] == {
        "id": 1,
        "method": "mining.subscribe",
        "params": ["Hashphere/0.1"],
    }

    client.authorize()
    assert client.state is StratumClientState.AUTHORIZED
    assert transport.sent[1] == {
        "id": 2,
        "method": "mining.authorize",
        "params": [make_settings().stratum_username, "secret"],
    }

    client.close()
    assert client.state is StratumClientState.DISCONNECTED
    assert client.subscribe_result is None


def test_request_ids_increment_across_repeated_sessions() -> None:
    transport = FakeTransport(
        [
            subscribe_response(1),
            authorize_response(True, 2),
            subscribe_response(3),
            authorize_response(True, 4),
        ]
    )
    client = make_client(transport)

    client.handshake()
    client.close()
    client.handshake()

    assert [message["id"] for message in transport.sent] == [1, 2, 3, 4]
    assert all(type(message["id"]) is int for message in transport.sent)


def test_invalid_method_ordering() -> None:
    client = make_client(FakeTransport())

    with pytest.raises(StratumClientStateError, match="subscribe requires"):
        client.subscribe()
    with pytest.raises(StratumClientStateError, match="authorize requires"):
        client.authorize()
    with pytest.raises(StratumClientStateError, match="receive_notification requires"):
        client.receive_notification()

    client.connect()
    with pytest.raises(StratumClientStateError, match="connect requires"):
        client.connect()
    with pytest.raises(StratumClientStateError, match="authorize requires"):
        client.authorize()


def test_authorization_false_is_rejected() -> None:
    transport = FakeTransport([subscribe_response(), authorize_response(False)])
    client = make_client(transport)
    client.connect()
    client.subscribe()

    with pytest.raises(StratumAuthorizationError, match="rejected"):
        client.authorize()

    assert client.state is StratumClientState.SUBSCRIBED


def test_stratum_error_response_is_exposed() -> None:
    transport = FakeTransport(
        [
            {
                "id": 1,
                "result": None,
                "error": [20, "Other/Unknown", {"retry": False}],
            }
        ]
    )
    client = make_client(transport)
    client.connect()

    with pytest.raises(StratumRequestError, match="Other/Unknown") as captured:
        client.subscribe()

    assert captured.value.request_id == 1
    assert captured.value.error is not None
    assert captured.value.error.code == 20


def test_mismatched_response_id_is_rejected() -> None:
    client = make_client(FakeTransport([subscribe_response(99)]))
    client.connect()

    with pytest.raises(StratumRequestError, match="expected 1"):
        client.subscribe()


@pytest.mark.parametrize("response_id", [None, True, -1, 1.0, "1"])
def test_malformed_response_ids_are_rejected(response_id: object) -> None:
    client = make_client(FakeTransport([subscribe_response(response_id)]))
    client.connect()

    with pytest.raises(StratumRequestError, match="nonnegative integer"):
        client.subscribe()


def test_missing_response_id_is_rejected() -> None:
    response = subscribe_response()
    del response["id"]
    client = make_client(FakeTransport([response]))
    client.connect()

    with pytest.raises(StratumRequestError, match="missing the id"):
        client.subscribe()


def test_notifications_before_responses_are_queued_in_arrival_order() -> None:
    transport = FakeTransport(
        [
            difficulty_notification(2048),
            subscribe_response(),
            mining_notification("job-before-auth"),
            authorize_response(),
        ]
    )
    client = make_client(transport)

    client.handshake()

    assert client.receive_notification() == SetDifficultyNotification(difficulty=2048)
    assert client.receive_notification() == MiningNotifyNotification(
        job_id="job-before-auth",
        previous_block_hash="00aabbcc",
        coinbase_part_1="01000000",
        coinbase_part_2="ffffffff",
        merkle_branches=("11223344",),
        version="20000000",
        network_bits="170fffff",
        network_time="65f04abc",
        clean_jobs=True,
    )


def test_receive_notification_reads_when_queue_is_empty() -> None:
    transport = FakeTransport(
        [subscribe_response(), authorize_response(), difficulty_notification(4096.5)]
    )
    client = make_client(transport)
    client.handshake()

    assert client.receive_notification() == SetDifficultyNotification(difficulty=4096.5)


def test_unexpected_response_while_receiving_notification_is_rejected() -> None:
    transport = FakeTransport([subscribe_response(), authorize_response(), {"id": 3}])
    client = make_client(transport)
    client.handshake()

    with pytest.raises(StratumRequestError, match="no request is pending"):
        client.receive_notification()


def test_unsupported_notification_is_rejected() -> None:
    transport = FakeTransport(
        [
            subscribe_response(),
            authorize_response(),
            {"id": None, "method": "client.reconnect", "params": []},
        ]
    )
    client = make_client(transport)
    client.handshake()

    with pytest.raises(StratumClientError, match="unsupported.*client.reconnect"):
        client.receive_notification()


def test_malformed_notification_is_not_ignored_while_waiting() -> None:
    transport = FakeTransport([difficulty_notification("high"), subscribe_response()])
    client = make_client(transport)
    client.connect()

    with pytest.raises(ValueError, match="integer or float"):
        client.subscribe()


@pytest.mark.parametrize(
    ("incoming", "connect_error", "expected_error"),
    [
        ([], RuntimeError("connect failed"), "connect failed"),
        (
            [{"id": 1, "result": None, "error": [20, "subscribe failed", None]}],
            None,
            "subscribe failed",
        ),
        ([subscribe_response(), authorize_response(False)], None, "rejected"),
    ],
)
def test_handshake_cleans_up_each_failure_stage(
    incoming: list[dict[str, object]],
    connect_error: BaseException | None,
    expected_error: str,
) -> None:
    transport = FakeTransport(incoming, connect_error=connect_error)
    client = make_client(transport)

    with pytest.raises(Exception, match=expected_error):
        client.handshake()

    assert client.state is StratumClientState.DISCONNECTED
    assert client.subscribe_result is None
    assert transport.close_calls == 1
    assert transport.connected is False


def test_handshake_reraises_original_exception() -> None:
    failure = RuntimeError("send failed")
    transport = FakeTransport(send_error=failure)
    client = make_client(transport)

    with pytest.raises(RuntimeError) as captured:
        client.handshake()

    assert captured.value is failure


def test_close_is_safe_and_idempotent_from_every_state() -> None:
    disconnected_transport = FakeTransport()
    disconnected = make_client(disconnected_transport)
    disconnected.close()
    disconnected.close()
    assert disconnected.state is StratumClientState.DISCONNECTED
    assert disconnected_transport.close_calls == 2

    connected = make_client(FakeTransport())
    connected.connect()
    connected.close()
    assert connected.state is StratumClientState.DISCONNECTED

    subscribed = make_client(FakeTransport([subscribe_response()]))
    subscribed.connect()
    subscribed.subscribe()
    subscribed.close()
    assert subscribed.state is StratumClientState.DISCONNECTED

    authorized = make_client(FakeTransport([subscribe_response(), authorize_response()]))
    authorized.handshake()
    authorized.close()
    authorized.close()
    assert authorized.state is StratumClientState.DISCONNECTED


def test_complete_successful_handshake() -> None:
    transport = FakeTransport([subscribe_response(), authorize_response()])
    client = make_client(transport)

    result = client.handshake()

    assert result == SubscribeResult(
        subscriptions=(("mining.notify", "subscription-id"),),
        extra_nonce_1="08000002",
        extra_nonce_2_size=4,
    )
    assert client.subscribe_result is result
    assert client.state is StratumClientState.AUTHORIZED
    assert transport.connect_calls == 1
    assert transport.connected is True
