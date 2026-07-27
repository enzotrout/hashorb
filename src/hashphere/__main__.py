"""Command-line entry point for Hashphere development operations."""

from __future__ import annotations

import os
import secrets
import sys
from collections.abc import Sequence
from dataclasses import dataclass

from hashphere.config import Settings
from hashphere.mining import (
    BlockHeaderError,
    CoinbaseError,
    MerkleError,
    MiningJob,
    MiningJobAssembler,
    MiningJobError,
    NonceSearchError,
    NonceSearchResult,
    PreparedMiningWork,
    TargetError,
    prepare_mining_work,
    search_nonce_range,
)
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
    SubscribeResult,
)

_LIVE_STRATUM_FLAG = "HASHPHERE_ENABLE_LIVE_STRATUM"
_LIVE_MINING_FLAG = "HASHPHERE_ENABLE_LIVE_MINING"
_STRATUM_USER_AGENT = "Hashphere/0.1"
_NONCE_LIMIT = 1 << 32
_MAX_NONCE = _NONCE_LIMIT - 1
_USAGE = (
    "Usage: python -m hashphere {stratum-handshake,stratum-observe,stratum-mine-once} [options]"
)


@dataclass(frozen=True, slots=True)
class _MiningOutcome:
    """Values produced by one bounded live mining session."""

    job: MiningJob
    work: PreparedMiningWork
    result: NonceSearchResult
    pool_accepted: bool | None


def main(argv: Sequence[str] | None = None) -> int:
    """Run the selected Hashphere command and return its process status."""

    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments == ["stratum-handshake"]:
        return _run_stratum_handshake()
    if arguments == ["stratum-observe"]:
        return _run_stratum_observer()
    if arguments and arguments[0] == "stratum-mine-once":
        try:
            start_nonce, stop_nonce = _parse_mining_range(arguments[1:])
        except ValueError as exc:
            print(f"Argument error: {exc}", file=sys.stderr)
            print(_USAGE, file=sys.stderr)
            return 2
        return _run_stratum_mine_once(start_nonce, stop_nonce)

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


def _run_stratum_mine_once(start_nonce: int, stop_nonce: int) -> int:
    """Run one explicitly enabled, bounded live Stratum mining range."""

    settings = _load_live_mining_settings()
    if settings is None:
        return 2

    client: StratumClient | None = None
    outcome: _MiningOutcome | None = None
    status = 0
    pending_failure: BaseException | None = None
    try:
        client = StratumClient(settings, _STRATUM_USER_AGENT)
        subscription = client.handshake()
        outcome = _mine_one_range(client, subscription, start_nonce, stop_nonce)
    except StratumAuthorizationError as exc:
        pending_failure = exc
        print("Stratum authorization failed.", file=sys.stderr)
        status = 1
    except StratumConnectionError as exc:
        pending_failure = exc
        print("Could not connect to the configured Stratum endpoint.", file=sys.stderr)
        status = 1
    except (
        StratumTransportError,
        StratumMessageError,
        StratumClientError,
        MiningJobError,
        CoinbaseError,
        MerkleError,
        BlockHeaderError,
        TargetError,
        NonceSearchError,
        TypeError,
        ValueError,
    ) as exc:
        pending_failure = exc
        print("Bounded Stratum mining failed.", file=sys.stderr)
        status = 1
    except BaseException as exc:
        pending_failure = exc
        raise
    finally:
        if client is not None:
            try:
                client.close()
            except BaseException:
                if pending_failure is None:
                    print("Could not close the Stratum connection cleanly.", file=sys.stderr)
                    status = 1

    if status != 0:
        return status
    if outcome is None:
        raise RuntimeError("bounded mining completed without an outcome")

    _print_mining_outcome(settings, start_nonce, stop_nonce, outcome)
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


def _load_live_mining_settings() -> Settings | None:
    """Load settings and enforce both explicit live-mining opt-ins."""

    settings = _load_live_settings("mining")
    if settings is None:
        return None
    if os.getenv(_LIVE_MINING_FLAG) != "1":
        print(
            f"Live Stratum mining disabled; set {_LIVE_MINING_FLAG}=1 to enable it.",
            file=sys.stderr,
        )
        return None
    return settings


