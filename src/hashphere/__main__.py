"""Command-line entry point for Hashphere development operations."""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence

from hashphere.config import Settings
from hashphere.network.stratum import (
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
_USAGE = "Usage: python -m hashphere stratum-handshake"


def main(argv: Sequence[str] | None = None) -> int:
    """Run the selected Hashphere command and return its process status."""

    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments != ["stratum-handshake"]:
        print(_USAGE, file=sys.stderr)
        return 2

    return _run_stratum_handshake()


def _run_stratum_handshake() -> int:
    """Run one explicitly enabled live Stratum handshake."""

    try:
        settings = Settings.from_env()
    except (TypeError, ValueError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    if os.getenv(_LIVE_STRATUM_FLAG) != "1":
        print(
            f"Live Stratum handshake disabled; set {_LIVE_STRATUM_FLAG}=1 to enable it.",
            file=sys.stderr,
        )
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


def _mask_username(username: str) -> str:
    """Mask a Stratum username while retaining a small recognition hint."""

    if len(username) <= 2:
        return "*" * len(username)
    if len(username) <= 8:
        return f"{username[0]}{'*' * (len(username) - 2)}{username[-1]}"
    return f"{username[:4]}…{username[-4:]}"


if __name__ == "__main__":
    raise SystemExit(main())
