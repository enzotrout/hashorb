"""Validated mining-job domain model and Stratum notification assembler."""

from __future__ import annotations

import math
import string
from dataclasses import dataclass

from hashorb.network.stratum.messages import (
    MiningNotifyNotification,
    SetDifficultyNotification,
    SubscribeResult,
)

_HEX_DIGITS = frozenset(string.hexdigits)


class MiningJobError(Exception):
    """Base error for mining-job domain failures."""


class MiningJobValidationError(MiningJobError, ValueError):
    """Raised when mining-job data violates a domain invariant."""


class MiningJobStateError(MiningJobError, RuntimeError):
    """Raised when a job cannot be assembled in the current state."""


@dataclass(frozen=True, slots=True)
class MiningJob:
    """An immutable, validated snapshot of one Stratum mining job."""

    job_id: str
    previous_block_hash: str
    coinbase_part_1: str
    coinbase_part_2: str
    merkle_branches: tuple[str, ...]
    version: str
    network_bits: str
    network_time: str
    clean_jobs: bool
    extra_nonce_1: str
    extra_nonce_2_size: int
    difficulty: int | float

    def __post_init__(self) -> None:
        """Validate invariants without normalizing protocol values."""

        _validate_identifier(self.job_id, "job_id")
        _validate_fixed_hex(self.previous_block_hash, "previous_block_hash", 32)
        _validate_hex_bytes(self.coinbase_part_1, "coinbase_part_1")
        _validate_hex_bytes(self.coinbase_part_2, "coinbase_part_2")
        _validate_merkle_branches(self.merkle_branches)
        _validate_fixed_hex(self.version, "version", 4)
        _validate_fixed_hex(self.network_bits, "network_bits", 4)
        _validate_fixed_hex(self.network_time, "network_time", 4)
        _validate_boolean(self.clean_jobs, "clean_jobs")
        _validate_hex_bytes(self.extra_nonce_1, "extra_nonce_1")
        _validate_extra_nonce_2_size(self.extra_nonce_2_size)
        _validate_difficulty(self.difficulty)


class MiningJobAssembler:
    """Assemble validated jobs using subscription and current difficulty state."""

    __slots__ = ("_current_difficulty", "_extra_nonce_1", "_extra_nonce_2_size")

    def __init__(self, subscription: SubscribeResult) -> None:
        if not isinstance(subscription, SubscribeResult):
            raise MiningJobValidationError("subscription must be a SubscribeResult")

        _validate_hex_bytes(subscription.extra_nonce_1, "extra_nonce_1")
        _validate_extra_nonce_2_size(subscription.extra_nonce_2_size)

        self._extra_nonce_1 = subscription.extra_nonce_1
        self._extra_nonce_2_size = subscription.extra_nonce_2_size
        self._current_difficulty: int | float | None = None

    @property
    def extra_nonce_1(self) -> str:
        """Return the session's unchanged first extra nonce."""

        return self._extra_nonce_1

    @property
    def extra_nonce_2_size(self) -> int:
        """Return the session's second extra-nonce size in bytes."""

        return self._extra_nonce_2_size

    @property
    def current_difficulty(self) -> int | float | None:
        """Return the current difficulty, or ``None`` before the first update."""

        return self._current_difficulty

    def apply_difficulty(self, notification: SetDifficultyNotification) -> None:
        """Replace the difficulty used for subsequently assembled jobs."""

        if not isinstance(notification, SetDifficultyNotification):
            raise MiningJobValidationError("notification must be a SetDifficultyNotification")
        _validate_difficulty(notification.difficulty)
        self._current_difficulty = notification.difficulty

    def build_job(self, notification: MiningNotifyNotification) -> MiningJob:
        """Build a job that snapshots the currently effective difficulty."""

        if self._current_difficulty is None:
            raise MiningJobStateError("cannot build a mining job before receiving difficulty")
        if not isinstance(notification, MiningNotifyNotification):
            raise MiningJobValidationError("notification must be a MiningNotifyNotification")

        return MiningJob(
            job_id=notification.job_id,
            previous_block_hash=notification.previous_block_hash,
            coinbase_part_1=notification.coinbase_part_1,
            coinbase_part_2=notification.coinbase_part_2,
            merkle_branches=notification.merkle_branches,
            version=notification.version,
            network_bits=notification.network_bits,
            network_time=notification.network_time,
            clean_jobs=notification.clean_jobs,
            extra_nonce_1=self._extra_nonce_1,
            extra_nonce_2_size=self._extra_nonce_2_size,
            difficulty=self._current_difficulty,
        )


def _validate_identifier(value: object, field: str) -> None:
    if not isinstance(value, str):
        raise MiningJobValidationError(f"{field} must be a string")
    if not value.strip():
        raise MiningJobValidationError(f"{field} must not be empty")


def _validate_hex_bytes(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise MiningJobValidationError(f"{field} must be a hexadecimal string")
    if not value:
        raise MiningJobValidationError(f"{field} must not be empty")
    if any(character not in _HEX_DIGITS for character in value):
        raise MiningJobValidationError(f"{field} must contain only hexadecimal characters")
    if len(value) % 2 != 0:
        raise MiningJobValidationError(f"{field} must contain a whole number of bytes")
    return value


def _validate_fixed_hex(value: object, field: str, byte_length: int) -> None:
    validated = _validate_hex_bytes(value, field)
    if len(validated) != byte_length * 2:
        raise MiningJobValidationError(f"{field} must encode exactly {byte_length} bytes")


def _validate_merkle_branches(value: object) -> None:
    if not isinstance(value, tuple):
        raise MiningJobValidationError("merkle_branches must be a tuple")
    for index, branch in enumerate(value):
        _validate_fixed_hex(branch, f"merkle_branches[{index}]", 32)


def _validate_extra_nonce_2_size(value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise MiningJobValidationError("extra_nonce_2_size must be a positive integer")
    if value <= 0:
        raise MiningJobValidationError("extra_nonce_2_size must be a positive integer")


def _validate_difficulty(value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MiningJobValidationError("difficulty must be an integer or float")
    if value <= 0:
        raise MiningJobValidationError("difficulty must be greater than zero")
    if isinstance(value, float) and not math.isfinite(value):
        raise MiningJobValidationError("difficulty must be finite")


def _validate_boolean(value: object, field: str) -> None:
    if not isinstance(value, bool):
        raise MiningJobValidationError(f"{field} must be a boolean")
