"""Hashphere runtime configuration."""

from __future__ import annotations

import os
import re
import socket
from dataclasses import dataclass

from dotenv import load_dotenv

DEFAULT_STRATUM_HOST = "stratum.ckpool.org"
DEFAULT_STRATUM_PORT = 3333
DEFAULT_STRATUM_PASSWORD = "x"
DEFAULT_WORKER_NAME = "auto"

_WORKER_INVALID_CHARACTERS = re.compile(r"[^a-zA-Z0-9_-]+")


def sanitize_worker_name(value: str) -> str:
    """Return a worker name suitable for a Stratum username."""

    sanitized = _WORKER_INVALID_CHARACTERS.sub("-", value.strip())
    sanitized = sanitized.strip("-_").lower()

    if not sanitized:
        return "hashphere"

    return sanitized[:64]


def resolve_worker_name(configured_name: str) -> str:
    """Resolve an explicit worker name or derive one from the hostname."""

    if configured_name.strip().lower() == "auto":
        configured_name = socket.gethostname()

    return sanitize_worker_name(configured_name)


@dataclass(frozen=True)
class Settings:
    """Validated Hashphere runtime settings."""

    stratum_host: str
    stratum_port: int
    bitcoin_address: str
    worker_name: str
    stratum_password: str
    compute_backend: str
    compute_profile: str

    @property
    def stratum_username(self) -> str:
        """Build the CKPool username from the payout address and worker."""

        return f"{self.bitcoin_address}.{self.worker_name}"

    @classmethod
    def from_env(cls) -> Settings:
        """Load Hashphere settings from the environment and optional .env file."""

        load_dotenv()

        bitcoin_address = os.getenv("HASHPHERE_BITCOIN_ADDRESS", "").strip()
        if not bitcoin_address:
            raise ValueError("HASHPHERE_BITCOIN_ADDRESS is required")

        port_text = os.getenv(
            "HASHPHERE_STRATUM_PORT",
            str(DEFAULT_STRATUM_PORT),
        )

        try:
            stratum_port = int(port_text)
        except ValueError as exc:
            raise ValueError("HASHPHERE_STRATUM_PORT must be an integer") from exc

        if not 1 <= stratum_port <= 65535:
            raise ValueError("HASHPHERE_STRATUM_PORT must be between 1 and 65535")

        worker_name = resolve_worker_name(os.getenv("HASHPHERE_WORKER_NAME", DEFAULT_WORKER_NAME))

        return cls(
            stratum_host=os.getenv(
                "HASHPHERE_STRATUM_HOST",
                DEFAULT_STRATUM_HOST,
            ).strip(),
            stratum_port=stratum_port,
            bitcoin_address=bitcoin_address,
            worker_name=worker_name,
            stratum_password=os.getenv(
                "HASHPHERE_STRATUM_PASSWORD",
                DEFAULT_STRATUM_PASSWORD,
            ),
            compute_backend=os.getenv(
                "HASHPHERE_COMPUTE_BACKEND",
                "auto",
            )
            .strip()
            .lower(),
            compute_profile=os.getenv(
                "HASHPHERE_COMPUTE_PROFILE",
                "lite",
            )
            .strip()
            .lower(),
        )
