"""Construction and parsing helpers for Stratum V1 messages."""

from __future__ import annotations

import math
import string
from collections.abc import Mapping
from dataclasses import dataclass

_HEX_DIGITS = frozenset(string.hexdigits)
_NETWORK_TIME_HEX_LENGTH = 8
_MAX_NONCE = 0xFFFFFFFF


class StratumMessageError(ValueError):
    """Raised when a Stratum message does not have the expected structure."""


@dataclass(frozen=True, slots=True)
class StratumError:
    """A structured error returned by a Stratum server."""

    code: int
    message: str
    data: object


@dataclass(frozen=True, slots=True)
class SubscribeResult:
    """The negotiated subscription and extra-nonce parameters."""

    subscriptions: tuple[tuple[str, str], ...]
    extra_nonce_1: str
    extra_nonce_2_size: int


@dataclass(frozen=True, slots=True)
class SetDifficultyNotification:
    """A difficulty value announced by a Stratum server."""

    difficulty: int | float


@dataclass(frozen=True, slots=True)
class MiningNotifyNotification:
    """A parsed ``mining.notify`` payload."""

    job_id: str
    previous_block_hash: str
    coinbase_part_1: str
    coinbase_part_2: str
    merkle_branches: tuple[str, ...]
    version: str
    network_bits: str
    network_time: str
    clean_jobs: bool


def build_subscribe_request(request_id: int, user_agent: str) -> dict[str, object]:
    """Build a ``mining.subscribe`` request after validating its inputs."""

    _validate_request_id(request_id)
    _validate_nonempty_request_value(user_agent, "user_agent")
    return {
        "id": request_id,
        "method": "mining.subscribe",
        "params": [user_agent],
    }


def build_authorize_request(
    request_id: int,
    username: str,
    password: str,
) -> dict[str, object]:
    """Build a ``mining.authorize`` request after validating its inputs."""

    _validate_request_id(request_id)
    _validate_nonempty_request_value(username, "username")
    _validate_nonempty_request_value(password, "password")
    return {
        "id": request_id,
        "method": "mining.authorize",
        "params": [username, password],
    }


def build_submit_request(
    request_id: int,
    username: str,
    job_id: str,
    extra_nonce_2: str,
    network_time: str,
    nonce: int,
) -> dict[str, object]:
    """Build a validated ``mining.submit`` request without transmitting it."""

    _validate_request_id(request_id)
    _validate_nonempty_request_value(username, "username")
    _validate_nonempty_request_value(job_id, "job_id")
    _validate_hex_bytes(extra_nonce_2, "extra_nonce_2")
    _validate_fixed_hex(network_time, "network_time", _NETWORK_TIME_HEX_LENGTH)
    nonce_hex = _serialize_submit_nonce(nonce)
    return {
        "id": request_id,
        "method": "mining.submit",
        "params": [
            username,
            job_id,
            extra_nonce_2,
            network_time,
            nonce_hex,
        ],
    }


def parse_subscribe_result(message: Mapping[str, object]) -> SubscribeResult:
    """Parse a successful ``mining.subscribe`` response result."""

    result = _require_list(_required_field(message, "result"), "result")
    if len(result) != 3:
        raise StratumMessageError("subscribe result must contain exactly three values")

    raw_subscriptions = _require_list(result[0], "result[0]")
    subscriptions: list[tuple[str, str]] = []
    for index, raw_subscription in enumerate(raw_subscriptions):
        subscription = _require_list(raw_subscription, f"result[0][{index}]")
        if len(subscription) != 2:
            raise StratumMessageError(f"result[0][{index}] must contain exactly two strings")
        method = _require_string(subscription[0], f"result[0][{index}][0]")
        subscription_id = _require_string(subscription[1], f"result[0][{index}][1]")
        subscriptions.append((method, subscription_id))

    extra_nonce_1 = _require_string(result[1], "result[1]")
    extra_nonce_2_size = _require_integer(result[2], "result[2]")
    if extra_nonce_2_size <= 0:
        raise StratumMessageError("result[2] must be greater than zero")

    return SubscribeResult(
        subscriptions=tuple(subscriptions),
        extra_nonce_1=extra_nonce_1,
        extra_nonce_2_size=extra_nonce_2_size,
    )


def parse_authorize_result(message: Mapping[str, object]) -> bool:
    """Parse a ``mining.authorize`` result, accepting only a JSON boolean."""

    result = _required_field(message, "result")
    if not isinstance(result, bool):
        raise StratumMessageError("result must be a boolean")
    return result


def parse_submit_result(message: Mapping[str, object]) -> bool:
    """Parse a ``mining.submit`` acceptance or rejection Boolean."""

    result = _required_field(message, "result")
    if not isinstance(result, bool):
        raise StratumMessageError("result must be a boolean")
    return result


