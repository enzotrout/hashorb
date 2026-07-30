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


@pytest.fixture(autouse=True)
def isolated_benchmark_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep offline CLI tests independent of an operator's local profile."""

    monkeypatch.setattr(cli_module, "load_dotenv", lambda: False)
    for name in (
        "HASHPHERE_COMPUTE_PROFILE",
        "HASHPHERE_COMPUTE_BACKEND",
        "HASHPHERE_COMPUTE_WORKERS",
        "HASHPHERE_CUDA_DEVICE",
        "HASHPHERE_CUDA_DEVICES",
        "HASHPHERE_CUDA_THREADS_PER_BLOCK",
        "HASHPHERE_CHUNK_SIZE",
        "HASHPHERE_INTER_RANGE_DELAY_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)


@dataclass(frozen=True, slots=True)
class FakeBenchmarkBackend:
    """Deterministic backend with captured benchmark calls."""

    capabilities: ComputeBackendCapabilities
    result: NonceSearchResult
    calls: list[tuple[PreparedMiningWork, int, int]]
    close_calls: list[None]
    worker_count: int | None = None
    device_ordinal: int | None = None
    device_ordinals: tuple[int, ...] | None = None
    close_failure: bool = False

    def search_nonce_range(
        self,
        work: PreparedMiningWork,
        start_nonce: int,
        stop_nonce: int,
    ) -> NonceSearchResult:
        self.calls.append((work, start_nonce, stop_nonce))
        return self.result

    def close(self) -> None:
        if self.close_failure:
            raise RuntimeError("private cleanup detail")
        self.close_calls.append(None)


def fake_backend(
    backend_name: str = "python",
    *,
    elapsed_ns: int = 10,
    match: NonceSearchMatch | None = None,
    close_failure: bool = False,
) -> FakeBenchmarkBackend:
    """Build one fake backend with a range-consistent result."""

    return FakeBenchmarkBackend(
        capabilities=ComputeBackendCapabilities(
            backend_name=backend_name,
            display_name=f"{backend_name} benchmark fake",
            backend_kind="gpu" if backend_name in {"cuda", "cuda-multi"} else "cpu",
            implementation=(
                backend_name
                if backend_name in {"cuda", "cuda-multi"}
                else "c-threadpool"
                if backend_name == "native-parallel"
                else "c"
                if backend_name == "native"
                else "python"
            ),
            supports_parallel_search=backend_name in {"cuda", "cuda-multi", "native-parallel"},
            supports_cooperative_cancellation=False,
            supports_device_selection=backend_name in {"cuda", "cuda-multi"},
            deterministic_search_order=True,
            preferred_batch_size=None,
            available=True,
        ),
        result=NonceSearchResult(
            start_nonce=7,
            stop_nonce=10,
            hashes_checked=(
                3
                if match is None or backend_name in {"cuda", "cuda-multi"}
                else match.nonce - 7 + 1
            ),
            elapsed_ns=elapsed_ns,
            match=match,
        ),
        calls=[],
        close_calls=[],
        worker_count=4 if backend_name == "native-parallel" else None,
        device_ordinal=3 if backend_name == "cuda" else None,
        device_ordinals=(0, 2) if backend_name == "cuda-multi" else None,
        close_failure=close_failure,
    )


def test_benchmark_fixture_is_stable_immutable_and_explicitly_synthetic() -> None:
    first = deterministic_benchmark_work()
    second = deterministic_benchmark_work()

    assert first == second
    assert first.job_id == "synthetic-compute-benchmark"
    assert first.header_prefix == bytes(range(76))
    assert first.share_target == first.network_target == 1


@pytest.mark.parametrize("backend_name", ["cuda", "python", "native", "native-parallel"])
def test_valid_benchmark_is_offline_sanitized_and_deterministic(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    backend_name: str,
) -> None:
    backend = fake_backend(backend_name)

    def select(
        received_name: str,
        received_workers: int | None,
        received_device: int | None,
    ) -> MiningComputeBackend:
        assert received_name == backend_name
        assert received_workers == (4 if backend_name == "native-parallel" else None)
        assert received_device == (3 if backend_name == "cuda" else None)
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
                *(["--workers", "4"] if backend_name == "native-parallel" else []),
                *(["--device", "3"] if backend_name == "cuda" else []),
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
    implementation = (
        "cuda"
        if backend_name == "cuda"
        else "c-threadpool"
        if backend_name == "native-parallel"
        else "c"
        if backend_name == "native"
        else "python"
    )
    workers_line = "Workers: 4\n" if backend_name == "native-parallel" else ""
    device_line = "CUDA device: 3\n" if backend_name == "cuda" else ""
    assert captured.out == (
        "Hashphere compute benchmark completed.\n"
        f"Backend: {backend_name}\n"
        f"Implementation: {implementation}\n"
        f"{workers_line}"
        f"{device_line}"
        "Hashes checked: 3\n"
        "Elapsed time: 10 ns\n"
        "Hashes per second: 300000000.00\n"
        "Result: range exhausted\n"
    )
    assert backend.calls == [(deterministic_benchmark_work(), 7, 10)]
    assert backend.close_calls == [None]


def test_zero_elapsed_rate_is_unavailable(
    capsys: pytest.CaptureFixture[str],
) -> None:
    backend = fake_backend(elapsed_ns=0)

    cli_module._print_compute_benchmark(backend, backend.result)

    assert "Hashes per second: unavailable" in capsys.readouterr().out


def test_multi_cuda_benchmark_requires_devices_and_reports_only_safe_ordinals(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    backend = fake_backend("cuda-multi")

    def select(
        name: str,
        workers: int | None,
        device: int | None,
        devices: tuple[int, ...],
    ) -> MiningComputeBackend:
        assert (name, workers, device, devices) == ("cuda-multi", None, None, (0, 2))
        return backend

    monkeypatch.setattr(cli_module, "_select_benchmark_compute_backend", select)

    assert (
        cli_module.main(
            [
                "compute-benchmark",
                "--backend",
                "cuda-multi",
                "--devices",
                "2,0",
                "--start-nonce",
                "7",
                "--hash-count",
                "3",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "Backend: cuda-multi" in output
    assert "Implementation: cuda-multi" in output
    assert "CUDA device count: 2" in output
    assert "CUDA devices: 0,2" in output


def test_multi_cuda_benchmark_rejects_missing_and_malformed_devices(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        cli_module.main(["compute-benchmark", "--backend", "cuda-multi", "--hash-count", "1"]) == 2
    )
    assert (
        cli_module.main(
            [
                "compute-benchmark",
                "--backend",
                "cuda-multi",
                "--devices",
                "0,0",
                "--hash-count",
                "1",
            ]
        )
        == 2
    )
    assert "--devices" in capsys.readouterr().err


def test_repeated_benchmark_separates_first_warmup_and_measured_runs(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    backend = fake_backend("cuda", elapsed_ns=10)
    monkeypatch.setattr(
        cli_module,
        "_select_benchmark_compute_backend",
        lambda name, workers, device: backend,
    )

    assert (
        cli_module.main(
            [
                "compute-benchmark",
                "--backend",
                "cuda",
                "--device",
                "3",
                "--hash-count",
                "3",
                "--warmup-runs",
                "2",
                "--repetitions",
                "3",
            ]
        )
        == 0
    )

    output = capsys.readouterr().out
    assert "Hashphere repeated compute benchmark completed." in output
    assert "First launch: 10 ns" in output
    assert "Warmup runs: 2" in output
    assert "Measured repetitions: 3" in output
    assert "Median elapsed time: 10 ns" in output
    assert "Minimum hashes per second: 300000000.00" in output
    assert "Maximum hashes per second: 300000000.00" in output
    assert "Initialization:" in output
    assert "Total backend-call wall time:" in output
    assert "Cleanup:" in output
    assert len(backend.calls) == 6
    assert backend.close_calls == [None]


def test_candidate_output_omits_candidate_and_fixture_values(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    private_digest = bytes.fromhex("ab" * 32)
    match = NonceSearchMatch(7, private_digest, True, False)
    backend = fake_backend(match=match)
    monkeypatch.setattr(
        cli_module,
        "_select_benchmark_compute_backend",
        lambda name, workers, device: backend,
    )

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
        ["--backend", "python", "--hash-count", "1", "--warmup-runs", "-1"],
        ["--backend", "python", "--hash-count", "1", "--warmup-runs", "101"],
        ["--backend", "python", "--hash-count", "1", "--repetitions", "0"],
        ["--backend", "python", "--hash-count", "1", "--repetitions", "101"],
        ["--backend", "python", "--hash-count", "1", "--repetitions", "1.5"],
        ["--backend", "python", "--backend", "native", "--hash-count", "1"],
        ["--backend", "python", "--workers", "2", "--hash-count", "1"],
        ["--backend", "native", "--workers", "2", "--hash-count", "1"],
        ["--backend", "cuda", "--workers", "2", "--hash-count", "1"],
        ["--backend", "python", "--device", "0", "--hash-count", "1"],
        ["--backend", "cuda", "--device", "00", "--hash-count", "1"],
        ["--backend", "cuda", "--device", "+1", "--hash-count", "1"],
        ["--backend", "cuda", "--device", "-1", "--hash-count", "1"],
        ["--backend", "cuda", "--device", "1.0", "--hash-count", "1"],
        ["--backend", "cuda", "--device", "2147483648", "--hash-count", "1"],
        ["--backend", "native-parallel", "--workers", "0", "--hash-count", "1"],
        ["--backend", "native-parallel", "--workers", "01", "--hash-count", "1"],
        ["--backend", "native-parallel", "--workers", "+2", "--hash-count", "1"],
        ["--backend", "native-parallel", "--workers", "2.0", "--hash-count", "1"],
        ["--backend", "native-parallel", "--workers", "257", "--hash-count", "1"],
        [
            "--backend",
            "native-parallel",
            "--workers",
            "2",
            "--workers",
            "3",
            "--hash-count",
            "1",
        ],
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
    def unavailable(
        name: str,
        workers: int | None,
        device: int | None,
    ) -> MiningComputeBackend:
        del name, workers, device
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

    def select(
        name: str,
        workers: int | None,
        device: int | None,
    ) -> MiningComputeBackend:
        nonlocal selections
        selections += 1
        assert name == "native"
        assert workers is None
        assert device is None
        return FailingBackend()

    monkeypatch.setattr(cli_module, "_select_benchmark_compute_backend", select)

    assert cli_module.main(["compute-benchmark", "--backend", "native", "--hash-count", "1"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "Compute benchmark failed.\n"
    assert "private" not in captured.err
    assert selections == 1


@pytest.mark.parametrize("backend_name", ["python", "native", "native-parallel"])
def test_actual_backend_completes_one_hash_when_available(
    capsys: pytest.CaptureFixture[str],
    backend_name: str,
) -> None:
    registry = builtin_compute_backend_registry()
    capabilities = {item.backend_name: item for item in registry.list_capabilities()}
    if not capabilities[backend_name].available:
        pytest.skip(f"{backend_name} backend is unavailable")

    assert (
        cli_module.main(
            [
                "compute-benchmark",
                "--backend",
                backend_name,
                *(["--workers", "2"] if backend_name == "native-parallel" else []),
                "--hash-count",
                "1",
            ]
        )
        == 0
    )
    captured = capsys.readouterr()
    assert captured.err == ""
    assert f"Backend: {backend_name}" in captured.out
    assert "Hashes checked: 1" in captured.out
    if backend_name == "native-parallel":
        assert "Workers: 2" in captured.out


def test_parallel_benchmark_defaults_to_two_workers(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    backend = fake_backend("native-parallel")
    received: list[tuple[str, int | None, int | None]] = []

    def select(
        name: str,
        workers: int | None,
        device: int | None,
    ) -> MiningComputeBackend:
        received.append((name, workers, device))
        return backend

    monkeypatch.setattr(cli_module, "_select_benchmark_compute_backend", select)

    assert (
        cli_module.main(["compute-benchmark", "--backend", "native-parallel", "--hash-count", "3"])
        == 0
    )
    assert received == [("native-parallel", 2, None)]
    assert "Workers: 4" in capsys.readouterr().out


def test_benchmark_cleanup_failure_returns_one_after_success(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    backend = fake_backend("native-parallel", close_failure=True)
    monkeypatch.setattr(
        cli_module,
        "_select_benchmark_compute_backend",
        lambda name, workers, device: backend,
    )

    assert (
        cli_module.main(["compute-benchmark", "--backend", "native-parallel", "--hash-count", "3"])
        == 1
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "Compute benchmark cleanup failed.\n"
    assert "private" not in captured.err
