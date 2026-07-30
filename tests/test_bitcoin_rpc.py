"""Deterministic tests for the narrow Bitcoin Core RPC boundary."""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from pathlib import Path

import pytest

import hashphere.config.bitcoin_rpc as rpc_settings_module
from hashphere.bitcoin.rpc import (
    BitcoinCoreRpcClient,
    BitcoinRpcAuthenticationError,
    BitcoinRpcProtocolError,
    BitcoinRpcRemoteError,
    BitcoinRpcTransportError,
    HttpResponse,
)
from hashphere.config.bitcoin_rpc import BitcoinRpcSettings
from hashphere.config.solo import SoloCommandSettings


class FakeTransport:
    """Capture requests and synthesize responses without network access."""

    def __init__(self, responder: Callable[[dict[str, object]], HttpResponse]) -> None:
        self.responder = responder
        self.requests: list[dict[str, object]] = []
        self.authorizations: list[str] = []
        self.closed = False

    def request(
        self,
        *,
        host: str,
        port: int,
        timeout_seconds: float,
        authorization: str,
        body: bytes,
        maximum_response_bytes: int,
    ) -> HttpResponse:
        del host, port, timeout_seconds, maximum_response_bytes
        request = json.loads(body)
        assert isinstance(request, dict)
        self.requests.append(request)
        self.authorizations.append(authorization)
        return self.responder(request)

    def close(self) -> None:
        self.closed = True


def _settings(**overrides: object) -> BitcoinRpcSettings:
    values: dict[str, object] = {
        "host": "127.0.0.1",
        "port": 18443,
        "timeout_seconds": 3.0,
        "username": "rpc-user",
        "password": "rpc-password",
    }
    values.update(overrides)
    return BitcoinRpcSettings(**values)  # type: ignore[arg-type]


def _response(request: dict[str, object], result: object) -> HttpResponse:
    return HttpResponse(
        status=200,
        body=json.dumps({"result": result, "error": None, "id": request["id"]}).encode(),
    )


def test_rpc_uses_deterministic_ids_basic_auth_and_allowlisted_params() -> None:
    transport = FakeTransport(
        lambda request: _response(
            request,
            {
                "chain": "regtest",
                "blocks": 7,
                "headers": 7,
                "initialblockdownload": False,
            },
        )
    )
    client = BitcoinCoreRpcClient(_settings(), transport)

    first = client.get_blockchain_info()
    client.get_blockchain_info()

    assert first.chain == "regtest"
    assert first.blocks == first.headers == 7
    assert not first.initial_block_download
    assert [request["id"] for request in transport.requests] == [1, 2]
    assert {request["method"] for request in transport.requests} == {"getblockchaininfo"}
    expected = base64.b64encode(b"rpc-user:rpc-password").decode()
    assert transport.authorizations == [f"Basic {expected}", f"Basic {expected}"]


def test_cookie_authentication_accepts_one_strict_trailing_newline(tmp_path: Path) -> None:
    cookie = tmp_path / ".cookie"
    cookie.write_text("__cookie__:synthetic-secret\n", encoding="utf-8")
    transport = FakeTransport(
        lambda request: _response(
            request,
            {"chain": "signet", "blocks": 1, "headers": 2, "initialblockdownload": True},
        )
    )
    client = BitcoinCoreRpcClient(
        _settings(username=None, password=None, cookie_file=cookie), transport
    )

    assert client.get_blockchain_info().chain == "signet"
    expected = base64.b64encode(b"__cookie__:synthetic-secret").decode()
    assert transport.authorizations == [f"Basic {expected}"]


@pytest.mark.parametrize(
    "contents",
    [b"", b"missing-separator", b"a:b:c", b"user:\npassword", b"user:password\n\n"],
)
def test_cookie_authentication_rejects_malformed_contents(tmp_path: Path, contents: bytes) -> None:
    cookie = tmp_path / ".cookie"
    cookie.write_bytes(contents)

    with pytest.raises(BitcoinRpcAuthenticationError, match="cookie"):
        BitcoinCoreRpcClient(_settings(username=None, password=None, cookie_file=cookie))


@pytest.mark.parametrize(
    "overrides",
    [
        {"username": None, "password": None},
        {"username": "user", "password": None},
        {"username": None, "password": "password"},
        {"username": "user\n", "password": "password"},
        {"username": "user", "password": "pass\rword"},
        {"username": "user:name", "password": "password"},
    ],
)
def test_settings_reject_missing_partial_or_injectable_authentication(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="Bitcoin RPC"):
        _settings(**overrides)


def test_settings_reject_ambiguous_authentication(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="mutually exclusive"):
        _settings(cookie_file=tmp_path / ".cookie")


def test_validate_address_returns_hidden_script_and_never_requires_wallet_fields() -> None:
    transport = FakeTransport(
        lambda request: _response(request, {"isvalid": True, "scriptPubKey": "0014" + "11" * 20})
    )
    destination = BitcoinCoreRpcClient(_settings(), transport).validate_address(
        "bcrt1qsyntheticdestination"
    )

    assert destination.script_pub_key == bytes.fromhex("0014" + "11" * 20)
    assert "11" * 20 not in repr(destination)
    assert transport.requests[0]["method"] == "validateaddress"


def test_validate_address_rejects_wrong_network_or_malformed_script() -> None:
    invalid = FakeTransport(lambda request: _response(request, {"isvalid": False}))
    with pytest.raises(BitcoinRpcProtocolError, match="connected chain"):
        BitcoinCoreRpcClient(_settings(), invalid).validate_address("synthetic-address")

    malformed = FakeTransport(
        lambda request: _response(request, {"isvalid": True, "scriptPubKey": "not-hex"})
    )
    with pytest.raises(BitcoinRpcProtocolError, match="payout script"):
        BitcoinCoreRpcClient(_settings(), malformed).validate_address("synthetic-address")


