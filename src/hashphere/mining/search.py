"""Prepared Bitcoin mining work and bounded sequential nonce search."""

from __future__ import annotations

import string
from dataclasses import dataclass
from time import perf_counter_ns

from hashphere.mining.coinbase import (
    build_coinbase_transaction,
    hash_coinbase_transaction,
)
from hashphere.mining.header import hash_block_header, serialize_block_header
from hashphere.mining.job import MiningJob
from hashphere.mining.merkle import calculate_merkle_root
from hashphere.mining.target import (
    block_hash_to_int,
    decode_compact_target,
    difficulty_to_share_target,
)

_HEX_DIGITS = frozenset(string.hexdigits)
_NETWORK_TIME_HEX_LENGTH = 8
_HEADER_PREFIX_BYTE_LENGTH = 76
_HEADER_BYTE_LENGTH = 80
_HASH_BYTE_LENGTH = 32
_NONCE_BYTE_LENGTH = 4
_MAX_NONCE = 0xFFFFFFFF
_NONCE_STOP_LIMIT = 1 << 32
_MAX_UINT256 = (1 << 256) - 1
_NANOSECONDS_PER_SECOND = 1_000_000_000


class NonceSearchError(Exception):
    """Base error for prepared-work and bounded nonce-search failures."""


class NonceSearchValidationError(NonceSearchError, ValueError):
    """Raised when search-domain input violates a structural invariant."""


@dataclass(frozen=True, slots=True)
class PreparedMiningWork:
    """Immutable header prefix and targets prepared for one mining job."""

    job_id: str
    extra_nonce_2: str
    network_time: str
    header_prefix: bytes
    network_target: int
    share_target: int

    def __post_init__(self) -> None:
        """Validate direct construction without normalizing protocol values."""

        _validate_nonempty_string(self.job_id, "job_id")
        _validate_hex_bytes(self.extra_nonce_2, "extra_nonce_2")
        _validate_fixed_hex(
            self.network_time,
            "network_time",
            _NETWORK_TIME_HEX_LENGTH,
        )
        _validate_bytes_length(
            self.header_prefix,
            "header_prefix",
            _HEADER_PREFIX_BYTE_LENGTH,
        )
        _validate_target(self.network_target, "network_target")
        _validate_target(self.share_target, "share_target")


@dataclass(frozen=True, slots=True)
class NonceSearchMatch:
    """The first raw block hash meeting a prepared work target."""

    nonce: int
    block_hash: bytes
    meets_share_target: bool
    meets_network_target: bool

    def __post_init__(self) -> None:
        """Validate match invariants."""

        _validate_nonce(self.nonce, "nonce")
        _validate_bytes_length(self.block_hash, "block_hash", _HASH_BYTE_LENGTH)
        _validate_boolean(self.meets_share_target, "meets_share_target")
        _validate_boolean(self.meets_network_target, "meets_network_target")
        if not self.meets_share_target and not self.meets_network_target:
            raise NonceSearchValidationError("a match must meet at least one target")


@dataclass(frozen=True, slots=True)
class NonceSearchResult:
    """Immutable outcome and local metrics for one bounded nonce range."""

    start_nonce: int
    stop_nonce: int
    hashes_checked: int
    elapsed_ns: int
    match: NonceSearchMatch | None

    def __post_init__(self) -> None:
        """Validate result counts, range membership, and timing."""

        _validate_nonce_range(self.start_nonce, self.stop_nonce)
        if isinstance(self.hashes_checked, bool) or not isinstance(
            self.hashes_checked,
            int,
        ):
            raise NonceSearchValidationError("hashes_checked must be an integer")
        if isinstance(self.elapsed_ns, bool) or not isinstance(self.elapsed_ns, int):
            raise NonceSearchValidationError("elapsed_ns must be an integer")
        if self.elapsed_ns < 0:
            raise NonceSearchValidationError("elapsed_ns must be nonnegative")

        if self.match is None:
            expected_hashes = self.stop_nonce - self.start_nonce
        else:
            if not isinstance(self.match, NonceSearchMatch):
                raise NonceSearchValidationError("match must be a NonceSearchMatch or None")
            if not self.start_nonce <= self.match.nonce < self.stop_nonce:
                raise NonceSearchValidationError("match nonce must be inside the searched range")
            expected_hashes = self.match.nonce - self.start_nonce + 1

        if self.hashes_checked != expected_hashes:
            raise NonceSearchValidationError(
                "hashes_checked must equal the number of searched nonces"
            )

    @property
    def found(self) -> bool:
        """Return whether the range produced a target match."""

        return self.match is not None

    @property
    def exhausted(self) -> bool:
        """Return whether every nonce was checked without a match."""

        return self.match is None

    @property
    def hashes_per_second(self) -> float | None:
        """Return the measured local rate, or ``None`` for zero elapsed time."""

        if self.elapsed_ns == 0:
            return None
        return self.hashes_checked * _NANOSECONDS_PER_SECOND / self.elapsed_ns


