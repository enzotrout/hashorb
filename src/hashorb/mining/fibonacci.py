"""Deterministic exhaustive Fibonacci-bounce parent-range ordering.

The historical HashOrb experiments used a Fibonacci recurrence directly in the
nonce domain and periodically reseeded it.  That produced an interesting path,
but it could revisit work and did not prove exhaustive coverage.  The current
search-strategy contract is stricter: a strategy should reorder ordinary parent
ranges without changing Bitcoin work or silently repeating hashes.

``fibonacci-bounce`` keeps the useful visual idea while making it a true finite
permutation.  It chooses the largest Fibonacci number below the parent-range
count that is coprime to that count.  Emission offsets then bounce around zero:
``0, +1, -1, +2, -2, ...``.  Multiplication by the coprime Fibonacci stride
maps those offsets bijectively onto every physical parent-range index.
"""

from __future__ import annotations

from math import gcd

from hashorb.mining.strategy import (
    SearchAssignment,
    SearchBackendCapabilities,
    SearchStrategyCapabilities,
    SearchStrategyExecutionError,
    SearchStrategyValidationError,
    SequentialSearchStrategy,
    calculate_orbiting_range_count,
)

_NONCE_LIMIT = 1 << 32

_FIBONACCI_BOUNCE_CAPABILITIES = SearchStrategyCapabilities(
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


def fibonacci_coprime_stride(range_count: int) -> int:
    """Return the largest Fibonacci number below ``range_count`` coprime to it.

    ``1`` is Fibonacci and is coprime to every positive integer, so a valid
    stride always exists.  The selected stride is deterministic and requires no
    random seed, clock state, or prepared-work data.
    """

    if isinstance(range_count, bool) or not isinstance(range_count, int):
        raise SearchStrategyValidationError("range_count must be an integer")
    if range_count <= 0:
        raise SearchStrategyValidationError("range_count must be positive")
    if range_count > _NONCE_LIMIT:
        raise SearchStrategyValidationError("range_count must not exceed 2**32")
    if range_count == 1:
        return 1

    candidates = [1]
    previous, current = 1, 2
    while current < range_count:
        candidates.append(current)
        previous, current = current, previous + current

    for candidate in reversed(candidates):
        if gcd(candidate, range_count) == 1:
            return candidate

    raise SearchStrategyExecutionError("fibonacci-bounce could not select a coprime stride")


def fibonacci_bounce_offset(assignment_index: int) -> int:
    """Return the signed bounce offset for one zero-based emission index.

    The sequence is ``0, +1, -1, +2, -2, +3, -3, ...``.  For any finite
    domain of size ``N``, the first ``N`` offsets are distinct modulo ``N``.
    """

    if isinstance(assignment_index, bool) or not isinstance(assignment_index, int):
        raise SearchStrategyValidationError("assignment_index must be an integer")
    if assignment_index < 0:
        raise SearchStrategyValidationError("assignment_index must be nonnegative")
    if assignment_index == 0:
        return 0
    magnitude = (assignment_index + 1) // 2
    return magnitude if assignment_index % 2 else -magnitude


class FibonacciBounceSearchCursor:
    """Compact cursor emitting a duplicate-free Fibonacci bounce permutation."""

    __slots__ = (
        "_assignment_index",
        "_chunk_size",
        "_emitted_count",
        "_fibonacci_stride",
        "_nonce_limit",
        "_range_count",
        "_start_nonce",
    )

    def __init__(
        self,
        start_nonce: int,
        chunk_size: int,
        *,
        nonce_limit: int = _NONCE_LIMIT,
    ) -> None:
        """Create a finite permutation cursor for one effective work variant."""

        range_count = calculate_orbiting_range_count(
            start_nonce,
            chunk_size,
            nonce_limit=nonce_limit,
        )
        self._start_nonce = start_nonce
        self._chunk_size = chunk_size
        self._nonce_limit = nonce_limit
        self._range_count = range_count
        self._fibonacci_stride = fibonacci_coprime_stride(range_count)
        self._assignment_index = 0
        self._emitted_count = 0

    @property
    def start_nonce(self) -> int:
        """Return the inclusive nonce-domain start."""

        return self._start_nonce

    @property
    def chunk_size(self) -> int:
        """Return the maximum size of one emitted parent range."""

        return self._chunk_size

    @property
    def nonce_limit(self) -> int:
        """Return the exclusive nonce-domain limit."""

        return self._nonce_limit

    @property
    def range_count(self) -> int:
        """Return the exact number of physical parent ranges."""

        return self._range_count

    @property
    def fibonacci_stride(self) -> int:
        """Return the selected coprime Fibonacci stride."""

        return self._fibonacci_stride

    @property
    def assignment_index(self) -> int:
        """Return the zero-based index of the next emitted assignment."""

        return self._assignment_index

    @property
    def emitted_count(self) -> int:
        """Return the number of assignments already emitted."""

        return self._emitted_count

    @property
    def exhausted(self) -> bool:
        """Return whether every physical parent range has been emitted once."""

        return self._assignment_index == self._range_count

    def next_assignment(self) -> SearchAssignment | None:
        """Return the next physical parent range in Fibonacci-bounce order."""

        if self.exhausted:
            if self._emitted_count != self._range_count:
                raise SearchStrategyExecutionError(
                    "fibonacci-bounce exhausted before complete coverage"
                )
            return None

        assignment_index = self._assignment_index
        offset = fibonacci_bounce_offset(assignment_index)
        physical_range_index = (offset * self._fibonacci_stride) % self._range_count
        start_nonce = self._start_nonce + physical_range_index * self._chunk_size
        stop_nonce = min(start_nonce + self._chunk_size, self._nonce_limit)
        if not self._start_nonce <= start_nonce < stop_nonce <= self._nonce_limit:
            raise SearchStrategyExecutionError("fibonacci-bounce produced an invalid parent range")

        assignment = SearchAssignment(
            assignment_index=assignment_index,
            start_nonce=start_nonce,
            stop_nonce=stop_nonce,
        )
        self._assignment_index += 1
        self._emitted_count += 1
        if self._emitted_count > self._range_count:
            raise SearchStrategyExecutionError("fibonacci-bounce emitted too many assignments")
        return assignment


class FibonacciBounceSearchStrategy:
    """Stateless exhaustive Fibonacci-stride bounce ordering policy."""

    __slots__ = ()

    @property
    def capabilities(self) -> SearchStrategyCapabilities:
        """Return immutable experimental strategy metadata."""

        return _FIBONACCI_BOUNCE_CAPABILITIES

    def create_cursor(
        self,
        start_nonce: int,
        chunk_size: int,
        *,
        nonce_limit: int = _NONCE_LIMIT,
    ) -> FibonacciBounceSearchCursor:
        """Create fresh Fibonacci-bounce state for one effective work variant."""

        return FibonacciBounceSearchCursor(
            start_nonce,
            chunk_size,
            nonce_limit=nonce_limit,
        )

    def supports_backend(self, capabilities: SearchBackendCapabilities) -> bool:
        """Support every available backend accepted by the reference strategy."""

        return SequentialSearchStrategy().supports_backend(capabilities)
