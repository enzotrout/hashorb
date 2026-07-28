from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from hashphere.mining import (
    MiningSearchStrategy,
    OrbitingBitSearchCursor,
    OrbitingBitSearchStrategy,
    SearchAssignment,
    SearchStrategyCapabilities,
    SearchStrategyValidationError,
    calculate_orbiting_range_count,
    next_power_of_two,
    reverse_bits,
    validate_search_strategy_compatibility,
)

NONCE_LIMIT = 1 << 32


class BackendCapabilities:
    def __init__(self, name: str, *, parallel: bool = False) -> None:
        self.backend_name = name
        self.supports_parallel_search = parallel
        self.available = True


def collect(cursor: OrbitingBitSearchCursor) -> tuple[SearchAssignment, ...]:
    assignments: list[SearchAssignment] = []
    while True:
        assignment = cursor.next_assignment()
        if assignment is None:
            return tuple(assignments)
        assignments.append(assignment)


def reference_reverse_bits(value: int, width: int) -> int:
    result = 0
    for source_index in range(width):
        if value & (1 << source_index):
            result |= 1 << (width - source_index - 1)
    return result


def reference_indexes(range_count: int) -> tuple[int, ...]:
    permutation_size = 1 << (range_count - 1).bit_length()
    width = permutation_size.bit_length() - 1
    return tuple(
        index
        for counter in range(permutation_size)
        if (index := reference_reverse_bits(counter, width)) < range_count
    )


def test_orbiting_bit_capabilities_are_exact_and_immutable() -> None:
    capabilities = OrbitingBitSearchStrategy().capabilities

    assert capabilities == SearchStrategyCapabilities(
        strategy_name="orbiting-bit",
        display_name="Orbiting-bit parent-range permutation",
        implementation="bit-reversal",
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
    ("value", "width", "expected"),
    [
        (0, 0, 0),
        (0, 1, 0),
        (1, 1, 1),
        (0b001, 3, 0b100),
        (0b011, 3, 0b110),
        (0b1010, 4, 0b0101),
        (0x00000001, 32, 0x80000000),
        (0xFFFFFFFF, 32, 0xFFFFFFFF),
    ],
)
def test_reverse_bits_known_values(value: int, width: int, expected: int) -> None:
    assert reverse_bits(value, width) == expected


def test_reverse_bits_produces_known_three_and_four_bit_sequences() -> None:
    assert tuple(reverse_bits(value, 3) for value in range(8)) == (0, 4, 2, 6, 1, 5, 3, 7)
    assert tuple(reverse_bits(value, 4) for value in range(16)) == (
        0,
        8,
        4,
        12,
        2,
        10,
        6,
        14,
        1,
        9,
        5,
        13,
        3,
        11,
        7,
        15,
    )


@pytest.mark.parametrize(
    ("value", "width"),
    [
        (-1, 1),
        (0, -1),
        (2, 1),
        (1, 0),
        (0, 33),
        (True, 1),
        (0, False),
        (1.0, 1),
        (0, "1"),
        (None, 1),
    ],
)
def test_reverse_bits_rejects_invalid_values(value: object, width: object) -> None:
    with pytest.raises(SearchStrategyValidationError):
        reverse_bits(value, width)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1, 1),
        (2, 2),
        (3, 4),
        (4, 4),
        (5, 8),
        ((1 << 31) + 1, 1 << 32),
        (1 << 32, 1 << 32),
    ],
)
def test_next_power_of_two_uses_exact_integer_arithmetic(value: int, expected: int) -> None:
    assert next_power_of_two(value) == expected


@pytest.mark.parametrize("value", [True, False, -1, 0, (1 << 32) + 1, 1.5, "1", None])
def test_next_power_of_two_rejects_invalid_values(value: object) -> None:
    with pytest.raises(SearchStrategyValidationError):
        next_power_of_two(value)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("start_nonce", "chunk_size", "nonce_limit", "expected"),
    [
        (0, 10, 10, 1),
        (0, 10, 11, 2),
        (7, 3, 17, 4),
        (NONCE_LIMIT - 1, 1, NONCE_LIMIT, 1),
        (0, 1, NONCE_LIMIT, NONCE_LIMIT),
    ],
)
def test_calculate_orbiting_range_count_is_exact(
    start_nonce: int,
    chunk_size: int,
    nonce_limit: int,
    expected: int,
) -> None:
    assert (
        calculate_orbiting_range_count(
            start_nonce,
            chunk_size,
            nonce_limit=nonce_limit,
        )
        == expected
    )