def parse_set_difficulty(message: Mapping[str, object]) -> SetDifficultyNotification:
    """Parse a ``mining.set_difficulty`` notification."""

    _require_method(message, "mining.set_difficulty")
    params = _require_list(_required_field(message, "params"), "params")
    if len(params) != 1:
        raise StratumMessageError("params must contain exactly one difficulty value")

    difficulty = params[0]
    if isinstance(difficulty, bool) or not isinstance(difficulty, (int, float)):
        raise StratumMessageError("params[0] must be an integer or float")
    if difficulty <= 0 or not math.isfinite(difficulty):
        raise StratumMessageError("params[0] must be finite and greater than zero")

    return SetDifficultyNotification(difficulty=difficulty)


def parse_mining_notify(message: Mapping[str, object]) -> MiningNotifyNotification:
    """Parse a ``mining.notify`` job notification without decoding hex fields."""

    _require_method(message, "mining.notify")
    params = _require_list(_required_field(message, "params"), "params")
    if len(params) != 9:
        raise StratumMessageError("params must contain exactly nine job values")

    raw_merkle_branches = _require_list(params[4], "params[4]")
    merkle_branches = tuple(
        _require_string(branch, f"params[4][{index}]")
        for index, branch in enumerate(raw_merkle_branches)
    )

    clean_jobs = params[8]
    if not isinstance(clean_jobs, bool):
        raise StratumMessageError("params[8] must be a boolean")

    return MiningNotifyNotification(
        job_id=_require_string(params[0], "params[0]"),
        previous_block_hash=_require_string(params[1], "params[1]"),
        coinbase_part_1=_require_string(params[2], "params[2]"),
        coinbase_part_2=_require_string(params[3], "params[3]"),
        merkle_branches=merkle_branches,
        version=_require_string(params[5], "params[5]"),
        network_bits=_require_string(params[6], "params[6]"),
        network_time=_require_string(params[7], "params[7]"),
        clean_jobs=clean_jobs,
    )


def parse_stratum_error(value: object) -> StratumError | None:
    """Parse a Stratum error array, or return ``None`` for a null error."""

    if value is None:
        return None

    error = _require_list(value, "error")
    if len(error) != 3:
        raise StratumMessageError("error must contain exactly three values")

    code = _require_integer(error[0], "error[0]")
    message = error[1]
    if not isinstance(message, str):
        raise StratumMessageError("error[1] must be a string")

    return StratumError(code=code, message=message, data=error[2])


def _validate_request_id(request_id: int) -> None:
    if isinstance(request_id, bool) or not isinstance(request_id, int):
        raise TypeError("request_id must be an integer")
    if request_id < 0:
        raise ValueError("request_id must be nonnegative")


def _validate_nonempty_request_value(value: str, name: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if not value.strip():
        raise ValueError(f"{name} must not be empty")


def _validate_hex_bytes(value: str, name: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if not value:
        raise ValueError(f"{name} must not be empty")
    if len(value) % 2 != 0:
        raise ValueError(f"{name} must contain an even number of hexadecimal characters")
    if any(character not in _HEX_DIGITS for character in value):
        raise ValueError(f"{name} must contain only ASCII hexadecimal characters")


def _validate_fixed_hex(value: str, name: str, length: int) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if len(value) != length:
        raise ValueError(f"{name} must contain exactly {length} hexadecimal characters")
    if any(character not in _HEX_DIGITS for character in value):
        raise ValueError(f"{name} must contain only ASCII hexadecimal characters")


def _serialize_submit_nonce(nonce: int) -> str:
    """Serialize a Stratum V1 nonce as canonical big-endian uint32 hex text."""

    if isinstance(nonce, bool) or not isinstance(nonce, int):
        raise TypeError("nonce must be an integer")
    if not 0 <= nonce <= _MAX_NONCE:
        raise ValueError("nonce must be between 0 and 0xffffffff")
    return f"{nonce:08x}"


def _required_field(message: Mapping[str, object], name: str) -> object:
    try:
        return message[name]
    except KeyError as exc:
        raise StratumMessageError(f"missing required field: {name}") from exc


def _require_method(message: Mapping[str, object], expected: str) -> None:
    method = _required_field(message, "method")
    if method != expected:
        raise StratumMessageError(f"method must be {expected!r}")


def _require_list(value: object, location: str) -> list[object]:
    if not isinstance(value, list):
        raise StratumMessageError(f"{location} must be an array")
    return value


def _require_string(value: object, location: str) -> str:
    if not isinstance(value, str):
        raise StratumMessageError(f"{location} must be a string")
    return value


def _require_integer(value: object, location: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise StratumMessageError(f"{location} must be an integer")
    return value
