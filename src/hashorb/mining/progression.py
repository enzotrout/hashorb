"""Deterministic Bitcoin mining work-space progression and identity."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace

from hashorb.mining.job import MiningJob
from hashorb.mining.search import PreparedMiningWork, prepare_mining_work

type WorkPreparer = Callable[[MiningJob, str], PreparedMiningWork]

_MAX_NETWORK_TIME = 0xFFFFFFFF
_LOWER_HEX_DIGITS = frozenset("0123456789abcdef")


class MiningWorkProgressionError(Exception):
    """Base error for deterministic mining work-space progression."""


class MiningWorkProgressionValidationError(MiningWorkProgressionError, ValueError):
    """Raised when cursor, variant, or identity input is invalid."""


@dataclass(frozen=True, slots=True)
class MiningJobContextIdentity:
    """Immutable pool-job and target context excluding clean-jobs state."""

    job_id: str
    previous_block_hash: str
    coinbase_part_1: str = field(repr=False)
    coinbase_part_2: str = field(repr=False)
    merkle_branches: tuple[str, ...] = field(repr=False)
    version: str
    network_bits: str
    network_time: str
    extra_nonce_1: str = field(repr=False)
    extra_nonce_2_size: int
    difficulty: int | float


@dataclass(frozen=True, slots=True)
class MiningWorkIdentity:
    """Effective header and acceptance-target identity for prepared work."""

    job_id: str
    header_prefix: bytes = field(repr=False)
    network_target: int
    share_target: int

    def __post_init__(self) -> None:
        """Validate direct identity construction."""

        if not isinstance(self.job_id, str) or not self.job_id.strip():
            raise MiningWorkProgressionValidationError("job_id must be a nonblank string")
        if not isinstance(self.header_prefix, bytes) or len(self.header_prefix) != 76:
            raise MiningWorkProgressionValidationError(
                "header_prefix must contain exactly 76 immutable bytes"
            )
        for name, value in (
            ("network_target", self.network_target),
            ("share_target", self.share_target),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise MiningWorkProgressionValidationError(f"{name} must be a positive integer")


@dataclass(frozen=True, slots=True)
class MiningWorkVariant:
    """One immutable effective job, extra nonce, and network-time combination."""

    job: MiningJob = field(repr=False)
    extra_nonce_2: str = field(repr=False)
    variant_index: int
    extra_nonce_2_advance_count: int
    extra_nonce_2_cycle_count: int
    network_time_roll_count: int

    def __post_init__(self) -> None:
        """Validate variant metadata and exact extra-nonce width."""

        if not isinstance(self.job, MiningJob):
            raise MiningWorkProgressionValidationError("job must be a MiningJob")
        _validate_extra_nonce_2(self.extra_nonce_2, self.job.extra_nonce_2_size)
        for name, value in (
            ("variant_index", self.variant_index),
            ("extra_nonce_2_advance_count", self.extra_nonce_2_advance_count),
            ("extra_nonce_2_cycle_count", self.extra_nonce_2_cycle_count),
            ("network_time_roll_count", self.network_time_roll_count),
        ):
            _validate_nonnegative_integer(value, name)


@dataclass(frozen=True, slots=True)
class MiningWorkProgress:
    """One cursor transition after a fully searched 32-bit nonce space."""

    cursor: MiningWorkCursor | None
    extra_nonce_2_advanced: bool
    extra_nonce_2_cycle_completed: bool
    network_time_rolled: bool
    progression_exhausted: bool

    def __post_init__(self) -> None:
        """Validate transition flags and optional successor state."""

        for name, value in (
            ("extra_nonce_2_advanced", self.extra_nonce_2_advanced),
            ("extra_nonce_2_cycle_completed", self.extra_nonce_2_cycle_completed),
            ("network_time_rolled", self.network_time_rolled),
            ("progression_exhausted", self.progression_exhausted),
        ):
            if not isinstance(value, bool):
                raise MiningWorkProgressionValidationError(f"{name} must be Boolean")
        if self.progression_exhausted:
            if self.cursor is not None:
                raise MiningWorkProgressionValidationError(
                    "exhausted progression cannot contain a successor cursor"
                )
            if not self.extra_nonce_2_cycle_completed:
                raise MiningWorkProgressionValidationError(
                    "exhaustion requires a completed extra-nonce cycle"
                )
        elif not isinstance(self.cursor, MiningWorkCursor):
            raise MiningWorkProgressionValidationError(
                "available progression requires a MiningWorkCursor"
            )


@dataclass(frozen=True, slots=True)
class MiningWorkCursor:
    """Compact immutable cursor over extra-nonce and network-time variants."""

    pool_job: MiningJob = field(repr=False)
    starting_extra_nonce_2: str = field(repr=False)
    current_extra_nonce_2_value: int = field(repr=False)
    variants_at_current_time: int
    variant_index: int
    extra_nonce_2_advance_count: int
    extra_nonce_2_cycle_count: int
    network_time_value: int
    network_time_roll_count: int

    def __post_init__(self) -> None:
        """Validate direct cursor construction without enumerating its space."""

        if not isinstance(self.pool_job, MiningJob):
            raise MiningWorkProgressionValidationError("pool_job must be a MiningJob")
        _validate_extra_nonce_2(
            self.starting_extra_nonce_2,
            self.pool_job.extra_nonce_2_size,
        )
        space_size = self.extra_nonce_2_space_size
        _validate_integer(self.current_extra_nonce_2_value, "current_extra_nonce_2_value")
        if not 0 <= self.current_extra_nonce_2_value < space_size:
            raise MiningWorkProgressionValidationError(
                "current_extra_nonce_2_value is outside the negotiated space"
            )
        _validate_integer(self.variants_at_current_time, "variants_at_current_time")
        if not 1 <= self.variants_at_current_time <= space_size:
            raise MiningWorkProgressionValidationError(
                "variants_at_current_time is outside the negotiated cycle"
            )
        for name, value in (
            ("variant_index", self.variant_index),
            ("extra_nonce_2_advance_count", self.extra_nonce_2_advance_count),
            ("extra_nonce_2_cycle_count", self.extra_nonce_2_cycle_count),
            ("network_time_roll_count", self.network_time_roll_count),
        ):
            _validate_nonnegative_integer(value, name)
        _validate_integer(self.network_time_value, "network_time_value")
        if not 0 <= self.network_time_value <= _MAX_NETWORK_TIME:
            raise MiningWorkProgressionValidationError(
                "network_time_value must be between 0 and 0xffffffff"
            )
        pool_time = int(self.pool_job.network_time, 16)
        if self.network_time_value < pool_time:
            raise MiningWorkProgressionValidationError(
                "network_time_value cannot precede the pool job time"
            )
        if self.network_time_roll_count != self.network_time_value - pool_time:
            raise MiningWorkProgressionValidationError(
                "network_time_roll_count must match elapsed local seconds"
            )

    @classmethod
    def start(cls, job: MiningJob, starting_extra_nonce_2: str) -> MiningWorkCursor:
        """Create the first cursor state from pool work and one random seed."""

        if not isinstance(job, MiningJob):
            raise MiningWorkProgressionValidationError("job must be a MiningJob")
        _validate_extra_nonce_2(starting_extra_nonce_2, job.extra_nonce_2_size)
        return cls(
            pool_job=job,
            starting_extra_nonce_2=starting_extra_nonce_2,
            current_extra_nonce_2_value=int(starting_extra_nonce_2, 16),
            variants_at_current_time=1,
            variant_index=0,
            extra_nonce_2_advance_count=0,
            extra_nonce_2_cycle_count=0,
            network_time_value=int(job.network_time, 16),
            network_time_roll_count=0,
        )

    @property
    def extra_nonce_2_space_size(self) -> int:
        """Return the negotiated numeric space size without enumeration."""

        return 1 << (8 * self.pool_job.extra_nonce_2_size)

    @property
    def current_extra_nonce_2(self) -> str:
        """Return fixed-width lowercase hexadecimal for the current value."""

        width = self.pool_job.extra_nonce_2_size * 2
        return f"{self.current_extra_nonce_2_value:0{width}x}"

    @property
    def current_network_time(self) -> str:
        """Return pool representation initially and lowercase local rolled time."""

        if self.network_time_roll_count == 0:
            return self.pool_job.network_time
        return f"{self.network_time_value:08x}"

    @property
    def current_variant(self) -> MiningWorkVariant:
        """Return the immutable effective work variant at this cursor state."""

        effective_job = (
            self.pool_job
            if self.network_time_roll_count == 0
            else replace(self.pool_job, network_time=self.current_network_time)
        )
        return MiningWorkVariant(
            job=effective_job,
            extra_nonce_2=self.current_extra_nonce_2,
            variant_index=self.variant_index,
            extra_nonce_2_advance_count=self.extra_nonce_2_advance_count,
            extra_nonce_2_cycle_count=self.extra_nonce_2_cycle_count,
            network_time_roll_count=self.network_time_roll_count,
        )

    def advance(self) -> MiningWorkProgress:
        """Advance after a complete nonce search for the current variant."""

        space_size = self.extra_nonce_2_space_size
        if self.variants_at_current_time < space_size:
            successor = replace(
                self,
                current_extra_nonce_2_value=(self.current_extra_nonce_2_value + 1) % space_size,
                variants_at_current_time=self.variants_at_current_time + 1,
                variant_index=self.variant_index + 1,
                extra_nonce_2_advance_count=self.extra_nonce_2_advance_count + 1,
            )
            return MiningWorkProgress(
                cursor=successor,
                extra_nonce_2_advanced=True,
                extra_nonce_2_cycle_completed=False,
                network_time_rolled=False,
                progression_exhausted=False,
            )

        if self.network_time_value == _MAX_NETWORK_TIME:
            return MiningWorkProgress(
                cursor=None,
                extra_nonce_2_advanced=False,
                extra_nonce_2_cycle_completed=True,
                network_time_rolled=False,
                progression_exhausted=True,
            )

        successor = replace(
            self,
            current_extra_nonce_2_value=int(self.starting_extra_nonce_2, 16),
            variants_at_current_time=1,
            variant_index=self.variant_index + 1,
            extra_nonce_2_advance_count=self.extra_nonce_2_advance_count + 1,
            extra_nonce_2_cycle_count=self.extra_nonce_2_cycle_count + 1,
            network_time_value=self.network_time_value + 1,
            network_time_roll_count=self.network_time_roll_count + 1,
        )
        return MiningWorkProgress(
            cursor=successor,
            extra_nonce_2_advanced=True,
            extra_nonce_2_cycle_completed=True,
            network_time_rolled=True,
            progression_exhausted=False,
        )


def mining_job_context_identity(job: MiningJob) -> MiningJobContextIdentity:
    """Return a compact identity for pool work and its acceptance context."""

    if not isinstance(job, MiningJob):
        raise MiningWorkProgressionValidationError("job must be a MiningJob")
    return MiningJobContextIdentity(
        job_id=job.job_id,
        previous_block_hash=job.previous_block_hash,
        coinbase_part_1=job.coinbase_part_1,
        coinbase_part_2=job.coinbase_part_2,
        merkle_branches=job.merkle_branches,
        version=job.version,
        network_bits=job.network_bits,
        network_time=job.network_time,
        extra_nonce_1=job.extra_nonce_1,
        extra_nonce_2_size=job.extra_nonce_2_size,
        difficulty=job.difficulty,
    )


def mining_work_identity(work: PreparedMiningWork) -> MiningWorkIdentity:
    """Return the effective header and target identity of prepared work."""

    if not isinstance(work, PreparedMiningWork):
        raise MiningWorkProgressionValidationError("work must be PreparedMiningWork")
    return MiningWorkIdentity(
        job_id=work.job_id,
        header_prefix=work.header_prefix,
        network_target=work.network_target,
        share_target=work.share_target,
    )


def prepare_work_variant(
    variant: MiningWorkVariant,
    *,
    prepare_work: WorkPreparer = prepare_mining_work,
) -> PreparedMiningWork:
    """Prepare one effective variant through the deterministic primitive."""

    if not isinstance(variant, MiningWorkVariant):
        raise MiningWorkProgressionValidationError("variant must be a MiningWorkVariant")
    if not callable(prepare_work):
        raise MiningWorkProgressionValidationError("prepare_work must be callable")
    work = prepare_work(variant.job, variant.extra_nonce_2)
    if not isinstance(work, PreparedMiningWork):
        raise MiningWorkProgressionValidationError("prepare_work must return PreparedMiningWork")
    if work.extra_nonce_2 != variant.extra_nonce_2:
        raise MiningWorkProgressionError("prepared work did not preserve the variant extra nonce")
    if work.network_time != variant.job.network_time:
        raise MiningWorkProgressionError("prepared work did not preserve the variant network time")
    return work


def _validate_extra_nonce_2(value: object, byte_size: object) -> str:
    validated_byte_size = _validate_integer(byte_size, "extra_nonce_2_size")
    if validated_byte_size <= 0:
        raise MiningWorkProgressionValidationError("extra_nonce_2_size must be positive")
    if not isinstance(value, str):
        raise MiningWorkProgressionValidationError("extra_nonce_2 must be a string")
    if len(value) != validated_byte_size * 2:
        raise MiningWorkProgressionValidationError(
            "extra_nonce_2 must match the negotiated fixed width"
        )
    if any(character not in _LOWER_HEX_DIGITS for character in value):
        raise MiningWorkProgressionValidationError(
            "extra_nonce_2 must contain lowercase hexadecimal characters"
        )
    return value


def _validate_integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise MiningWorkProgressionValidationError(f"{name} must be an integer")
    return value


def _validate_nonnegative_integer(value: object, name: str) -> None:
    if _validate_integer(value, name) < 0:
        raise MiningWorkProgressionValidationError(f"{name} must be nonnegative")
