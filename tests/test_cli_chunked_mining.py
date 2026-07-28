"""Tests for the opt-in finite chunked Stratum mining command."""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest

import hashphere.__main__ as cli_module
from hashphere.compute import ComputeBackendCapabilities
from hashphere.config import Settings
from hashphere.mining import (
    ChunkedMiningPlan,
    MiningJob,
    NonceSearchMatch,
    NonceSearchResult,
    PreparedMiningWork,
)
from hashphere.network.stratum import (
    MiningNotifyNotification,
    SetDifficultyNotification,
    StratumClientError,
    StratumClientState,
    StratumConnectionError,
    SubscribeResult,
)
from hashphere.observability import JsonlEventSink, summarize_jsonl


def make_settings() -> Settings:
    """Return deterministic synthetic settings."""

    return Settings(
        stratum_host="pool.example.com",
        stratum_port=3333,
        bitcoin_address="bc1qchunkedprivateaddress",
        worker_name="chunked-rig",
        stratum_password="synthetic-chunked-password",
        compute_backend="cpu",
        compute_profile="lite",
    )


def difficulty(value: int | float = 10000) -> SetDifficultyNotification:
    """Build one parsed difficulty notification."""

    return SetDifficultyNotification(difficulty=value)


def job(
    job_id: str = "initial-job",
    *,
    clean_jobs: bool = True,
    network_time: str = "65f04abc",
) -> MiningNotifyNotification:
    """Build one valid parsed mining notification."""

    return MiningNotifyNotification(
        job_id=job_id,
        previous_block_hash="00" * 32,
        coinbase_part_1="01000000cafebabe",
        coinbase_part_2="ffffffffdeadbeef",
        merkle_branches=("11" * 32,),
        version="20000000",
        network_bits="170fffff",
        network_time=network_time,
        clean_jobs=clean_jobs,
    )


class FakeClient:
    """In-memory authorized client with deterministic polling."""

    def __init__(
        self,
        *,
        initial_notifications: list[object] | None = None,
        polled_notifications: list[object | None] | None = None,
        handshake_failure: BaseException | None = None,
        receive_failure: BaseException | None = None,
        poll_failure: BaseException | None = None,
        submission_result: bool = True,
        submission_failure: BaseException | None = None,
        close_failure: BaseException | None = None,
    ) -> None:
        self.initial_notifications = deque(initial_notifications or [difficulty(), job()])
        self.polled_notifications = deque(polled_notifications or [])
        self.handshake_failure = handshake_failure
        self.receive_failure = receive_failure
        self.poll_failure = poll_failure
        self.submission_result = submission_result
        self.submission_failure = submission_failure
        self.close_failure = close_failure
        self.state = StratumClientState.DISCONNECTED
        self.handshake_calls = 0
        self.receive_calls = 0
        self.poll_timeouts: list[float] = []
        self.submit_calls: list[tuple[str, str, str, int]] = []
        self.close_calls = 0

    def handshake(self) -> SubscribeResult:
        self.handshake_calls += 1
        if self.handshake_failure is not None:
            raise self.handshake_failure
        self.state = StratumClientState.AUTHORIZED
        return SubscribeResult(
            subscriptions=(("mining.notify", "subscription-id"),),
            extra_nonce_1="08000002",
            extra_nonce_2_size=4,
        )

    def receive_notification(self) -> object:
        self.receive_calls += 1
        if self.receive_failure is not None:
            raise self.receive_failure
        if not self.initial_notifications:
            raise AssertionError("fake has no initial notification")
        return self.initial_notifications.popleft()

    def poll_notification(self, timeout_seconds: float = 0.0) -> object | None:
        self.poll_timeouts.append(timeout_seconds)
        if self.poll_failure is not None:
            raise self.poll_failure
        if not self.polled_notifications:
            return None
        return self.polled_notifications.popleft()

    def submit_share(
        self,
        job_id: str,
        extra_nonce_2: str,
        network_time: str,
        nonce: int,
    ) -> bool:
        self.submit_calls.append((job_id, extra_nonce_2, network_time, nonce))
        if self.submission_failure is not None:
            raise self.submission_failure
        return self.submission_result

    def close(self) -> None:
        self.close_calls += 1
        self.state = StratumClientState.DISCONNECTED
        if self.close_failure is not None:
            raise self.close_failure


