"""Compatibility tests for informational CKPool Stratum messages."""

from __future__ import annotations

from test_stratum_client_submission import (
    FakeTransport,
    authorize_response,
    difficulty_notification,
    make_authorized_client,
    make_client,
    submit_response,
    subscribe_response,
)

from hashorb.network.stratum import SetDifficultyNotification


def ping_message() -> dict[str, object]:
    """Build CKPool's informational mining.ping server message."""

    return {"id": 42, "method": "mining.ping", "params": []}


def show_message() -> dict[str, object]:
    """Build CKPool's informational client.show_message server message."""

    return {"id": None, "method": "client.show_message", "params": ["synthetic"]}


def test_informational_messages_do_not_interrupt_share_response_wait() -> None:
    client, _transport = make_authorized_client(
        [
            ping_message(),
            show_message(),
            submit_response(True, 3),
        ]
    )

    assert client.submit_share("job-1", "00000000", "65f04abc", 1) is True


def test_receive_notification_skips_informational_messages() -> None:
    transport = FakeTransport(
        [
            subscribe_response(),
            authorize_response(),
            ping_message(),
            show_message(),
            difficulty_notification(4096),
        ]
    )
    client = make_client(transport)
    client.handshake()

    assert client.receive_notification() == SetDifficultyNotification(difficulty=4096)
