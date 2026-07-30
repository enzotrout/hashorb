"""Narrow, sanitized Bitcoin Core JSON-RPC client."""

from __future__ import annotations

import base64
import binascii
import http.client
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Protocol, Self, runtime_checkable

from hashphere.config.bitcoin_rpc import BitcoinRpcSettings

DEFAULT_MAX_RPC_RESPONSE_BYTES = 16 * 1024 * 1024
MAX_COOKIE_BYTES = 4096
_SUPPORTED_CHAINS = frozenset({"main", "test", "testnet4", "signet", "regtest"})
_HEX = re.compile(r"^[0-9a-fA-F]+$")
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")
_REJECTION_CATEGORIES = {
    "duplicate": "duplicate",
    "duplicate-invalid": "duplicate_invalid",
    "duplicate-inconclusive": "duplicate_inconclusive",
    "inconclusive": "inconclusive",
}


class BitcoinRpcError(RuntimeError):
    """Base class for errors that never include raw RPC material."""

    category = "rpc_failure"


class BitcoinRpcAuthenticationError(BitcoinRpcError):
    """Authentication configuration or rejection."""

    category = "authentication_failure"


class BitcoinRpcTransportError(BitcoinRpcError):
    """Bounded HTTP transport failure."""

    category = "transport_failure"


class BitcoinRpcProtocolError(BitcoinRpcError):
    """Malformed or contradictory HTTP/JSON-RPC response."""

    category = "protocol_failure"


class BitcoinRpcRemoteError(BitcoinRpcError):
    """A structured JSON-RPC error returned by Bitcoin Core."""

    category = "remote_failure"

    def __init__(self, code_category: str) -> None:
        super().__init__("Bitcoin Core rejected the RPC request")
        self.code_category = code_category


class _DuplicateJsonKeyError(ValueError):
    """Internal marker for a non-strict JSON object."""


class _NonFiniteJsonNumberError(ValueError):
    """Internal marker for a non-standard JSON number."""


@dataclass(frozen=True, slots=True)
class HttpResponse:
    """Bounded HTTP response returned by an injected transport."""

    status: int
    body: bytes = field(repr=False)


@runtime_checkable
class BitcoinRpcTransport(Protocol):
    """Single-request transport boundary used by deterministic tests."""

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
        """POST one request and return a bounded response."""

    def close(self) -> None:
        """Release transport state."""


class UrllibBitcoinRpcTransport:
    """One-shot standard-library HTTP transport with no connection pool."""

    def __init__(self) -> None:
        self._closed = False

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
        """POST JSON-RPC over ordinary HTTP to the configured Core endpoint."""

        if self._closed:
            raise BitcoinRpcTransportError("Bitcoin RPC transport is closed")
        connection = http.client.HTTPConnection(host, port, timeout=timeout_seconds)
        try:
            connection.request(
                "POST",
                "/",
                body=body,
                headers={
                    "Authorization": authorization,
                    "Content-Type": "application/json",
                    "Connection": "close",
                },
            )
            response = connection.getresponse()
            response_body = response.read(maximum_response_bytes + 1)
        except (OSError, TimeoutError, http.client.HTTPException) as exc:
            raise BitcoinRpcTransportError("Bitcoin RPC HTTP request failed") from exc
        finally:
            connection.close()
        if len(response_body) > maximum_response_bytes:
            raise BitcoinRpcTransportError("Bitcoin RPC response exceeded the size limit")
        return HttpResponse(status=response.status, body=response_body)

    def close(self) -> None:
        """Make future requests fail; active requests are always one-shot."""

        self._closed = True


@dataclass(frozen=True, slots=True)
class BlockchainInfo:
    """Sanitized chain identity and synchronization state."""

    chain: str
    blocks: int
    headers: int
    initial_block_download: bool


@dataclass(frozen=True, slots=True)
class PayoutDestination:
    """Validated destination script, hidden from representations."""

    script_pub_key: bytes = field(repr=False)


@dataclass(frozen=True, slots=True)
class ProposalOutcome:
    """Strict proposal result without Core's raw rejection text."""

    accepted: bool
    category: str


@dataclass(frozen=True, slots=True)
class SubmissionOutcome:
    """Strict block-submission result without raw candidate material."""

    accepted: bool
    category: str


