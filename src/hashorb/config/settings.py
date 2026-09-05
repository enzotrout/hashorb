"""HashOrb runtime configuration."""

from __future__ import annotations

import math
import os
import re
import socket
import sys
from dataclasses import dataclass

from hashorb.config.environment import load_hashorb_environment
from hashorb.config.profile import (
    DEFAULT_CUDA_THREADS_PER_BLOCK,
    ComputeProfileOverrides,
    parse_compute_profile,
)

DEFAULT_STRATUM_HOST = "stratum.ckpool.org"
DEFAULT_STRATUM_PORT = 3333
# Conventional public Stratum placeholder; never treated as a secret.
DEFAULT_STRATUM_PASSWORD = "x"  # nosec B105
DEFAULT_WORKER_NAME = "auto"
DEFAULT_COMPUTE_WORKERS = 2
MAX_COMPUTE_WORKERS = 256
DEFAULT_SEARCH_STRATEGY = "sequential"
DEFAULT_CUDA_DEVICE = 0
DEFAULT_CUDA_DEVICES = (DEFAULT_CUDA_DEVICE,)
MAX_CUDA_DEVICE = (1 << 31) - 1
MAX_CUDA_DEVICES = 256
HASHORB_SUPPORT_BITCOIN_ADDRESS = "bc1qgr9cv6n8tl33k96q2nxk6cf9gj2f7asjj264te"

_WORKER_INVALID_CHARACTERS = re.compile(r"[^a-zA-Z0-9_-]+")


def sanitize_worker_name(value: str) -> str:
    """Return a worker name suitable for a Stratum username."""

    sanitized = _WORKER_INVALID_CHARACTERS.sub("-", value.strip())
    sanitized = sanitized.strip("-_").lower()

    if not sanitized:
        return "hashorb"

    return sanitized[:64]


def resolve_worker_name(configured_name: str) -> str:
    """Resolve an explicit worker name or derive one from the hostname."""

    if configured_name.strip().lower() == "auto":
        configured_name = socket.gethostname()

    return sanitize_worker_name(configured_name)


