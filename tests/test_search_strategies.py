from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest

from hashorb.mining import (
    FibonacciBounceSearchStrategy,
    MiningSearchStrategy,
    OrbitingBitSearchStrategy,
    SearchAssignment,
    SearchStrategyCapabilities,
    SearchStrategyCompatibilityError,
    SearchStrategyRegistry,
    SearchStrategySelectionError,
    SearchStrategyValidationError,
    SequentialSearchCursor,
    SequentialSearchStrategy,
    builtin_search_strategy_registry,
    list_search_strategies,
    select_search_strategy,
    validate_search_strategy_compatibility,
)

NONCE_LIMIT = 1 << 32


class BackendCapabilities:
    def __init__(
        self,
        backend_name: str = "python",
        *,
        parallel: bool = False,
        available: bool = True,
    ) -> None:
        self.backend_name = backend_name
        self.supports_parallel_search = parallel
        self.available = available


class IncompatibleStrategy(SequentialSearchStrategy):
    def supports_backend(self, capabilities: object) -> bool:
        del capabilities
        return False


def collect(cursor: SequentialSearchCursor) -> tuple[SearchAssignment, ...]:
    assignments: list[SearchAssignment] = []
    while True:
        assignment = cursor.next_assignment()
        if assignment is None:
            return tuple(assignments)
        assignments.append(assignment)


def test_sequential_capabilities_are_exact_and_immutable() -> None:
    capabilities = SequentialSearchStrategy().capabilities

    assert capabilities == SearchStrategyCapabilities(
        strategy_name="sequential",
        display_name="Ascending sequential parent ranges",
        implementation="sequential",
        deterministic=True,
        contiguous_parent_ranges=True,
        exhaustive=True,
        may_repeat_nonce=False,
        supports_parallel_backends=True,
        experimental=False,
        available=True,
    )
    with pytest.raises(FrozenInstanceError):
        capabilities.experimental = True  # type: ignore[misc]


@pytest.mark.parametrize(
    ("start_nonce", "chunk_size", "nonce_limit", "expected"),
    [
        (0, 100_000, 300_000, ((0, 100_000), (100_000, 200_000), (200_000, 300_000))),
        (7, 3, 14, ((7, 10), (10, 13), (13, 14))),
        (0, 1, 3, ((0, 1), (1, 2), (2, 3))),
        (100, 1_000, 105, ((100, 105),)),
        (NONCE_LIMIT - 1, 100_000, NONCE_LIMIT, ((NONCE_LIMIT - 1, NONCE_LIMIT),)),
    ],
)
def test_sequential_cursor_emits_exact_ranges(
    start_nonce: int,
    chunk_size: int,
    nonce_limit: int,
    expected: tuple[tuple[int, int], ...],
) -> None:
    cursor = SequentialSearchStrategy().create_cursor(
        start_nonce,
        chunk_size,
        nonce_limit=nonce_limit,
    )

    assignments = collect(cursor)

    assert tuple((item.start_nonce, item.stop_nonce) for item in assignments) == expected
    assert tuple(item.assignment_index for item in assignments) == tuple(range(len(expected)))
    assert cursor.exhausted
    assert cursor.next_nonce == nonce_limit
    assert cursor.next_assignment() is None
    assert cursor.next_assignment() is None


def test_default_cursor_exhausts_at_exact_unsigned_nonce_boundary() -> None:
    cursor = SequentialSearchCursor(NONCE_LIMIT - 3, 2)

    assert collect(cursor) == (
        SearchAssignment(0, NONCE_LIMIT - 3, NONCE_LIMIT - 1),
        SearchAssignment(1, NONCE_LIMIT - 1, NONCE_LIMIT),
    )


def test_assignments_are_contiguous_nonoverlapping_and_complete() -> None:
    assignments = collect(SequentialSearchCursor(23, 17, nonce_limit=117))

    assert assignments[0].start_nonce == 23
    assert assignments[-1].stop_nonce == 117
    assert all(
        left.stop_nonce == right.start_nonce
        for left, right in zip(assignments, assignments[1:], strict=False)
    )
    assert all(0 < item.size <= 17 for item in assignments)
    assert sum(item.size for item in assignments) == 117 - 23


def test_cursor_is_deterministic_and_retains_no_prepared_work() -> None:
    first = SequentialSearchCursor(11, 9, nonce_limit=65)
    second = SequentialSearchCursor(11, 9, nonce_limit=65)

    assert collect(first) == collect(second)
    assert set(SequentialSearchCursor.__slots__) == {
        "_assignment_index",
        "_chunk_size",
        "_next_nonce",
        "_nonce_limit",
        "_start_nonce",
    }