class BitcoinCoreRpcClient:
    """Allowlisted Bitcoin Core RPC operations with deterministic request IDs."""

    def __init__(
        self,
        settings: BitcoinRpcSettings,
        transport: BitcoinRpcTransport | None = None,
        *,
        maximum_response_bytes: int = DEFAULT_MAX_RPC_RESPONSE_BYTES,
    ) -> None:
        if not isinstance(settings, BitcoinRpcSettings):
            raise TypeError("settings must be BitcoinRpcSettings")
        if not isinstance(maximum_response_bytes, int) or not 1 <= maximum_response_bytes:
            raise ValueError("maximum_response_bytes must be a positive integer")
        selected_transport = UrllibBitcoinRpcTransport() if transport is None else transport
        if not isinstance(selected_transport, BitcoinRpcTransport):
            raise TypeError("transport must implement BitcoinRpcTransport")
        self._settings = settings
        self._transport = selected_transport
        self._maximum_response_bytes = maximum_response_bytes
        self._authorization = _authorization_header(settings)
        self._next_request_id = 1
        self._closed = False

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exception_type, exception, traceback
        self.close()

    def close(self) -> None:
        """Close once without exposing transport implementation details."""

        if self._closed:
            return
        self._closed = True
        try:
            self._transport.close()
        except Exception as exc:
            raise BitcoinRpcTransportError("Bitcoin RPC transport cleanup failed") from exc

    def get_blockchain_info(self) -> BlockchainInfo:
        """Return strict network identity and synchronization state."""

        result = self._call("getblockchaininfo", [])
        value = _require_object(result, "getblockchaininfo result")
        chain = _required_string(value, "chain")
        if chain not in _SUPPORTED_CHAINS:
            raise BitcoinRpcProtocolError("Bitcoin Core reported an unsupported chain")
        return BlockchainInfo(
            chain=chain,
            blocks=_required_nonnegative_integer(value, "blocks"),
            headers=_required_nonnegative_integer(value, "headers"),
            initial_block_download=_required_boolean(value, "initialblockdownload"),
        )

    def validate_address(self, address: str) -> PayoutDestination:
        """Ask Core's non-wallet utility RPC for the exact destination script."""

        _validate_secret_text(address, "payout address", maximum_length=128)
        result = self._call("validateaddress", [address])
        value = _require_object(result, "validateaddress result")
        if not _required_boolean(value, "isvalid"):
            raise BitcoinRpcProtocolError("payout destination is invalid for the connected chain")
        script_text = _required_string(value, "scriptPubKey")
        script = _strict_hex_bytes(script_text, "payout script", minimum=2, maximum=100)
        return PayoutDestination(script_pub_key=script)

    def get_block_template(self) -> dict[str, object]:
        """Request a SegWit-aware template for strict model parsing by the caller."""

        result = self._call("getblocktemplate", [{"rules": ["segwit"]}])
        return dict(_require_object(result, "getblocktemplate result"))

    def propose_block(self, serialized_block: bytes) -> ProposalOutcome:
        """Locally validate one complete block through GBT proposal mode."""

        block_hex = _serialized_block_hex(serialized_block)
        result = self._call(
            "getblocktemplate",
            [{"mode": "proposal", "data": block_hex, "rules": ["segwit"]}],
        )
        if result is None:
            return ProposalOutcome(accepted=True, category="accepted")
        if not isinstance(result, str) or not result:
            raise BitcoinRpcProtocolError("Bitcoin Core returned an invalid proposal result")
        return ProposalOutcome(accepted=False, category=_rejection_category(result))

    def submit_block(self, serialized_block: bytes) -> SubmissionOutcome:
        """Submit one locally and proposal-validated complete block exactly once."""

        result = self._call("submitblock", [_serialized_block_hex(serialized_block)])
        if result is None:
            return SubmissionOutcome(accepted=True, category="accepted")
        if not isinstance(result, str) or not result:
            raise BitcoinRpcProtocolError("Bitcoin Core returned an invalid submission result")
        return SubmissionOutcome(accepted=False, category=_rejection_category(result))

    def _call(self, method: str, params: list[object]) -> object:
        if self._closed:
            raise BitcoinRpcTransportError("Bitcoin RPC client is closed")
        request_id = self._next_request_id
        self._next_request_id += 1
        request = {"jsonrpc": "1.0", "id": request_id, "method": method, "params": params}
        try:
            body = json.dumps(request, separators=(",", ":"), allow_nan=False).encode("utf-8")
            response = self._transport.request(
                host=self._settings.host,
                port=self._settings.port,
                timeout_seconds=self._settings.timeout_seconds,
                authorization=self._authorization,
                body=body,
                maximum_response_bytes=self._maximum_response_bytes,
            )
        except BitcoinRpcError:
            raise
        except Exception as exc:
            raise BitcoinRpcTransportError("Bitcoin RPC transport failed") from exc
        if not isinstance(response, HttpResponse):
            raise BitcoinRpcTransportError("Bitcoin RPC transport returned an invalid response")
        if response.status == 401:
            raise BitcoinRpcAuthenticationError("Bitcoin Core rejected RPC authentication")
        if response.status != 200:
            raise BitcoinRpcTransportError("Bitcoin Core returned an unexpected HTTP status")
        if len(response.body) > self._maximum_response_bytes:
            raise BitcoinRpcTransportError("Bitcoin RPC response exceeded the size limit")
        try:
            decoded = json.loads(
                response.body,
                object_pairs_hook=_object_without_duplicate_keys,
                parse_constant=_reject_nonfinite_json_number,
            )
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
            _DuplicateJsonKeyError,
            _NonFiniteJsonNumberError,
        ) as exc:
            raise BitcoinRpcProtocolError("Bitcoin Core returned malformed JSON") from exc
        envelope = _require_object(decoded, "JSON-RPC response")
        response_id = envelope.get("id")
        if type(response_id) is not int or response_id != request_id:
            raise BitcoinRpcProtocolError("Bitcoin Core returned a mismatched request ID")
        if "result" not in envelope or "error" not in envelope:
            raise BitcoinRpcProtocolError("Bitcoin Core returned an incomplete JSON-RPC response")
        error = envelope["error"]
        if error is not None:
            if envelope["result"] is not None:
                raise BitcoinRpcProtocolError(
                    "Bitcoin Core returned contradictory result and error fields"
                )
            error_object = _require_object(error, "JSON-RPC error")
            code = error_object.get("code")
            if not isinstance(code, int) or isinstance(code, bool):
                raise BitcoinRpcProtocolError("Bitcoin Core returned a malformed JSON-RPC error")
            raise BitcoinRpcRemoteError(_rpc_code_category(code))
        return envelope["result"]


