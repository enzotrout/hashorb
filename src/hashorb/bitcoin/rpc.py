"""Narrow, sanitized Bitcoin Core JSON-RPC client."""

from __future__ import annotations

import base64
import binascii
import http.client
import ipaddress
import json
import os
import re
import socket
import stat
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Protocol, Self, runtime_checkable

from hashorb.config.bitcoin_rpc import BitcoinRpcSettings

DEFAULT_MAX_RPC_RESPONSE_BYTES = 16 * 1024 * 1024
MAX_COOKIE_BYTES = 4096
MAX_JSON_NESTING_DEPTH = 64
_SUPPORTED_CHAINS = frozenset({"main", "test", "testnet4", "signet", "regtest"})
_HEX = re.compile(r"^[0-9a-fA-F]+$")
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")
_SAFE_REJECTION_TOKEN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_VERSION_REJECTION_TOKEN = re.compile(r"^bad-version\(0x[0-9a-f]{8}\)$")
_MAX_REJECTION_TOKEN_CHARACTERS = 64
_DUPLICATE_REJECTION_CATEGORIES = {
    "duplicate": "duplicate",
    "duplicate-invalid": "duplicate_invalid",
    "duplicate-inconclusive": "duplicate_inconclusive",
    "inconclusive": "inconclusive",
}
_PROPOSAL_REJECTION_CATEGORIES = {
    "bad-cb-length": "bad_coinbase",
    "bad-cb-missing": "bad_coinbase",
    "bad-cb-multiple": "bad_coinbase",
    "bad-cb-height": "bad_coinbase_height",
    "bad-cb-amount": "bad_coinbase_amount",
    "bad-witness-merkle-match": "bad_witness_commitment",
    "bad-witness-nonce-size": "bad_witness_commitment",
    "unexpected-witness": "bad_witness_commitment",
    "bad-txnmrklroot": "bad_transaction_merkle_root",
    "high-hash": "high_hash",
    "time-invalid": "invalid_time",
    "time-too-new": "invalid_time",
    "time-too-old": "invalid_time",
    "bad-version": "invalid_version",
    "bad-diffbits": "invalid_bits",
    "bad-prevblk": "stale_previous_block",
    "stale-prevblk": "stale_previous_block",
    "stale-work": "stale_previous_block",
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


class BitcoinCoreTemplateClient:
    """Read-only Bitcoin Core template operations with no submission methods."""

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
        self._host = _resolve_loopback_host(settings.host, settings.port)
        self._port = settings.port
        self._timeout_seconds = settings.timeout_seconds
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

        result = self._call_read_only("getblockchaininfo")
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
        result = self._call_validate_address(address)
        value = _require_object(result, "validateaddress result")
        if not _required_boolean(value, "isvalid"):
            raise BitcoinRpcProtocolError("payout destination is invalid for the connected chain")
        script_text = _required_string(value, "scriptPubKey")
        script = _strict_hex_bytes(script_text, "payout script", minimum=2, maximum=100)
        return PayoutDestination(script_pub_key=script)

    def get_block_template(self) -> dict[str, object]:
        """Request a SegWit-aware template for strict model parsing by the caller."""

        result = self._call_read_only("getblocktemplate")
        return dict(_require_object(result, "getblocktemplate result"))

    def _call_read_only(self, method: str) -> object:
        parameters: dict[str, list[object]] = {
            "getblockchaininfo": [],
            "validateaddress": [],
            "getblocktemplate": [{"rules": ["segwit"]}],
        }
        if method not in parameters:
            raise BitcoinRpcProtocolError("Bitcoin RPC method is not available")
        params = parameters[method]
        if method == "validateaddress":
            raise BitcoinRpcProtocolError("validateaddress requires its fixed argument boundary")
        return self._exchange(method, params)

    def _call_validate_address(self, address: str) -> object:
        return self._exchange("validateaddress", [address])

    def _exchange(self, method: str, params: list[object]) -> object:
        if type(self) is BitcoinCoreTemplateClient and not _is_read_only_request(method, params):
            raise BitcoinRpcProtocolError("Bitcoin RPC method is not available")
        if self._closed:
            raise BitcoinRpcTransportError("Bitcoin RPC client is closed")
        request_id = self._next_request_id
        self._next_request_id += 1
        request = {"jsonrpc": "1.0", "id": request_id, "method": method, "params": params}
        try:
            body = json.dumps(request, separators=(",", ":"), allow_nan=False).encode("utf-8")
            response = self._transport.request(
                host=self._host,
                port=self._port,
                timeout_seconds=self._timeout_seconds,
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
        _validate_json_nesting(response.body)
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
            RecursionError,
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


class BitcoinCoreRpcClient(BitcoinCoreTemplateClient):
    """Submission-capable client used only by the explicitly armed solo-mine command."""

    def propose_block(self, serialized_block: bytes) -> ProposalOutcome:
        """Locally validate one complete block through GBT proposal mode."""

        block_hex = _serialized_block_hex(serialized_block)
        result = self._exchange(
            "getblocktemplate",
            [{"mode": "proposal", "data": block_hex, "rules": ["segwit"]}],
        )
        if result is None:
            return ProposalOutcome(accepted=True, category="accepted")
        if not isinstance(result, str) or not result:
            raise BitcoinRpcProtocolError("Bitcoin Core returned an invalid proposal result")
        return ProposalOutcome(
            accepted=False,
            category=_proposal_rejection_category(result),
        )

    def submit_block(self, serialized_block: bytes) -> SubmissionOutcome:
        """Submit one locally and proposal-validated complete block exactly once."""

        result = self._exchange("submitblock", [_serialized_block_hex(serialized_block)])
        if result is None:
            return SubmissionOutcome(accepted=True, category="accepted")
        if not isinstance(result, str) or not result:
            raise BitcoinRpcProtocolError("Bitcoin Core returned an invalid submission result")
        return SubmissionOutcome(
            accepted=False,
            category=_submission_rejection_category(result),
        )


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


def _is_read_only_request(method: str, params: list[object]) -> bool:
    if method == "getblockchaininfo":
        return params == []
    if method == "validateaddress":
        return len(params) == 1 and isinstance(params[0], str)
    return method == "getblocktemplate" and params == [{"rules": ["segwit"]}]


def _resolve_loopback_host(host: str, port: int) -> str:
    """Resolve once and require every address to remain on the local host."""

    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        try:
            records = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise BitcoinRpcTransportError("Bitcoin RPC host could not be resolved") from exc
        addresses: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set()
        try:
            for record in records:
                addresses.add(ipaddress.ip_address(record[4][0]))
        except (IndexError, TypeError, ValueError) as exc:
            raise BitcoinRpcTransportError("Bitcoin RPC host resolution was invalid") from exc
        if not addresses or any(not address.is_loopback for address in addresses):
            raise BitcoinRpcTransportError(
                "Bitcoin RPC host must resolve only to loopback"
            ) from None
        return str(sorted(addresses, key=lambda address: (address.version, int(address)))[0])
    else:
        if not literal.is_loopback:
            raise BitcoinRpcTransportError("Bitcoin RPC host must be loopback")
        return str(literal)


def _read_cookie(path: Path) -> tuple[str, str]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if nofollow:
        flags |= nofollow
    elif path.is_symlink():
        raise BitcoinRpcAuthenticationError("Bitcoin RPC cookie could not be read")
    descriptor = -1
    try:
        descriptor = os.open(path, flags)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise BitcoinRpcAuthenticationError("Bitcoin RPC cookie could not be read")
        if os.name == "posix":
            if metadata.st_uid != os.geteuid() or metadata.st_mode & 0o077:
                raise BitcoinRpcAuthenticationError("Bitcoin RPC cookie permissions are unsafe")
        chunks: list[bytes] = []
        remaining = MAX_COOKIE_BYTES + 1
        while remaining > 0:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        cookie = b"".join(chunks)
    except BitcoinRpcAuthenticationError:
        raise
    except OSError as exc:
        raise BitcoinRpcAuthenticationError("Bitcoin RPC cookie could not be read") from exc
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
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


def _validate_json_nesting(payload: bytes) -> None:
    """Reject deeply nested JSON before the recursive decoder allocates its tree."""

    depth = 0
    in_string = False
    escaped = False
    for value in payload:
        if in_string:
            if escaped:
                escaped = False
            elif value == 0x5C:
                escaped = True
            elif value == 0x22:
                in_string = False
            continue
        if value == 0x22:
            in_string = True
        elif value in {0x5B, 0x7B}:
            depth += 1
            if depth > MAX_JSON_NESTING_DEPTH:
                raise BitcoinRpcProtocolError("Bitcoin Core returned excessively nested JSON")
        elif value in {0x5D, 0x7D}:
            depth -= 1
            if depth < 0:
                return


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


def _proposal_rejection_category(reason: str) -> str:
    _validate_rejection_token(reason)
    if reason in _DUPLICATE_REJECTION_CATEGORIES:
        return _DUPLICATE_REJECTION_CATEGORIES[reason]
    if reason in _PROPOSAL_REJECTION_CATEGORIES:
        return _PROPOSAL_REJECTION_CATEGORIES[reason]
    if _VERSION_REJECTION_TOKEN.fullmatch(reason) is not None:
        return "invalid_version"
    if reason.startswith("bad-txns-") or reason.startswith("bad-blk-"):
        return "bad_transactions"
    return "other_proposal_rejection"


def _submission_rejection_category(reason: str) -> str:
    _validate_rejection_token(reason)
    if reason in _DUPLICATE_REJECTION_CATEGORIES:
        return _DUPLICATE_REJECTION_CATEGORIES[reason]
    return "rejected"


def _validate_rejection_token(reason: str) -> None:
    if (
        len(reason) > _MAX_REJECTION_TOKEN_CHARACTERS
        or _CONTROL_CHARACTERS.search(reason) is not None
        or (
            _SAFE_REJECTION_TOKEN.fullmatch(reason) is None
            and _VERSION_REJECTION_TOKEN.fullmatch(reason) is None
        )
    ):
        raise BitcoinRpcProtocolError("Bitcoin Core returned an unsafe rejection token")
