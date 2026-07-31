"""Python sequential reference compute backend."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from hashorb.compute.backend import (
    ComputeBackendCapabilities,
    ComputeBackendExecutionError,
    ComputeBackendValidationError,
)
from hashorb.mining.search import (
    NonceSearchError,
    NonceSearchResult,
    NonceSearchValidationError,
    PreparedMiningWork,
    search_nonce_range,
)

type RangeSearcher = Callable[[PreparedMiningWork, int, int], NonceSearchResult]

_PYTHON_CAPABILITIES = ComputeBackendCapabilities(
    backend_name="python",
    display_name="Python sequential reference",
    backend_kind="cpu",
    implementation="python",
    supports_parallel_search=False,
    supports_cooperative_cancellation=False,
    supports_device_selection=False,
    deterministic_search_order=True,
    preferred_batch_size=None,
    available=True,
)


@dataclass(frozen=True, slots=True)
class PythonSequentialBackend:
    """Delegate bounded searches to the validated Python reference scanner."""

    _searcher: RangeSearcher = field(
        default=search_nonce_range,
        repr=False,
        compare=False,
    )
    capabilities: ComputeBackendCapabilities = field(
        default=_PYTHON_CAPABILITIES,
        init=False,
    )

    def __post_init__(self) -> None:
        """Validate the injectable search boundary."""

        if not callable(self._searcher):
            raise ComputeBackendValidationError("searcher must be callable")

    def search_nonce_range(
        self,
        work: PreparedMiningWork,
        start_nonce: int,
        stop_nonce: int,
    ) -> NonceSearchResult:
        """Return the reference scanner's unchanged result for the exact range."""

        try:
            result = self._searcher(work, start_nonce, stop_nonce)
        except NonceSearchValidationError as exc:
            raise ComputeBackendValidationError(
                "Python compute backend received invalid search input"
            ) from exc
        except NonceSearchError as exc:
            raise ComputeBackendExecutionError("Python compute backend search failed") from exc
        except Exception as exc:
            raise ComputeBackendExecutionError("Python compute backend search failed") from exc

        if not isinstance(result, NonceSearchResult):
            raise ComputeBackendExecutionError(
                "Python compute backend returned an invalid search result"
            )
        if result.start_nonce != start_nonce or result.stop_nonce != stop_nonce:
            raise ComputeBackendExecutionError(
                "Python compute backend returned a result for a different range"
            )
        return result
