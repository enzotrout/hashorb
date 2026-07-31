"""Offline command-boundary tests for Bitcoin Core readiness and solo mining."""

from __future__ import annotations

import json
import signal
from pathlib import Path

import pytest

import hashphere.bitcoin.command as command_module
import hashphere.config.bitcoin_rpc as rpc_settings_module
import hashphere.config.solo as solo_settings_module
from hashphere.bitcoin.command import run_bitcoin_command
from hashphere.bitcoin.rpc import (
    BlockchainInfo,
    PayoutDestination,
    ProposalOutcome,
    SubmissionOutcome,
)
from hashphere.bitcoin.template import calculate_hash_merkle_root
from hashphere.compute.python import PythonSequentialBackend
from hashphere.crypto import double_sha256
from hashphere.mining.target import decode_compact_target
from hashphere.observability import summarize_jsonl

_PAYOUT = "bcrt1qsyntheticdestination000000000000000000000"
_PASSWORD = "synthetic-rpc-password"
_SCRIPT = bytes.fromhex("0014" + "61" * 20)
_COMMITMENT_PREFIX = bytes.fromhex("6a24aa21a9ed")


class FakeClient:
    """Strict command fake with no socket, wallet, or node dependency."""

    def __init__(self, template: dict[str, object] | None = None) -> None:
        self.template = _raw_template() if template is None else template
        self.blockchain_calls = 0
        self.addresses: list[str] = []
        self.template_calls = 0
        self.proposals: list[bytes] = []
        self.submissions: list[bytes] = []
        self.closed = False

    def get_blockchain_info(self) -> BlockchainInfo:
        self.blockchain_calls += 1
        return BlockchainInfo("regtest", 100, 100, False)

    def validate_address(self, address: str) -> PayoutDestination:
        self.addresses.append(address)
        return PayoutDestination(_SCRIPT)

    def get_block_template(self) -> dict[str, object]:
        self.template_calls += 1
        return dict(self.template)

    def propose_block(self, block: bytes) -> ProposalOutcome:
        self.proposals.append(block)
        return ProposalOutcome(True, "accepted")

    def submit_block(self, block: bytes) -> SubmissionOutcome:
        self.submissions.append(block)
        return SubmissionOutcome(True, "accepted")

    def close(self) -> None:
        self.closed = True


def _raw_template() -> dict[str, object]:
    witness_root = calculate_hash_merkle_root((bytes(32),))
    commitment = _COMMITMENT_PREFIX + double_sha256(witness_root + bytes(32))
    target = decode_compact_target("207fffff")
    return {
        "previousblockhash": "21" * 32,
        "version": 0x20000000,
        "bits": "207fffff",
        "target": f"{target:064x}",
        "height": 101,
        "curtime": 1_700_000_001,
        "mintime": 1_700_000_000,
        "transactions": [],
        "coinbasevalue": 5_000_000_000,
        "coinbaseaux": {"flags": "51"},
        "rules": ["csv", "segwit", "taproot"],
        "mutable": ["time", "transactions", "prevblock"],
        "sizelimit": 1_000_000,
        "weightlimit": 4_000_000,
        "default_witness_commitment": commitment.hex(),
    }


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(command_module, "load_dotenv", lambda: False)
    monkeypatch.setattr(rpc_settings_module, "load_dotenv", lambda: False)
    monkeypatch.setattr(solo_settings_module, "load_dotenv", lambda: False)
    for name in (
        "HASHPHERE_ENABLE_BITCOIN_RPC_CHECK",
        "HASHPHERE_ENABLE_TRUE_SOLO",
        "HASHPHERE_ENABLE_BLOCK_SUBMISSION",
        "HASHPHERE_SOLO_PAYOUT_ADDRESS",
        "HASHPHERE_BITCOIN_RPC_HOST",
        "HASHPHERE_BITCOIN_RPC_PORT",
        "HASHPHERE_BITCOIN_RPC_USER",
        "HASHPHERE_BITCOIN_RPC_PASSWORD",
        "HASHPHERE_BITCOIN_RPC_COOKIE_FILE",
        "HASHPHERE_BITCOIN_RPC_TIMEOUT_SECONDS",
        "HASHPHERE_COMPUTE_PROFILE",
        "HASHPHERE_COMPUTE_BACKEND",
        "HASHPHERE_COMPUTE_WORKERS",
        "HASHPHERE_SEARCH_STRATEGY",
        "HASHPHERE_CHUNK_SIZE",
        "HASHPHERE_INTER_RANGE_DELAY_SECONDS",
        "HASHPHERE_STRATUM_HOST",
        "HASHPHERE_STRATUM_PORT",
        "HASHPHERE_BITCOIN_ADDRESS",
    ):
        monkeypatch.delenv(name, raising=False)


