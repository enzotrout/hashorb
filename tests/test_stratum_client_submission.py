"""Tests for authenticated share transmission through the Stratum client."""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping

import pytest

import hashorb.network.stratum.client as client_module
from hashorb.config import Settings
from hashorb.mining import NonceSearchMatch, PreparedMiningWork
from hashorb.network.stratum import (
    MiningNotifyNotification,
    SetDifficultyNotification,
    StratumClient,
    StratumClientState,
    StratumClientStateError,
    StratumMessageError,
    StratumRequestError,
)
from hashorb.network.stratum.messages import build_submit_request


class FakeTransport:
    """Deterministic in-memory transport for submission tests."""

    def __init__(
        self,
        incoming: list[dict[str, object] | BaseException] | None = None,
    ) -> None:
        self.incoming = deque(incoming or [])
        self.sent: list[dict[str, object]] = []
        self.send_error: BaseException | None = None
        self.send_calls = 0
        self.close_calls = 0
        self.connected = False

    def connect(self) -> None:
        self.connected = True

    def send_message(self, message: Mapping[str, object]) -> None:
        self.send_calls += 1
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
    """Return deterministic settings with a distinguishable username."""

    return Settings(
        stratum_host="pool.example.com",
        stratum_port=3333,
        bitcoin_address="bc1qsubmissiontest",
        worker_name="submit-rig",
        stratum_password="test-password",
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


def authorize_response(request_id: object = 2) -> dict[str, object]:
    """Build a successful authorization response."""

    return {"id": request_id, "result": True, "error": None}


def submit_response(
    result: object = True,
    request_id: object = 3,
) -> dict[str, object]:
    """Build a submission response."""

    return {"id": request_id, "result": result, "error": None}


def difficulty_notification(difficulty: int | float = 2048) -> dict[str, object]:
    """Build a valid difficulty notification."""

    return {
        "id": None,
        "method": "mining.set_difficulty",
        "params": [difficulty],
    }


def mining_notification(job_id: str = "queued-job") -> dict[str, object]:
    """Build a valid mining notification."""

    return {
        "id": None,
        "method": "mining.notify",
        "params": [
            job_id,
            "00" * 32,
            "01000000",
            "ffffffff",
            ["11" * 32],
            "20000000",
            "170fffff",
            "65f04abc",
            True,
        ],
    }


def make_client(transport: FakeTransport) -> StratumClient:
    """Create a client backed by the deterministic transport."""

    return StratumClient(make_settings(), "HashOrb/0.1", transport=transport)


def make_authorized_client(
    submission_messages: list[dict[str, object] | BaseException],
) -> tuple[StratumClient, FakeTransport]:
    """Complete a handshake before exposing submission messages."""

    transport = FakeTransport([subscribe_response(), authorize_response(), *submission_messages])
    client = make_client(transport)
    client.handshake()
    return client, transport


@pytest.mark.parametrize(
    "target_state",
    [
        StratumClientState.DISCONNECTED,
        StratumClientState.CONNECTED,
        StratumClientState.SUBSCRIBED,
    ],
)
def test_submit_share_requires_authorized_state(
    target_state: StratumClientState,
) -> None:
    incoming = [subscribe_response()] if target_state is StratumClientState.SUBSCRIBED else []
    transport = FakeTransport(incoming)
    client = make_client(transport)
    if target_state is not StratumClientState.DISCONNECTED:
        client.connect()
    if target_state is StratumClientState.SUBSCRIBED:
        client.subscribe()

    with pytest.raises(StratumClientStateError, match="submit_share requires state AUTHORIZED"):
        client.submit_share("job-1", "00000000", "65f04abc", 1)


@pytest.mark.parametrize("accepted", [True, False])
def test_submit_share_returns_pool_boolean_and_stays_authorized(accepted: bool) -> None:
    client, _transport = make_authorized_client([submit_response(accepted)])

    assert client.submit_share("job-1", "A1b2C3d4", "65F04aBc", 0x12345678) is accepted
    assert client.state is StratumClientState.AUTHORIZED


def test_submit_share_sends_exact_request_with_authenticated_username() -> None:
    client, transport = make_authorized_client([submit_response()])

    client.submit_share("Job-AbC", "A1b2C3d4", "65F04aBc", 0x12345678)

    assert transport.sent[2] == {
        "id": 3,
        "method": "mining.submit",
        "params": [
            make_settings().stratum_username,
            "Job-AbC",
            "A1b2C3d4",
            "65F04aBc",
            "12345678",
        ],
    }


def test_submit_nonce_uses_stratum_uint32_hex_not_header_byte_order() -> None:
    request = build_submit_request(
        7,
        make_settings().stratum_username,
        "job-live-regression",
        "00000000",
        "65f04abc",
        0xF2A916E1,
    )

    params = request["params"]
    assert isinstance(params, list)
    assert params[4] == "f2a916e1"
    assert params[4] != (0xF2A916E1).to_bytes(4, "little").hex()


def test_submit_share_delegates_request_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, transport = make_authorized_client([submit_response()])
    calls: list[tuple[int, str, str, str, str, int]] = []

    def recording_builder(
        request_id: int,
        username: str,
        job_id: str,
        extra_nonce_2: str,
        network_time: str,
        nonce: int,
    ) -> dict[str, object]:
        calls.append((request_id, username, job_id, extra_nonce_2, network_time, nonce))
        return build_submit_request(
            request_id,
            username,
            job_id,
            extra_nonce_2,
            network_time,
            nonce,
        )

    monkeypatch.setattr(client_module, "build_submit_request", recording_builder)

    client.submit_share("job-2", "AaBbCcDd", "65AbCdEf", 0x12345678)

    assert calls == [
        (
            3,
            make_settings().stratum_username,
            "job-2",
            "AaBbCcDd",
            "65AbCdEf",
            0x12345678,
        )
    ]
    assert transport.sent[2]["params"][-1] == "12345678"


def test_submit_share_allocates_consecutive_request_ids() -> None:
    client, transport = make_authorized_client(
        [submit_response(True, 3), submit_response(False, 4)]
    )

    client.submit_share("job-1", "00000000", "65f04abc", 1)
    client.submit_share("job-1", "00000000", "65f04abc", 2)

    assert [message["id"] for message in transport.sent] == [1, 2, 3, 4]
    assert all(type(message["id"]) is int for message in transport.sent)


def test_request_ids_remain_monotonic_across_client_sessions() -> None:
    transport = FakeTransport(
        [
            subscribe_response(1),
            authorize_response(2),
            submit_response(True, 3),
            subscribe_response(4),
            authorize_response(5),
            submit_response(False, 6),
        ]
    )
    client = make_client(transport)

    client.handshake()
    client.submit_share("job-1", "00000000", "65f04abc", 1)
    client.close()
    client.handshake()
    client.submit_share("job-2", "00000001", "65f04abd", 2)

    assert [message["id"] for message in transport.sent] == [1, 2, 3, 4, 5, 6]


def test_submit_share_does_not_mutate_inputs() -> None:
    client, _transport = make_authorized_client([submit_response()])
    inputs = ("Job-AbC", "AaBbCcDd", "65AbCdEf", 0x10203040)

    client.submit_share(*inputs)

    assert inputs == ("Job-AbC", "AaBbCcDd", "65AbCdEf", 0x10203040)


@pytest.mark.parametrize("result", [1, 0, "true", "false", None])
def test_submit_share_rejects_non_boolean_results(result: object) -> None:
    client, _transport = make_authorized_client([submit_response(result)])

    with pytest.raises(StratumMessageError, match="result"):
        client.submit_share("job-1", "00000000", "65f04abc", 1)

    assert client.state is StratumClientState.AUTHORIZED


def test_submit_share_rejects_missing_result() -> None:
    client, _transport = make_authorized_client([{"id": 3, "error": None}])

    with pytest.raises(StratumMessageError, match="missing required field: result"):
        client.submit_share("job-1", "00000000", "65f04abc", 1)


def test_submit_share_converts_stratum_errors() -> None:
    response: dict[str, object] = {
        "id": 3,
        "result": None,
        "error": [23, "Low difficulty share", {"detail": "synthetic"}],
    }
    client, _transport = make_authorized_client([response])

    with pytest.raises(StratumRequestError, match="Low difficulty share") as captured:
        client.submit_share("job-1", "00000000", "65f04abc", 1)

    assert captured.value.request_id == 3
    assert captured.value.error is not None
    assert captured.value.error.code == 23
    assert client.state is StratumClientState.AUTHORIZED


@pytest.mark.parametrize("response_id", [None, True, -1, 3.0, "3"])
def test_submit_share_rejects_malformed_response_ids(response_id: object) -> None:
    client, _transport = make_authorized_client([submit_response(True, response_id)])

    with pytest.raises(StratumRequestError, match="nonnegative integer"):
        client.submit_share("job-1", "00000000", "65f04abc", 1)


def test_submit_share_rejects_missing_response_id() -> None:
    client, _transport = make_authorized_client([{"result": True, "error": None}])

    with pytest.raises(StratumRequestError, match="missing the id"):
        client.submit_share("job-1", "00000000", "65f04abc", 1)


def test_submit_share_rejects_mismatched_response_id() -> None:
    client, _transport = make_authorized_client([submit_response(True, 99)])

    with pytest.raises(StratumRequestError, match="expected 3"):
        client.submit_share("job-1", "00000000", "65f04abc", 1)


def test_submit_share_queues_notifications_in_arrival_order() -> None:
    client, _transport = make_authorized_client(
        [
            difficulty_notification(4096),
            mining_notification("job-during-submit"),
            difficulty_notification(8192.5),
            submit_response(),
        ]
    )

    assert client.submit_share("job-1", "00000000", "65f04abc", 1) is True
    assert client.receive_notification() == SetDifficultyNotification(difficulty=4096)
    assert client.receive_notification() == MiningNotifyNotification(
        job_id="job-during-submit",
        previous_block_hash="00" * 32,
        coinbase_part_1="01000000",
        coinbase_part_2="ffffffff",
        merkle_branches=("11" * 32,),
        version="20000000",
        network_bits="170fffff",
        network_time="65f04abc",
        clean_jobs=True,
    )
    assert client.receive_notification() == SetDifficultyNotification(difficulty=8192.5)


@pytest.mark.parametrize(
    ("invalid_arguments", "expected_error"),
    [
        (("", "00000000", "65f04abc", 1), ValueError),
        (("job-1", "bad", "65f04abc", 1), ValueError),
        (("job-1", "00000000", "short", 1), ValueError),
        (("job-1", "00000000", "65f04abc", True), TypeError),
    ],
)
def test_request_validation_failure_occurs_before_transmission(
    invalid_arguments: tuple[object, object, object, object],
    expected_error: type[Exception],
) -> None:
    client, transport = make_authorized_client([])
    sends_before = transport.send_calls

    with pytest.raises(expected_error):
        client.submit_share(  # type: ignore[arg-type]
            invalid_arguments[0],
            invalid_arguments[1],
            invalid_arguments[2],
            invalid_arguments[3],
        )

    assert transport.send_calls == sends_before
    assert client.state is StratumClientState.AUTHORIZED


def test_submit_send_failure_propagates_without_retry_or_close() -> None:
    client, transport = make_authorized_client([])
    failure = RuntimeError("synthetic send failure")
    transport.send_error = failure
    sends_before = transport.send_calls

    with pytest.raises(RuntimeError) as captured:
        client.submit_share("job-1", "00000000", "65f04abc", 1)

    assert captured.value is failure
    assert transport.send_calls == sends_before + 1
    assert transport.close_calls == 0
    assert client.state is StratumClientState.AUTHORIZED


def test_submit_receive_failure_propagates_without_close() -> None:
    failure = RuntimeError("synthetic receive failure")
    client, transport = make_authorized_client([failure])

    with pytest.raises(RuntimeError) as captured:
        client.submit_share("job-1", "00000000", "65f04abc", 1)

    assert captured.value is failure
    assert transport.close_calls == 0
    assert client.state is StratumClientState.AUTHORIZED

    client.close()
    assert client.state is StratumClientState.DISCONNECTED
    assert transport.close_calls == 1


@pytest.mark.parametrize("accepted", [True, False])
def test_public_mining_result_boundary_reaches_fake_pool(accepted: bool) -> None:
    work = PreparedMiningWork(
        job_id="synthetic-job",
        extra_nonce_2="A1b2C3d4",
        network_time="65F04aBc",
        header_prefix=bytes(range(76)),
        network_target=1,
        share_target=2,
    )
    match = NonceSearchMatch(
        nonce=0x12345678,
        block_hash=bytes(32),
        meets_share_target=True,
        meets_network_target=False,
    )
    client, transport = make_authorized_client([submit_response(accepted)])

    result = client.submit_share(
        work.job_id,
        work.extra_nonce_2,
        work.network_time,
        match.nonce,
    )
    params = transport.sent[2]["params"]
    candidate_header = work.header_prefix + match.nonce.to_bytes(4, "little")

    assert result is accepted
    assert isinstance(params, list)
    assert params[1:4] == [work.job_id, work.extra_nonce_2, work.network_time]
    assert params[4] == f"{match.nonce:08x}"
    assert bytes.fromhex(params[4]) == match.nonce.to_bytes(4, "big")
    assert bytes.fromhex(params[4]) != candidate_header[76:80]