@dataclass
class Harness:
    """Captured preparation/search calls and deterministic results."""

    elapsed_values: deque[int] = field(default_factory=deque)
    match_call: int | None = None
    match_flags: tuple[bool, bool] = (True, False)
    prepare_failure: BaseException | None = None
    search_failure: BaseException | None = None
    generated_extra_nonce_2: str = "abababab"
    backend_selection_calls: int = 0
    generated_sizes: list[int] = field(default_factory=list)
    prepare_calls: list[tuple[MiningJob, str]] = field(default_factory=list)
    search_calls: list[tuple[PreparedMiningWork, int, int]] = field(default_factory=list)


def install_fakes(
    monkeypatch: pytest.MonkeyPatch,
    client: FakeClient,
    harness: Harness | None = None,
    *,
    deterministic_log: bool = False,
) -> Harness:
    """Install client, crypto-free mining, settings, and optional sink fakes."""

    configured = harness if harness is not None else Harness()
    settings = make_settings()
    monkeypatch.setattr(
        cli_module.Settings,
        "from_env",
        classmethod(lambda cls: settings),
    )

    def client_factory(received_settings: Settings, user_agent: str) -> FakeClient:
        assert received_settings is settings
        assert user_agent == "Hashphere/0.1"
        return client

    def generate(byte_size: int) -> str:
        configured.generated_sizes.append(byte_size)
        return configured.generated_extra_nonce_2

    def prepare(received_job: MiningJob, extra_nonce_2: str) -> PreparedMiningWork:
        configured.prepare_calls.append((received_job, extra_nonce_2))
        if configured.prepare_failure is not None:
            raise configured.prepare_failure
        return PreparedMiningWork(
            job_id=received_job.job_id,
            extra_nonce_2=extra_nonce_2,
            network_time=received_job.network_time,
            header_prefix=bytes(range(76)),
            network_target=1,
            share_target=2,
        )

    def search(
        work: PreparedMiningWork,
        start_nonce: int,
        stop_nonce: int,
    ) -> NonceSearchResult:
        configured.search_calls.append((work, start_nonce, stop_nonce))
        if configured.search_failure is not None:
            raise configured.search_failure
        elapsed_ns = configured.elapsed_values.popleft() if configured.elapsed_values else 100
        match: NonceSearchMatch | None = None
        hashes_checked = stop_nonce - start_nonce
        if configured.match_call == len(configured.search_calls):
            meets_share, meets_network = configured.match_flags
            match = NonceSearchMatch(
                nonce=start_nonce,
                block_hash=bytes.fromhex("12345678" + "00" * 28),
                meets_share_target=meets_share,
                meets_network_target=meets_network,
            )
            hashes_checked = 1
        return NonceSearchResult(
            start_nonce=start_nonce,
            stop_nonce=stop_nonce,
            hashes_checked=hashes_checked,
            elapsed_ns=elapsed_ns,
            match=match,
        )

    monkeypatch.setattr(cli_module, "StratumClient", client_factory)
    monkeypatch.setattr(cli_module, "_generate_extra_nonce_2", generate)
    monkeypatch.setattr(cli_module, "prepare_mining_work", prepare)
    monkeypatch.setattr(cli_module, "search_nonce_range", search)
    original_selector = cli_module._select_configured_compute_backend

    def select_backend(received_settings: Settings) -> object:
        configured.backend_selection_calls += 1
        return original_selector(received_settings)

    monkeypatch.setattr(cli_module, "_select_configured_compute_backend", select_backend)
    monkeypatch.setenv("HASHPHERE_ENABLE_LIVE_STRATUM", "1")
    monkeypatch.setenv("HASHPHERE_ENABLE_LIVE_MINING", "1")

    if deterministic_log:
        fixed_time = datetime(2026, 7, 27, 12, 0, 0, tzinfo=UTC)

        def sink_factory(path: str, command: str) -> JsonlEventSink:
            return JsonlEventSink(
                path,
                command,
                clock=lambda: fixed_time,
                run_id_factory=lambda: "chunked-run",
            )

        monkeypatch.setattr(cli_module, "JsonlEventSink", sink_factory)
    return configured


