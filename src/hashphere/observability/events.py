"""Validated append-only JSON Lines event sinks."""

from __future__ import annotations

import json
import math
import re
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, TextIO

type EventValue = None | bool | int | float | str | Sequence[EventValue] | Mapping[str, EventValue]

_SCHEMA_VERSION = 1
_EVENT_NAME = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
_LEVELS = frozenset({"INFO", "WARNING", "ERROR"})
_ENVELOPE_FIELDS = frozenset(
    {"schema_version", "timestamp", "run_id", "sequence", "level", "event", "command"}
)
_FORBIDDEN_FIELDS = frozenset(
    {
        "authorization",
        "authorization_header",
        "authorization_request",
        "bitcoin_address",
        "bits",
        "block",
        "block_hash",
        "block_header",
        "block_template",
        "candidate_hash",
        "candidate_nonce",
        "coinbase",
        "coinbase_extra_nonce",
        "coinbase_part_1",
        "coinbase_part_2",
        "coinbase_transaction",
        "cookie_contents",
        "extra_nonce_1",
        "extra_nonce_2",
        "header",
        "merkle_root",
        "password",
        "payout_address",
        "payout_script",
        "previous_block_hash",
        "raw_authorization_request",
        "raw_block",
        "raw_coinbase",
        "raw_job",
        "raw_submit_request",
        "raw_subscribe_request",
        "request_payload",
        "response_payload",
        "rpc_cookie",
        "rpc_cookie_file",
        "rpc_password",
        "rpc_username",
        "script_pub_key",
        "stratum_password",
        "stratum_username",
        "subscribe_request",
        "submit_request",
        "target",
        "transaction_data",
        "transaction_bytes",
        "transaction_id",
        "txid",
        "username",
        "wtxid",
    }
)


class EventLogError(RuntimeError):
    """Raised when an event sink cannot initialize, write, or close safely."""


class EventSink(Protocol):
    """Destination for validated structured command events."""

    def emit(
        self,
        event: str,
        *,
        level: str = "INFO",
        fields: Mapping[str, EventValue] | None = None,
    ) -> None:
        """Emit one event."""

    def close(self) -> None:
        """Close the sink safely."""


class NullEventSink:
    """Validate and discard events when persistent logging is disabled."""

    def __init__(self) -> None:
        self._closed = False

    def emit(
        self,
        event: str,
        *,
        level: str = "INFO",
        fields: Mapping[str, EventValue] | None = None,
    ) -> None:
        """Validate an event without persisting it."""

        if self._closed:
            raise EventLogError("event sink is closed")
        _validated_event_fields(event, level, fields)

    def close(self) -> None:
        """Close the sink; repeated calls are safe."""

        self._closed = True


class JsonlEventSink:
    """Append validated event envelopes to a UTF-8 JSON Lines file."""

    def __init__(
        self,
        path: str | Path,
        command: str,
        *,
        clock: Callable[[], datetime] | None = None,
        run_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._command = _validate_nonblank_string(command, "command")
        self._clock = clock if clock is not None else _utc_now
        self._run_id = _validate_nonblank_string(
            (run_id_factory if run_id_factory is not None else _new_run_id)(),
            "run_id",
        )
        self._next_sequence = 1
        self._closed = False
        self._stream = _open_event_stream(path)

    def emit(
        self,
        event: str,
        *,
        level: str = "INFO",
        fields: Mapping[str, EventValue] | None = None,
    ) -> None:
        """Append and flush one compact event envelope."""

        if self._closed:
            raise EventLogError("event sink is closed")

        validated_fields = _validated_event_fields(event, level, fields)
        envelope: dict[str, EventValue] = {
            "schema_version": _SCHEMA_VERSION,
            "timestamp": _format_timestamp(self._clock()),
            "run_id": self._run_id,
            "sequence": self._next_sequence,
            "level": level,
            "event": event,
            "command": self._command,
            **validated_fields,
        }
        try:
            record = json.dumps(
                envelope,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            self._stream.write(f"{record}\n")
            self._stream.flush()
        except (OSError, TypeError, ValueError, UnicodeError) as exc:
            raise EventLogError("could not write structured event log") from exc

        self._next_sequence += 1

    def close(self) -> None:
        """Close the event file; repeated calls are safe."""

        if self._closed:
            return
        self._closed = True
        try:
            self._stream.close()
        except OSError as exc:
            raise EventLogError("could not close structured event log") from exc


def _validated_event_fields(
    event: str,
    level: str,
    fields: Mapping[str, EventValue] | None,
) -> dict[str, EventValue]:
    if not isinstance(event, str) or _EVENT_NAME.fullmatch(event) is None:
        raise ValueError("event must be a nonblank snake_case string")
    if not isinstance(level, str) or level not in _LEVELS:
        raise ValueError("level must be INFO, WARNING, or ERROR")
    if fields is None:
        return {}
    if not isinstance(fields, Mapping):
        raise TypeError("fields must be a mapping")

    validated: dict[str, EventValue] = {}
    for key, value in fields.items():
        field_name = _validate_field_name(key)
        if field_name in _ENVELOPE_FIELDS:
            raise ValueError(f"event field {field_name!r} conflicts with the event envelope")
        validated[field_name] = _validate_event_value(value, location=field_name)
    return validated


def _validate_event_value(value: object, *, location: str) -> EventValue:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"event field {location!r} must contain finite numbers")
        return value
    if isinstance(value, Mapping):
        result: dict[str, EventValue] = {}
        for key, nested_value in value.items():
            nested_name = _validate_field_name(key)
            result[nested_name] = _validate_event_value(
                nested_value,
                location=f"{location}.{nested_name}",
            )
        return result
    if isinstance(value, (list, tuple)):
        return [
            _validate_event_value(item, location=f"{location}[{index}]")
            for index, item in enumerate(value)
        ]
    raise TypeError(f"event field {location!r} contains an unsupported value")


def _validate_field_name(value: object) -> str:
    field_name = _validate_nonblank_string(value, "event field name")
    normalized = field_name.strip().lower()
    if normalized in _FORBIDDEN_FIELDS:
        raise ValueError(f"event field {field_name!r} is forbidden")
    return field_name


def _validate_nonblank_string(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if not value.strip():
        raise ValueError(f"{name} must not be blank")
    return value


def _format_timestamp(value: datetime) -> str:
    if not isinstance(value, datetime):
        raise EventLogError("event clock must return a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise EventLogError("event clock must return a timezone-aware datetime")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _open_event_stream(path: str | Path) -> TextIO:
    if isinstance(path, str):
        if not path.strip():
            raise ValueError("event log path must not be blank")
        event_path = Path(path)
    elif isinstance(path, Path):
        event_path = path
    else:
        raise TypeError("event log path must be a string or Path")

    try:
        event_path.parent.mkdir(parents=True, exist_ok=True)
        return event_path.open("a", encoding="utf-8", newline="\n")
    except (OSError, ValueError) as exc:
        raise EventLogError("could not initialize structured event log") from exc


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _new_run_id() -> str:
    return uuid.uuid4().hex