def _rpc_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HASHPHERE_SOLO_PAYOUT_ADDRESS", _PAYOUT)
    monkeypatch.setenv("HASHPHERE_BITCOIN_RPC_USER", "synthetic-user")
    monkeypatch.setenv("HASHPHERE_BITCOIN_RPC_PASSWORD", _PASSWORD)


def _solo_arguments(*extra: str) -> list[str]:
    return [
        "solo-mine",
        "--profile",
        "custom",
        "--backend",
        "python",
        "--chunk-size",
        "128",
        "--max-chunks",
        "1",
        *extra,
    ]


def test_help_has_no_rpc_profile_or_package_side_effects(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = 0

    def factory(settings: object) -> FakeClient:
        nonlocal calls
        del settings
        calls += 1
        return FakeClient()

    assert run_bitcoin_command(["solo-mine", "--help"], rpc_client_factory=factory) == 0  # type: ignore[arg-type]
    assert run_bitcoin_command(["bitcoin-core-check", "--help"], rpc_client_factory=factory) == 0  # type: ignore[arg-type]
    assert calls == 0
    assert "solo-mine" in capsys.readouterr().out


def test_readiness_requires_its_own_opt_in_before_rpc(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _rpc_environment(monkeypatch)
    calls = 0

    def factory(settings: object) -> FakeClient:
        nonlocal calls
        del settings
        calls += 1
        return FakeClient()

    status = run_bitcoin_command(["bitcoin-core-check"], rpc_client_factory=factory)  # type: ignore[arg-type]

    assert status == 1
    assert calls == 0
    output = capsys.readouterr()
    assert "configuration_failure" in output.err
    assert _PAYOUT not in output.err
    assert _PASSWORD not in output.err


def test_readiness_is_read_only_sanitized_and_closes_rpc(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    _rpc_environment(monkeypatch)
    monkeypatch.setenv("HASHPHERE_ENABLE_BITCOIN_RPC_CHECK", "1")
    client = FakeClient()
    log = tmp_path / "readiness.jsonl"

    status = run_bitcoin_command(
        ["bitcoin-core-check", "--event-log", str(log)],
        rpc_client_factory=lambda settings: client,  # type: ignore[arg-type]
    )

    assert status == 0
    assert client.blockchain_calls == 1
    assert client.template_calls == 1
    assert client.addresses == [_PAYOUT]
    assert client.proposals == client.submissions == []
    assert client.closed
    output = capsys.readouterr().out
    assert "Chain: regtest" in output
    assert "Template RPC: available" in output
    contents = log.read_text(encoding="utf-8")
    records = [json.loads(line) for line in contents.splitlines()]
    assert [record["event"] for record in records] == [
        "command_started",
        "bitcoin_rpc_connected",
        "bitcoin_readiness_stage",
        "bitcoin_readiness_stage",
        "bitcoin_readiness_stage",
        "bitcoin_readiness_stage",
        "solo_template_received",
        "command_completed",
    ]
    assert [
        record["stage"] for record in records if record["event"] == "bitcoin_readiness_stage"
    ] == [
        "rpc_authenticated",
        "chain_verified",
        "synchronization_verified",
        "template_rpc_reachable",
    ]
    for secret in (_PAYOUT, _PASSWORD, _SCRIPT.hex(), "21" * 32, "207fffff"):
        assert secret not in output
        assert secret not in contents
    summary = summarize_jsonl(log)
    assert summary.completed_run_count == 1
    assert dict(summary.completion_outcome_counts) == {"ready": 1}


def test_readiness_template_failure_emits_only_sanitized_diagnostic_stages(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    _rpc_environment(monkeypatch)
    monkeypatch.setenv("HASHPHERE_ENABLE_BITCOIN_RPC_CHECK", "1")
    template = _raw_template()
    private_value = "private-template-value"
    template["version"] = private_value
    client = FakeClient(template)
    log = tmp_path / "readiness-failure.jsonl"

    status = run_bitcoin_command(
        ["bitcoin-core-check", "--event-log", str(log)],
        rpc_client_factory=lambda settings: client,  # type: ignore[arg-type]
    )

    assert status == 1
    assert client.template_calls == 1
    assert client.proposals == client.submissions == []
    assert client.closed
    output = capsys.readouterr()
    contents = log.read_text(encoding="utf-8")
    records = [json.loads(line) for line in contents.splitlines()]
    assert [record["event"] for record in records] == [
        "command_started",
        "bitcoin_rpc_connected",
        "bitcoin_readiness_stage",
        "bitcoin_readiness_stage",
        "bitcoin_readiness_stage",
        "bitcoin_readiness_stage",
        "command_failed",
    ]
    failure = records[-1]
    assert failure["error_category"] == "template_parse_failure"
    assert failure["template_error_category"] == "invalid_type"
    assert failure["template_field_path"] == "version"
    assert failure["template_expected_kind"] == "bounded integer"
    assert failure["template_observed_condition"] == "wrong_type"
    for private_material in (private_value, _PAYOUT, _PASSWORD, _SCRIPT.hex()):
        assert private_material not in output.out
        assert private_material not in output.err
        assert private_material not in contents


def test_solo_requires_both_distinct_opt_ins_before_rpc(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _rpc_environment(monkeypatch)
    calls = 0

    def factory(settings: object) -> FakeClient:
        nonlocal calls
        del settings
        calls += 1
        return FakeClient()

    monkeypatch.setenv("HASHPHERE_ENABLE_TRUE_SOLO", "1")
    status = run_bitcoin_command(_solo_arguments(), rpc_client_factory=factory)  # type: ignore[arg-type]
    assert status == 1
    assert calls == 0
    assert "configuration_failure" in capsys.readouterr().err


def test_solo_uses_no_stratum_configuration_and_submits_complete_block_once(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    _rpc_environment(monkeypatch)
    monkeypatch.setenv("HASHPHERE_ENABLE_TRUE_SOLO", "1")
    monkeypatch.setenv("HASHPHERE_ENABLE_BLOCK_SUBMISSION", "1")
    monkeypatch.setenv("HASHPHERE_STRATUM_HOST", "invalid\nstratum")
    monkeypatch.setenv("HASHPHERE_STRATUM_PORT", "not-a-port")
    client = FakeClient()
    backend_options: list[dict[str, object]] = []

    def select_backend(name: str, **options: object) -> PythonSequentialBackend:
        assert name == "python"
        backend_options.append(options)
        return PythonSequentialBackend()

    log = tmp_path / "solo.jsonl"
    status = run_bitcoin_command(
        _solo_arguments("--event-log", str(log)),
        rpc_client_factory=lambda settings: client,  # type: ignore[arg-type]
        backend_selector=select_backend,
    )

    assert status == 0
    assert len(client.proposals) == len(client.submissions) == 1
    assert client.proposals[0] == client.submissions[0]
    assert len(client.submissions[0]) > 80
    assert client.closed
    assert backend_options[0]["worker_count"] == 2
    output = capsys.readouterr().out
    assert "direct Bitcoin Core true solo (no Stratum)" in output
    assert "block_accepted" in output
    contents = log.read_text(encoding="utf-8")
    records = [json.loads(line) for line in contents.splitlines()]
    assert [record["event"] for record in records].count("command_completed") == 1
    assert [record["event"] for record in records].count("solo_block_submission_completed") == 1
    for secret in (
        _PAYOUT,
        _PASSWORD,
        _SCRIPT.hex(),
        "21" * 32,
        "207fffff",
        client.submissions[0].hex(),
    ):
        assert secret not in output
        assert secret not in contents
    summary = summarize_jsonl(log)
    assert summary.solo_chain_counts == (("regtest", 1),)
    assert summary.solo_template_count == 3
    assert summary.solo_work_variant_count == 1
    assert summary.solo_completed_nonce_range_count == 1
    assert summary.solo_candidate_count == 1
    assert summary.solo_proposal_outcome_counts == (("accepted", 1),)
    assert summary.solo_submission_outcome_counts == (("accepted", 1),)
    assert summary.solo_accepted_block_count == 1
    assert summary.share_candidate_count == summary.share_submission_count == 0


def test_sanitized_proposal_rejection_event_suppresses_submission(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    class RejectingClient(FakeClient):
        def propose_block(self, block: bytes) -> ProposalOutcome:
            self.proposals.append(block)
            return ProposalOutcome(False, "bad_coinbase_height")

    _rpc_environment(monkeypatch)
    monkeypatch.setenv("HASHPHERE_ENABLE_TRUE_SOLO", "1")
    monkeypatch.setenv("HASHPHERE_ENABLE_BLOCK_SUBMISSION", "1")
    client = RejectingClient()
    log = tmp_path / "rejected.jsonl"

    status = run_bitcoin_command(
        _solo_arguments("--event-log", str(log)),
        rpc_client_factory=lambda settings: client,  # type: ignore[arg-type]
        backend_selector=lambda name, **options: PythonSequentialBackend(),
    )

    assert status == 1
    assert len(client.proposals) == 1
    assert client.submissions == []
    contents = log.read_text(encoding="utf-8")
    output = capsys.readouterr().out
    assert "bad_coinbase_height" in contents
    for private_value in (_PAYOUT, _PASSWORD, _SCRIPT.hex(), client.proposals[0].hex()):
        assert private_value not in contents
        assert private_value not in output


def test_solo_rejects_unbounded_invocation_before_rpc(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _rpc_environment(monkeypatch)
    monkeypatch.setenv("HASHPHERE_ENABLE_TRUE_SOLO", "1")
    monkeypatch.setenv("HASHPHERE_ENABLE_BLOCK_SUBMISSION", "1")
    calls = 0

    def factory(settings: object) -> FakeClient:
        nonlocal calls
        del settings
        calls += 1
        return FakeClient()

    arguments = _solo_arguments()
    del arguments[-2:]
    status = run_bitcoin_command(arguments, rpc_client_factory=factory)  # type: ignore[arg-type]

    assert status == 1
    assert calls == 0
    assert "configuration_failure" in capsys.readouterr().err


def test_solo_argument_errors_have_no_rpc_side_effect(
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls = 0

    def factory(settings: object) -> FakeClient:
        nonlocal calls
        del settings
        calls += 1
        return FakeClient()

    status = run_bitcoin_command(
        ["solo-mine", "--max-chunks", "01"],
        rpc_client_factory=factory,  # type: ignore[arg-type]
    )
    assert status == 2
    assert calls == 0
    assert "unpadded" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("profile", "extra", "expected_backend"),
    [
        ("lite", (), "python"),
        ("auto", (), "python"),
        ("max", (), "python"),
        ("custom", ("--backend", "python", "--chunk-size", "128"), "python"),
    ],
)
def test_all_profiles_drive_the_same_solo_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
    profile: str,
    extra: tuple[str, ...],
    expected_backend: str,
) -> None:
    _rpc_environment(monkeypatch)
    monkeypatch.setenv("HASHPHERE_ENABLE_TRUE_SOLO", "1")
    monkeypatch.setenv("HASHPHERE_ENABLE_BLOCK_SUBMISSION", "1")

    class CpuOnlyCapabilities:
        def logical_cpu_count(self) -> int:
            return 1

        def native_available(self) -> bool:
            return False

        def cuda_available(self, device_ordinal: int, threads_per_block: int) -> bool:
            del device_ordinal, threads_per_block
            return False

        def cuda_multi_available(
            self, device_ordinals: tuple[int, ...], threads_per_block: int
        ) -> bool:
            del device_ordinals, threads_per_block
            return False

    monkeypatch.setattr(command_module, "LocalComputeProfileCapabilities", CpuOnlyCapabilities)
    selected: list[str] = []

    def backend(name: str, **options: object) -> PythonSequentialBackend:
        del options
        selected.append(name)
        return PythonSequentialBackend()

    status = run_bitcoin_command(
        ["solo-mine", "--profile", profile, *extra, "--max-chunks", "1"],
        rpc_client_factory=lambda settings: FakeClient(),  # type: ignore[arg-type]
        backend_selector=backend,
    )

    assert status == 0
    assert selected == [expected_backend]


@pytest.mark.parametrize(
    ("device_arguments", "expected_backend"),
    [(("--device", "0"), "cuda"), (("--devices", "0,1"), "cuda-multi")],
)
def test_mocked_cuda_profile_selection_reaches_existing_backend_boundary(
    monkeypatch: pytest.MonkeyPatch,
    device_arguments: tuple[str, ...],
    expected_backend: str,
) -> None:
    _rpc_environment(monkeypatch)
    monkeypatch.setenv("HASHPHERE_ENABLE_TRUE_SOLO", "1")
    monkeypatch.setenv("HASHPHERE_ENABLE_BLOCK_SUBMISSION", "1")

    class CudaCapabilities:
        def logical_cpu_count(self) -> int:
            return 1

        def native_available(self) -> bool:
            return False

        def cuda_available(self, device_ordinal: int, threads_per_block: int) -> bool:
            del device_ordinal, threads_per_block
            return True

        def cuda_multi_available(
            self, device_ordinals: tuple[int, ...], threads_per_block: int
        ) -> bool:
            del device_ordinals, threads_per_block
            return True

    monkeypatch.setattr(command_module, "LocalComputeProfileCapabilities", CudaCapabilities)
    selected: list[str] = []

    def backend(name: str, **options: object) -> PythonSequentialBackend:
        del options
        selected.append(name)
        return PythonSequentialBackend()

    status = run_bitcoin_command(
        [
            "solo-mine",
            "--profile",
            "auto",
            *device_arguments,
            "--max-chunks",
            "1",
        ],
        rpc_client_factory=lambda settings: FakeClient(),  # type: ignore[arg-type]
        backend_selector=backend,
    )

    assert status == 0
    assert selected == [expected_backend]


def test_solo_signal_scope_translates_and_restores_portable_handlers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    installed: dict[signal.Signals, object] = {}
    previous = object()
    controller = command_module.StopController()
    scope = command_module._SoloSignalScope(controller)

    monkeypatch.setattr(command_module.signal, "getsignal", lambda number: previous)
    monkeypatch.setattr(
        command_module.signal,
        "signal",
        lambda number, handler: installed.__setitem__(number, handler),
    )

    scope.install()
    handler = installed[signal.SIGINT]
    assert callable(handler)
    handler(signal.SIGINT, None)
    assert controller.stop_requested
    scope.restore()
    assert installed[signal.SIGINT] is previous