def prepare_mining_work(job: MiningJob, extra_nonce_2: str) -> PreparedMiningWork:
    """Prepare fixed header data and both targets for one job and extra nonce."""

    coinbase_transaction = build_coinbase_transaction(job, extra_nonce_2)
    coinbase_hash = hash_coinbase_transaction(coinbase_transaction)
    merkle_root = calculate_merkle_root(coinbase_hash, job.merkle_branches)
    nonce_zero_header = serialize_block_header(job, merkle_root, nonce=0)
    network_target = decode_compact_target(job.network_bits)
    share_target = difficulty_to_share_target(job.difficulty)

    if len(nonce_zero_header) != _HEADER_BYTE_LENGTH:
        raise NonceSearchError("prepared block header must contain exactly 80 bytes")
    if nonce_zero_header[_HEADER_PREFIX_BYTE_LENGTH:] != bytes(_NONCE_BYTE_LENGTH):
        raise NonceSearchError("prepared block header must contain a zero nonce")

    return PreparedMiningWork(
        job_id=job.job_id,
        extra_nonce_2=extra_nonce_2,
        network_time=job.network_time,
        header_prefix=nonce_zero_header[:_HEADER_PREFIX_BYTE_LENGTH],
        network_target=network_target,
        share_target=share_target,
    )


def search_nonce_range(
    work: PreparedMiningWork,
    start_nonce: int,
    stop_nonce: int,
) -> NonceSearchResult:
    """Search sequential nonces in the half-open range ``[start, stop)``."""

    if not isinstance(work, PreparedMiningWork):
        raise NonceSearchValidationError("work must be PreparedMiningWork")
    _validate_nonce_range(start_nonce, stop_nonce)

    started_ns = perf_counter_ns()
    hashes_checked = 0
    for nonce in range(start_nonce, stop_nonce):
        nonce_bytes = nonce.to_bytes(
            _NONCE_BYTE_LENGTH,
            byteorder="little",
            signed=False,
        )
        header = work.header_prefix + nonce_bytes
        if len(header) != _HEADER_BYTE_LENGTH:
            raise NonceSearchError("candidate block header must contain exactly 80 bytes")

        block_hash = hash_block_header(header)
        hashes_checked += 1
        hash_value = block_hash_to_int(block_hash)
        meets_share_target = hash_value <= work.share_target
        meets_network_target = hash_value <= work.network_target
        if meets_share_target or meets_network_target:
            match = NonceSearchMatch(
                nonce=nonce,
                block_hash=block_hash,
                meets_share_target=meets_share_target,
                meets_network_target=meets_network_target,
            )
            return NonceSearchResult(
                start_nonce=start_nonce,
                stop_nonce=stop_nonce,
                hashes_checked=hashes_checked,
                elapsed_ns=_elapsed_since(started_ns),
                match=match,
            )

    return NonceSearchResult(
        start_nonce=start_nonce,
        stop_nonce=stop_nonce,
        hashes_checked=hashes_checked,
        elapsed_ns=_elapsed_since(started_ns),
        match=None,
    )


def _elapsed_since(started_ns: int) -> int:
    return max(0, perf_counter_ns() - started_ns)


def _validate_nonce_range(start_nonce: object, stop_nonce: object) -> None:
    if isinstance(start_nonce, bool) or not isinstance(start_nonce, int):
        raise NonceSearchValidationError("start_nonce must be an integer")
    if isinstance(stop_nonce, bool) or not isinstance(stop_nonce, int):
        raise NonceSearchValidationError("stop_nonce must be an integer")
    if not 0 <= start_nonce <= _MAX_NONCE:
        raise NonceSearchValidationError("start_nonce must be between 0 and 0xffffffff")
    if not 1 <= stop_nonce <= _NONCE_STOP_LIMIT:
        raise NonceSearchValidationError("stop_nonce must be between 1 and 2**32")
    if start_nonce >= stop_nonce:
        raise NonceSearchValidationError("start_nonce must be less than stop_nonce")


def _validate_nonce(value: object, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise NonceSearchValidationError(f"{field} must be an integer")
    if not 0 <= value <= _MAX_NONCE:
        raise NonceSearchValidationError(f"{field} must be between 0 and 0xffffffff")


def _validate_target(value: object, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise NonceSearchValidationError(f"{field} must be an integer")
    if not 1 <= value <= _MAX_UINT256:
        raise NonceSearchValidationError(f"{field} must be between 1 and 2**256 - 1")


def _validate_nonempty_string(value: object, field: str) -> None:
    if not isinstance(value, str):
        raise NonceSearchValidationError(f"{field} must be a string")
    if not value.strip():
        raise NonceSearchValidationError(f"{field} must not be empty")


def _validate_hex_bytes(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise NonceSearchValidationError(f"{field} must be a hexadecimal string")
    if not value:
        raise NonceSearchValidationError(f"{field} must not be empty")
    if any(character not in _HEX_DIGITS for character in value):
        raise NonceSearchValidationError(f"{field} must contain only hexadecimal characters")
    if len(value) % 2 != 0:
        raise NonceSearchValidationError(f"{field} must contain a whole number of bytes")
    return value


def _validate_fixed_hex(value: object, field: str, character_length: int) -> None:
    if not isinstance(value, str):
        raise NonceSearchValidationError(f"{field} must be a hexadecimal string")
    if not value:
        raise NonceSearchValidationError(f"{field} must not be empty")
    if any(character not in _HEX_DIGITS for character in value):
        raise NonceSearchValidationError(f"{field} must contain only hexadecimal characters")
    if len(value) != character_length:
        raise NonceSearchValidationError(
            f"{field} must contain exactly {character_length} hexadecimal characters"
        )


def _validate_bytes_length(value: object, field: str, byte_length: int) -> None:
    if not isinstance(value, bytes):
        raise NonceSearchValidationError(f"{field} must be bytes")
    if len(value) != byte_length:
        raise NonceSearchValidationError(f"{field} must contain exactly {byte_length} bytes")


def _validate_boolean(value: object, field: str) -> None:
    if not isinstance(value, bool):
        raise NonceSearchValidationError(f"{field} must be a boolean")
