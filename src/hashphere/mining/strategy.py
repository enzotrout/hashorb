"""Deterministic parent-range search strategies for prepared mining work."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

_NONCE_LIMIT = 1 << 32
_MAX_NONCE = _NONCE_LIMIT - 1
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_-]*$")
_CATEGORY = re.compile(r"^[A-Z][A-Za-z0-9]*$")


class SearchStrategyError(RuntimeError):
    """Base error for search-strategy selection and execution."""


class SearchStrategyValidationError(SearchStrategyError, ValueError):
    """Raised when a search-strategy contract receives invalid data."""


class SearchStrategySelectionError(SearchStrategyError):
    """Raised when a configured search strategy cannot be selected."""


class SearchStrategyCompatibilityError(SearchStrategyError):
    """Raised when a strategy cannot schedule work for a selected backend."""


class SearchStrategyExecutionError(SearchStrategyError):
    """Raised when a selected strategy cannot produce a valid assignment."""


@dataclass(frozen=True, slots=True)
class SearchStrategyCapabilities:
    """Immutable low-cardinality identity and behavior for one strategy."""

    strategy_name: str
    display_name: str
    implementation: str
    deterministic: bool
    contiguous_parent_ranges: bool
    exhaustive: bool
    may_repeat_nonce: bool
    supports_parallel_backends: bool
    experimental: bool
    available: bool
    unavailable_reason: str | None = None

    def __post_init__(self) -> None:
        """Validate stable identifiers, Boolean flags, and availability."""

        identity_fields: tuple[tuple[str, object], ...] = (
            ("strategy_name", self.strategy_name),
            ("implementation", self.implementation),
        )
        for name, value in identity_fields:
            if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
                raise SearchStrategyValidationError(
                    f"{name} must be a lowercase strategy identifier"
                )
        if not isinstance(self.display_name, str) or not self.display_name.strip():
            raise SearchStrategyValidationError("display_name must be a nonblank string")
        boolean_fields: tuple[tuple[str, object], ...] = (
            ("deterministic", self.deterministic),
            ("contiguous_parent_ranges", self.contiguous_parent_ranges),
            ("exhaustive", self.exhaustive),
            ("may_repeat_nonce", self.may_repeat_nonce),
            ("supports_parallel_backends", self.supports_parallel_backends),
            ("experimental", self.experimental),
            ("available", self.available),
        )
        for name, value in boolean_fields:
            if not isinstance(value, bool):
                raise SearchStrategyValidationError(f"{name} must be a Boolean")
        if self.available:
            if self.unavailable_reason is not None:
                raise SearchStrategyValidationError(
                    "available strategies cannot have an unavailable_reason"
                )
        elif (
            not isinstance(self.unavailable_reason, str)
            or _CATEGORY.fullmatch(self.unavailable_reason) is None
        ):
            raise SearchStrategyValidationError(
                "unavailable strategies require a controlled reason category"
            )


@dataclass(frozen=True, slots=True)
class SearchAssignment:
    """One immutable nonempty half-open parent nonce range."""

    assignment_index: int
    start_nonce: int
    stop_nonce: int

    def __post_init__(self) -> None:
        """Validate sequence identity and unsigned nonce bounds."""

        _validate_nonnegative_integer(self.assignment_index, "assignment_index")
        _validate_nonce(self.start_nonce, "start_nonce")
        _validate_stop_nonce(self.stop_nonce, "stop_nonce")
        if self.start_nonce >= self.stop_nonce:
            raise SearchStrategyValidationError("assignment range must be nonempty")

    @property
    def size(self) -> int:
        """Return the exact number of nonces in the assignment."""

        return self.stop_nonce - self.start_nonce


@runtime_checkable
class SearchStrategyCursor(Protocol):
    """Narrow state boundary that yields parent assignments for one work variant."""

    @property
    def exhausted(self) -> bool:
        """Return whether no assignment remains."""

    def next_assignment(self) -> SearchAssignment | None:
        """Return the next assignment, or ``None`` after exhaustion."""


@runtime_checkable
class SearchBackendCapabilities(Protocol):
    """Compute capability subset used for strategy compatibility checks."""

    @property
    def backend_name(self) -> str:
        """Return the stable compute-backend name."""

    @property
    def supports_parallel_search(self) -> bool:
        """Return whether the backend internally searches in parallel."""

    @property
    def available(self) -> bool:
        """Return whether the backend can execute in this process."""


@runtime_checkable
class MiningSearchStrategy(Protocol):
    """Policy selecting parent nonce ranges independently of hashing."""

    @property
    def capabilities(self) -> SearchStrategyCapabilities:
        """Return immutable identity and behavior metadata."""

    def create_cursor(
        self,
        start_nonce: int,
        chunk_size: int,
        *,
        nonce_limit: int = _NONCE_LIMIT,
    ) -> SearchStrategyCursor:
        """Create fresh strategy-local state for one effective work variant."""

    def supports_backend(self, capabilities: SearchBackendCapabilities) -> bool:
        """Return whether this strategy can schedule the selected backend."""


_SEQUENTIAL_CAPABILITIES = SearchStrategyCapabilities(
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


class SequentialSearchCursor:
    """Narrow mutable cursor yielding contiguous ascending parent ranges."""

    __slots__ = (
        "_assignment_index",
        "_chunk_size",
        "_next_nonce",
        "_nonce_limit",
        "_start_nonce",
    )

    def __init__(
        self,
        start_nonce: int,
        chunk_size: int,
        *,
        nonce_limit: int = _NONCE_LIMIT,
    ) -> None:
        """Create a cursor bounded by an exclusive nonce limit."""

        _validate_nonce(start_nonce, "start_nonce")
        _validate_positive_integer(chunk_size, "chunk_size")
        if chunk_size > _NONCE_LIMIT:
            raise SearchStrategyValidationError("chunk_size must not exceed 2**32")
        _validate_stop_nonce(nonce_limit, "nonce_limit")
        if start_nonce >= nonce_limit:
            raise SearchStrategyValidationError("nonce_limit must be greater than start_nonce")
        self._start_nonce = start_nonce
        self._chunk_size = chunk_size
        self._nonce_limit = nonce_limit
        self._next_nonce = start_nonce
        self._assignment_index = 0

    @property
    def start_nonce(self) -> int:
        """Return the configured inclusive start nonce."""

        return self._start_nonce

    @property
    def chunk_size(self) -> int:
        """Return the maximum number of nonces in a normal assignment."""

        return self._chunk_size

    @property
    def next_nonce(self) -> int:
        """Return the next logical nonce position."""

        return self._next_nonce

    @property
    def assignment_index(self) -> int:
        """Return the index that will identify the next assignment."""

        return self._assignment_index

    @property
    def exhausted(self) -> bool:
        """Return whether the configured strategy space is exhausted."""

        return self._next_nonce == self._nonce_limit

    def next_assignment(self) -> SearchAssignment | None:
        """Issue the next contiguous assignment exactly once."""

        if self.exhausted:
            return None
        start_nonce = self._next_nonce
        stop_nonce = min(start_nonce + self._chunk_size, self._nonce_limit)
        assignment = SearchAssignment(
            assignment_index=self._assignment_index,
            start_nonce=start_nonce,
            stop_nonce=stop_nonce,
        )
        self._next_nonce = stop_nonce
        self._assignment_index += 1
        return assignment


class SequentialSearchStrategy:
    """Stateless definition of exhaustive ascending parent-range order."""

    __slots__ = ()

    @property
    def capabilities(self) -> SearchStrategyCapabilities:
        """Return the immutable sequential capability declaration."""

        return _SEQUENTIAL_CAPABILITIES

    def create_cursor(
        self,
        start_nonce: int,
        chunk_size: int,
        *,
        nonce_limit: int = _NONCE_LIMIT,
    ) -> SequentialSearchCursor:
        """Create fresh cursor state for one effective work variant."""

        return SequentialSearchCursor(
            start_nonce,
            chunk_size,
            nonce_limit=nonce_limit,
        )

    def supports_backend(self, capabilities: SearchBackendCapabilities) -> bool:
        """Support every available current backend, including parallel execution."""

        _validate_backend_capabilities(capabilities)
        return capabilities.available and (
            not capabilities.supports_parallel_search
            or self.capabilities.supports_parallel_backends
        )


class SearchStrategyRegistry:
    """Isolated deterministic collection of search-strategy definitions."""

    __slots__ = ("_strategies",)

    def __init__(self, strategies: Iterable[MiningSearchStrategy]) -> None:
        """Snapshot strategies and reject invalid or duplicate names."""

        selected: list[MiningSearchStrategy] = []
        names: set[str] = set()
        for strategy in strategies:
            if not isinstance(strategy, MiningSearchStrategy):
                raise SearchStrategyValidationError(
                    "strategies must implement MiningSearchStrategy"
                )
            name = strategy.capabilities.strategy_name
            if name in names:
                raise SearchStrategyValidationError("strategy names must be unique")
            names.add(name)
            selected.append(strategy)
        self._strategies = tuple(sorted(selected, key=lambda item: item.capabilities.strategy_name))

    def list_capabilities(self) -> tuple[SearchStrategyCapabilities, ...]:
        """Return capabilities in stable strategy-name order."""

        return tuple(strategy.capabilities for strategy in self._strategies)

    def select(self, strategy_name: str) -> MiningSearchStrategy:
        """Select one available strategy by exact name or the static auto alias."""

        if not isinstance(strategy_name, str) or _IDENTIFIER.fullmatch(strategy_name) is None:
            raise SearchStrategySelectionError("configured search strategy is invalid")
        selected_name = "sequential" if strategy_name == "auto" else strategy_name
        for strategy in self._strategies:
            capabilities = strategy.capabilities
            if capabilities.strategy_name != selected_name:
                continue
            if not capabilities.available:
                raise SearchStrategySelectionError("configured search strategy is unavailable")
            return strategy
        raise SearchStrategySelectionError("configured search strategy is unknown")


def builtin_search_strategy_registry() -> SearchStrategyRegistry:
    """Create a fresh registry containing the sequential reference strategy."""

    return SearchStrategyRegistry((SequentialSearchStrategy(),))


def select_search_strategy(
    strategy_name: str,
    registry: SearchStrategyRegistry | None = None,
) -> MiningSearchStrategy:
    """Select one operational strategy from a caller registry or fresh built-ins."""

    selected_registry = builtin_search_strategy_registry() if registry is None else registry
    if not isinstance(selected_registry, SearchStrategyRegistry):
        raise SearchStrategyValidationError("registry must be a SearchStrategyRegistry")
    return selected_registry.select(strategy_name)


def list_search_strategies(
    registry: SearchStrategyRegistry | None = None,
) -> tuple[SearchStrategyCapabilities, ...]:
    """List deterministic immutable strategy capabilities."""

    selected_registry = builtin_search_strategy_registry() if registry is None else registry
    if not isinstance(selected_registry, SearchStrategyRegistry):
        raise SearchStrategyValidationError("registry must be a SearchStrategyRegistry")
    return selected_registry.list_capabilities()


def validate_search_strategy_compatibility(
    strategy: MiningSearchStrategy,
    backend_capabilities: SearchBackendCapabilities,
) -> None:
    """Reject an unavailable or unsupported strategy/backend pairing."""

    if not isinstance(strategy, MiningSearchStrategy):
        raise SearchStrategyValidationError("strategy must implement MiningSearchStrategy")
    _validate_backend_capabilities(backend_capabilities)
    try:
        compatible = strategy.supports_backend(backend_capabilities)
    except SearchStrategyError:
        raise
    except Exception as exc:
        raise SearchStrategyCompatibilityError(
            "search strategy compatibility check failed"
        ) from exc
    if not isinstance(compatible, bool):
        raise SearchStrategyCompatibilityError(
            "search strategy compatibility check returned invalid data"
        )
    if not compatible:
        raise SearchStrategyCompatibilityError(
            "configured search strategy is incompatible with compute backend"
        )


def _validate_backend_capabilities(capabilities: object) -> None:
    if not isinstance(capabilities, SearchBackendCapabilities):
        raise SearchStrategyValidationError("backend capabilities are invalid")
    if not isinstance(capabilities.backend_name, str) or not capabilities.backend_name:
        raise SearchStrategyValidationError("backend_name must be a nonblank string")
    if not isinstance(capabilities.supports_parallel_search, bool):
        raise SearchStrategyValidationError("supports_parallel_search must be a Boolean")
    if not isinstance(capabilities.available, bool):
        raise SearchStrategyValidationError("backend available must be a Boolean")


def _validate_nonce(value: object, name: str) -> None:
    parsed = _validate_nonnegative_integer(value, name)
    if parsed > _MAX_NONCE:
        raise SearchStrategyValidationError(f"{name} must not exceed 0xffffffff")


def _validate_stop_nonce(value: object, name: str) -> None:
    parsed = _validate_positive_integer(value, name)
    if parsed > _NONCE_LIMIT:
        raise SearchStrategyValidationError(f"{name} must not exceed 2**32")


def _validate_nonnegative_integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SearchStrategyValidationError(f"{name} must be an integer")
    if value < 0:
        raise SearchStrategyValidationError(f"{name} must be nonnegative")
    return value


def _validate_positive_integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SearchStrategyValidationError(f"{name} must be an integer")
    if value <= 0:
        raise SearchStrategyValidationError(f"{name} must be positive")
    return value