def _parse_mining_range(arguments: Sequence[str]) -> tuple[int, int]:
    """Parse strict decimal options into one validated half-open nonce range."""

    option_values: dict[str, str] = {}
    index = 0
    while index < len(arguments):
        option = arguments[index]
        if option not in {"--start-nonce", "--hash-count"}:
            raise ValueError("unsupported stratum-mine-once argument")
        if option in option_values:
            raise ValueError(f"{option} may be supplied only once")
        if index + 1 >= len(arguments):
            raise ValueError(f"{option} requires a value")
        option_values[option] = arguments[index + 1]
        index += 2

    if "--hash-count" not in option_values:
        raise ValueError("--hash-count is required")

    start_nonce = _parse_decimal_option(
        "--start-nonce",
        option_values.get("--start-nonce", "0"),
        minimum=0,
        maximum=_MAX_NONCE,
    )
    hash_count = _parse_decimal_option(
        "--hash-count",
        option_values["--hash-count"],
        minimum=1,
        maximum=_NONCE_LIMIT,
    )
    stop_nonce = start_nonce + hash_count
    if stop_nonce > _NONCE_LIMIT:
        raise ValueError("the requested nonce range exceeds 2**32")
    return start_nonce, stop_nonce


def _parse_decimal_option(
    name: str,
    value: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    """Parse one unpadded ASCII decimal integer inside an inclusive range."""

    if not value or not value.isascii() or not value.isdecimal():
        raise ValueError(f"{name} must be an ASCII decimal integer")
    parsed = int(value, 10)
    if not minimum <= parsed <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return parsed


def _mine_one_range(
    client: StratumClient,
    subscription: SubscribeResult,
    start_nonce: int,
    stop_nonce: int,
) -> _MiningOutcome:
    """Assemble one current job, search once, and conditionally submit once."""

    assembler = MiningJobAssembler(subscription)
    job = _receive_buildable_job(client, assembler)
    extra_nonce_2 = _generate_extra_nonce_2(subscription.extra_nonce_2_size)
    work = prepare_mining_work(job, extra_nonce_2)
    result = search_nonce_range(work, start_nonce, stop_nonce)

    pool_accepted: bool | None = None
    if result.match is not None:
        pool_accepted = client.submit_share(
            work.job_id,
            work.extra_nonce_2,
            work.network_time,
            result.match.nonce,
        )

    return _MiningOutcome(
        job=job,
        work=work,
        result=result,
        pool_accepted=pool_accepted,
    )


def _receive_buildable_job(
    client: StratumClient,
    assembler: MiningJobAssembler,
) -> MiningJob:
    """Return the first valid job arriving after a current difficulty exists."""

    while True:
        notification = client.receive_notification()
        if isinstance(notification, SetDifficultyNotification):
            assembler.apply_difficulty(notification)
            continue
        if not isinstance(notification, MiningNotifyNotification):
            raise StratumClientError("unsupported parsed Stratum notification")
        if assembler.current_difficulty is None:
            continue
        return assembler.build_job(notification)


def _generate_extra_nonce_2(byte_size: int) -> str:
    """Generate one lowercase hexadecimal second extra nonce."""

    return secrets.token_hex(byte_size)


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


def _print_mining_outcome(
    settings: Settings,
    start_nonce: int,
    stop_nonce: int,
    outcome: _MiningOutcome,
) -> None:
    """Print a sanitized summary of one completed bounded mining range."""

    print("Bounded Stratum mining completed.")
    print(f"Endpoint: {settings.stratum_host}:{settings.stratum_port}")
    print(f"Username: {_mask_username(settings.stratum_username)}")
    print(f"Job ID: {outcome.job.job_id}")
    print(f"Difficulty: {outcome.job.difficulty}")
    print(f"Network bits: {outcome.job.network_bits}")
    print(f"Extra nonce 2 size: {outcome.job.extra_nonce_2_size}")
    print(f"Start nonce: {start_nonce}")
    print(f"Exclusive stop nonce: {stop_nonce}")
    print(f"Hashes checked: {outcome.result.hashes_checked}")
    print(f"Elapsed time: {outcome.result.elapsed_ns} ns")
    if outcome.result.hashes_per_second is None:
        print("Hashes per second: unavailable")
    else:
        print(f"Hashes per second: {outcome.result.hashes_per_second:.2f}")

    match = outcome.result.match
    if match is None:
        print("Result: no qualifying hash found")
        return

    nonce_hex = match.nonce.to_bytes(4, byteorder="little", signed=False).hex()
    print(f"Matched nonce: {match.nonce}")
    print(f"Submitted nonce hex: {nonce_hex}")
    print(f"Raw block hash: {_abbreviate_hex(match.block_hash.hex())}")
    print(f"Meets share target: {str(match.meets_share_target).lower()}")
    print(f"Meets network target: {str(match.meets_network_target).lower()}")
    print(f"Pool result: {'accepted' if outcome.pool_accepted else 'rejected'}")


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