def arguments(
    *,
    max_hashes: str = "5",
    chunk_size: str = "2",
    start_nonce: str | None = None,
    log_file: Path | None = None,
) -> list[str]:
    """Build one chunked-command argument list."""

    result = [
        "stratum-mine-chunks",
        "--chunk-size",
        chunk_size,
        "--max-hashes",
        max_hashes,
    ]
    if start_nonce is not None:
        result.extend(["--start-nonce", start_nonce])
    if log_file is not None:
        result.extend(["--log-file", str(log_file)])
    return result


def read_events(path: Path) -> list[dict[str, object]]:
    """Read all independently valid event records."""

    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_command_appears_in_usage(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli_module.main([]) == 2
    assert "stratum-mine-chunks" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        (["--chunk-size", "1", "--max-hashes", "1"], ChunkedMiningPlan(0, 1, 1)),
        (
            ["--max-hashes", "5", "--chunk-size", "10", "--start-nonce", "7"],
            ChunkedMiningPlan(7, 10, 5),
        ),
        (
            ["--start-nonce", "4294967295", "--chunk-size", "2", "--max-hashes", "1"],
            ChunkedMiningPlan(4294967295, 2, 1),
        ),
        (
            ["--chunk-size", "4294967296", "--max-hashes", "4294967296"],
            ChunkedMiningPlan(0, 2**32, 2**32),
        ),
    ],
)
def test_parse_chunked_plan_accepts_strict_valid_values(
    values: list[str],
    expected: ChunkedMiningPlan,
) -> None:
    assert cli_module._parse_chunked_mining_plan(values) == expected


@pytest.mark.parametrize(
    "arguments_value",
    [
        [],
        ["--chunk-size", "1"],
        ["--max-hashes", "1"],
        ["--chunk-size"],
        ["--chunk-size", "1", "--max-hashes"],
        ["--unknown", "1", "--chunk-size", "1", "--max-hashes", "1"],
        ["--chunk-size", "1", "--chunk-size", "2", "--max-hashes", "1"],
        ["--chunk-size", "1", "--max-hashes", "1", "--max-hashes", "2"],
    ],
)
def test_parse_chunked_plan_rejects_missing_duplicate_and_unknown_options(
    arguments_value: list[str],
) -> None:
    with pytest.raises(ValueError):
        cli_module._parse_chunked_mining_plan(arguments_value)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "0",
        "-1",
        "+1",
        "0x10",
        "1.0",
        "01",
        " 1",
        "1 ",
        "true",
        "4294967297",
    ],
)
@pytest.mark.parametrize("option", ["--chunk-size", "--max-hashes"])
def test_positive_chunk_options_reject_noncanonical_values(
    option: str,
    value: str,
) -> None:
    values = ["--chunk-size", "1", "--max-hashes", "1"]
    values[values.index(option) + 1] = value
    with pytest.raises(ValueError, match=option):
        cli_module._parse_chunked_mining_plan(values)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "-1",
        "+1",
        "0x10",
        "1.0",
        "00",
        "01",
        " 1",
        "1 ",
        "true",
        "4294967296",
    ],
)
def test_start_nonce_rejects_noncanonical_values(value: str) -> None:
    with pytest.raises(ValueError, match="--start-nonce"):
        cli_module._parse_chunked_mining_plan(
            [
                "--start-nonce",
                value,
                "--chunk-size",
                "1",
                "--max-hashes",
                "1",
            ]
        )


def test_global_budget_cannot_exceed_remaining_nonce_space() -> None:
    with pytest.raises(ValueError, match="remaining"):
        cli_module._parse_chunked_mining_plan(
            [
                "--start-nonce",
                "1",
                "--chunk-size",
                "1",
                "--max-hashes",
                "4294967296",
            ]
        )


