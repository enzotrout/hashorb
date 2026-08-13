"""Best Hash observability tests."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

import pytest

import hashorb.__main__ as cli_module
from hashorb.compute import MiningComputeBackend
from hashorb.mining import NonceSearchResult, PreparedMiningWork
from hashorb.network.stratum import StratumClient, SubscribeResult
from hashorb.observability.events import EventValue


class RecordingEventSink:
    """Small in-memory EventSink-compatible recorder."""

    def __init__(self) -> None:
        self.records: list[tuple[str, str, dict[str, EventValue]]] = []

    def emit(
        self,
        event: str,
        *,
        level: str = "INFO",
        fields: Mapping[str, EventValue] | None = None,
    ) -> None:
        self.records.append((event, level, dict(fields or {})))

    def close(self) -> None:
        return None


def _work() -> PreparedMiningWork:
    return PreparedMiningWork(
        job_id="quality-job",
        extra_nonce_2="00000000",
        network_time="65f04abc",
        header_prefix=bytes(range(76)),
        network_target=1,
        share_target=2,
    )


def _result(
    *,
    value: int,
    start_nonce: int,
    stop_nonce: int,
) -> NonceSearchResult:
    return NonceSearchResult(
        start_nonce=start_nonce,
        stop_nonce=stop_nonce,
        hashes_checked=stop_nonce - start_nonce,
        elapsed_ns=100,
        match=None,
        best_nonce=start_nonce,
        best_hash=value.to_bytes(32, "little"),
    )


def test_chunk_observer_emits_only_strict_run_wide_best_hash_improvements() -> None:
    sink = RecordingEventSink()
    observer = cli_module._ChunkedEventObserver(sink)
    work = _work()

    observer.chunk_completed(
        work,
        _result(value=200, start_nonce=0, stop_nonce=4),
    )
    observer.chunk_completed(
        work,
        _result(value=300, start_nonce=4, stop_nonce=8),
    )
    observer.chunk_completed(
        work,
        _result(value=100, start_nonce=8, stop_nonce=12),
    )

    improvements = [
        fields for event, _level, fields in sink.records if event == "best_hash_improved"
    ]

    assert improvements == [
        {"best_hash": f"{200:064x}"},
        {"best_hash": f"{100:064x}"},
    ]


def test_one_shot_range_emits_canonical_best_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sink = RecordingEventSink()
    work = _work()
    result = _result(value=12345, start_nonce=7, stop_nonce=11)

    subscription = SubscribeResult(
        subscriptions=(("mining.notify", "subscription-id"),),
        extra_nonce_1="08000002",
        extra_nonce_2_size=4,
    )

    monkeypatch.setattr(
        cli_module,
        "_receive_buildable_job",
        lambda client, assembler, events: object(),
    )
    monkeypatch.setattr(
        cli_module,
        "_generate_extra_nonce_2",
        lambda byte_size: "00" * byte_size,
    )
    monkeypatch.setattr(
        cli_module,
        "prepare_mining_work",
        lambda job, extra_nonce_2: work,
    )

    class Backend:
        def search_nonce_range(
            self,
            received_work: PreparedMiningWork,
            start_nonce: int,
            stop_nonce: int,
        ) -> NonceSearchResult:
            assert received_work is work
            assert (start_nonce, stop_nonce) == (7, 11)
            return result

    cli_module._mine_one_range(
        cast(StratumClient, object()),
        subscription,
        7,
        11,
        sink,
        cast(MiningComputeBackend, Backend()),
    )

    improvements = [
        fields for event, _level, fields in sink.records if event == "best_hash_improved"
    ]

    assert improvements == [{"best_hash": f"{12345:064x}"}]
