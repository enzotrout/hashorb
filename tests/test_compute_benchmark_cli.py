"""Tests for the deterministic offline compute benchmark command."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

import hashphere.__main__ as cli_module
from hashphere.compute import (
    ComputeBackendCapabilities,
    ComputeBackendExecutionError,
    ComputeBackendSelectionError,
    MiningComputeBackend,
    builtin_compute_backend_registry,
    deterministic_benchmark_work,
)
from hashphere.mining import NonceSearchMatch, NonceSearchResult, PreparedMiningWork


@dataclass(frozen=True, slots=True)
class FakeBenchmarkBackend:
    """Deterministic backend with captured benchmark calls."""

    capabilities: ComputeBackendCapabilities
    result: NonceSearchResult
    calls: list[tuple[PreparedMiningWork, int, int]]

    def search_nonce_range(
        self,
        work: PreparedMiningWork,
        start_nonce: int,
        stop_nonce: int,
    ) -> NonceSearchResult:
        self.calls.append((work, start_nonce, stop_nonce))
        return self.result


def fake_backend(
    backend_name: str = "python",
    *,
    elapsed_ns: int = 10,
    match: NonceSearchMatch | None = None,
) -> FakeBenchmarkBackend:
    """Build one fake backend with a range-consistent result."""

    return FakeBenchmarkBackend(
        capabilities=ComputeBackendCapabilities(
            backend_name=backend_name,
            display_name=f"{backend_name} benchmark fake",
            backend_kind="cpu",
            implementation="c" if backend_name == "native" else "python",
            supports_parallel_search=False,
            supports_cooperative_cancellation=False,
            supports_device_selection=False,
            deterministic_search_order=True,
            preferred_batch_size=None,
            available=True,
        ),
        result=NonceSearchResult(
            start_nonce=7,
            stop_nonce=10,
            hashes_checked=3 if match is None else match.nonce - 7 + 1,
            elapsed_ns=elapsed_ns,
            match=match,
        ),
        calls=[],
    )


def test_benchmark_fixture_is_stable_immutable_and_explicitly_synthetic() -> None:
    first = deterministic_benchmark_work()
    second = deterministic_benchmark_work()

    assert first == second
    assert first.job_id == "synthetic-compute-benchmark"
    assert first.header_prefix == bytes(range(76))
    assert first.share_target == first.network_target == 1


@pytest.mark.parametrize("backend_name", ["python", "native"])
def test_valid_benchmark_is_offline_sanitized_and_deterministic(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    backend_name: str,
) -> None:
    backend = fake_backend(backend_name)

    def select(received_name: str) -> MiningComputeBackend:
        assert received_name == backend_name
        return backend

    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("live configuration, networking, or event logging was accessed")

    monkeypatch.setattr(cli_module, "_select_benchmark_compute_backend", select)
    monkeypatch.setattr(cli_module.Settings, "from_env", classmethod(forbidden))
    monkeypatch.setattr(cli_module, "StratumClient", forbidden)
    monkeypatch.setattr(cli_module, "JsonlEventSink", forbidden)

    assert (
        cli_module.main(
            [
                "compute-benchmark",
                "--backend",
                backend_name,
                "--start-nonce",
                "7",
                "--hash-count",
                "3",
            ]
        )
        == 0
    )

    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == (
        "Hashphere compute benchmark completed.\n"
        f"Backend: {backend_name}\n"
        f"Implementation: {'c' if backend_name == 'native' else 'python'}\n"
        "Hashes checked: 3\n"
        "Elapsed time: 10 ns\n"
        "Hashes per second: 300000000.00\n"
        "Result: range exhausted\n"
    )
    assert backend.calls == [(deterministic_benchmark_work(), 7, 10)]


def test_zero_elapsed_rate_is_unavailable(
    capsys: pytest.CaptureFixture[str],
) -> None:
    backend = fake_backend(elapsed_ns=0)

    cli_module._print_compute_benchmark(backend, backend.result)

    assert "Hashes per second: unavailable" in capsys.readouterr().out


def test_candidate_output_omits_candidate_and_fixture_values(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    private_digest = bytes.fromhex("ab" * 32)
    match = NonceSearchMatch(7, private_digest, True, False)
    backend = fake_backend(match=match)
    monkeypatch.setattr(cli_module, "_select_benchmark_compute_backend", lambda name: backend)

    assert (
        cli_module.main(
            [
                "compute-benchmark",
                "--backend",
                "python",
                "--start-nonce",
                "7",
                "--hash-count",
                "3",
            ]
        )
        == 0
    )

    captured = capsys.readouterr()
    assert private_digest.hex() not in captured.out + captured.err
    assert "0001020304050607" not in captured.out + captured.err
    assert "Result: candidate found" in captured.out
    assert "Nonce:" not in captured.out


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["--backend", "python"],
        ["--hash-count", "1"],
        ["--backend", "auto", "--hash-count", "1"],
        ["--backend", "python", "--hash-count", "0"],
        ["--backend", "python", "--hash-count", "01"],
        ["--backend", "python", "--hash-count", "+1"],
        ["--backend", "python", "--hash-count", "1.0"],
        ["--backend", "python", "--start-nonce", "01", "--hash-count", "1"],
        ["--backend", "python", "--start-nonce", "4294967295", "--hash-count", "2"],
        ["--backend", "python", "--hash-count", "1", "--unknown", "x"],
        ["--backend", "python", "--backend", "native", "--hash-count", "1"],
    ],
)
def test_malformed_benchmark_options_return_two(
    capsys: pytest.CaptureFixture[str],
    arguments: list[str],
) -> None:
    assert cli_module.main(["compute-benchmark", *arguments]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "compute-benchmark" in captured.err


def test_unavailable_backend_returns_two_without_raw_selection_detail(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def unavailable(name: str) -> MiningComputeBackend:
        del name
        raise ComputeBackendSelectionError("private compiler path")

    monkeypatch.setattr(cli_module, "_select_benchmark_compute_backend", unavailable)

    assert cli_module.main(["compute-benchmark", "--backend", "native", "--hash-count", "1"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "Compute benchmark backend is unavailable or invalid.\n"
    assert "private" not in captured.err


def test_backend_execution_failure_returns_one_without_fallback_or_raw_detail(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FailingBackend:
        capabilities = fake_backend("native").capabilities

        def search_nonce_range(
            self,
            work: PreparedMiningWork,
            start_nonce: int,
            stop_nonce: int,
        ) -> NonceSearchResult:
            del work, start_nonce, stop_nonce
            raise ComputeBackendExecutionError("private native trace")

    selections = 0

    def select(name: str) -> MiningComputeBackend:
        nonlocal selections
        selections += 1
        assert name == "native"
        return FailingBackend()

    monkeypatch.setattr(cli_module, "_select_benchmark_compute_backend", select)

    assert cli_module.main(["compute-benchmark", "--backend", "native", "--hash-count", "1"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "Compute benchmark failed.\n"
    assert "private" not in captured.err
    assert selections == 1


@pytest.mark.parametrize("backend_name", ["python", "native"])
def test_actual_backend_completes_one_hash_when_available(
    capsys: pytest.CaptureFixture[str],
    backend_name: str,
) -> None:
    registry = builtin_compute_backend_registry()
    capabilities = {item.backend_name: item for item in registry.list_capabilities()}
    if not capabilities[backend_name].available:
        pytest.skip(f"{backend_name} backend is unavailable")

    assert (
        cli_module.main(["compute-benchmark", "--backend", backend_name, "--hash-count", "1"]) == 0
    )
    captured = capsys.readouterr()
    assert captured.err == ""
    assert f"Backend: {backend_name}" in captured.out
    assert "Hashes checked: 1" in captured.out