@pytest.mark.parametrize("value", [True, False, -1, NONCE_LIMIT, 1.5, "0", None])
def test_invalid_start_nonce_is_rejected(value: object) -> None:
    with pytest.raises(SearchStrategyValidationError):
        SequentialSearchCursor(value, 1)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [True, False, -1, 0, NONCE_LIMIT + 1, 1.5, "1", None])
def test_invalid_chunk_size_is_rejected(value: object) -> None:
    with pytest.raises(SearchStrategyValidationError):
        SequentialSearchCursor(0, value)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [True, False, -1, 0, NONCE_LIMIT + 1, 1.5, "1", None])
def test_invalid_nonce_limit_is_rejected(value: object) -> None:
    with pytest.raises(SearchStrategyValidationError):
        SequentialSearchCursor(0, 1, nonce_limit=value)  # type: ignore[arg-type]


def test_nonce_limit_must_follow_start_nonce() -> None:
    with pytest.raises(SearchStrategyValidationError, match="greater"):
        SequentialSearchCursor(10, 1, nonce_limit=10)


@pytest.mark.parametrize(
    "assignment",
    [
        (-1, 0, 1),
        (True, 0, 1),
        (0, True, 1),
        (0, -1, 1),
        (0, 0, 0),
        (0, 1, 1),
        (0, 0, NONCE_LIMIT + 1),
    ],
)
def test_direct_assignment_validation(assignment: tuple[object, object, object]) -> None:
    with pytest.raises(SearchStrategyValidationError):
        SearchAssignment(*assignment)  # type: ignore[arg-type]


def test_builtin_registry_is_deterministic_and_isolated() -> None:
    first = builtin_search_strategy_registry()
    second = builtin_search_strategy_registry()

    assert first is not second
    assert first.list_capabilities() == second.list_capabilities()
    assert tuple(item.strategy_name for item in list_search_strategies(first)) == (
        "fibonacci-bounce",
        "orbiting-bit",
        "sequential",
    )
    assert isinstance(first.select("fibonacci-bounce"), FibonacciBounceSearchStrategy)
    assert isinstance(first.select("orbiting-bit"), OrbitingBitSearchStrategy)
    assert isinstance(first.select("sequential"), SequentialSearchStrategy)
    assert isinstance(select_search_strategy("fibonacci-bounce", first), FibonacciBounceSearchStrategy)
    assert isinstance(select_search_strategy("auto", first), SequentialSearchStrategy)


@pytest.mark.parametrize("name", ["missing", "Sequential", " sequential", "", True, None])
def test_registry_rejects_unknown_or_malformed_selection(name: object) -> None:
    with pytest.raises(SearchStrategySelectionError):
        builtin_search_strategy_registry().select(name)  # type: ignore[arg-type]


def test_registry_rejects_duplicate_names() -> None:
    with pytest.raises(SearchStrategyValidationError, match="unique"):
        SearchStrategyRegistry((SequentialSearchStrategy(), SequentialSearchStrategy()))


def test_registry_rejects_unavailable_selection() -> None:
    unavailable = replace(
        SequentialSearchStrategy().capabilities,
        available=False,
        unavailable_reason="NotInstalled",
    )

    class UnavailableSequential:
        capabilities = unavailable

        def create_cursor(
            self,
            start_nonce: int,
            chunk_size: int,
            *,
            nonce_limit: int = NONCE_LIMIT,
        ) -> SequentialSearchCursor:
            return SequentialSearchCursor(start_nonce, chunk_size, nonce_limit=nonce_limit)

        def supports_backend(self, capabilities: object) -> bool:
            del capabilities
            return True

    registry = SearchStrategyRegistry((UnavailableSequential(),))

    with pytest.raises(SearchStrategySelectionError, match="unavailable"):
        registry.select("sequential")


@pytest.mark.parametrize(
    "capabilities",
    [
        BackendCapabilities("python"),
        BackendCapabilities("native"),
        BackendCapabilities("native-parallel", parallel=True),
        BackendCapabilities("cuda", parallel=True),
    ],
)
def test_sequential_is_compatible_with_every_current_backend(
    capabilities: BackendCapabilities,
) -> None:
    strategy: MiningSearchStrategy = SequentialSearchStrategy()

    validate_search_strategy_compatibility(strategy, capabilities)


def test_incompatible_pair_is_rejected_without_fallback() -> None:
    with pytest.raises(SearchStrategyCompatibilityError, match="incompatible"):
        validate_search_strategy_compatibility(
            IncompatibleStrategy(),
            BackendCapabilities("future"),
        )


def test_unavailable_backend_is_incompatible() -> None:
    with pytest.raises(SearchStrategyCompatibilityError, match="incompatible"):
        validate_search_strategy_compatibility(
            SequentialSearchStrategy(),
            BackendCapabilities(available=False),
        )
