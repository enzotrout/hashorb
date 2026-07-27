"""Command-line entry point for Hashphere development operations."""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence

from hashphere.config import Settings
from hashphere.network.stratum import (
    MiningNotifyNotification,
    SetDifficultyNotification,
    StratumAuthorizationError,
    StratumClient,
    StratumClientError,
    StratumClientState,
    StratumConnectionError,
    StratumMessageError,
    StratumTransportError,
)

_LIVE_STRATUM_FLAG = "HASHPHERE_ENABLE_LIVE_STRATUM"
_STRATUM_USER_AGENT = "Hashphere/0.1"
_USAGE = "Usage: python -m hashphere {stratum-handshake,stratum-observe}"


def main(argv: Sequence[str] | None = None) -> int:
    """Run the selected Hashphere command and return its process status."""

    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments == ["stratum-handshake"]:
        return _run_stratum_handshake()
    if arguments == ["stratum-observe"]:
        return _run_stratum_observer()

    print(_USAGE, file=sys.stderr)
    return 2


def _run_stratum_handshake() -> int:
    """Run one explicitly enabled live Stratum handshake."""

    settings = _load_live_settings("handshake")
    if settings is None:
        return 2

    client: StratumClient | None = None
    try:
        client = StratumClient(settings, _STRATUM_USER_AGENT)
        result = client.handshake()
        final_state = client.state
    except StratumAuthorizationError:
        print("Stratum authorization failed.", file=sys.stderr)
        return 1
    except StratumConnectionError:
        print("Could not connect to the configured Stratum endpoint.", file=sys.stderr)
        return 1
    except (StratumTransportError, StratumMessageError, StratumClientError):
        print("Stratum protocol handshake failed.", file=sys.stderr)
        return 1
    except (TypeError, ValueError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    finally:
        if client is not None:
            client.close()

    _print_success(settings, result.extra_nonce_1, result.extra_nonce_2_size, final_state)
    return 0


def _run_stratum_observer() -> int:
    """Handshake and observe one difficulty and one mining job notification."""

    settings = _load_live_settings("notification observation")
    if settings is None:
        return 2

    client: StratumClient | None = None
    try:
        client = StratumClient(settings, _STRATUM_USER_AGENT)
        subscription = client.handshake()
        difficulty, job, arrival_order = _observe_required_notifications(client)
        final_state = client.state
    except StratumAuthorizationError:
        print("Stratum authorization failed.", file=sys.stderr)
        return 1
    except StratumConnectionError:
        print("Could not connect to the configured Stratum endpoint.", file=sys.stderr)
        return 1
    except (StratumTransportError, StratumMessageError, StratumClientError):
        print("Stratum notification observation failed.", file=sys.stderr)
        return 1
    except (TypeError, ValueError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    finally:
        if client is not None:
            client.close()

    _print_observation_success(
        settings,
        subscription.extra_nonce_1,
        subscription.extra_nonce_2_size,
        difficulty,
        job,
        arrival_order,
        final_state,
    )
    return 0


def _load_live_settings(operation: str) -> Settings | None:
    """Load settings and enforce the shared explicit live-network opt-in."""

    try:
        settings = Settings.from_env()
    except (TypeError, ValueError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return None

    if os.getenv(_LIVE_STRATUM_FLAG) != "1":
        print(
            f"Live Stratum {operation} disabled; set {_LIVE_STRATUM_FLAG}=1 to enable it.",
            file=sys.stderr,
        )
        return None

    return settings


def _observe_required_notifications(
    client: StratumClient,
) -> tuple[
    SetDifficultyNotification,
    MiningNotifyNotification,
    tuple[str, ...],
]:
    """Receive until both notification types appear, preserving arrival order."""

    difficulty: SetDifficultyNotification | None = None
    job: MiningNotifyNotification | None = None
    arrival_order: list[str] = []

    while difficulty is None or job is None:
        notification = client.receive_notification()
        if isinstance(notification, SetDifficultyNotification):
            arrival_order.append("mining.set_difficulty")
            if difficulty is None:
                difficulty = notification
        else:
            arrival_order.append("mining.notify")
            if job is None:
                job = notification

    return difficulty, job, tuple(arrival_order)


def _print_success(
    settings: Settings,
    extra_nonce_1: str,
    extra_nonce_2_size: int,
    final_state: StratumClientState,
) -> None:
    """Print a sanitized summary of a successful handshake."""

    print("Stratum handshake succeeded.")
    print(f"Endpoint: {settings.stratum_host}:{settings.stratum_port}")
    print(f"Username: {_mask_username(settings.stratum_username)}")
    print(f"Extra nonce 1: {extra_nonce_1}")
    print(f"Extra nonce 2 size: {extra_nonce_2_size}")
    print(f"State: {final_state.name}")


def _print_observation_success(
    settings: Settings,
    extra_nonce_1: str,
    extra_nonce_2_size: int,
    difficulty: SetDifficultyNotification,
    job: MiningNotifyNotification,
    arrival_order: tuple[str, ...],
    final_state: StratumClientState,
) -> None:
    """Print a sanitized summary of independently observed notifications."""

    print("Stratum notification observation succeeded.")
    print(f"Endpoint: {settings.stratum_host}:{settings.stratum_port}")
    print(f"Username: {_mask_username(settings.stratum_username)}")
    print(f"Arrival order: {' -> '.join(arrival_order)}")
    print(f"Difficulty: {difficulty.difficulty}")
    print(f"Job ID: {job.job_id}")
    print(f"Previous block hash: {_abbreviate_hex(job.previous_block_hash)}")
    print(f"Coinbase part 1 hex characters: {len(job.coinbase_part_1)}")
    print(f"Coinbase part 2 hex characters: {len(job.coinbase_part_2)}")
    print(f"Merkle branch count: {len(job.merkle_branches)}")
    print(f"Version: {job.version}")
    print(f"Network bits: {job.network_bits}")
    print(f"Network time: {job.network_time}")
    print(f"Clean jobs: {str(job.clean_jobs).lower()}")
    print(f"Extra nonce 1: {extra_nonce_1}")
    print(f"Extra nonce 2 size: {extra_nonce_2_size}")
    print(f"State: {final_state.name}")


def _mask_username(username: str) -> str:
    """Mask a Stratum username while retaining a small recognition hint."""

    if len(username) <= 2:
        return "*" * len(username)
    if len(username) <= 8:
        return f"{username[0]}{'*' * (len(username) - 2)}{username[-1]}"
    return f"{username[:4]}…{username[-4:]}"


def _abbreviate_hex(value: str) -> str:
    """Abbreviate a hexadecimal field without returning the complete value."""

    if not value:
        return "<empty>"
    if len(value) <= 2:
        return "*" * len(value)
    if len(value) <= 8:
        return f"{value[:2]}…{value[-2:]}"
    if len(value) <= 16:
        return f"{value[:4]}…{value[-4:]}"
    return f"{value[:8]}…{value[-8:]}"


if __name__ == "__main__":
    raise SystemExit(main())
