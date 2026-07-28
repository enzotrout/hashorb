"""Deterministic range partitioning for parallel compute backends."""

from __future__ import annotations

from dataclasses import dataclass

from hashphere.compute.backend import ComputeBackendValidationError
from hashphere.mining.search import _validate_nonce_range

DEFAULT_COMPUTE_WORKERS = 2
MAX_COMPUTE_WORKERS = 256


@dataclass(frozen=True, slots=True)
class NonceRangeAssignment:
    """One immutable nonempty half-open worker nonce range."""

    start_nonce: int
    stop_nonce: int

    def __post_init__(self) -> None:
        """Validate direct construction through the established nonce boundary."""

        _validate_range(self.start_nonce, self.stop_nonce)

    @property
    def size(self) -> int:
        """Return the exact number of nonces in this assignment."""

        return self.stop_nonce - self.start_nonce


def partition_nonce_range(
    start_nonce: int,
    stop_nonce: int,
    worker_count: int,
) -> tuple[NonceRangeAssignment, ...]:
    """Partition one half-open nonce range into balanced ascending assignments."""

    _validate_range(start_nonce, stop_nonce)
    _validate_worker_count(worker_count)

    range_size = stop_nonce - start_nonce
    assignment_count = min(worker_count, range_size)
    base_size, larger_assignments = divmod(range_size, assignment_count)
    assignments: list[NonceRangeAssignment] = []
    assignment_start = start_nonce
    for index in range(assignment_count):
        assignment_size = base_size + (1 if index < larger_assignments else 0)
        assignment_stop = assignment_start + assignment_size
        assignments.append(NonceRangeAssignment(assignment_start, assignment_stop))
        assignment_start = assignment_stop

    return tuple(assignments)


def _validate_range(start_nonce: object, stop_nonce: object) -> None:
    try:
        _validate_nonce_range(start_nonce, stop_nonce)
    except (TypeError, ValueError) as exc:
        raise ComputeBackendValidationError("parallel nonce range is invalid") from exc


def _validate_worker_count(worker_count: object) -> None:
    if isinstance(worker_count, bool) or not isinstance(worker_count, int):
        raise ComputeBackendValidationError("worker_count must be an integer")
    if not 1 <= worker_count <= MAX_COMPUTE_WORKERS:
        raise ComputeBackendValidationError(
            f"worker_count must be between 1 and {MAX_COMPUTE_WORKERS}"
        )
