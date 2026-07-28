"""Tests for the opt-in one-shot bounded Stratum mining command."""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest

import hashphere.__main__ as cli_module
from hashphere.compute import ComputeBackendSelectionError
from hashphere.config import Settings
from hashphere.mining import (
    CoinbaseError,
    MiningJob,
    NonceSearchError,
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


def make_settings() -> Settings:
    """Return deterministic synthetic settings."""

    return Settings(
        stratum_host="pool.example.com",
        stratum_port=3333,
        bitcoin_address="bc1qminingtestaddress",
        worker_name="bounded-rig",
        stratum_password="synthetic-password",
        compute_backend="cpu",
        compute_profile="lite",
    )


def difficulty_notification(difficulty: int | float = 10000) -> SetDifficultyNotification:
    """Build one parsed difficulty notification."""

    return SetDifficultyNotification(difficulty=difficulty)


def mining_notification(job_id: str = "job-current") -> MiningNotifyNotification:
    """Build one domain-valid parsed job notification."""

    return MiningNotifyNotification(
        job_id=job_id,
        previous_block_hash="00" * 32,
        coinbase_part_1="01000000cafebabe",
        coinbase_part_2="ffffffffdeadbeef",
        merkle_branches=("11" * 32,),
        version="20000000",
        network_bits="170fffff",
        network_time="65f04abc",
        clean_jobs=True,
    )


class FakeClient:
    """In-memory client double that never creates a network transport."""

    def __init__(
        self,
        notifications: list[object] | None = None,
        *,
        handshake_failure: BaseException | None = None,
        receive_failure: BaseException | None = None,
        submission_result: bool = True,
        submission_failure: BaseException | None = None,
        close_failure: BaseException | None = None,
    ) -> None:
        self.notifications = deque(notifications or [])
        self.handshake_failure = handshake_failure
        self.receive_failure = receive_failure
        self.submission_result = submission_result
        self.submission_failure = submission_failure
        self.close_failure = close_failure
        self.state = StratumClientState.DISCONNECTED
        self.handshake_calls = 0
        self.receive_calls = 0
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
        if not self.notifications:
            raise AssertionError("fake client has no queued notification")
        return self.notifications.popleft()

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
class MiningHarness:
    """Captured calls and configured deterministic mining behavior."""

    match_flags: tuple[bool, bool] | None = None
    elapsed_ns: int = 1_000_000
    prepare_failure: BaseException | None = None
    search_failure: BaseException | None = None
    generated_extra_nonce_2: str = "abababab"
    backend_selection_calls: int = 0
    generated_sizes: list[int] = field(default_factory=list)
    prepare_calls: list[tuple[MiningJob, str]] = field(default_factory=list)
    search_calls: list[tuple[PreparedMiningWork, int, int]] = field(default_factory=list)


def install_mining_fakes(
    monkeypatch: pytest.MonkeyPatch,
    client: FakeClient,
    harness: MiningHarness | None = None,
) -> MiningHarness:
    """Install deterministic configuration, client, generator, and mining fakes."""

    configured = harness if harness is not None else MiningHarness()
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

    def generate_extra_nonce_2(byte_size: int) -> str:
        configured.generated_sizes.append(byte_size)
        return configured.generated_extra_nonce_2

    def prepare(job: MiningJob, extra_nonce_2: str) -> PreparedMiningWork:
        configured.prepare_calls.append((job, extra_nonce_2))
        if configured.prepare_failure is not None:
            raise configured.prepare_failure
        return PreparedMiningWork(
            job_id=job.job_id,
            extra_nonce_2=extra_nonce_2,
            network_time=job.network_time,
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
        match: NonceSearchMatch | None = None
        hashes_checked = stop_nonce - start_nonce
        if configured.match_flags is not None:
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
            elapsed_ns=configured.elapsed_ns,
            match=match,
        )

    monkeypatch.setattr(cli_module, "StratumClient", client_factory)
    monkeypatch.setattr(cli_module, "_generate_extra_nonce_2", generate_extra_nonce_2)
    monkeypatch.setattr(cli_module, "prepare_mining_work", prepare)
    monkeypatch.setattr(cli_module, "search_nonce_range", search)
    original_selector = cli_module._select_configured_compute_backend

    def select_backend(received_settings: Settings) -> object:
        configured.backend_selection_calls += 1
        return original_selector(received_settings)

    monkeypatch.setattr(cli_module, "_select_configured_compute_backend", select_backend)
    monkeypatch.setenv("HASHPHERE_ENABLE_LIVE_STRATUM", "1")
    monkeypatch.setenv("HASHPHERE_ENABLE_LIVE_MINING", "1")
    return configured


def mining_arguments(
    hash_count: str = "3",
    start_nonce: str | None = None,
    log_file: str | Path | None = None,
) -> list[str]:
    """Build command arguments for a bounded mining invocation."""

    arguments = ["stratum-mine-once", "--hash-count", hash_count]
    if start_nonce is not None:
        arguments.extend(["--start-nonce", start_nonce])
    if log_file is not None:
        arguments.extend(["--log-file", str(log_file)])
    return arguments


def read_event_log(path: Path) -> list[dict[str, object]]:
    """Read independently parseable mining JSONL events."""

    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_mining_command_appears_in_usage(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli_module.main([]) == 2

    assert "stratum-mine-once" in capsys.readouterr().err


@pytest.mark.parametrize(
    "arguments",
    [
        ["stratum-mine-once"],
        ["stratum-mine-once", "--hash-count"],
        ["stratum-mine-once", "--unknown", "1"],
        ["stratum-mine-once", "--hash-count", "1", "extra"],
    ],
)
def test_malformed_mining_arguments_fail(
    arguments: list[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli_module.main(arguments) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Argument error:" in captured.err
    assert "Usage:" in captured.err


@pytest.mark.parametrize(
    "arguments",
    [
        ["stratum-mine-once", "--hash-count", "1", "--log-file"],
        ["stratum-mine-once", "--hash-count", "1", "--log-file", ""],
        ["stratum-mine-once", "--hash-count", "1", "--log-file", "   "],
        [
            "stratum-mine-once",
            "--hash-count",
            "1",
            "--log-file",
            "one",
            "--log-file",
            "two",
        ],
    ],
)
def test_mining_log_file_argument_errors_are_rejected(
    arguments: list[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli_module.main(arguments) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Argument error:" in captured.err
    assert "Usage:" in captured.err


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        (["--hash-count", "1"], (0, 1)),
        (["--start-nonce", "7", "--hash-count", "3"], (7, 10)),
        (["--hash-count", "3", "--start-nonce", "7"], (7, 10)),
        (["--start-nonce", "4294967295", "--hash-count", "1"], (4294967295, 2**32)),
        (["--hash-count", "4294967296"], (0, 2**32)),
    ],
)
def test_parse_mining_range_accepts_valid_half_open_ranges(
    arguments: list[str],
    expected: tuple[int, int],
) -> None:
    assert cli_module._parse_mining_range(arguments) == expected


@pytest.mark.parametrize(
    "value",
    ["", "0", "-1", "4294967297", "1.0", "0x10", "+1", " 1", "1 ", "true", "False"],
)
def test_parse_mining_range_rejects_invalid_hash_count(value: str) -> None:
    with pytest.raises(ValueError, match="--hash-count"):
        cli_module._parse_mining_range(["--hash-count", value])


@pytest.mark.parametrize(
    "value",
    ["", "-1", "4294967296", "1.0", "0x10", "+1", " 1", "1 ", "true"],
)
def test_parse_mining_range_rejects_invalid_start_nonce(value: str) -> None:
    with pytest.raises(ValueError, match="--start-nonce"):
        cli_module._parse_mining_range(["--start-nonce", value, "--hash-count", "1"])


def test_parse_mining_range_rejects_overflow_and_duplicate_options() -> None:
    with pytest.raises(ValueError, match="exceeds"):
        cli_module._parse_mining_range(["--start-nonce", "1", "--hash-count", "4294967296"])
    with pytest.raises(ValueError, match="only once"):
        cli_module._parse_mining_range(["--hash-count", "1", "--hash-count", "2"])


def test_live_stratum_opt_in_is_required(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeClient([difficulty_notification(), mining_notification()])
    install_mining_fakes(monkeypatch, client)
    monkeypatch.delenv("HASHPHERE_ENABLE_LIVE_STRATUM")

    assert cli_module.main(mining_arguments()) == 2
    assert "HASHPHERE_ENABLE_LIVE_STRATUM=1" in capsys.readouterr().err
    assert client.handshake_calls == 0


@pytest.mark.parametrize("selector", ["auto", "python", "cpu"])
def test_configured_backend_selectors_resolve_to_python(selector: str) -> None:
    settings = replace(make_settings(), compute_backend=selector)

    backend = cli_module._select_configured_compute_backend(settings)

    assert backend.capabilities.backend_name == "python"
    assert settings.compute_profile == "lite"


def test_unknown_backend_fails_before_client_construction_without_echoing_selector(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    private_selector = "private-looking-selector"
    settings = replace(make_settings(), compute_backend=private_selector)
    client_calls = 0

    def forbidden_client(*args: object, **kwargs: object) -> object:
        nonlocal client_calls
        client_calls += 1
        raise AssertionError("client must not be constructed")

    monkeypatch.setattr(cli_module.Settings, "from_env", classmethod(lambda cls: settings))
    monkeypatch.setattr(cli_module, "StratumClient", forbidden_client)
    monkeypatch.setenv("HASHPHERE_ENABLE_LIVE_STRATUM", "1")
    monkeypatch.setenv("HASHPHERE_ENABLE_LIVE_MINING", "1")

    assert cli_module.main(mining_arguments()) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "Compute backend configuration is invalid.\n"
    assert private_selector not in captured.err
    assert client_calls == 0


def test_unavailable_backend_fails_before_client_construction(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = replace(make_settings(), compute_backend="native")
    client_calls = 0

    def unavailable(received_settings: Settings) -> object:
        assert received_settings is settings
        raise ComputeBackendSelectionError("configured compute backend is unavailable")

    def forbidden_client(*args: object, **kwargs: object) -> object:
        nonlocal client_calls
        client_calls += 1
        raise AssertionError("client must not be constructed")

    monkeypatch.setattr(cli_module.Settings, "from_env", classmethod(lambda cls: settings))
    monkeypatch.setattr(cli_module, "_select_configured_compute_backend", unavailable)
    monkeypatch.setattr(cli_module, "StratumClient", forbidden_client)
    monkeypatch.setenv("HASHPHERE_ENABLE_LIVE_STRATUM", "1")
    monkeypatch.setenv("HASHPHERE_ENABLE_LIVE_MINING", "1")

    assert cli_module.main(mining_arguments()) == 2
    assert capsys.readouterr().err == "Compute backend configuration is invalid.\n"
    assert client_calls == 0


@pytest.mark.parametrize("value", [None, "", "true", "01", " 1"])
def test_live_mining_opt_in_must_equal_exactly_one(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    value: str | None,
) -> None:
    client = FakeClient([difficulty_notification(), mining_notification()])
    install_mining_fakes(monkeypatch, client)
    if value is None:
        monkeypatch.delenv("HASHPHERE_ENABLE_LIVE_MINING")
    else:
        monkeypatch.setenv("HASHPHERE_ENABLE_LIVE_MINING", value)

    assert cli_module.main(mining_arguments()) == 2
    assert "HASHPHERE_ENABLE_LIVE_MINING=1" in capsys.readouterr().err
    assert client.handshake_calls == 0


def test_exact_range_reaches_one_prepare_and_one_search(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeClient([difficulty_notification(), mining_notification()])
    harness = install_mining_fakes(monkeypatch, client)

    assert cli_module.main(mining_arguments("5", "9")) == 0

    assert len(harness.prepare_calls) == 1
    assert len(harness.search_calls) == 1
    assert harness.backend_selection_calls == 1
    assert harness.search_calls[0][1:] == (9, 14)
    assert client.submit_calls == []
    assert client.close_calls == 1
    assert "Compute backend: python" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("notifications", "expected_job_id", "expected_difficulty"),
    [
        (
            [difficulty_notification(100), mining_notification("job-after")],
            "job-after",
            100,
        ),
        (
            [
                mining_notification("job-before"),
                difficulty_notification(200),
                mining_notification("job-later"),
            ],
            "job-later",
            200,
        ),
        (
            [
                difficulty_notification(300),
                difficulty_notification(400),
                mining_notification("job-newest-difficulty"),
            ],
            "job-newest-difficulty",
            400,
        ),
    ],
)
def test_queued_notifications_preserve_difficulty_job_semantics(
    monkeypatch: pytest.MonkeyPatch,
    notifications: list[object],
    expected_job_id: str,
    expected_difficulty: int,
) -> None:
    client = FakeClient(notifications)
    harness = install_mining_fakes(monkeypatch, client)

    assert cli_module.main(mining_arguments()) == 0

    prepared_job = harness.prepare_calls[0][0]
    assert prepared_job.job_id == expected_job_id
    assert prepared_job.difficulty == expected_difficulty


@pytest.mark.parametrize(
    "notification",
    [
        object(),
        MiningNotifyNotification(
            job_id="malformed-job",
            previous_block_hash="short",
            coinbase_part_1="00",
            coinbase_part_2="00",
            merkle_branches=(),
            version="20000000",
            network_bits="170fffff",
            network_time="65f04abc",
            clean_jobs=True,
        ),
    ],
)
def test_malformed_or_unsupported_notifications_fail_safely(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    notification: object,
) -> None:
    client = FakeClient([difficulty_notification(), notification])
    install_mining_fakes(monkeypatch, client)

    assert cli_module.main(mining_arguments()) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "Bounded Stratum mining failed.\n"
    assert client.close_calls == 1


def test_extra_nonce_is_generated_once_and_reused_for_submission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient(
        [difficulty_notification(), mining_notification()],
        submission_result=True,
    )
    harness = install_mining_fakes(
        monkeypatch,
        client,
        MiningHarness(match_flags=(True, False)),
    )

    assert cli_module.main(mining_arguments("1")) == 0

    assert harness.generated_sizes == [4]
    assert harness.generated_extra_nonce_2 == "abababab"
    assert harness.prepare_calls[0][1] == "abababab"
    assert client.submit_calls == [("job-current", "abababab", "65f04abc", 0)]


def test_exhausted_range_reports_metrics_without_submission(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeClient([difficulty_notification(), mining_notification()])
    install_mining_fakes(monkeypatch, client, MiningHarness(elapsed_ns=0))

    assert cli_module.main(mining_arguments("3", "7")) == 0

    captured = capsys.readouterr()
    assert captured.err == ""
    assert "Endpoint: pool.example.com:3333" in captured.out
    assert "Username: bc1q…-rig" in captured.out
    assert "Job ID: job-current" in captured.out
    assert "Difficulty: 10000" in captured.out
    assert "Network bits: 170fffff" in captured.out
    assert "Extra nonce 2 size: 4" in captured.out
    assert "Start nonce: 7" in captured.out
    assert "Exclusive stop nonce: 10" in captured.out
    assert "Hashes checked: 3" in captured.out
    assert "Elapsed time: 0 ns" in captured.out
    assert "Hashes per second: unavailable" in captured.out
    assert "Result: no qualifying hash found" in captured.out
    assert client.submit_calls == []
    assert client.close_calls == 1


def test_exhausted_mining_jsonl_event_order_and_metrics(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "mining-exhausted.jsonl"
    client = FakeClient([difficulty_notification(), mining_notification()])
    install_mining_fakes(monkeypatch, client, MiningHarness(elapsed_ns=1_000_000))

    assert cli_module.main(mining_arguments("3", "7", path)) == 0

    records = read_event_log(path)
    assert [record["event"] for record in records] == [
        "command_started",
        "stratum_authorized",
        "difficulty_received",
        "mining_job_received",
        "nonce_range_started",
        "nonce_range_completed",
        "command_completed",
    ]
    assert records[4]["start_nonce"] == 7
    assert records[4]["stop_nonce"] == 10
    assert records[5]["hashes_checked"] == 3
    assert records[5]["elapsed_ns"] == 1_000_000
    assert records[5]["hashes_per_second"] == 3000.0
    assert records[5]["match_found"] is False
    assert records[-1]["outcome"] == "range_exhausted"
    assert all(not record["event"].startswith("share_") for record in records)
    assert client.submit_calls == []
    assert client.close_calls == 1


@pytest.mark.parametrize(
    ("flags", "pool_result", "expected_pool_text"),
    [
        ((True, False), True, "accepted"),
        ((True, False), False, "rejected"),
        ((False, True), True, "accepted"),
    ],
)
def test_matches_are_submitted_once_and_reported(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    flags: tuple[bool, bool],
    pool_result: bool,
    expected_pool_text: str,
) -> None:
    client = FakeClient(
        [difficulty_notification(), mining_notification()],
        submission_result=pool_result,
    )
    install_mining_fakes(monkeypatch, client, MiningHarness(match_flags=flags))

    assert cli_module.main(mining_arguments("1", "305419896")) == 0

    captured = capsys.readouterr()
    assert captured.err == ""
    assert "Matched nonce: 305419896" in captured.out
    assert "Submitted nonce hex: 78563412" in captured.out
    assert "Raw block hash: 12345678…00000000" in captured.out
    assert f"Meets share target: {str(flags[0]).lower()}" in captured.out
    assert f"Meets network target: {str(flags[1]).lower()}" in captured.out
    assert f"Pool result: {expected_pool_text}" in captured.out
    assert len(client.submit_calls) == 1
    assert client.close_calls == 1


@pytest.mark.parametrize(
    ("pool_result", "expected_outcome", "expected_level"),
    [
        (True, "share_accepted", "INFO"),
        (False, "share_rejected", "WARNING"),
    ],
)
def test_matched_mining_jsonl_event_order_and_submission_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    pool_result: bool,
    expected_outcome: str,
    expected_level: str,
) -> None:
    path = tmp_path / f"mining-{expected_outcome}.jsonl"
    client = FakeClient(
        [difficulty_notification(), mining_notification()],
        submission_result=pool_result,
    )
    install_mining_fakes(monkeypatch, client, MiningHarness(match_flags=(True, False)))

    assert cli_module.main(mining_arguments("1", "305419896", path)) == 0

    records = read_event_log(path)
    assert [record["event"] for record in records] == [
        "command_started",
        "stratum_authorized",
        "difficulty_received",
        "mining_job_received",
        "nonce_range_started",
        "nonce_range_completed",
        "share_candidate_found",
        "share_submission_completed",
        "command_completed",
    ]
    assert records[6]["nonce"] == 305419896
    assert records[6]["abbreviated_block_hash"] == "12345678…00000000"
    assert records[7]["accepted"] is pool_result
    assert records[-1]["outcome"] == expected_outcome
    assert records[-1]["level"] == expected_level
    assert len(client.submit_calls) == 1
    assert client.close_calls == 1


@pytest.mark.parametrize(
    ("stage", "failure"),
    [
        ("handshake", StratumConnectionError("sensitive connection detail")),
        ("receive", StratumClientError("sensitive protocol detail")),
        ("prepare", CoinbaseError("sensitive coinbase detail")),
        ("search", NonceSearchError("sensitive search detail")),
        ("submit", StratumClientError("sensitive submission detail")),
    ],
)
def test_runtime_failures_are_sanitized_nonzero_and_close(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    stage: str,
    failure: BaseException,
) -> None:
    client = FakeClient(
        [difficulty_notification(), mining_notification()],
        handshake_failure=failure if stage == "handshake" else None,
        receive_failure=failure if stage == "receive" else None,
        submission_failure=failure if stage == "submit" else None,
    )
    harness = MiningHarness(
        match_flags=(True, False) if stage == "submit" else None,
        prepare_failure=failure if stage == "prepare" else None,
        search_failure=failure if stage == "search" else None,
    )
    install_mining_fakes(monkeypatch, client, harness)

    assert cli_module.main(mining_arguments()) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "sensitive" not in captured.err
    assert client.close_calls == 1
    if stage == "submit":
        assert len(client.submit_calls) == 1


def test_mining_failure_writes_sanitized_command_failed_event(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "mining-failed.jsonl"
    sensitive_detail = "synthetic-password.bc1qminingtestaddress.abababab"
    client = FakeClient(
        [difficulty_notification(), mining_notification()],
        receive_failure=StratumClientError(sensitive_detail),
    )
    install_mining_fakes(monkeypatch, client)

    assert cli_module.main(mining_arguments(log_file=path)) == 1

    records = read_event_log(path)
    assert [record["event"] for record in records] == [
        "command_started",
        "stratum_authorized",
        "command_failed",
    ]
    assert records[-1]["stage"] == "bounded_mining"
    assert records[-1]["error_category"] == "StratumClientError"
    assert sensitive_detail not in path.read_text(encoding="utf-8")
    assert client.close_calls == 1


def test_configuration_failure_is_nonzero_without_client(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        cli_module.Settings,
        "from_env",
        classmethod(lambda cls: (_ for _ in ()).throw(ValueError("address is required"))),
    )
    monkeypatch.setenv("HASHPHERE_ENABLE_LIVE_STRATUM", "1")
    monkeypatch.setenv("HASHPHERE_ENABLE_LIVE_MINING", "1")

    assert cli_module.main(mining_arguments()) == 2
    assert "Configuration error: address is required" in capsys.readouterr().err


def test_cleanup_failure_does_not_hide_original_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeClient(
        handshake_failure=StratumClientError("sensitive original failure"),
        close_failure=RuntimeError("sensitive cleanup failure"),
    )
    install_mining_fakes(monkeypatch, client)

    assert cli_module.main(mining_arguments()) == 1
    captured = capsys.readouterr()
    assert captured.err == "Bounded Stratum mining failed.\n"
    assert client.close_calls == 1


def test_cleanup_failure_after_success_is_nonzero(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeClient(
        [difficulty_notification(), mining_notification()],
        close_failure=RuntimeError("sensitive cleanup failure"),
    )
    install_mining_fakes(monkeypatch, client)

    assert cli_module.main(mining_arguments()) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "Could not close the Stratum connection cleanly.\n"
    assert client.close_calls == 1


def test_output_omits_credentials_coinbase_and_raw_requests(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    job = mining_notification()
    client = FakeClient(
        [difficulty_notification(), job],
        submission_result=True,
    )
    install_mining_fakes(
        monkeypatch,
        client,
        MiningHarness(match_flags=(True, False)),
    )
    settings = make_settings()

    assert cli_module.main(mining_arguments("1")) == 0

    output = capsys.readouterr().out
    assert settings.stratum_password not in output
    assert settings.bitcoin_address not in output
    assert settings.stratum_username not in output
    assert job.coinbase_part_1 not in output
    assert job.coinbase_part_2 not in output
    assert "mining.submit" not in output
    assert "params" not in output


def test_mining_event_log_omits_credentials_nonces_coinbase_and_raw_payloads(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "sanitized.jsonl"
    job = mining_notification()
    client = FakeClient(
        [difficulty_notification(), job],
        submission_result=True,
    )
    harness = install_mining_fakes(
        monkeypatch,
        client,
        MiningHarness(match_flags=(True, False)),
    )
    settings = make_settings()

    assert cli_module.main(mining_arguments("1", log_file=path)) == 0

    log_text = path.read_text(encoding="utf-8")
    assert settings.stratum_password not in log_text
    assert settings.bitcoin_address not in log_text
    assert settings.stratum_username not in log_text
    assert "08000002" not in log_text
    assert harness.generated_extra_nonce_2 not in log_text
    assert job.coinbase_part_1 not in log_text
    assert job.coinbase_part_2 not in log_text
    assert "mining.authorize" not in log_text
    assert "mining.submit" not in log_text
    assert '"params"' not in log_text
