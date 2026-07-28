"""Stable compute-backend contracts for bounded mining searches."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from hashphere.mining.search import NonceSearchResult, PreparedMiningWork

_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_-]*$")
_CATEGORY = re.compile(r"^[A-Z][A-Za-z0-9]*$")


class ComputeBackendError(RuntimeError):
    """Base error for compute-backend selection and execution."""


class ComputeBackendValidationError(ComputeBackendError, ValueError):
    """Raised when a compute contract receives structurally invalid data."""


class ComputeBackendSelectionError(ComputeBackendError):
    """Raised when a requested backend cannot be selected safely."""


class ComputeBackendExecutionError(ComputeBackendError):
    """Raised when a selected backend cannot complete its assigned range."""


@dataclass(frozen=True, slots=True)
class ComputeBackendCapabilities:
    """Immutable low-cardinality identity and capabilities for one backend."""

    backend_name: str
    display_name: str
    backend_kind: str
    implementation: str
    supports_parallel_search: bool
    supports_cooperative_cancellation: bool
    supports_device_selection: bool
    deterministic_search_order: bool
    preferred_batch_size: int | None
    available: bool
    unavailable_reason: str | None = None

    def __post_init__(self) -> None:
        """Validate stable identity, capability flags, and availability metadata."""

        for identity_name, identity_value in (
            ("backend_name", self.backend_name),
            ("backend_kind", self.backend_kind),
            ("implementation", self.implementation),
        ):
            if not isinstance(identity_value, str) or _IDENTIFIER.fullmatch(identity_value) is None:
                raise ComputeBackendValidationError(
                    f"{identity_name} must be a lowercase backend identifier"
                )
        if not isinstance(self.display_name, str) or not self.display_name.strip():
            raise ComputeBackendValidationError("display_name must be a nonblank string")
        boolean_fields: tuple[tuple[str, object], ...] = (
            ("supports_parallel_search", self.supports_parallel_search),
            (
                "supports_cooperative_cancellation",
                self.supports_cooperative_cancellation,
            ),
            ("supports_device_selection", self.supports_device_selection),
            ("deterministic_search_order", self.deterministic_search_order),
            ("available", self.available),
        )
        for flag_name, flag_value in boolean_fields:
            if not isinstance(flag_value, bool):
                raise ComputeBackendValidationError(f"{flag_name} must be a Boolean")
        if self.preferred_batch_size is not None:
            if (
                isinstance(self.preferred_batch_size, bool)
                or not isinstance(self.preferred_batch_size, int)
                or self.preferred_batch_size <= 0
            ):
                raise ComputeBackendValidationError(
                    "preferred_batch_size must be a positive integer or None"
                )
        if self.available:
            if self.unavailable_reason is not None:
                raise ComputeBackendValidationError(
                    "available backends cannot have an unavailable_reason"
                )
        elif (
            not isinstance(self.unavailable_reason, str)
            or _CATEGORY.fullmatch(self.unavailable_reason) is None
        ):
            raise ComputeBackendValidationError(
                "unavailable backends require a controlled reason category"
            )


@runtime_checkable
class MiningComputeBackend(Protocol):
    """Execution contract for one already-prepared half-open nonce range."""

    @property
    def capabilities(self) -> ComputeBackendCapabilities:
        """Return immutable identity and capability metadata."""

    def search_nonce_range(
        self,
        work: PreparedMiningWork,
        start_nonce: int,
        stop_nonce: int,
    ) -> NonceSearchResult:
        """Search exactly the assigned half-open nonce range."""


def close_compute_backend(backend: MiningComputeBackend) -> None:
    """Close optional backend-owned resources; sequential backends are no-ops."""

    if not isinstance(backend, MiningComputeBackend):
        raise ComputeBackendValidationError("backend must implement MiningComputeBackend")
    close = getattr(backend, "close", None)
    if close is None:
        return
    if not callable(close):
        raise ComputeBackendValidationError("backend close boundary must be callable")
    try:
        close()
    except Exception as exc:
        raise ComputeBackendExecutionError("compute backend cleanup failed") from exc


def compute_backend_worker_count(backend: MiningComputeBackend) -> int | None:
    """Return safe optional worker metadata for output and observability."""

    if not isinstance(backend, MiningComputeBackend):
        raise ComputeBackendValidationError("backend must implement MiningComputeBackend")
    worker_count = getattr(backend, "worker_count", None)
    if worker_count is None:
        return None
    if isinstance(worker_count, bool) or not isinstance(worker_count, int) or worker_count <= 0:
        raise ComputeBackendValidationError("backend worker_count must be a positive integer")
    return worker_count
