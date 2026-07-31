"""Deterministic tests for the narrow Bitcoin Core RPC boundary."""

from __future__ import annotations

import base64
import json
import os
import socket
from collections.abc import Callable
from pathlib import Path

import pytest

import hashorb.config.bitcoin_rpc as rpc_settings_module
from hashorb.bitcoin.rpc import (
    BitcoinCoreRpcClient,
    BitcoinCoreTemplateClient,
    BitcoinRpcAuthenticationError,
    BitcoinRpcProtocolError,
    BitcoinRpcRemoteError,
    BitcoinRpcTransportError,
    HttpResponse,
)
from hashorb.config.bitcoin_rpc import BitcoinRpcSettings
from hashorb.config.solo import SoloCommandSettings


class FakeTransport:
    """Capture requests and synthesize responses without network access."""

    def __init__(self, responder: Callable[[dict[str, object]], HttpResponse]) -> None:
        self.responder = responder
        self.requests: list[dict[str, object]] = []
        self.authorizations: list[str] = []
        self.hosts: list[str] = []
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
        del port, timeout_seconds, maximum_response_bytes
        request = json.loads(body)
        assert isinstance(request, dict)
        self.requests.append(request)
        self.authorizations.append(authorization)
        self.hosts.append(host)
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


def test_read_only_client_has_no_proposal_submission_or_generic_rpc_capability() -> None:
    transport = FakeTransport(lambda request: _response(request, {}))
    client = BitcoinCoreTemplateClient(_settings(), transport)

    assert not hasattr(client, "propose_block")
    assert not hasattr(client, "submit_block")
    assert not hasattr(client, "call")
    with pytest.raises(BitcoinRpcProtocolError, match="not available"):
        client._exchange("submitblock", ["synthetic-block"])
    assert transport.requests == []


def test_rpc_resolves_once_and_accepts_only_loopback_addresses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = FakeTransport(
        lambda request: _response(
            request,
            {"chain": "regtest", "blocks": 1, "headers": 1, "initialblockdownload": False},
        )
    )
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", 18443, 0, 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 18443)),
        ],
    )
    client = BitcoinCoreTemplateClient(_settings(host="localhost"), transport)
    client.get_blockchain_info()
    assert transport.hosts == ["127.0.0.1"]

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 18443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.10", 18443)),
        ],
    )
    with pytest.raises(BitcoinRpcTransportError, match="only to loopback"):
        BitcoinCoreTemplateClient(_settings(host="mixed.invalid"), transport)


@pytest.mark.parametrize("host", ["192.0.2.10", "2001:db8::10"])
def test_rpc_rejects_accidental_remote_literal_hosts(host: str) -> None:
    with pytest.raises(BitcoinRpcTransportError, match="loopback"):
        BitcoinCoreTemplateClient(_settings(host=host))


def test_rpc_accepts_ipv6_loopback_without_second_resolution() -> None:
    transport = FakeTransport(lambda request: _response(request, {}))
    client = BitcoinCoreTemplateClient(_settings(host="::1"), transport)
    client.get_block_template()
    assert transport.hosts == ["::1"]


def test_cookie_authentication_accepts_one_strict_trailing_newline(tmp_path: Path) -> None:
    cookie = tmp_path / ".cookie"
    cookie.write_text("__cookie__:synthetic-secret\n", encoding="utf-8")
    cookie.chmod(0o600)
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
    cookie.chmod(0o600)

    with pytest.raises(BitcoinRpcAuthenticationError, match="cookie"):
        BitcoinCoreRpcClient(_settings(username=None, password=None, cookie_file=cookie))


def test_cookie_authentication_accepts_crlf_line_endings(tmp_path: Path) -> None:
    cookie = tmp_path / ".cookie"
    cookie.write_bytes(b"__cookie__:synthetic-secret\r\n")
    cookie.chmod(0o600)
    transport = FakeTransport(
        lambda request: _response(
            request,
            {"chain": "regtest", "blocks": 1, "headers": 1, "initialblockdownload": False},
        )
    )
    client = BitcoinCoreRpcClient(
        _settings(username=None, password=None, cookie_file=cookie),
        transport,
    )

    assert client.get_blockchain_info().chain == "regtest"
    expected = base64.b64encode(b"__cookie__:synthetic-secret").decode()
    assert transport.authorizations == [f"Basic {expected}"]


@pytest.mark.skipif(
    os.name == "nt",
    reason="requires POSIX file mode semantics",
)
def test_cookie_authentication_rejects_unsafe_permissions_on_posix(tmp_path: Path) -> None:
    cookie = tmp_path / ".cookie"
    cookie.write_bytes(b"__cookie__:synthetic-secret\r\n")
    cookie.chmod(0o640)

    with pytest.raises(BitcoinRpcAuthenticationError, match="permissions"):
        BitcoinCoreTemplateClient(_settings(username=None, password=None, cookie_file=cookie))


