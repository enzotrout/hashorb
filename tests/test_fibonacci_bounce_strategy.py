"""Contract and coverage tests for the experimental Fibonacci-bounce strategy."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from math import gcd

import pytest

from hashorb.mining import (
    FibonacciBounceSearchCursor,
    FibonacciBounceSearchStrategy,
    SearchAssignment,
    SearchStrategyCapabilities,
    SearchStrategyValidationError,
    fibonacci_bounce_offset,
    fibonacci_coprime_stride,
    validate_search_strategy_compatibility,
)

NONCE_LIMIT = 1 << 32


class BackendCapabilities:
    def __init__(
        self,
        backend_name: str,
        *,
        parallel: bool,
        available: bool = True,
    ) -> None:
        self.backend_name = backend_name
        self.supports_parallel_search = parallel
        self.available = available


def collect(cursor: FibonacciBounceSearchCursor) -> tuple[SearchAssignment, ...]:
    assignments: list[SearchAssignment] = []
    while True:
        assignment = cursor.next_assignment()
        if assignment is None:
            return tuple(assignments)
        assignments.append(assignment)


def physical_indexes(
    cursor: FibonacciBounceSearchCursor,
    assignments: tuple[SearchAssignment, ...],
) -> tuple[int, ...]:
    return tuple(
        (assignment.start_nonce - cursor.start_nonce) // cursor.chunk_size
        for assignment in assignments
    )


def test_capabilities_are_exact_and_immutable() -> None:
    capabilities = FibonacciBounceSearchStrategy().capabilities

    assert capabilities == SearchStrategyCapabilities(
        strategy_name="fibonacci-bounce",
        display_name="Fibonacci coprime-stride bounce permutation",
        implementation="fibonacci-bounce",
        deterministic=True,
        contiguous_parent_ranges=False,
        exhaustive=True,
        may_repeat_nonce=False,
        supports_parallel_backends=True,
        experimental=True,
        available=True,
    )
    with pytest.raises(FrozenInstanceError):
        capabilities.experimental = False  # type: ignore[misc]


@pytest.mark.parametrize(
    ("range_count", "expected_stride"),
    [
        (1, 1),
        (2, 1),
        (3, 2),
        (4, 3),
        (5, 3),
        (6, 5),
        (8, 5),
        (9, 8),
        (10, 3),
        (12, 5),
    ],
)
def test_fibonacci_stride_is_largest_available_coprime(
    range_count: int,
    expected_stride: int,
) -> None:
    selected = fibonacci_coprime_stride(range_count)

    assert selected == expected_stride
    assert gcd(selected, range_count) == 1


def test_bounce_offsets_alternate_around_the_origin() -> None:
    assert tuple(fibonacci_bounce_offset(index) for index in range(10)) == (
        0,
        1,
        -1,
        2,
        -2,
        3,
        -3,
        4,
        -4,
        5,
    )


def test_eight_range_order_is_documented_fibonacci_bounce() -> None:
    cursor = FibonacciBounceSearchCursor(0, 10, nonce_limit=80)
    assignments = collect(cursor)

    assert cursor.fibonacci_stride == 5
    assert physical_indexes(cursor, assignments) == (0, 5, 3, 2, 6, 7, 1, 4)
    assert tuple(item.assignment_index for item in assignments) == tuple(range(8))
    assert cursor.exhausted
    assert cursor.next_assignment() is None
    assert cursor.next_assignment() is None


def test_nine_range_order_bounces_between_far_edges() -> None:
    cursor = FibonacciBounceSearchCursor(0, 1, nonce_limit=9)

    assert cursor.fibonacci_stride == 8
    assert physical_indexes(cursor, collect(cursor)) == (0, 8, 1, 7, 2, 6, 3, 5, 4)


def test_partial_final_parent_range_is_preserved_exactly() -> None:
    cursor = FibonacciBounceSearchCursor(7, 10, nonce_limit=42)
    assignments = collect(cursor)

    assert cursor.range_count == 4
    assert cursor.fibonacci_stride == 3
    assert physical_indexes(cursor, assignments) == (0, 3, 1, 2)
    assert {(item.start_nonce, item.stop_nonce) for item in assignments} == {
        (7, 17),
        (17, 27),
        (27, 37),
        (37, 42),
    }
    assert sum(item.size for item in assignments) == 35


@pytest.mark.parametrize("range_count", list(range(1, 258)))
def test_every_small_domain_is_a_complete_duplicate_free_permutation(
    range_count: int,
) -> None:
    cursor = FibonacciBounceSearchCursor(0, 1, nonce_limit=range_count)
    assignments = collect(cursor)
    indexes = physical_indexes(cursor, assignments)

    assert len(indexes) == range_count
    assert len(set(indexes)) == range_count
    assert sorted(indexes) == list(range(range_count))
    assert sum(item.size for item in assignments) == range_count


def test_nonzero_start_and_uneven_chunks_cover_exact_nonce_domain() -> None:
    start_nonce = 23
    nonce_limit = 117
    cursor = FibonacciBounceSearchCursor(start_nonce, 17, nonce_limit=nonce_limit)
    assignments = collect(cursor)

    covered: list[int] = []
    for assignment in assignments:
        covered.extend(range(assignment.start_nonce, assignment.stop_nonce))

    assert len(covered) == nonce_limit - start_nonce
    assert len(set(covered)) == nonce_limit - start_nonce
    assert sorted(covered) == list(range(start_nonce, nonce_limit))


def test_cursor_is_deterministic_and_compact() -> None:
    first = FibonacciBounceSearchCursor(11, 9, nonce_limit=401)
    second = FibonacciBounceSearchCursor(11, 9, nonce_limit=401)

    assert collect(first) == collect(second)
    assert set(FibonacciBounceSearchCursor.__slots__) == {
        "_assignment_index",
        "_chunk_size",
        "_emitted_count",
        "_fibonacci_stride",
        "_nonce_limit",
        "_range_count",
        "_start_nonce",
    }


@pytest.mark.parametrize(
    "capabilities",
    [
        BackendCapabilities("python", parallel=False),
        BackendCapabilities("native", parallel=False),
        BackendCapabilities("native-parallel", parallel=True),
        BackendCapabilities("cuda", parallel=True),
        BackendCapabilities("cuda-multi", parallel=True),
    ],
)
def test_strategy_is_compatible_with_every_current_available_backend(
    capabilities: BackendCapabilities,
) -> None:
    validate_search_strategy_compatibility(
        FibonacciBounceSearchStrategy(),
        capabilities,
    )


def test_unavailable_backend_is_rejected() -> None:
    with pytest.raises(Exception, match="incompatible"):
        validate_search_strategy_compatibility(
            FibonacciBounceSearchStrategy(),
            BackendCapabilities("cuda", parallel=True, available=False),
        )


@pytest.mark.parametrize("value", [True, False, 0, -1, NONCE_LIMIT + 1, 1.5, "8", None])
def test_invalid_range_count_is_rejected(value: object) -> None:
    with pytest.raises(SearchStrategyValidationError):
        fibonacci_coprime_stride(value)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [True, False, -1, 1.5, "0", None])
def test_invalid_bounce_index_is_rejected(value: object) -> None:
    with pytest.raises(SearchStrategyValidationError):
        fibonacci_bounce_offset(value)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [True, False, -1, NONCE_LIMIT, 1.5, "0", None])
def test_invalid_start_nonce_is_rejected(value: object) -> None:
    with pytest.raises(SearchStrategyValidationError):
        FibonacciBounceSearchCursor(value, 1)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [True, False, -1, 0, NONCE_LIMIT + 1, 1.5, "1", None])
def test_invalid_chunk_size_is_rejected(value: object) -> None:
    with pytest.raises(SearchStrategyValidationError):
        FibonacciBounceSearchCursor(0, value)  # type: ignore[arg-type]


def test_nonce_limit_must_follow_start_nonce() -> None:
    with pytest.raises(SearchStrategyValidationError, match="greater"):
        FibonacciBounceSearchCursor(10, 1, nonce_limit=10)
