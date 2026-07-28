"""Tests for the opt-in continuous live Stratum mining command."""

from __future__ import annotations

import json
import signal
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import FrameType

import pytest

import hashphere.__main__ as cli_module
from hashphere.compute import ComputeBackendCapabilities, ComputeBackendExecutionError
from hashphere.config import Settings
from hashphere.mining import (
    ContinuousMiningPlan,
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

type PythonSignalHandler = Callable[[int, FrameType | None], None]


def make_settings() -> Settings:
    """Return deterministic synthetic settings."""

    return Settings(
        stratum_host="pool.example.com",
        stratum_port=3333,
        bitcoin_address="bc1qcontinuousprivateaddress",
        worker_name="continuous-rig",
        stratum_password="synthetic-continuous-password",
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
    """Authorized in-memory client with deterministic bounded polling."""

    def __init__(
        self,
        *,
        notifications: list[object | None] | None = None,
        handshake_failure: BaseException | None = None,
        poll_failure: BaseException | None = None,
        submission_result: bool = True,
        submission_failure: BaseException | None = None,
        close_failure: BaseException | None = None,
        poll_hook: Callable[[int], None] | None = None,
        extra_nonce_2_size: int = 4,
    ) -> None:
        self.notifications = deque(
            notifications if notifications is not None else [difficulty(), job()]
        )
        self.handshake_failure = handshake_failure
        self.poll_failure = poll_failure
        self.submission_result = submission_result
        self.submission_failure = submission_failure
        self.close_failure = close_failure
        self.poll_hook = poll_hook
        self.extra_nonce_2_size = extra_nonce_2_size
        self.state = StratumClientState.DISCONNECTED
        self.handshake_calls = 0
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
            extra_nonce_2_size=self.extra_nonce_2_size,
        )

    def poll_notification(self, timeout_seconds: float = 0.0) -> object | None:
        self.poll_timeouts.append(timeout_seconds)
        if self.poll_hook is not None:
            self.poll_hook(len(self.poll_timeouts))
        if self.poll_failure is not None:
            raise self.poll_failure
        if not self.notifications:
            return None
        value = self.notifications.popleft()
        if isinstance(value, BaseException):
            raise value
        return value

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
class SearchHarness:
    """Captured deterministic preparation and search behavior."""

    elapsed_values: deque[int] = field(default_factory=deque)
    match_call: int | None = None
    match_flags: tuple[bool, bool] = (True, False)
    prepare_failure: BaseException | None = None
    search_failure: BaseException | None = None
    signal_trigger_call: int | None = None
    signal_trigger: Callable[[], None] | None = None
    generated_extra_nonce_2: str = "abababab"
    generated_extra_nonce_2_values: deque[str] = field(default_factory=deque)
    generated_sizes: list[int] = field(default_factory=list)
    reconnect_delays: list[float] = field(default_factory=list)
    stop_during_reconnect_wait: bool = False
    backend_selection_calls: int = 0
    prepare_calls: list[tuple[MiningJob, str]] = field(default_factory=list)
    search_calls: list[tuple[PreparedMiningWork, int, int]] = field(default_factory=list)


@dataclass
class SignalHarness:
    """Fake process signal registry used without sending operating-system signals."""

    previous: dict[signal.Signals, PythonSignalHandler]
    current: dict[signal.Signals, object]
    calls: list[tuple[signal.Signals, object]] = field(default_factory=list)

    def trigger(self, signal_number: signal.Signals = signal.SIGINT) -> None:
        handler = self.current[signal_number]
        assert callable(handler)
        handler(int(signal_number), None)


def install_signal_fakes(monkeypatch: pytest.MonkeyPatch) -> SignalHarness:
    """Replace signal registration with an in-memory registry."""

    supported = cli_module._supported_stop_signals()

    def prior_handler(signal_number: int, frame: FrameType | None) -> None:
        del signal_number, frame

    previous = {signal_number: prior_handler for signal_number in supported}
    current: dict[signal.Signals, object] = dict(previous)
    harness = SignalHarness(previous=previous, current=current)

    def fake_getsignal(signal_number: signal.Signals) -> object:
        return current[signal_number]

    def fake_signal(signal_number: signal.Signals, handler: object) -> object:
        old = current[signal_number]
        current[signal_number] = handler
        harness.calls.append((signal_number, handler))
        return old

    monkeypatch.setattr(cli_module.signal, "getsignal", fake_getsignal)
    monkeypatch.setattr(cli_module.signal, "signal", fake_signal)
    return harness


def install_fakes(
    monkeypatch: pytest.MonkeyPatch,
    client: FakeClient,
    search_harness: SearchHarness | None = None,
    *,
    deterministic_log: bool = False,
    additional_clients: list[FakeClient] | None = None,
) -> tuple[SearchHarness, SignalHarness]:
    """Install settings, client, signal, and cryptography-free search fakes."""

    configured = search_harness if search_harness is not None else SearchHarness()
    settings = make_settings()
    monkeypatch.setattr(
        cli_module.Settings,
        "from_env",
        classmethod(lambda cls: settings),
    )

    clients = deque([client, *(additional_clients or [])])

    def client_factory(received_settings: Settings, user_agent: str) -> FakeClient:
        assert received_settings is settings
        assert user_agent == "Hashphere/0.1"
        if not clients:
            raise AssertionError("unexpected Stratum client creation")
        return clients.popleft()

    def generate(byte_size: int) -> str:
        configured.generated_sizes.append(byte_size)
        if configured.generated_extra_nonce_2_values:
            return configured.generated_extra_nonce_2_values.popleft()
        return configured.generated_extra_nonce_2

    def prepare(received_job: MiningJob, extra_nonce_2: str) -> PreparedMiningWork:
        configured.prepare_calls.append((received_job, extra_nonce_2))
        if configured.prepare_failure is not None:
            raise configured.prepare_failure
        marker = bytes.fromhex(received_job.network_time + extra_nonce_2)
        job_marker = received_job.job_id.encode("ascii")
        return PreparedMiningWork(
            job_id=received_job.job_id,
            extra_nonce_2=extra_nonce_2,
            network_time=received_job.network_time,
            header_prefix=(marker + job_marker + bytes(76))[:76],
            network_target=1,
            share_target=2,
        )

    def search_range(
        work: PreparedMiningWork,
        start_nonce: int,
        stop_nonce: int,
    ) -> NonceSearchResult:
        configured.search_calls.append((work, start_nonce, stop_nonce))
        call_number = len(configured.search_calls)
        if configured.signal_trigger_call == call_number:
            assert configured.signal_trigger is not None
            configured.signal_trigger()
        if configured.search_failure is not None:
            raise configured.search_failure
        elapsed_ns = configured.elapsed_values.popleft() if configured.elapsed_values else 100
        match: NonceSearchMatch | None = None
        hashes_checked = stop_nonce - start_nonce
        if configured.match_call == call_number:
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
    monkeypatch.setattr(cli_module, "search_nonce_range", search_range)
    original_selector = cli_module._select_configured_compute_backend

    def select_backend(received_settings: Settings) -> object:
        configured.backend_selection_calls += 1
        return original_selector(received_settings)

    monkeypatch.setattr(cli_module, "_select_configured_compute_backend", select_backend)

    def wait_without_sleep(delay_seconds: float, stop_token: object) -> bool:
        del stop_token
        configured.reconnect_delays.append(delay_seconds)
        if configured.stop_during_reconnect_wait:
            assert configured.signal_trigger is not None
            configured.signal_trigger()
            return False
        return True

    monkeypatch.setattr(cli_module, "wait_for_reconnect_delay", wait_without_sleep)
    monkeypatch.setenv("HASHPHERE_ENABLE_LIVE_STRATUM", "1")
    monkeypatch.setenv("HASHPHERE_ENABLE_LIVE_MINING", "1")
    signals = install_signal_fakes(monkeypatch)

    if deterministic_log:
        fixed_time = datetime(2026, 7, 27, 12, 0, 0, tzinfo=UTC)

        def sink_factory(path: str, command: str) -> JsonlEventSink:
            return JsonlEventSink(
                path,
                command,
                clock=lambda: fixed_time,
                run_id_factory=lambda: "continuous-run",
            )

        monkeypatch.setattr(cli_module, "JsonlEventSink", sink_factory)
    return configured, signals


def arguments(
    *,
    chunk_size: str = "2",
    start_nonce: str | None = None,
    max_chunks: str | None = "3",
    max_reconnect_attempts: str | None = None,
    log_file: Path | None = None,
) -> list[str]:
    """Build one continuous command argument list."""

    result = ["stratum-mine", "--chunk-size", chunk_size]
    if start_nonce is not None:
        result.extend(["--start-nonce", start_nonce])
    if max_chunks is not None:
        result.extend(["--max-chunks", max_chunks])
    if max_reconnect_attempts is not None:
        result.extend(["--max-reconnect-attempts", max_reconnect_attempts])
    if log_file is not None:
        result.extend(["--log-file", str(log_file)])
    return result


def read_events(path: Path) -> list[dict[str, object]]:
    """Read independently parseable JSONL records."""

    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_command_appears_in_usage(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli_module.main([]) == 2
    assert "stratum-mine" in capsys.readouterr().err


def test_parse_continuous_plan_supports_unlimited_and_limited_forms() -> None:
    assert cli_module._parse_continuous_mining_plan(["--chunk-size", "10"]) == (
        ContinuousMiningPlan(0, 10, None)
    )
    assert cli_module._parse_continuous_mining_plan(
        ["--start-nonce", "7", "--max-chunks", "3", "--chunk-size", "10"]
    ) == ContinuousMiningPlan(7, 10, 3)


def test_parse_continuous_reconnect_policy_defaults_and_accepts_bounds() -> None:
    plan, policy, log_file = cli_module._parse_continuous_mining_arguments(["--chunk-size", "10"])
    assert plan == ContinuousMiningPlan(0, 10, None)
    assert policy.maximum_attempts == 5
    assert log_file is None

    _, disabled, _ = cli_module._parse_continuous_mining_arguments(
        ["--chunk-size", "10", "--max-reconnect-attempts", "0"]
    )
    _, maximum, _ = cli_module._parse_continuous_mining_arguments(
        ["--chunk-size", "10", "--max-reconnect-attempts", "100"]
    )
    assert disabled.maximum_attempts == 0
    assert maximum.maximum_attempts == 100


@pytest.mark.parametrize(
    "values",
    [
        [],
        ["--start-nonce", "1"],
        ["--chunk-size"],
        ["--unknown", "1", "--chunk-size", "1"],
        ["--chunk-size", "1", "--chunk-size", "2"],
        ["--chunk-size", "1", "--max-reconnect-attempts"],
        ["--chunk-size", "1", "--max-chunks", "1", "--max-chunks", "2"],
        [
            "--chunk-size",
            "1",
            "--max-reconnect-attempts",
            "1",
            "--max-reconnect-attempts",
            "2",
        ],
    ],
)
def test_parse_rejects_missing_duplicate_and_unknown_options(values: list[str]) -> None:
    with pytest.raises(ValueError):
        cli_module._parse_continuous_mining_plan(values)


@pytest.mark.parametrize(
    "value",
    ["", "0", "-1", "+1", "0x10", "1.0", "01", " 1", "1 ", "4294967297"],
)
@pytest.mark.parametrize("option", ["--chunk-size", "--max-chunks"])
def test_positive_options_require_strict_unpadded_ascii_decimal(
    option: str,
    value: str,
) -> None:
    values = ["--chunk-size", "1", "--max-chunks", "1"]
    values[values.index(option) + 1] = value
    with pytest.raises(ValueError, match=option):
        cli_module._parse_continuous_mining_plan(values)


@pytest.mark.parametrize(
    "value",
    ["", "-1", "+1", "0x10", "1.0", "00", "01", " 1", "1 ", "4294967296"],
)
def test_start_nonce_requires_canonical_in_range_decimal(value: str) -> None:
    with pytest.raises(ValueError, match="--start-nonce"):
        cli_module._parse_continuous_mining_plan(["--start-nonce", value, "--chunk-size", "1"])


@pytest.mark.parametrize(
    "value",
    ["", "-1", "+1", "0x10", "1.0", "00", "01", " 1", "1 ", "101"],
)
def test_reconnect_attempts_require_bounded_unpadded_ascii_decimal(value: str) -> None:
    with pytest.raises(ValueError, match="--max-reconnect-attempts"):
        cli_module._parse_continuous_mining_arguments(
            ["--chunk-size", "1", "--max-reconnect-attempts", value]
        )


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


def test_three_chunk_limit_prints_sanitized_aggregate_and_closes(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeClient()
    harness, signals = install_fakes(
        monkeypatch,
        client,
        SearchHarness(elapsed_values=deque([100, 300, 600])),
    )
    settings = make_settings()

    assert cli_module.main(arguments(start_nonce="7")) == 0

    assert [(start, stop) for _, start, stop in harness.search_calls] == [
        (7, 9),
        (9, 11),
        (11, 13),
    ]
    assert harness.backend_selection_calls == 1
    assert client.poll_timeouts == [0.25, 0.25, 0.0, 0.0, 0.0]
    assert harness.generated_sizes == [4]
    assert len(harness.prepare_calls) == 1
    assert client.submit_calls == []
    assert client.close_calls == 1
    assert signals.current == signals.previous
    assert len(signals.calls) == len(signals.previous) * 2
    output = capsys.readouterr().out
    assert "Maximum chunks: 3" in output
    assert "Compute backend: python" in output
    assert "Search strategy: sequential" in output
    assert "Chunks completed: 3" in output
    assert "Work variants used: 1" in output
    assert "Extra nonce 2 advances: 0" in output
    assert "Network-time rolls: 0" in output
    assert "Duplicate work ignored: 0" in output
    assert "Hashes checked: 6" in output
    assert "Elapsed time: 1000 ns" in output
    assert "Hashes per second: 6000000.00" in output
    assert "Result: chunk_limit_reached" in output
    assert settings.stratum_password not in output
    assert settings.stratum_username not in output
    assert harness.generated_extra_nonce_2 not in output


def test_controlled_nonce_boundary_plan_reports_safe_progression_totals(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    path = tmp_path / "progression.jsonl"
    client = FakeClient()
    harness, _ = install_fakes(monkeypatch, client, deterministic_log=True)

    assert (
        cli_module.main(
            arguments(
                start_nonce="4294967295",
                chunk_size="1",
                max_chunks="3",
                log_file=path,
            )
        )
        == 0
    )

    assert [(start, stop) for _, start, stop in harness.search_calls] == [
        (0xFFFFFFFF, 2**32),
        (0xFFFFFFFF, 2**32),
        (0xFFFFFFFF, 2**32),
    ]
    assert [extra for _, extra in harness.prepare_calls] == [
        "abababab",
        "abababac",
        "abababad",
    ]
    assert harness.generated_sizes == [4]
    output = capsys.readouterr().out
    assert "Work variants used: 3" in output
    assert "Extra nonce 2 advances: 2" in output
    assert "Extra nonce 2 cycles: 0" in output
    assert "Network-time rolls: 0" in output
    assert "Duplicate work ignored: 0" in output
    assert harness.generated_extra_nonce_2 not in output

    summary = summarize_jsonl(path)
    assert summary.work_variant_count == 3
    assert summary.extra_nonce_2_advance_count == 2
    assert summary.extra_nonce_2_cycle_count == 0
    assert summary.network_time_roll_count == 0
    assert summary.duplicate_work_ignored_count == 0
    assert harness.generated_extra_nonce_2 not in path.read_text(encoding="utf-8")


def test_omitted_max_chunks_prints_unlimited_and_signal_stops_after_chunk(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeClient()
    harness = SearchHarness(signal_trigger_call=1)
    harness, signals = install_fakes(monkeypatch, client, harness)
    harness.signal_trigger = signals.trigger

    assert cli_module.main(arguments(max_chunks=None)) == 0

    assert len(harness.search_calls) == 1
    assert client.poll_timeouts == [0.25, 0.25, 0.0]
    assert client.submit_calls == []
    assert client.close_calls == 1
    assert signals.current == signals.previous
    output = capsys.readouterr().out
    assert "Maximum chunks: unlimited" in output
    assert "Result: stopped_by_user" in output


def test_initial_wait_timeout_then_stop_is_responsive_and_does_not_search(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    signals_holder: list[SignalHarness] = []

    def stop_on_first_poll(call_number: int) -> None:
        if call_number == 1:
            signals_holder[0].trigger()

    client = FakeClient(notifications=[None], poll_hook=stop_on_first_poll)
    harness, signals = install_fakes(monkeypatch, client)
    signals_holder.append(signals)

    assert cli_module.main(arguments(max_chunks=None)) == 0

    assert client.poll_timeouts == [0.25]
    assert harness.generated_sizes == [4]
    assert harness.prepare_calls == []
    assert harness.search_calls == []
    assert client.close_calls == 1
    output = capsys.readouterr().out
    assert "Final difficulty: unavailable" in output
    assert "Chunks completed: 0" in output
    assert "Result: stopped_by_user" in output


def test_initial_timeouts_and_pre_difficulty_job_are_not_reused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient(notifications=[None, job("too-early"), difficulty(15000), job("usable")])
    harness, _ = install_fakes(monkeypatch, client)

    assert cli_module.main(arguments(max_chunks="1")) == 0

    assert client.poll_timeouts == [0.25, 0.25, 0.25, 0.25, 0.0]
    assert [prepared.job_id for prepared, _ in harness.prepare_calls] == ["usable"]
    assert harness.prepare_calls[0][0].difficulty == 15000


def test_initial_connection_failure_recovers_with_fresh_client_and_session(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    path = tmp_path / "recovered.jsonl"
    failed = FakeClient(handshake_failure=StratumConnectionError("private initial failure"))
    successful = FakeClient()
    harness, _ = install_fakes(
        monkeypatch,
        failed,
        deterministic_log=True,
        additional_clients=[successful],
    )

    assert (
        cli_module.main(
            arguments(
                max_chunks="1",
                max_reconnect_attempts="1",
                log_file=path,
            )
        )
        == 0
    )

    assert failed.close_calls == 1
    assert successful.close_calls == 1
    assert harness.reconnect_delays == [1.0]
    assert harness.generated_sizes == [4]
    output = capsys.readouterr().out
    assert "Reconnect attempts: 1" in output
    assert "Successful reconnects: 1" in output
    assert "Failed reconnect attempts: 0" in output
    assert "Sessions established: 1" in output
    assert "private initial failure" not in output
    records = read_events(path)
    recovery_events = [
        record["event"]
        for record in records
        if record["event"].startswith("stratum_reconnect")
        or record["event"] == "stratum_connection_lost"
    ]
    assert recovery_events == [
        "stratum_connection_lost",
        "stratum_reconnect_scheduled",
        "stratum_reconnect_attempted",
        "stratum_reconnect_succeeded",
    ]
    reconnect_success = next(
        record for record in records if record["event"] == "stratum_reconnect_succeeded"
    )
    assert reconnect_success["successful_reconnect_count"] == 1
    assert reconnect_success["session_index"] == 1
    serialized = path.read_text(encoding="utf-8")
    for sensitive in (
        "private initial failure",
        "secret-password",
        "worker.1234567890",
        "08000002",
        "abababab",
    ):
        assert sensitive not in serialized


@pytest.mark.parametrize(
    ("backend_name", "implementation", "parallel", "worker_count"),
    [
        ("native", "c", False, None),
        ("native-parallel", "c-threadpool", True, 4),
    ],
)
def test_poll_connection_loss_recovers_with_changed_negotiated_extra_nonce_size(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    backend_name: str,
    implementation: str,
    parallel: bool,
    worker_count: int | None,
) -> None:
    failed = FakeClient(
        notifications=[
            difficulty(),
            job(),
            None,
            StratumConnectionError("private poll failure"),
        ]
    )
    recovered = FakeClient(
        notifications=[difficulty(20000), job("new-session"), None],
        extra_nonce_2_size=2,
    )
    search_harness = SearchHarness(generated_extra_nonce_2_values=deque(["abababab", "cdcd"]))
    harness, _ = install_fakes(
        monkeypatch,
        failed,
        search_harness,
        additional_clients=[recovered],
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
    original_strategy_selector = cli_module._select_configured_search_strategy
    strategy_selection_calls = 0

    def select_strategy(settings: Settings) -> object:
        nonlocal strategy_selection_calls
        strategy_selection_calls += 1
        return original_strategy_selector(settings)

    monkeypatch.setattr(cli_module, "_select_configured_search_strategy", select_strategy)

    assert cli_module.main(arguments(max_chunks="2", max_reconnect_attempts="1")) == 0

    assert [(prepared.job_id, seed) for prepared, seed in harness.prepare_calls] == [
        ("initial-job", "abababab"),
        ("new-session", "cdcd"),
    ]
    assert [(work.job_id, start) for work, start, _ in harness.search_calls] == [
        ("initial-job", 0),
        ("new-session", 0),
    ]
    assert harness.generated_sizes == [4, 2]
    assert harness.backend_selection_calls == 1
    assert strategy_selection_calls == 1
    assert backend.close_calls == 1
    assert failed.close_calls == 1
    assert recovered.close_calls == 1
    output = capsys.readouterr().out
    assert f"Compute backend: {backend_name}" in output
    assert "Search strategy: sequential" in output
    if worker_count is not None:
        assert f"Compute workers: {worker_count}" in output
    assert "Extra nonce 2 size: 2" in output
    assert "Reconnect attempts: 1" in output
    assert "Sessions established: 2" in output
    assert "abababab" not in output
    assert "cdcd" not in output


def test_stop_during_reconnect_backoff_is_successful_and_creates_no_new_client(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    failed = FakeClient(handshake_failure=StratumConnectionError("private failure"))
    unused = FakeClient()
    search_harness = SearchHarness(stop_during_reconnect_wait=True)
    harness, signals = install_fakes(
        monkeypatch,
        failed,
        search_harness,
        additional_clients=[unused],
    )
    harness.signal_trigger = signals.trigger

    assert cli_module.main(arguments(max_chunks=None)) == 0

    assert harness.reconnect_delays == [1.0]
    assert failed.close_calls == 1
    assert unused.handshake_calls == 0
    assert unused.close_calls == 0
    assert harness.search_calls == []
    output = capsys.readouterr().out
    assert "Result: stopped_by_user" in output
    assert "Reconnect attempts: 0" in output
    assert "Sessions established: 0" in output


def test_reconnect_exhaustion_is_sanitized_runtime_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    path = tmp_path / "exhausted.jsonl"
    clients = [
        FakeClient(handshake_failure=StratumConnectionError(f"private failure {index}"))
        for index in range(3)
    ]
    harness, _ = install_fakes(
        monkeypatch,
        clients[0],
        deterministic_log=True,
        additional_clients=clients[1:],
    )

    assert (
        cli_module.main(
            arguments(
                max_chunks="1",
                max_reconnect_attempts="2",
                log_file=path,
            )
        )
        == 1
    )

    assert harness.reconnect_delays == [1.0, 2.0]
    assert [client.close_calls for client in clients] == [1, 1, 1]
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "Stratum reconnect attempts exhausted.\n"
    text = path.read_text(encoding="utf-8")
    assert "private failure" not in text
    records = read_events(path)
    assert records[-2]["event"] == "stratum_reconnect_exhausted"
    assert records[-1]["event"] == "command_failed"
    assert records[-1]["attempts"] == 2
    assert records[-1]["recovery_stage"] == "handshake"


def test_signal_returned_candidate_is_submitted_before_controlled_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient()
    harness = SearchHarness(match_call=1, signal_trigger_call=1)
    harness, signals = install_fakes(monkeypatch, client, harness)
    harness.signal_trigger = signals.trigger

    assert cli_module.main(arguments(max_chunks=None)) == 0

    assert client.submit_calls == [("initial-job", "abababab", "65f04abc", 0)]
    assert client.poll_timeouts == [0.25, 0.25, 0.0]


@pytest.mark.parametrize("accepted", [True, False])
def test_share_acceptance_and_rejection_are_terminal_successes(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    accepted: bool,
) -> None:
    client = FakeClient(submission_result=accepted)
    harness, _ = install_fakes(monkeypatch, client, SearchHarness(match_call=1))

    assert cli_module.main(arguments(max_chunks=None)) == 0

    assert len(harness.search_calls) == 1
    assert len(client.submit_calls) == 1
    assert f"Pool result: {'accepted' if accepted else 'rejected'}" in capsys.readouterr().out


def test_network_only_candidate_is_submitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient()
    install_fakes(
        monkeypatch,
        client,
        SearchHarness(match_call=1, match_flags=(False, True)),
    )

    assert cli_module.main(arguments(max_chunks=None)) == 0
    assert len(client.submit_calls) == 1


def test_submission_connection_failure_is_terminal_without_reconnect(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeClient(submission_failure=StratumConnectionError("private uncertain submission"))
    harness, _ = install_fakes(
        monkeypatch,
        client,
        SearchHarness(match_call=1),
    )

    assert cli_module.main(arguments(max_chunks=None)) == 1

    assert len(client.submit_calls) == 1
    assert harness.reconnect_delays == []
    assert client.close_calls == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "private uncertain submission" not in captured.err


def test_compute_backend_failure_is_terminal_without_reconnect_or_fallback(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeClient()
    private_detail = "private backend execution detail"
    harness = SearchHarness(
        search_failure=ComputeBackendExecutionError(private_detail),
    )
    harness, _ = install_fakes(monkeypatch, client, harness)

    assert cli_module.main(arguments(max_chunks=None)) == 1

    assert harness.backend_selection_calls == 1
    assert len(harness.search_calls) == 1
    assert harness.reconnect_delays == []
    assert client.close_calls == 1
    assert client.handshake_calls == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "Continuous Stratum mining failed.\n"
    assert private_detail not in captured.err


@pytest.mark.parametrize("outcome", ["stop", "recovery_exhaustion"])
def test_parallel_backend_closes_after_stop_and_recovery_exhaustion(
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
) -> None:
    client = FakeClient(
        notifications=(
            [difficulty(), job(), None, StratumConnectionError("private connection detail")]
            if outcome == "recovery_exhaustion"
            else None
        )
    )
    harness, signals = install_fakes(monkeypatch, client)
    if outcome == "stop":
        harness.signal_trigger_call = 1
        harness.signal_trigger = signals.trigger
    searcher = cli_module.search_nonce_range

    class ParallelFakeBackend:
        capabilities = ComputeBackendCapabilities(
            backend_name="native-parallel",
            display_name="Parallel fake",
            backend_kind="cpu",
            implementation="c-threadpool",
            supports_parallel_search=True,
            supports_cooperative_cancellation=False,
            supports_device_selection=False,
            deterministic_search_order=True,
            preferred_batch_size=None,
            available=True,
        )
        worker_count = 4

        def __init__(self) -> None:
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

    backend = ParallelFakeBackend()
    monkeypatch.setattr(
        cli_module,
        "_select_configured_compute_backend",
        lambda settings: backend,
    )

    status = cli_module.main(
        arguments(
            max_chunks=None if outcome == "stop" else "2",
            max_reconnect_attempts=("0" if outcome == "recovery_exhaustion" else None),
        )
    )

    assert status == (0 if outcome == "stop" else 1)
    assert backend.close_calls == 1
    assert signals.current == signals.previous


@pytest.mark.parametrize(
    ("failure_location", "failure"),
    [
        ("handshake", StratumConnectionError("private connection detail")),
        ("poll", StratumClientError("private polling detail")),
        ("prepare", ValueError("private preparation detail")),
        ("search", ValueError("private search detail")),
        ("submit", StratumClientError("private submission detail")),
    ],
)
def test_runtime_failures_are_sanitized_close_and_restore_signals(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure_location: str,
    failure: BaseException,
) -> None:
    client = FakeClient(
        handshake_failure=failure if failure_location == "handshake" else None,
        poll_failure=failure if failure_location == "poll" else None,
        submission_failure=failure if failure_location == "submit" else None,
    )
    harness = SearchHarness(
        match_call=1 if failure_location == "submit" else None,
        prepare_failure=failure if failure_location == "prepare" else None,
        search_failure=failure if failure_location == "search" else None,
    )
    _, signals = install_fakes(monkeypatch, client, harness)

    assert (
        cli_module.main(
            arguments(
                max_chunks="1",
                max_reconnect_attempts=("0" if failure_location == "handshake" else None),
            )
        )
        == 1
    )

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "private" not in captured.err
    assert "Traceback" not in captured.err
    assert client.close_calls == 1
    assert signals.current == signals.previous


def test_client_cleanup_failure_after_success_returns_one_and_restores_signals(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeClient(close_failure=RuntimeError("private cleanup detail"))
    _, signals = install_fakes(monkeypatch, client)

    assert cli_module.main(arguments(max_chunks="1")) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "Could not close the Stratum connection cleanly.\n"
    assert signals.current == signals.previous


def test_signal_install_failure_is_sanitized_before_client_creation(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeClient()
    install_fakes(monkeypatch, client)

    def fail_signal(signal_number: signal.Signals, handler: object) -> object:
        del signal_number, handler
        raise ValueError("private signal detail")

    monkeypatch.setattr(cli_module.signal, "signal", fail_signal)

    assert cli_module.main(arguments(max_chunks="1")) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "Continuous Stratum mining failed.\n"
    assert client.handshake_calls == 0
    assert client.close_calls == 0


def test_signal_restore_failure_returns_one_after_other_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeClient()
    _, signals = install_fakes(monkeypatch, client)
    registered_signal = cli_module.signal.signal
    restore_attempts = 0

    def fail_first_restore(signal_number: signal.Signals, handler: object) -> object:
        nonlocal restore_attempts
        if handler is signals.previous[signal_number]:
            restore_attempts += 1
            if restore_attempts == 1:
                raise ValueError("private restoration detail")
        return registered_signal(signal_number, handler)

    monkeypatch.setattr(cli_module.signal, "signal", fail_first_restore)

    assert cli_module.main(arguments(max_chunks="1")) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "Could not restore signal handlers cleanly.\n"
    assert restore_attempts == len(signals.previous)
    assert client.close_calls == 1


def test_logged_stop_is_ordered_sanitized_and_summary_compatible(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "continuous.jsonl"
    client = FakeClient()
    harness = SearchHarness(signal_trigger_call=1)
    harness, signals = install_fakes(
        monkeypatch,
        client,
        harness,
        deterministic_log=True,
    )
    harness.signal_trigger = signals.trigger

    assert cli_module.main(arguments(max_chunks=None, log_file=path)) == 0

    records = read_events(path)
    assert [record["event"] for record in records] == [
        "command_started",
        "compute_backend_selected",
        "search_strategy_selected",
        "stratum_authorized",
        "difficulty_received",
        "mining_job_received",
        "mining_work_advanced",
        "nonce_range_started",
        "nonce_range_completed",
        "mining_stop_requested",
        "command_completed",
    ]
    assert records[-1]["outcome"] == "stopped_by_user"
    assert records[7]["start_nonce"] == 0
    assert records[7]["stop_nonce"] == 2
    assert records[8]["hashes_checked"] == 2
    summary = summarize_jsonl(path)
    assert summary.command_counts == (("stratum-mine", 1),)
    assert summary.completed_nonce_range_count == 1
    assert summary.total_hashes_checked == 2

    text = path.read_text(encoding="utf-8")
    settings = make_settings()
    for forbidden in (
        settings.stratum_password,
        settings.stratum_username,
        settings.bitcoin_address,
        harness.generated_extra_nonce_2,
        "08000002",
    ):
        assert forbidden not in text


def test_pool_replacement_precedes_local_progression_events(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "waiting.jsonl"
    client = FakeClient(
        notifications=[
            difficulty(),
            job(),
            None,
            job("replacement"),
            None,
        ]
    )
    harness, _ = install_fakes(monkeypatch, client, deterministic_log=True)

    assert (
        cli_module.main(
            arguments(
                start_nonce="4294967295",
                chunk_size="10",
                max_chunks="2",
                log_file=path,
            )
        )
        == 0
    )

    assert [(work.job_id, start, stop) for work, start, stop in harness.search_calls] == [
        ("initial-job", 0xFFFFFFFF, 2**32),
        ("replacement", 0xFFFFFFFF, 2**32),
    ]
    events = [record["event"] for record in read_events(path)]
    exhausted = events.index("nonce_space_exhausted")
    replacement = events.index("mining_job_replaced")
    next_work = events.index("mining_work_advanced", replacement)
    next_range = events.index("nonce_range_started", replacement)
    assert exhausted < replacement < next_work < next_range
    assert "mining_waiting_for_job" not in events


@pytest.mark.parametrize(
    ("accepted", "expected_outcome"),
    [(True, "share_accepted"), (False, "share_rejected")],
)
def test_logged_share_outcome_is_terminal_and_summary_counts_submission(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    accepted: bool,
    expected_outcome: str,
) -> None:
    path = tmp_path / f"{expected_outcome}.jsonl"
    client = FakeClient(submission_result=accepted)
    install_fakes(
        monkeypatch,
        client,
        SearchHarness(match_call=1),
        deterministic_log=True,
    )

    assert cli_module.main(arguments(max_chunks=None, log_file=path)) == 0

    records = read_events(path)
    assert records[-1]["event"] == "command_completed"
    assert records[-1]["outcome"] == expected_outcome
    assert [record["event"] for record in records].count("share_candidate_found") == 1
    assert [record["event"] for record in records].count("share_submission_completed") == 1
    summary = summarize_jsonl(path)
    assert summary.share_candidate_count == 1
    assert summary.share_submission_count == 1
    assert summary.accepted_share_count == int(accepted)
    assert summary.rejected_share_count == int(not accepted)


def test_logs_summary_prints_continuous_command_in_stable_known_order(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    path = tmp_path / "summary.jsonl"
    client = FakeClient()
    install_fakes(monkeypatch, client, deterministic_log=True)
    assert cli_module.main(arguments(max_chunks="1", log_file=path)) == 0
    capsys.readouterr()

    assert cli_module.main(["logs-summary", "--log-file", str(path)]) == 0
    output = capsys.readouterr().out
    assert "stratum-mine-chunks: 0" in output
    assert "stratum-mine: 1" in output
