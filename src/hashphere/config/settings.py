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
DEFAULT_COMPUTE_WORKERS = 2
MAX_COMPUTE_WORKERS = 256
DEFAULT_SEARCH_STRATEGY = "sequential"
DEFAULT_CUDA_DEVICE = 0
DEFAULT_CUDA_DEVICES = (DEFAULT_CUDA_DEVICE,)
MAX_CUDA_DEVICE = (1 << 31) - 1
MAX_CUDA_DEVICES = 256

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
    compute_workers: int = DEFAULT_COMPUTE_WORKERS
    search_strategy: str = DEFAULT_SEARCH_STRATEGY
    cuda_device: int = DEFAULT_CUDA_DEVICE
    cuda_devices: tuple[int, ...] = DEFAULT_CUDA_DEVICES

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
        compute_workers = _parse_compute_workers(
            os.getenv("HASHPHERE_COMPUTE_WORKERS", str(DEFAULT_COMPUTE_WORKERS))
        )
        search_strategy = _parse_search_strategy(
            os.getenv("HASHPHERE_SEARCH_STRATEGY", DEFAULT_SEARCH_STRATEGY)
        )
        compute_backend = (
            os.getenv(
                "HASHPHERE_COMPUTE_BACKEND",
                "auto",
            )
            .strip()
            .lower()
        )
        cuda_device = (
            _parse_cuda_device(os.getenv("HASHPHERE_CUDA_DEVICE", str(DEFAULT_CUDA_DEVICE)))
            if compute_backend == "cuda"
            else DEFAULT_CUDA_DEVICE
        )
        cuda_devices = (
            parse_cuda_devices(_required_cuda_devices_environment())
            if compute_backend == "cuda-multi"
            else DEFAULT_CUDA_DEVICES
        )

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
            compute_backend=compute_backend,
            compute_profile=os.getenv(
                "HASHPHERE_COMPUTE_PROFILE",
                "lite",
            )
            .strip()
            .lower(),
            compute_workers=compute_workers,
            search_strategy=search_strategy,
            cuda_device=cuda_device,
            cuda_devices=cuda_devices,
        )


def _parse_compute_workers(value: object) -> int:
    if not isinstance(value, str):
        raise ValueError("HASHPHERE_COMPUTE_WORKERS must be an ASCII decimal integer")
    if (
        not value
        or not value.isascii()
        or not value.isdecimal()
        or (len(value) > 1 and value.startswith("0"))
    ):
        raise ValueError("HASHPHERE_COMPUTE_WORKERS must be an unpadded ASCII decimal integer")
    worker_count = int(value)
    if not 1 <= worker_count <= MAX_COMPUTE_WORKERS:
        raise ValueError(f"HASHPHERE_COMPUTE_WORKERS must be between 1 and {MAX_COMPUTE_WORKERS}")
    return worker_count


def _parse_search_strategy(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("HASHPHERE_SEARCH_STRATEGY must be a strategy identifier")
    if re.fullmatch(r"[a-z][a-z0-9_-]*", value) is None:
        raise ValueError("HASHPHERE_SEARCH_STRATEGY must be an exact lowercase strategy identifier")
    return value


def _parse_cuda_device(value: object) -> int:
    if not isinstance(value, str):
        raise ValueError("HASHPHERE_CUDA_DEVICE must be an ASCII decimal integer")
    if (
        not value
        or not value.isascii()
        or not value.isdecimal()
        or (len(value) > 1 and value.startswith("0"))
    ):
        raise ValueError("HASHPHERE_CUDA_DEVICE must be an unpadded ASCII decimal integer")
    device_ordinal = int(value)
    if not 0 <= device_ordinal <= MAX_CUDA_DEVICE:
        raise ValueError(f"HASHPHERE_CUDA_DEVICE must be between 0 and {MAX_CUDA_DEVICE}")
    return device_ordinal


def parse_cuda_devices(value: object) -> tuple[int, ...]:
    """Parse an explicit comma-separated CUDA device list into canonical order."""

    if not isinstance(value, str) or not value:
        raise ValueError("HASHPHERE_CUDA_DEVICES must be a nonempty device list")
    fields = value.split(",")
    if len(fields) > MAX_CUDA_DEVICES:
        raise ValueError(f"HASHPHERE_CUDA_DEVICES must contain at most {MAX_CUDA_DEVICES} devices")
    ordinals: list[int] = []
    for field in fields:
        token = field.strip()
        if (
            not token
            or not token.isascii()
            or not token.isdecimal()
            or (len(token) > 1 and token.startswith("0"))
        ):
            raise ValueError("HASHPHERE_CUDA_DEVICES must contain unpadded ASCII decimal integers")
        ordinal = int(token)
        if ordinal > MAX_CUDA_DEVICE:
            raise ValueError(
                f"HASHPHERE_CUDA_DEVICES ordinals must be between 0 and {MAX_CUDA_DEVICE}"
            )
        ordinals.append(ordinal)
    if len(set(ordinals)) != len(ordinals):
        raise ValueError("HASHPHERE_CUDA_DEVICES must not contain duplicate ordinals")
    return tuple(sorted(ordinals))


def _required_cuda_devices_environment() -> str:
    value = os.getenv("HASHPHERE_CUDA_DEVICES")
    if value is None:
        raise ValueError("HASHPHERE_CUDA_DEVICES is required for cuda-multi")
    return value