def test_invalid_cli_arguments_return_two(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli_module.main(["stratum-mine-chunks", "--chunk-size", "1"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Argument error:" in captured.err
    assert "Usage:" in captured.err


@pytest.mark.parametrize(
    "invalid_arguments",
    [
        arguments() + ["--log-file"],
        arguments() + ["--log-file", ""],
        arguments() + ["--log-file", "   "],
        arguments() + ["--log-file", "one", "--log-file", "two"],
    ],
)
def test_log_file_argument_errors_return_two(
    invalid_arguments: list[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli_module.main(invalid_arguments) == 2
    assert "Argument error:" in capsys.readouterr().err


def test_both_live_opt_ins_are_required(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeClient()
    install_fakes(monkeypatch, client)
    monkeypatch.delenv("HASHPHERE_ENABLE_LIVE_STRATUM")

    assert cli_module.main(arguments()) == 2
    assert "HASHPHERE_ENABLE_LIVE_STRATUM=1" in capsys.readouterr().err
    assert client.handshake_calls == 0

    monkeypatch.setenv("HASHPHERE_ENABLE_LIVE_STRATUM", "1")
    monkeypatch.delenv("HASHPHERE_ENABLE_LIVE_MINING")
    assert cli_module.main(arguments()) == 2
    assert "HASHPHERE_ENABLE_LIVE_MINING=1" in capsys.readouterr().err
    assert client.handshake_calls == 0


@pytest.mark.parametrize(
    ("backend_name", "implementation", "parallel", "worker_count"),
    [
        ("native", "c", False, None),
        ("native-parallel", "c-threadpool", True, 4),
    ],
)
def test_chunks_are_exact_nonblocking_and_final_chunk_is_shortened(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    backend_name: str,
    implementation: str,
    parallel: bool,
    worker_count: int | None,
) -> None:
    client = FakeClient()
    harness = install_fakes(
        monkeypatch,
        client,
        Harness(elapsed_values=deque([100, 200, 700])),
    )
    searcher = cli_module.search_nonce_range

    class NativeFakeBackend:
        def __init__(self) -> None:
            self.capabilities = ComputeBackendCapabilities(
                backend_name=backend_name,
                display_name="Native fake",
                backend_kind="cpu",
                implementation=implementation,
                supports_parallel_search=parallel,
                supports_cooperative_cancellation=False,
                supports_device_selection=False,
                deterministic_search_order=True,
                preferred_batch_size=None,
                available=True,
            )
            self.worker_count = worker_count
            self.close_calls = 0

        def search_nonce_range(
            self,
            work: PreparedMiningWork,
            start_nonce: int,
            stop_nonce: int,
        ) -> NonceSearchResult:
            return searcher(work, start_nonce, stop_nonce)

        def close(self) -> None:
            self.close_calls += 1

    backend = NativeFakeBackend()

    def select(settings: Settings) -> NativeFakeBackend:
        del settings
        harness.backend_selection_calls += 1
        return backend

    harness.backend_selection_calls = 0
    monkeypatch.setattr(cli_module, "_select_configured_compute_backend", select)

    assert cli_module.main(arguments(max_hashes="5", chunk_size="2", start_nonce="7")) == 0

    assert [(start, stop) for _, start, stop in harness.search_calls] == [
        (7, 9),
        (9, 11),
        (11, 12),
    ]
    assert harness.backend_selection_calls == 1
    assert backend.close_calls == 1
    assert client.poll_timeouts == [0.0, 0.0]
    assert harness.generated_sizes == [4]
    assert len(harness.prepare_calls) == 1
    assert client.submit_calls == []
    assert client.close_calls == 1
    output = capsys.readouterr().out
    assert "Chunk size: 2" in output
    assert f"Compute backend: {backend_name}" in output
    if worker_count is not None:
        assert f"Compute workers: {worker_count}" in output
    assert "Maximum hash budget: 5" in output
    assert "Chunks completed: 3" in output
    assert "Jobs used: 1" in output
    assert "Job replacements: 0" in output
    assert "Candidates found: 0" in output
    assert "Submissions performed: 0" in output
    assert "Hashes checked: 5" in output
    assert "Elapsed time: 1000 ns" in output
    assert "Hashes per second: 5000000.00" in output
    assert "hash budget exhausted" in output


def test_initial_job_before_first_difficulty_is_not_reused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient(initial_notifications=[job("too-early"), difficulty(15000), job("usable")])
    harness = install_fakes(monkeypatch, client)

    assert cli_module.main(arguments(max_hashes="1", chunk_size="1")) == 0

    assert [prepared.job_id for prepared, _ in harness.prepare_calls] == ["usable"]
    assert harness.prepare_calls[0][0].difficulty == 15000


def test_zero_elapsed_rate_is_unavailable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    client = FakeClient()
    install_fakes(monkeypatch, client, Harness(elapsed_values=deque([0])))

    assert cli_module.main(arguments(max_hashes="1", chunk_size="1")) == 0
    assert "Hashes per second: unavailable" in capsys.readouterr().out


@pytest.mark.parametrize("clean_jobs", [True, False])
def test_replacement_uses_same_extra_nonce_and_resets_configured_start(
    monkeypatch: pytest.MonkeyPatch,
    clean_jobs: bool,
) -> None:
    client = FakeClient(polled_notifications=[job("new-job", clean_jobs=clean_jobs), None])
    harness = install_fakes(monkeypatch, client)

    assert cli_module.main(arguments(max_hashes="4", chunk_size="2", start_nonce="5")) == 0

    assert [(work.job_id, start, stop) for work, start, stop in harness.search_calls] == [
        ("initial-job", 5, 7),
        ("new-job", 5, 7),
    ]
    assert [(prepared.job_id, extra) for prepared, extra in harness.prepare_calls] == [
        ("initial-job", "abababab"),
        ("new-job", "abababab"),
    ]
    assert harness.generated_sizes == [4]


def test_notifications_drain_in_order_and_only_newest_job_is_searched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient(
        polled_notifications=[
            job("old-difficulty-job"),
            difficulty(20000),
            job("new-difficulty-job"),
            difficulty(30000),
            None,
        ]
    )
    harness = install_fakes(monkeypatch, client)

    assert cli_module.main(arguments(max_hashes="2", chunk_size="1", start_nonce="8")) == 0

    assert [(prepared.job_id, prepared.difficulty) for prepared, _ in harness.prepare_calls] == [
        ("initial-job", 10000),
        ("new-difficulty-job", 20000),
    ]
    assert [(work.job_id, start, stop) for work, start, stop in harness.search_calls] == [
        ("initial-job", 8, 9),
        ("new-difficulty-job", 8, 9),
    ]
    assert client.poll_timeouts == [0.0] * 5


def test_difficulty_alone_keeps_current_work_and_nonce_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient(polled_notifications=[difficulty(25000), None])
    harness = install_fakes(monkeypatch, client)

    assert cli_module.main(arguments(max_hashes="4", chunk_size="2", start_nonce="3")) == 0

    assert len(harness.prepare_calls) == 1
    assert [(start, stop) for _, start, stop in harness.search_calls] == [(3, 5), (5, 7)]


@pytest.mark.parametrize(
    ("flags", "accepted"),
    [((True, False), True), ((True, False), False), ((False, True), True)],
)
def test_match_submits_once_without_polling_or_continuation(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    flags: tuple[bool, bool],
    accepted: bool,
) -> None:
    client = FakeClient(
        polled_notifications=[job("must-not-be-read")],
        submission_result=accepted,
    )
    harness = install_fakes(
        monkeypatch,
        client,
        Harness(match_call=1, match_flags=flags),
    )

    assert cli_module.main(arguments(max_hashes="5", chunk_size="2", start_nonce="9")) == 0

    assert len(harness.search_calls) == 1
    assert client.poll_timeouts == []
    assert client.submit_calls == [("initial-job", "abababab", "65f04abc", 9)]
    output = capsys.readouterr().out
    assert f"Meets share target: {str(flags[0]).lower()}" in output
    assert f"Meets network target: {str(flags[1]).lower()}" in output
    assert f"Pool result: {'accepted' if accepted else 'rejected'}" in output


@pytest.mark.parametrize(
    ("failure_location", "failure"),
    [
        ("handshake", StratumConnectionError("private connection detail")),
        ("receive", StratumClientError("private receive detail")),
        ("poll", StratumClientError("private poll detail")),
        ("prepare", ValueError("private preparation detail")),
        ("search", ValueError("private search detail")),
        ("submit", StratumClientError("private submission detail")),
    ],
)
def test_runtime_failures_are_sanitized_and_close(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure_location: str,
    failure: BaseException,
) -> None:
    client = FakeClient(
        polled_notifications=[None],
        handshake_failure=failure if failure_location == "handshake" else None,
        receive_failure=failure if failure_location == "receive" else None,
        poll_failure=failure if failure_location == "poll" else None,
        submission_failure=failure if failure_location == "submit" else None,
    )
    harness = Harness(
        match_call=1 if failure_location == "submit" else None,
        prepare_failure=failure if failure_location == "prepare" else None,
        search_failure=failure if failure_location == "search" else None,
    )
    install_fakes(monkeypatch, client, harness)
    invocation = arguments(max_hashes="2", chunk_size="1")

    assert cli_module.main(invocation) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "private" not in captured.err
    assert client.close_calls == 1


def test_cleanup_failure_after_success_returns_one(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeClient(close_failure=RuntimeError("private cleanup detail"))
    install_fakes(monkeypatch, client)

    assert cli_module.main(arguments(max_hashes="1", chunk_size="1")) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "Could not close the Stratum connection cleanly.\n"


def test_cleanup_failure_does_not_hide_original_runtime_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeClient(
        handshake_failure=StratumClientError("private original detail"),
        close_failure=RuntimeError("private cleanup detail"),
    )
    install_fakes(monkeypatch, client)

    assert cli_module.main(arguments()) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "Chunked Stratum mining failed.\n"
    assert client.close_calls == 1


def test_two_chunk_log_is_ordered_sanitized_and_summary_compatible(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "chunks.jsonl"
    client = FakeClient()
    harness = install_fakes(
        monkeypatch,
        client,
        Harness(elapsed_values=deque([100, 300])),
        deterministic_log=True,
    )
    settings = make_settings()

    assert (
        cli_module.main(arguments(max_hashes="3", chunk_size="2", start_nonce="7", log_file=path))
        == 0
    )

    records = read_events(path)
    assert [record["event"] for record in records] == [
        "command_started",
        "compute_backend_selected",
        "stratum_authorized",
        "difficulty_received",
        "mining_job_received",
        "nonce_range_started",
        "nonce_range_completed",
        "nonce_range_started",
        "nonce_range_completed",
        "command_completed",
    ]
    assert [(records[index]["start_nonce"], records[index]["stop_nonce"]) for index in (5, 7)] == [
        (7, 9),
        (9, 10),
    ]
    assert records[-1]["outcome"] == "hash_budget_exhausted"
    assert {record["run_id"] for record in records} == {"chunked-run"}
    assert [record["sequence"] for record in records] == list(range(1, 11))
    summary = summarize_jsonl(path)
    assert summary.completed_run_count == 1
    assert summary.completed_nonce_range_count == 2
    assert summary.total_hashes_checked == 3
    assert summary.total_mining_elapsed_ns == 400
    original_bytes = path.read_bytes()
    assert summarize_jsonl(path) == summary
    assert path.read_bytes() == original_bytes

    text = path.read_text(encoding="utf-8")
    for forbidden in (
        settings.stratum_password,
        settings.stratum_username,
        settings.bitcoin_address,
        "08000002",
        harness.generated_extra_nonce_2,
        job().coinbase_part_1,
        job().coinbase_part_2,
        "mining.submit",
        '"params"',
    ):
        assert forbidden not in text


def test_replacement_event_precedes_next_chunk_for_both_clean_values(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "replacement.jsonl"
    client = FakeClient(
        polled_notifications=[difficulty(20000), job("replacement", clean_jobs=False), None]
    )
    install_fakes(monkeypatch, client, deterministic_log=True)

    assert cli_module.main(arguments(max_hashes="2", chunk_size="1", log_file=path)) == 0

    records = read_events(path)
    events = [record["event"] for record in records]
    assert events == [
        "command_started",
        "compute_backend_selected",
        "stratum_authorized",
        "difficulty_received",
        "mining_job_received",
        "nonce_range_started",
        "nonce_range_completed",
        "difficulty_received",
        "mining_job_received",
        "mining_job_replaced",
        "nonce_range_started",
        "nonce_range_completed",
        "command_completed",
    ]
    replacement = records[9]
    assert replacement["previous_job_id"] == "initial-job"
    assert replacement["new_job_id"] == "replacement"
    assert replacement["clean_jobs"] is False
    assert replacement["replacement_index"] == 1
    assert summarize_jsonl(path).record_count == 13


def test_multiple_jobs_emit_one_transition_to_final_selected_work(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "multiple-jobs.jsonl"
    client = FakeClient(
        polled_notifications=[
            job("intermediate"),
            difficulty(20000),
            job("final-newest"),
            None,
        ]
    )
    harness = install_fakes(monkeypatch, client, deterministic_log=True)

    assert cli_module.main(arguments(max_hashes="2", chunk_size="1", log_file=path)) == 0

    assert [prepared.job_id for prepared, _ in harness.prepare_calls] == [
        "initial-job",
        "final-newest",
    ]
    assert [work.job_id for work, _, _ in harness.search_calls] == [
        "initial-job",
        "final-newest",
    ]
    records = read_events(path)
    received_jobs = [
        record["job_id"] for record in records if record["event"] == "mining_job_received"
    ]
    replacements = [record for record in records if record["event"] == "mining_job_replaced"]
    assert received_jobs == ["initial-job", "intermediate", "final-newest"]
    assert len(replacements) == 1
    assert replacements[0]["previous_job_id"] == "initial-job"
    assert replacements[0]["new_job_id"] == "final-newest"
    replacement_position = records.index(replacements[0])
    next_range_position = next(
        index
        for index, record in enumerate(records)
        if index > replacement_position and record["event"] == "nonce_range_started"
    )
    assert replacement_position < next_range_position


@pytest.mark.parametrize(
    ("accepted", "expected_outcome", "expected_level"),
    [
        (True, "share_accepted", "INFO"),
        (False, "share_rejected", "WARNING"),
    ],
)
def test_matched_log_orders_single_submission_and_completion(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    accepted: bool,
    expected_outcome: str,
    expected_level: str,
) -> None:
    path = tmp_path / f"{expected_outcome}.jsonl"
    client = FakeClient(submission_result=accepted)
    install_fakes(
        monkeypatch,
        client,
        Harness(match_call=1),
        deterministic_log=True,
    )

    assert cli_module.main(arguments(max_hashes="5", chunk_size="2", log_file=path)) == 0

    records = read_events(path)
    assert [record["event"] for record in records] == [
        "command_started",
        "compute_backend_selected",
        "stratum_authorized",
        "difficulty_received",
        "mining_job_received",
        "nonce_range_started",
        "nonce_range_completed",
        "share_candidate_found",
        "share_submission_completed",
        "command_completed",
    ]
    assert records[8]["accepted"] is accepted
    assert records[-1]["outcome"] == expected_outcome
    assert records[-1]["level"] == expected_level
    assert len(client.submit_calls) == 1
    assert client.poll_timeouts == []


def test_failed_command_event_omits_arbitrary_error_text(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "failed.jsonl"
    sensitive = "synthetic-chunked-password.bc1qchunkedprivateaddress.abababab"
    client = FakeClient(poll_failure=StratumClientError(sensitive))
    install_fakes(monkeypatch, client, deterministic_log=True)

    assert cli_module.main(arguments(max_hashes="2", chunk_size="1", log_file=path)) == 1

    records = read_events(path)
    assert records[-1]["event"] == "command_failed"
    assert records[-1]["stage"] == "chunked_mining"
    assert records[-1]["error_category"] == "StratumClientError"
    assert sensitive not in path.read_text(encoding="utf-8")
