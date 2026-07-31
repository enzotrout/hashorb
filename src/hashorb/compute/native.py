"""Verified wrapper for the optional portable native CPU extension."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from importlib import import_module
from time import perf_counter_ns
from typing import cast

from hashorb.compute.backend import (
    ComputeBackendCapabilities,
    ComputeBackendExecutionError,
    ComputeBackendValidationError,
)
from hashorb.mining.header import hash_block_header
from hashorb.mining.search import (
    NonceSearchMatch,
    NonceSearchResult,
    PreparedMiningWork,
    _validate_nonce_range,
)
from hashorb.mining.target import hash_meets_target

type NativeSearcher = Callable[[bytes, bytes, bytes, int, int], object]
type MonotonicClock = Callable[[], int]

_AUTO_LOAD = object()
_TARGET_BYTE_LENGTH = 32
_DIGEST_BYTE_LENGTH = 32
_NONCE_BYTE_LENGTH = 4


def _capabilities(unavailable_reason: str | None) -> ComputeBackendCapabilities:
    return ComputeBackendCapabilities(
        backend_name="native",
        display_name="Portable native C sequential",
        backend_kind="cpu",
        implementation="c",
        supports_parallel_search=False,
        supports_cooperative_cancellation=False,
        supports_device_selection=False,
        deterministic_search_order=True,
        preferred_batch_size=None,
        available=unavailable_reason is None,
        unavailable_reason=unavailable_reason,
    )


def _load_native_searcher() -> tuple[NativeSearcher | None, str | None]:
    """Load the optional extension without leaking importer error details."""

    try:
        extension = import_module("hashorb.compute._native")
    except ImportError:
        return None, "ExtensionNotInstalled"
    except Exception:
        return None, "ExtensionImportFailed"

    searcher = getattr(extension, "search_nonce_range", None)
    if not callable(searcher):
        return None, "ExtensionInvalid"
    return cast(NativeSearcher, searcher), None


@dataclass(frozen=True, slots=True, init=False)
class NativeSequentialBackend:
    """Search through portable C and verify every reported candidate in Python."""

    _searcher: NativeSearcher | None
    _clock: MonotonicClock
    capabilities: ComputeBackendCapabilities

    def __init__(
        self,
        searcher: NativeSearcher | None | object = _AUTO_LOAD,
        *,
        clock: MonotonicClock = perf_counter_ns,
    ) -> None:
        """Load the extension or install an explicit deterministic test boundary."""

        unavailable_reason: str | None = None
        selected_searcher: NativeSearcher | None
        if searcher is _AUTO_LOAD:
            selected_searcher, unavailable_reason = _load_native_searcher()
        elif searcher is None:
            selected_searcher = None
            unavailable_reason = "ExtensionNotInstalled"
        elif callable(searcher):
            selected_searcher = cast(NativeSearcher, searcher)
        else:
            raise ComputeBackendValidationError("native searcher must be callable or None")
        if not callable(clock):
            raise ComputeBackendValidationError("clock must be callable")

        object.__setattr__(self, "_searcher", selected_searcher)
        object.__setattr__(self, "_clock", clock)
        object.__setattr__(self, "capabilities", _capabilities(unavailable_reason))

    def search_nonce_range(
        self,
        work: PreparedMiningWork,
        start_nonce: int,
        stop_nonce: int,
    ) -> NonceSearchResult:
        """Search one exact range and return only a Python-verified candidate."""

        if not isinstance(work, PreparedMiningWork):
            raise ComputeBackendValidationError("work must be PreparedMiningWork")
        try:
            _validate_nonce_range(start_nonce, stop_nonce)
        except (TypeError, ValueError) as exc:
            raise ComputeBackendValidationError("native search range is invalid") from exc
        if self._searcher is None:
            raise ComputeBackendExecutionError("native compute backend is unavailable")

        share_target = work.share_target.to_bytes(
            _TARGET_BYTE_LENGTH,
            byteorder="little",
            signed=False,
        )
        network_target = work.network_target.to_bytes(
            _TARGET_BYTE_LENGTH,
            byteorder="little",
            signed=False,
        )
        try:
            started_ns = self._clock()
            native_result = self._searcher(
                work.header_prefix,
                share_target,
                network_target,
                start_nonce,
                stop_nonce,
            )
            finished_ns = self._clock()
        except Exception as exc:
            raise ComputeBackendExecutionError("native compute backend search failed") from exc

        elapsed_ns = _elapsed_nanoseconds(started_ns, finished_ns)
        return _validated_result(
            work,
            start_nonce,
            stop_nonce,
            elapsed_ns,
            native_result,
        )


def _elapsed_nanoseconds(started_ns: object, finished_ns: object) -> int:
    if (
        isinstance(started_ns, bool)
        or not isinstance(started_ns, int)
        or isinstance(finished_ns, bool)
        or not isinstance(finished_ns, int)
    ):
        raise ComputeBackendExecutionError("native compute clock returned invalid data")
    return max(0, finished_ns - started_ns)


def _validated_result(
    work: PreparedMiningWork,
    start_nonce: int,
    stop_nonce: int,
    elapsed_ns: int,
    native_result: object,
) -> NonceSearchResult:
    if not isinstance(native_result, tuple) or len(native_result) != 5:
        raise ComputeBackendExecutionError("native compute backend returned invalid data")
    nonce, digest, meets_share, meets_network, hashes_checked = native_result
    if not isinstance(meets_share, bool) or not isinstance(meets_network, bool):
        raise ComputeBackendExecutionError("native compute backend returned invalid flags")
    if isinstance(hashes_checked, bool) or not isinstance(hashes_checked, int):
        raise ComputeBackendExecutionError("native compute backend returned an invalid count")

    if nonce is None:
        if (
            digest is not None
            or meets_share
            or meets_network
            or hashes_checked != stop_nonce - start_nonce
        ):
            raise ComputeBackendExecutionError("native exhausted result is inconsistent")
        return NonceSearchResult(
            start_nonce=start_nonce,
            stop_nonce=stop_nonce,
            hashes_checked=hashes_checked,
            elapsed_ns=elapsed_ns,
            match=None,
        )

    if isinstance(nonce, bool) or not isinstance(nonce, int):
        raise ComputeBackendExecutionError("native compute backend returned an invalid nonce")
    if not start_nonce <= nonce < stop_nonce:
        raise ComputeBackendExecutionError("native candidate nonce is outside the range")
    if hashes_checked != nonce - start_nonce + 1:
        raise ComputeBackendExecutionError("native candidate nonce and count disagree")
    if not isinstance(digest, bytes) or len(digest) != _DIGEST_BYTE_LENGTH:
        raise ComputeBackendExecutionError("native compute backend returned an invalid digest")
    if not meets_share and not meets_network:
        raise ComputeBackendExecutionError("native candidate has no matching target")

    header = work.header_prefix + nonce.to_bytes(
        _NONCE_BYTE_LENGTH,
        byteorder="little",
        signed=False,
    )
    verified_digest = hash_block_header(header)
    if digest != verified_digest:
        raise ComputeBackendExecutionError("native candidate digest verification failed")
    verified_share = hash_meets_target(verified_digest, work.share_target)
    verified_network = hash_meets_target(verified_digest, work.network_target)
    if meets_share is not verified_share or meets_network is not verified_network:
        raise ComputeBackendExecutionError("native candidate target verification failed")

    match = NonceSearchMatch(
        nonce=nonce,
        block_hash=verified_digest,
        meets_share_target=verified_share,
        meets_network_target=verified_network,
    )
    return NonceSearchResult(
        start_nonce=start_nonce,
        stop_nonce=stop_nonce,
        hashes_checked=hashes_checked,
        elapsed_ns=elapsed_ns,
        match=match,
    )