def test_eight_range_order_is_exact_bit_reversal() -> None:
    assignments = collect(OrbitingBitSearchCursor(0, 10, nonce_limit=80))

    assert tuple(item.start_nonce // 10 for item in assignments) == (0, 4, 2, 6, 1, 5, 3, 7)
    assert tuple(item.assignment_index for item in assignments) == tuple(range(8))


def test_five_range_domain_skips_only_invalid_physical_indexes() -> None:
    cursor = OrbitingBitSearchCursor(0, 10, nonce_limit=50)

    assignments = collect(cursor)

    assert tuple(item.start_nonce // 10 for item in assignments) == (0, 4, 2, 1, 3)
    assert len(assignments) == 5
    assert cursor.permutation_size == 8
    assert cursor.permutation_counter == 8
    assert cursor.emitted_count == 5


@pytest.mark.parametrize("range_count", range(1, 65))
def test_complete_small_domains_match_independent_reference(range_count: int) -> None:
    chunk_size = 7
    assignments = collect(
        OrbitingBitSearchCursor(
            0,
            chunk_size,
            nonce_limit=range_count * chunk_size,
        )
    )
    physical_indexes = tuple(item.start_nonce // chunk_size for item in assignments)

    assert physical_indexes == reference_indexes(range_count)
    assert len(set(physical_indexes)) == range_count
    assert sorted(physical_indexes) == list(range(range_count))
    assert sum(item.size for item in assignments) == range_count * chunk_size
    assert all(item.size == chunk_size for item in assignments)


def test_nonzero_start_and_shortened_final_range_cover_exact_domain() -> None:
    start_nonce = 13
    nonce_limit = 37
    assignments = collect(OrbitingBitSearchCursor(start_nonce, 7, nonce_limit=nonce_limit))

    assert tuple((item.start_nonce, item.stop_nonce) for item in assignments) == (
        (13, 20),
        (27, 34),
        (20, 27),
        (34, 37),
    )
    covered = sorted(
        nonce for item in assignments for nonce in range(item.start_nonce, item.stop_nonce)
    )
    assert covered == list(range(start_nonce, nonce_limit))


@pytest.mark.parametrize(
    ("start_nonce", "chunk_size"),
    [
        (NONCE_LIMIT - 1, 1),
        (NONCE_LIMIT - 1, NONCE_LIMIT),
        (0, NONCE_LIMIT),
    ],
)
def test_single_range_boundary_emits_once_then_exhausts(start_nonce: int, chunk_size: int) -> None:
    cursor = OrbitingBitSearchCursor(start_nonce, chunk_size)

    assert collect(cursor) == (SearchAssignment(0, start_nonce, NONCE_LIMIT),)
    assert cursor.bit_width == 0
    assert cursor.exhausted
    assert cursor.next_assignment() is None
    assert cursor.next_assignment() is None


def test_large_domain_uses_bounded_arithmetic_state_without_history() -> None:
    cursor = OrbitingBitSearchCursor(0, 1)

    first = tuple(cursor.next_assignment() for _ in range(5))

    assert tuple(item.start_nonce for item in first if item is not None) == (
        0,
        1 << 31,
        1 << 30,
        3 << 30,
        1 << 29,
    )
    assert cursor.range_count == NONCE_LIMIT
    assert cursor.permutation_size == NONCE_LIMIT
    assert set(OrbitingBitSearchCursor.__slots__) == {
        "_assignment_index",
        "_bit_width",
        "_chunk_size",
        "_emitted_count",
        "_nonce_limit",
        "_permutation_counter",
        "_permutation_size",
        "_range_count",
        "_start_nonce",
    }


def test_cursor_is_deterministic_and_does_not_mutate_inputs() -> None:
    parameters = (11, 9, 65)
    first = OrbitingBitSearchCursor(parameters[0], parameters[1], nonce_limit=parameters[2])
    second = OrbitingBitSearchCursor(parameters[0], parameters[1], nonce_limit=parameters[2])

    assert collect(first) == collect(second)
    assert parameters == (11, 9, 65)


@pytest.mark.parametrize("value", [True, False, -1, NONCE_LIMIT, 1.5, "0", None])
def test_invalid_cursor_start_is_rejected(value: object) -> None:
    with pytest.raises(SearchStrategyValidationError):
        OrbitingBitSearchCursor(value, 1)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [True, False, -1, 0, NONCE_LIMIT + 1, 1.5, "1", None])
def test_invalid_cursor_chunk_size_is_rejected(value: object) -> None:
    with pytest.raises(SearchStrategyValidationError):
        OrbitingBitSearchCursor(0, value)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [True, False, -1, 0, NONCE_LIMIT + 1, 1.5, "1", None])
def test_invalid_cursor_nonce_limit_is_rejected(value: object) -> None:
    with pytest.raises(SearchStrategyValidationError):
        OrbitingBitSearchCursor(0, 1, nonce_limit=value)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "capabilities",
    [
        BackendCapabilities("python"),
        BackendCapabilities("native"),
        BackendCapabilities("native-parallel", parallel=True),
    ],
)
def test_orbiting_bit_is_compatible_with_every_current_backend(
    capabilities: BackendCapabilities,
) -> None:
    strategy: MiningSearchStrategy = OrbitingBitSearchStrategy()

    validate_search_strategy_compatibility(strategy, capabilities)