def _interactive_bitcoin_address() -> str | None:
    """Offer an explicit payout choice only when stdin is an interactive terminal."""

    try:
        interactive = sys.stdin.isatty()
    except (AttributeError, OSError):
        interactive = False
    if not interactive:
        return None

    print("No Bitcoin payout address is configured.")
    print()
    print("Choose:")
    print("1. Enter my Bitcoin address")
    print("2. Mine temporarily to the HashOrb support address")
    print("3. Cancel")

    while True:
        try:
            choice = input("Selection: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            print("Mining cancelled.")
            return None

        if choice == "1":
            try:
                address = input("Bitcoin address: ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                print("Mining cancelled.")
                return None
            if not address:
                print("Bitcoin address cannot be blank.")
                continue
            print("Using your Bitcoin address for this session.")
            print("Add HASHORB_BITCOIN_ADDRESS to .env to remember it for future runs.")
            return address
        if choice == "2":
            print("Using the HashOrb support address for this mining session only:")
            print(HASHORB_SUPPORT_BITCOIN_ADDRESS)
            return HASHORB_SUPPORT_BITCOIN_ADDRESS
        if choice == "3":
            print("Mining cancelled.")
            return None
        print("Choose 1, 2, or 3.")


@dataclass(frozen=True)
class Settings:
    """Validated HashOrb runtime settings."""

    stratum_host: str
    stratum_port: int
    bitcoin_address: str
    worker_name: str
    stratum_password: str
    compute_backend: str
    compute_profile: str | None = None
    compute_workers: int = DEFAULT_COMPUTE_WORKERS
    search_strategy: str = DEFAULT_SEARCH_STRATEGY
    cuda_device: int = DEFAULT_CUDA_DEVICE
    cuda_devices: tuple[int, ...] = DEFAULT_CUDA_DEVICES
    cuda_threads_per_block: int = DEFAULT_CUDA_THREADS_PER_BLOCK
    profile_overrides: ComputeProfileOverrides = ComputeProfileOverrides()

    @property
    def stratum_username(self) -> str:
        """Build the CKPool username from the payout address and worker."""

        return f"{self.bitcoin_address}.{self.worker_name}"

    @classmethod
    def from_env(cls) -> Settings:
        """Load HashOrb settings from the environment and optional .env file."""

        load_hashorb_environment()

        bitcoin_address = os.getenv("HASHORB_BITCOIN_ADDRESS", "").strip()
        if not bitcoin_address:
            bitcoin_address = _interactive_bitcoin_address() or ""
        if not bitcoin_address:
            raise ValueError("HASHORB_BITCOIN_ADDRESS is required")

        port_text = os.getenv(
            "HASHORB_STRATUM_PORT",
            str(DEFAULT_STRATUM_PORT),
        )

        try:
            stratum_port = int(port_text)
        except ValueError as exc:
            raise ValueError("HASHORB_STRATUM_PORT must be an integer") from exc

        if not 1 <= stratum_port <= 65535:
            raise ValueError("HASHORB_STRATUM_PORT must be between 1 and 65535")

        worker_name = resolve_worker_name(os.getenv("HASHORB_WORKER_NAME", DEFAULT_WORKER_NAME))
        compute_workers = _parse_compute_workers(
            os.getenv("HASHORB_COMPUTE_WORKERS", str(DEFAULT_COMPUTE_WORKERS))
        )
        search_strategy = _parse_search_strategy(
            os.getenv("HASHORB_SEARCH_STRATEGY", DEFAULT_SEARCH_STRATEGY)
        )
        compute_backend = (
            os.getenv(
                "HASHORB_COMPUTE_BACKEND",
                "auto",
            )
            .strip()
            .lower()
        )
        cuda_device = (
            _parse_cuda_device(os.getenv("HASHORB_CUDA_DEVICE", str(DEFAULT_CUDA_DEVICE)))
            if compute_backend == "cuda"
            else DEFAULT_CUDA_DEVICE
        )
        cuda_devices = (
            parse_cuda_devices(_required_cuda_devices_environment())
            if compute_backend == "cuda-multi"
            else DEFAULT_CUDA_DEVICES
        )
        profile_text = os.getenv("HASHORB_COMPUTE_PROFILE")
        compute_profile = parse_compute_profile(profile_text) if profile_text is not None else None
        cuda_threads_per_block = (
            _parse_cuda_threads_per_block(
                os.getenv(
                    "HASHORB_CUDA_THREADS_PER_BLOCK",
                    str(DEFAULT_CUDA_THREADS_PER_BLOCK),
                )
            )
            if compute_profile is not None or compute_backend in {"cuda", "cuda-multi"}
            else DEFAULT_CUDA_THREADS_PER_BLOCK
        )
        profile_overrides = (
            parse_compute_profile_overrides_from_env()
            if compute_profile is not None
            else ComputeProfileOverrides()
        )

        return cls(
            stratum_host=os.getenv(
                "HASHORB_STRATUM_HOST",
                DEFAULT_STRATUM_HOST,
            ).strip(),
            stratum_port=stratum_port,
            bitcoin_address=bitcoin_address,
            worker_name=worker_name,
            stratum_password=os.getenv(
                "HASHORB_STRATUM_PASSWORD",
                DEFAULT_STRATUM_PASSWORD,
            ),
            compute_backend=compute_backend,
            compute_profile=compute_profile,
            compute_workers=compute_workers,
            search_strategy=search_strategy,
            cuda_device=cuda_device,
            cuda_devices=cuda_devices,
            cuda_threads_per_block=cuda_threads_per_block,
            profile_overrides=profile_overrides,
        )


def _parse_compute_workers(value: object) -> int:
    if not isinstance(value, str):
        raise ValueError("HASHORB_COMPUTE_WORKERS must be an ASCII decimal integer")
    if (
        not value
        or not value.isascii()
        or not value.isdecimal()
        or (len(value) > 1 and value.startswith("0"))
    ):
        raise ValueError("HASHORB_COMPUTE_WORKERS must be an unpadded ASCII decimal integer")
    worker_count = int(value)
    if not 1 <= worker_count <= MAX_COMPUTE_WORKERS:
        raise ValueError(f"HASHORB_COMPUTE_WORKERS must be between 1 and {MAX_COMPUTE_WORKERS}")
    return worker_count


def _parse_search_strategy(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("HASHORB_SEARCH_STRATEGY must be a strategy identifier")
    if re.fullmatch(r"[a-z][a-z0-9_-]*", value) is None:
        raise ValueError("HASHORB_SEARCH_STRATEGY must be an exact lowercase strategy identifier")
    return value


def _parse_cuda_device(value: object) -> int:
    if not isinstance(value, str):
        raise ValueError("HASHORB_CUDA_DEVICE must be an ASCII decimal integer")
    if (
        not value
        or not value.isascii()
        or not value.isdecimal()
        or (len(value) > 1 and value.startswith("0"))
    ):
        raise ValueError("HASHORB_CUDA_DEVICE must be an unpadded ASCII decimal integer")
    device_ordinal = int(value)
    if not 0 <= device_ordinal <= MAX_CUDA_DEVICE:
        raise ValueError(f"HASHORB_CUDA_DEVICE must be between 0 and {MAX_CUDA_DEVICE}")
    return device_ordinal


def _parse_cuda_threads_per_block(value: object) -> int:
    parsed = _parse_unpadded_environment_integer(
        value,
        "HASHORB_CUDA_THREADS_PER_BLOCK",
    )
    if parsed not in {64, 128, 256, 512}:
        raise ValueError("HASHORB_CUDA_THREADS_PER_BLOCK must be 64, 128, 256, or 512")
    return parsed


def _parse_profile_chunk_size(value: object) -> int:
    parsed = _parse_unpadded_environment_integer(value, "HASHORB_CHUNK_SIZE")
    if not 1 <= parsed <= 1 << 32:
        raise ValueError("HASHORB_CHUNK_SIZE must be between 1 and 2**32")
    return parsed


def _parse_profile_delay(value: object) -> float:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("HASHORB_INTER_RANGE_DELAY_SECONDS must be an unpadded finite decimal")
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ValueError(
            "HASHORB_INTER_RANGE_DELAY_SECONDS must be an unpadded finite decimal"
        ) from exc
    if not math.isfinite(parsed) or not 0 <= parsed <= 60:
        raise ValueError("HASHORB_INTER_RANGE_DELAY_SECONDS must be finite and between 0 and 60")
    return parsed


def _parse_unpadded_environment_integer(value: object, name: str) -> int:
    if (
        not isinstance(value, str)
        or not value
        or not value.isascii()
        or not value.isdecimal()
        or (len(value) > 1 and value.startswith("0"))
    ):
        raise ValueError(f"{name} must be an unpadded ASCII decimal integer")
    return int(value)


def parse_compute_profile_overrides_from_env() -> ComputeProfileOverrides:
    """Capture only explicitly configured compute controls for profile policy."""

    return ComputeProfileOverrides(
        backend_name=(
            os.environ["HASHORB_COMPUTE_BACKEND"].strip().lower()
            if "HASHORB_COMPUTE_BACKEND" in os.environ
            else None
        ),
        worker_count=(
            _parse_compute_workers(os.environ["HASHORB_COMPUTE_WORKERS"])
            if "HASHORB_COMPUTE_WORKERS" in os.environ
            else None
        ),
        cuda_device=(
            _parse_cuda_device(os.environ["HASHORB_CUDA_DEVICE"])
            if "HASHORB_CUDA_DEVICE" in os.environ
            else None
        ),
        cuda_devices=(
            parse_cuda_devices(os.environ["HASHORB_CUDA_DEVICES"])
            if "HASHORB_CUDA_DEVICES" in os.environ
            else None
        ),
        cuda_threads_per_block=(
            _parse_cuda_threads_per_block(os.environ["HASHORB_CUDA_THREADS_PER_BLOCK"])
            if "HASHORB_CUDA_THREADS_PER_BLOCK" in os.environ
            else None
        ),
        chunk_size=(
            _parse_profile_chunk_size(os.environ["HASHORB_CHUNK_SIZE"])
            if "HASHORB_CHUNK_SIZE" in os.environ
            else None
        ),
        inter_range_delay_seconds=(
            _parse_profile_delay(os.environ["HASHORB_INTER_RANGE_DELAY_SECONDS"])
            if "HASHORB_INTER_RANGE_DELAY_SECONDS" in os.environ
            else None
        ),
    )


def parse_cuda_devices(value: object) -> tuple[int, ...]:
    """Parse an explicit comma-separated CUDA device list into canonical order."""

    if not isinstance(value, str) or not value:
        raise ValueError("HASHORB_CUDA_DEVICES must be a nonempty device list")
    fields = value.split(",")
    if len(fields) > MAX_CUDA_DEVICES:
        raise ValueError(f"HASHORB_CUDA_DEVICES must contain at most {MAX_CUDA_DEVICES} devices")
    ordinals: list[int] = []
    for field in fields:
        token = field.strip()
        if (
            not token
            or not token.isascii()
            or not token.isdecimal()
            or (len(token) > 1 and token.startswith("0"))
        ):
            raise ValueError("HASHORB_CUDA_DEVICES must contain unpadded ASCII decimal integers")
        ordinal = int(token)
        if ordinal > MAX_CUDA_DEVICE:
            raise ValueError(
                f"HASHORB_CUDA_DEVICES ordinals must be between 0 and {MAX_CUDA_DEVICE}"
            )
        ordinals.append(ordinal)
    if len(set(ordinals)) != len(ordinals):
        raise ValueError("HASHORB_CUDA_DEVICES must not contain duplicate ordinals")
    return tuple(sorted(ordinals))


def _required_cuda_devices_environment() -> str:
    value = os.getenv("HASHORB_CUDA_DEVICES")
    if value is None:
        raise ValueError("HASHORB_CUDA_DEVICES is required for cuda-multi")
    return value
