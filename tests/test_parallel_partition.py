"""Tests for deterministic parallel nonce-range partitioning."""

from dataclasses import FrozenInstanceError

import pytest

from hashorb.compute import (
    MAX_COMPUTE_WORKERS,
    ComputeBackendValidationError,
    NonceRangeAssignment,
    partition_nonce_range,
)


@pytest.mark.parametrize(
    ("start_nonce", "stop_nonce", "worker_count", "expected"),
    [
        (0, 8, 1, ((0, 8),)),
        (0, 8, 2, ((0, 4), (4, 8))),
        (0, 8, 4, ((0, 2), (2, 4), (4, 6), (6, 8))),
        (10, 13, 8, ((10, 11), (11, 12), (12, 13))),
        (7, 17, 3, ((7, 11), (11, 14), (14, 17))),
        (19, 20, 4, ((19, 20),)),
        (
            2**32 - 5,
            2**32,
            2,
            ((2**32 - 5, 2**32 - 2), (2**32 - 2, 2**32)),
        ),
    ],
)
def test_partition_nonce_range_has_exact_deterministic_assignments(
    start_nonce: int,
    stop_nonce: int,
    worker_count: int,
    expected: tuple[tuple[int, int], ...],
) -> None:
    first = partition_nonce_range(start_nonce, stop_nonce, worker_count)
    second = partition_nonce_range(start_nonce, stop_nonce, worker_count)

    assert first == second
    assert tuple((item.start_nonce, item.stop_nonce) for item in first) == expected
    assert len(first) <= worker_count
    assert first[0].start_nonce == start_nonce
    assert first[-1].stop_nonce == stop_nonce
    assert all(
        left.stop_nonce == right.start_nonce for left, right in zip(first, first[1:], strict=False)
    )
    assert all(item.size > 0 for item in first)
    assert max(item.size for item in first) - min(item.size for item in first) <= 1
    assert sum(item.size for item in first) == stop_nonce - start_nonce


def test_assignment_is_frozen_slotted_and_validates_direct_construction() -> None:
    assignment = NonceRangeAssignment(3, 5)

    assert assignment.size == 2
    assert not hasattr(assignment, "__dict__")
    with pytest.raises(FrozenInstanceError):
        assignment.start_nonce = 4  # type: ignore[misc]
    with pytest.raises(ComputeBackendValidationError):
        NonceRangeAssignment(5, 5)


@pytest.mark.parametrize(
    ("start_nonce", "stop_nonce"),
    [
        (True, 1),
        (0, False),
        (-1, 1),
        (1, 1),
        (2, 1),
        (0, 2**32 + 1),
    ],
)
def test_partition_rejects_invalid_parent_ranges(
    start_nonce: object,
    stop_nonce: object,
) -> None:
    with pytest.raises(ComputeBackendValidationError):
        partition_nonce_range(
            start_nonce,  # type: ignore[arg-type]
            stop_nonce,  # type: ignore[arg-type]
            2,
        )


@pytest.mark.parametrize(
    "worker_count",
    [True, False, 0, -1, MAX_COMPUTE_WORKERS + 1, 1.0, "2", None],
)
def test_partition_rejects_invalid_worker_counts(worker_count: object) -> None:
    with pytest.raises(ComputeBackendValidationError):
        partition_nonce_range(0, 4, worker_count)  # type: ignore[arg-type]