def _authorization_header(settings: BitcoinRpcSettings) -> str:
    if settings.cookie_file is not None:
        username, password = _read_cookie(settings.cookie_file)
    else:
        if settings.username is None or settings.password is None:
            raise BitcoinRpcAuthenticationError("Bitcoin RPC authentication is incomplete")
        username, password = settings.username, settings.password
    try:
        token = base64.b64encode(f"{username}:{password}".encode(), altchars=None)
    except (UnicodeError, binascii.Error) as exc:
        raise BitcoinRpcAuthenticationError("Bitcoin RPC authentication is invalid") from exc
    return f"Basic {token.decode('ascii')}"


def _read_cookie(path: Path) -> tuple[str, str]:
    try:
        cookie = path.read_bytes()
    except OSError as exc:
        raise BitcoinRpcAuthenticationError("Bitcoin RPC cookie could not be read") from exc
    if not cookie or len(cookie) > MAX_COOKIE_BYTES:
        raise BitcoinRpcAuthenticationError("Bitcoin RPC cookie is malformed")
    try:
        text = cookie.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BitcoinRpcAuthenticationError("Bitcoin RPC cookie is malformed") from exc
    if text.endswith("\n"):
        text = text[:-1]
        if text.endswith("\r"):
            text = text[:-1]
    if text.count(":") != 1:
        raise BitcoinRpcAuthenticationError("Bitcoin RPC cookie is malformed")
    username, password = text.split(":", 1)
    try:
        _validate_secret_text(username, "cookie username", maximum_length=256)
        _validate_secret_text(password, "cookie password", maximum_length=1024)
        if ":" in username:
            raise ValueError("cookie username is invalid")
    except ValueError as exc:
        raise BitcoinRpcAuthenticationError("Bitcoin RPC cookie is malformed") from exc
    return username, password


def _require_object(value: object, name: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise BitcoinRpcProtocolError(f"{name} must be an object")
    return value


def _object_without_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKeyError
        result[key] = value
    return result


def _reject_nonfinite_json_number(value: str) -> None:
    raise _NonFiniteJsonNumberError(value)


def _required_string(value: dict[str, object], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result:
        raise BitcoinRpcProtocolError(f"Bitcoin Core response field {key} is invalid")
    return result


def _required_boolean(value: dict[str, object], key: str) -> bool:
    result = value.get(key)
    if not isinstance(result, bool):
        raise BitcoinRpcProtocolError(f"Bitcoin Core response field {key} is invalid")
    return result


def _required_nonnegative_integer(value: dict[str, object], key: str) -> int:
    result = value.get(key)
    if not isinstance(result, int) or isinstance(result, bool) or result < 0:
        raise BitcoinRpcProtocolError(f"Bitcoin Core response field {key} is invalid")
    return result


def _strict_hex_bytes(value: str, name: str, *, minimum: int, maximum: int) -> bytes:
    if len(value) % 2 != 0 or _HEX.fullmatch(value) is None:
        raise BitcoinRpcProtocolError(f"Bitcoin Core returned an invalid {name}")
    result = bytes.fromhex(value)
    if not minimum <= len(result) <= maximum:
        raise BitcoinRpcProtocolError(f"Bitcoin Core returned an invalid {name}")
    return result


def _serialized_block_hex(value: bytes) -> str:
    if not isinstance(value, bytes) or len(value) < 81 or len(value) > 4_000_000:
        raise ValueError("serialized block must be bounded nonempty bytes")
    return value.hex()


def _validate_secret_text(value: object, name: str, *, maximum_length: int) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum_length:
        raise ValueError(f"{name} is invalid")
    if value != value.strip() or _CONTROL_CHARACTERS.search(value) is not None:
        raise ValueError(f"{name} is invalid")
    return value


def _rpc_code_category(code: int) -> str:
    if code == -32601:
        return "method_unavailable"
    if code in {-32600, -32602}:
        return "invalid_request"
    if code in {-32700, -1, -3, -8}:
        return "invalid_response_or_parameter"
    if code in {-28, -342}:
        return "node_not_ready"
    return "rejected"


def _rejection_category(reason: str) -> str:
    normalized = reason.strip().lower()
    if normalized in _REJECTION_CATEGORIES:
        return _REJECTION_CATEGORIES[normalized]
    if normalized.startswith("bad-"):
        return "consensus_rejected"
    return "rejected"