def test_template_proposal_and_submission_have_strict_semantics() -> None:
    results: list[object] = [{"height": 1}, None, "duplicate-invalid"]

    def respond(request: dict[str, object]) -> HttpResponse:
        return _response(request, results.pop(0))

    transport = FakeTransport(respond)
    client = BitcoinCoreRpcClient(_settings(), transport)
    block = bytes(81)

    assert client.get_block_template() == {"height": 1}
    assert client.propose_block(block).accepted
    submission = client.submit_block(block)
    assert not submission.accepted
    assert submission.category == "duplicate_invalid"
    assert [request["method"] for request in transport.requests] == [
        "getblocktemplate",
        "getblocktemplate",
        "submitblock",
    ]
    assert transport.requests[0]["params"] == [{"rules": ["segwit"]}]


@pytest.mark.parametrize(
    ("payload", "error_type"),
    [
        (b"not-json", BitcoinRpcProtocolError),
        (json.dumps([]).encode(), BitcoinRpcProtocolError),
        (json.dumps({"id": 99, "result": None, "error": None}).encode(), BitcoinRpcProtocolError),
        (json.dumps({"id": True, "result": None, "error": None}).encode(), BitcoinRpcProtocolError),
        (json.dumps({"id": 1, "error": None}).encode(), BitcoinRpcProtocolError),
        (b'{"id":1,"id":1,"result":null,"error":null}', BitcoinRpcProtocolError),
        (b'{"id":1,"result":NaN,"error":null}', BitcoinRpcProtocolError),
        (
            json.dumps(
                {"id": 1, "result": {"unexpected": True}, "error": {"code": -32601}}
            ).encode(),
            BitcoinRpcProtocolError,
        ),
        (
            json.dumps({"id": 1, "result": None, "error": {"code": -32601}}).encode(),
            BitcoinRpcRemoteError,
        ),
    ],
)
def test_protocol_failures_are_strict_and_sanitized(
    payload: bytes, error_type: type[Exception]
) -> None:
    transport = FakeTransport(lambda request: HttpResponse(status=200, body=payload))

    with pytest.raises(error_type) as caught:
        BitcoinCoreRpcClient(_settings(), transport).get_blockchain_info()

    message = str(caught.value)
    assert "rpc-password" not in message
    assert "payload" not in message


def test_http_and_transport_failures_are_categorized_without_details() -> None:
    unauthorized = FakeTransport(lambda request: HttpResponse(status=401, body=b"secret"))
    with pytest.raises(BitcoinRpcAuthenticationError):
        BitcoinCoreRpcClient(_settings(), unauthorized).get_blockchain_info()

    oversized = FakeTransport(lambda request: HttpResponse(status=200, body=b"x" * 9))
    with pytest.raises(BitcoinRpcTransportError, match="size limit"):
        BitcoinCoreRpcClient(_settings(), oversized, maximum_response_bytes=8).get_blockchain_info()

    class FailedTransport(FakeTransport):
        def request(self, **kwargs: object) -> HttpResponse:
            del kwargs
            raise TimeoutError("sensitive endpoint detail")

    with pytest.raises(BitcoinRpcTransportError) as caught:
        failed = FailedTransport(lambda request: _response(request, None))
        BitcoinCoreRpcClient(_settings(), failed).get_blockchain_info()
    assert "sensitive endpoint detail" not in str(caught.value)


def test_close_is_idempotent_and_prevents_later_requests() -> None:
    transport = FakeTransport(lambda request: _response(request, None))
    client = BitcoinCoreRpcClient(_settings(), transport)

    client.close()
    client.close()

    assert transport.closed
    with pytest.raises(BitcoinRpcTransportError, match="closed"):
        client.get_block_template()


def test_rpc_objects_hide_credentials_paths_and_payloads(tmp_path: Path) -> None:
    settings = _settings(username=None, password=None, cookie_file=tmp_path / "private.cookie")
    client = BitcoinCoreRpcClient(
        _settings(), FakeTransport(lambda request: _response(request, None))
    )

    assert "private.cookie" not in repr(settings)
    assert "rpc-password" not in repr(client)


def test_rpc_environment_defaults_only_to_loopback_and_requires_authentication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(rpc_settings_module, "load_dotenv", lambda: False)
    for name in (
        "HASHPHERE_BITCOIN_RPC_HOST",
        "HASHPHERE_BITCOIN_RPC_PORT",
        "HASHPHERE_BITCOIN_RPC_USER",
        "HASHPHERE_BITCOIN_RPC_PASSWORD",
        "HASHPHERE_BITCOIN_RPC_COOKIE_FILE",
        "HASHPHERE_BITCOIN_RPC_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ValueError, match="authentication"):
        BitcoinRpcSettings.from_env()

    monkeypatch.setenv("HASHPHERE_BITCOIN_RPC_USER", "synthetic-user")
    monkeypatch.setenv("HASHPHERE_BITCOIN_RPC_PASSWORD", "synthetic-password")
    settings = BitcoinRpcSettings.from_env()
    assert settings.host == "127.0.0.1"
    assert settings.port == 8332


def test_solo_destination_is_required_strict_and_hidden_from_repr() -> None:
    destination = "bcrt1qsyntheticdestination"
    settings = SoloCommandSettings(destination)
    assert settings.payout_address == destination
    assert destination not in repr(settings)
    with pytest.raises(ValueError, match="invalid"):
        SoloCommandSettings(f"{destination}\n")