@pytest.mark.skipif(
    os.name == "nt",
    reason="requires POSIX symlink semantics",
)
def test_cookie_authentication_rejects_symlinks(tmp_path: Path) -> None:
    target = tmp_path / "target.cookie"
    target.write_text("__cookie__:synthetic-secret", encoding="utf-8")
    target.chmod(0o600)
    linked = tmp_path / "linked.cookie"
    linked.symlink_to(target)
    with pytest.raises(BitcoinRpcAuthenticationError, match="could not be read"):
        BitcoinCoreTemplateClient(_settings(username=None, password=None, cookie_file=linked))


def test_cookie_authentication_rejects_bounded_reads_and_malformed_contents(tmp_path: Path) -> None:
    target = tmp_path / "target.cookie"
    target.write_bytes(b"a:" + b"x" * 4096)
    target.chmod(0o600)
    with pytest.raises(BitcoinRpcAuthenticationError, match="malformed"):
        BitcoinCoreTemplateClient(_settings(username=None, password=None, cookie_file=target))


def test_cookie_authentication_rejects_unreadable_and_non_utf8_data(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cookie = tmp_path / ".cookie"
    cookie.write_bytes(b"user:\xff")
    cookie.chmod(0o600)
    with pytest.raises(BitcoinRpcAuthenticationError, match="malformed"):
        BitcoinCoreTemplateClient(_settings(username=None, password=None, cookie_file=cookie))

    monkeypatch.setattr(
        os, "open", lambda *args, **kwargs: (_ for _ in ()).throw(PermissionError())
    )
    with pytest.raises(BitcoinRpcAuthenticationError, match="could not be read"):
        BitcoinCoreTemplateClient(_settings(username=None, password=None, cookie_file=cookie))


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
    proposal_parameters = transport.requests[1]["params"]
    assert isinstance(proposal_parameters, list)
    assert len(proposal_parameters) == 1
    assert isinstance(proposal_parameters[0], dict)
    assert set(proposal_parameters[0]) == {"mode", "data", "rules"}
    assert proposal_parameters[0]["mode"] == "proposal"
    assert proposal_parameters[0]["rules"] == ["segwit"]
    assert proposal_parameters[0]["data"] == block.hex()


@pytest.mark.parametrize(
    ("reason", "category"),
    [
        ("bad-cb-missing", "bad_coinbase"),
        ("bad-cb-height", "bad_coinbase_height"),
        ("bad-cb-amount", "bad_coinbase_amount"),
        ("bad-witness-merkle-match", "bad_witness_commitment"),
        ("bad-txnmrklroot", "bad_transaction_merkle_root"),
        ("bad-txns-nonfinal", "bad_transactions"),
        ("high-hash", "high_hash"),
        ("time-too-old", "invalid_time"),
        ("bad-version", "invalid_version"),
        ("bad-diffbits", "invalid_bits"),
        ("bad-prevblk", "stale_previous_block"),
        ("duplicate", "duplicate"),
        ("private-secret-token", "other_proposal_rejection"),
    ],
)
def test_proposal_rejection_reasons_map_to_strict_categories(reason: str, category: str) -> None:
    transport = FakeTransport(lambda request: _response(request, reason))

    outcome = BitcoinCoreRpcClient(_settings(), transport).propose_block(bytes(81))

    assert not outcome.accepted
    assert outcome.category == category
    if reason == "private-secret-token":
        assert reason not in repr(outcome)


@pytest.mark.parametrize(
    "reason",
    [
        " bad-cb-height",
        "bad-cb-height ",
        "bad-cb-height\nprivate-detail",
        "x" * 65,
        '{"reason":"private-detail"}',
        "private arbitrary RPC text",
    ],
)
def test_proposal_rejection_rejects_unsafe_strings_without_exposing_them(reason: str) -> None:
    transport = FakeTransport(lambda request: _response(request, reason))

    with pytest.raises(BitcoinRpcProtocolError) as caught:
        BitcoinCoreRpcClient(_settings(), transport).propose_block(bytes(81))

    assert reason not in str(caught.value)


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


def test_protocol_rejects_excessive_json_nesting_before_decoding() -> None:
    private_value = "private-nested-value"
    payload = (
        b'{"id":1,"result":'
        + b"[" * 65
        + json.dumps(private_value).encode()
        + b"]" * 65
        + b',"error":null}'
    )
    transport = FakeTransport(lambda request: HttpResponse(status=200, body=payload))

    with pytest.raises(BitcoinRpcProtocolError, match="nested") as caught:
        BitcoinCoreTemplateClient(_settings(), transport).get_block_template()

    assert private_value not in str(caught.value)


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
    monkeypatch.setattr(rpc_settings_module, "load_hashorb_environment", lambda: False)
    for name in (
        "HASHORB_BITCOIN_RPC_HOST",
        "HASHORB_BITCOIN_RPC_PORT",
        "HASHORB_BITCOIN_RPC_USER",
        "HASHORB_BITCOIN_RPC_PASSWORD",
        "HASHORB_BITCOIN_RPC_COOKIE_FILE",
        "HASHORB_BITCOIN_RPC_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ValueError, match="authentication"):
        BitcoinRpcSettings.from_env()

    monkeypatch.setenv("HASHORB_BITCOIN_RPC_USER", "synthetic-user")
    monkeypatch.setenv("HASHORB_BITCOIN_RPC_PASSWORD", "synthetic-password")
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
