"""Strict configuration for the user-operated Bitcoin Core RPC boundary."""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_BITCOIN_RPC_HOST = "127.0.0.1"
DEFAULT_BITCOIN_RPC_PORT = 8332
DEFAULT_BITCOIN_RPC_TIMEOUT_SECONDS = 10.0
MAX_BITCOIN_RPC_TIMEOUT_SECONDS = 120.0

_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True, slots=True)
class BitcoinRpcSettings:
    """Validated RPC endpoint and exactly one authentication method."""

    host: str
    port: int
    timeout_seconds: float
    username: str | None = field(default=None, repr=False)
    password: str | None = field(default=None, repr=False)
    cookie_file: Path | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        """Reject ambiguous, injectable, or unbounded configuration."""

        _validate_text(self.host, "Bitcoin RPC host", maximum_length=253)
        if self.host != self.host.strip():
            raise ValueError("Bitcoin RPC host must not have surrounding whitespace")
        if (
            not isinstance(self.port, int)
            or isinstance(self.port, bool)
            or not 1 <= self.port <= 65535
        ):
            raise ValueError("Bitcoin RPC port must be between 1 and 65535")
        if (
            not isinstance(self.timeout_seconds, (int, float))
            or isinstance(self.timeout_seconds, bool)
            or not math.isfinite(self.timeout_seconds)
            or not 0 < self.timeout_seconds <= MAX_BITCOIN_RPC_TIMEOUT_SECONDS
        ):
            raise ValueError(
                "Bitcoin RPC timeout must be finite, positive, and at most 120 seconds"
            )

        password_auth = self.username is not None or self.password is not None
        cookie_auth = self.cookie_file is not None
        if password_auth and cookie_auth:
            raise ValueError("Bitcoin RPC authentication methods are mutually exclusive")
        if not password_auth and not cookie_auth:
            raise ValueError("Bitcoin RPC authentication is required")
        if password_auth:
            if self.username is None or self.password is None:
                raise ValueError("Bitcoin RPC username and password must be configured together")
            _validate_text(self.username, "Bitcoin RPC username", maximum_length=256)
            _validate_text(self.password, "Bitcoin RPC password", maximum_length=1024)
            if ":" in self.username:
                raise ValueError("Bitcoin RPC username must not contain a colon")
        if self.cookie_file is not None and not isinstance(self.cookie_file, Path):
            raise ValueError("Bitcoin RPC cookie file must be a filesystem path")

    @classmethod
    def from_env(cls) -> BitcoinRpcSettings:
        """Load only Bitcoin Core RPC settings from the environment."""

        load_dotenv()
        host = os.getenv("HASHPHERE_BITCOIN_RPC_HOST", DEFAULT_BITCOIN_RPC_HOST)
        port = _parse_environment_integer(
            os.getenv("HASHPHERE_BITCOIN_RPC_PORT", str(DEFAULT_BITCOIN_RPC_PORT)),
            "HASHPHERE_BITCOIN_RPC_PORT",
            minimum=1,
            maximum=65535,
        )
        timeout_seconds = _parse_timeout(
            os.getenv(
                "HASHPHERE_BITCOIN_RPC_TIMEOUT_SECONDS",
                str(DEFAULT_BITCOIN_RPC_TIMEOUT_SECONDS),
            )
        )
        username = _optional_environment_text("HASHPHERE_BITCOIN_RPC_USER")
        password = _optional_environment_text("HASHPHERE_BITCOIN_RPC_PASSWORD")
        cookie_text = _optional_environment_text("HASHPHERE_BITCOIN_RPC_COOKIE_FILE")
        return cls(
            host=host,
            port=port,
            timeout_seconds=timeout_seconds,
            username=username,
            password=password,
            cookie_file=Path(cookie_text) if cookie_text is not None else None,
        )


def _optional_environment_text(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    if not value or value != value.strip():
        raise ValueError(f"{name} must be a nonblank unpadded string")
    return value


def _parse_environment_integer(value: object, name: str, *, minimum: int, maximum: int) -> int:
    if (
        not isinstance(value, str)
        or not value
        or not value.isascii()
        or not value.isdecimal()
        or (len(value) > 1 and value.startswith("0"))
    ):
        raise ValueError(f"{name} must be an unpadded ASCII decimal integer")
    parsed = int(value)
    if not minimum <= parsed <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return parsed


def _parse_timeout(value: object) -> float:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("HASHPHERE_BITCOIN_RPC_TIMEOUT_SECONDS must be a finite decimal")
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ValueError("HASHPHERE_BITCOIN_RPC_TIMEOUT_SECONDS must be a finite decimal") from exc
    if not math.isfinite(parsed) or not 0 < parsed <= MAX_BITCOIN_RPC_TIMEOUT_SECONDS:
        raise ValueError("HASHPHERE_BITCOIN_RPC_TIMEOUT_SECONDS must be positive and at most 120")
    return parsed


def _validate_text(value: object, name: str, *, maximum_length: int) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a nonblank string")
    if len(value) > maximum_length:
        raise ValueError(f"{name} is too long")
    if _CONTROL_CHARACTERS.search(value) is not None:
        raise ValueError(f"{name} must not contain control characters")
    return value
