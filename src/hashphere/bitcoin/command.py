"""CLI boundary for read-only Bitcoin Core checks and bounded solo mining."""

from __future__ import annotations

import argparse
import math
import os
import signal
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from types import FrameType
from typing import Protocol

from dotenv import load_dotenv

from hashphere.bitcoin.rpc import BitcoinCoreRpcClient, BitcoinRpcError
from hashphere.bitcoin.solo import (
    SoloMiningOutcome,
    SoloMiningPlan,
    SoloMiningResult,
    run_solo_mining,
)
from hashphere.bitcoin.template import BlockTemplate, parse_block_template
from hashphere.compute import (
    LocalComputeProfileCapabilities,
    MiningComputeBackend,
    builtin_compute_backend_registry,
    close_compute_backend,
    select_compute_backend,
)
from hashphere.config import (
    BITCOIN_RPC_CHECK_FLAG,
    BLOCK_SUBMISSION_FLAG,
    DEFAULT_CUDA_DEVICE,
    DEFAULT_CUDA_DEVICES,
    DEFAULT_CUDA_THREADS_PER_BLOCK,
    TRUE_SOLO_FLAG,
    BitcoinRpcSettings,
    ComputeProfileOverrides,
    ResolvedComputeProfile,
    SoloCommandSettings,
    parse_compute_profile,
    parse_compute_profile_overrides_from_env,
    parse_cuda_devices,
    require_exact_opt_in,
    resolve_compute_profile,
)
from hashphere.mining import (
    MiningSearchStrategy,
    NonceSearchResult,
    StopController,
    select_search_strategy,
)
from hashphere.observability import EventLogError, EventSink, JsonlEventSink, NullEventSink

BITCOIN_COMMANDS = frozenset({"bitcoin-core-check", "solo-mine"})
_SUCCESSFUL_SOLO_OUTCOMES = frozenset(
    {
        SoloMiningOutcome.BLOCK_ACCEPTED,
        SoloMiningOutcome.CANDIDATE_SUPPRESSED,
        SoloMiningOutcome.CHUNK_LIMIT_REACHED,
        SoloMiningOutcome.RUNTIME_LIMIT_REACHED,
        SoloMiningOutcome.SAFE_PROGRESSION_EXHAUSTED,
        SoloMiningOutcome.STOPPED_BY_USER,
    }
)

type RpcClientFactory = Callable[[BitcoinRpcSettings], BitcoinCoreRpcClient]
type BackendSelector = Callable[..., MiningComputeBackend]


class _SignalError(RuntimeError):
    """Raised when solo signal handlers cannot be installed or restored."""


class _SoloSignalScope:
    """Translate SIGINT and SIGTERM into cooperative stop requests."""

    def __init__(self, controller: StopController) -> None:
        self._controller = controller
        self._previous: list[tuple[signal.Signals, object]] = []

    def install(self) -> None:
        try:
            for signal_number in _supported_signals():
                previous = signal.getsignal(signal_number)
                signal.signal(signal_number, self._handle)
                self._previous.append((signal_number, previous))
        except (OSError, RuntimeError, ValueError) as exc:
            self.restore()
            raise _SignalError("could not install solo stop handlers") from exc

    def restore(self) -> None:
        failed = False
        for signal_number, previous in reversed(self._previous):
            try:
                signal.signal(signal_number, previous)  # type: ignore[arg-type]
            except (OSError, RuntimeError, TypeError, ValueError):
                failed = True
        self._previous.clear()
        if failed:
            raise _SignalError("could not restore solo stop handlers")

    def _handle(self, signal_number: int, frame: FrameType | None) -> None:
        del signal_number, frame
        self._controller.request_stop()


def _supported_signals() -> tuple[signal.Signals, ...]:
    result = [signal.SIGINT]
    termination = getattr(signal, "SIGTERM", None)
    if isinstance(termination, signal.Signals) and termination not in result:
        result.append(termination)
    return tuple(result)


@dataclass(slots=True)
class _SoloEventObserver:
    """Convert internal lifecycle state into privacy-reviewed event fields."""

    events: EventSink
    result: SoloMiningResult | None = None

    def template_received(self, template: BlockTemplate, replacement: bool) -> None:
        self.events.emit(
            "solo_template_received",
            fields={
                "template_identity": template.fingerprint,
                "replacement": replacement,
            },
        )

    def template_replaced(
        self, previous: BlockTemplate, current: BlockTemplate, reason: str
    ) -> None:
        self.events.emit(
            "solo_template_replaced",
            fields={
                "previous_template_identity": previous.fingerprint,
                "template_identity": current.fingerprint,
                "reason": reason,
            },
        )

    def work_variant_started(
        self,
        variant: object,
        variant_index: int,
        coinbase_advance_count: int,
        timestamp_roll_count: int,
    ) -> None:
        identity = getattr(variant, "identity", None)
        if not isinstance(identity, str):
            raise ValueError("solo variant identity is invalid")
        self.events.emit(
            "solo_work_variant_started",
            fields={
                "work_identity": identity,
                "work_variant_index": variant_index,
                "coinbase_extra_nonce_advance_count": coinbase_advance_count,
                "timestamp_roll_count": timestamp_roll_count,
            },
        )

    def range_completed(self, variant: object, result: NonceSearchResult) -> None:
        del variant
        self.events.emit(
            "solo_nonce_range_completed",
            fields={
                "hashes_checked": result.hashes_checked,
                "elapsed_ns": result.elapsed_ns,
                "hashes_per_second": result.hashes_per_second,
                "candidate_found": result.match is not None,
            },
        )

    def coinbase_extra_nonce_advanced(self, advance_count: int) -> None:
        self.events.emit(
            "solo_coinbase_extra_nonce_advanced",
            fields={"advance_count": advance_count},
        )

    def timestamp_rolled(self, roll_count: int) -> None:
        self.events.emit("solo_timestamp_rolled", fields={"roll_count": roll_count})

    def candidate_found(self, variant: object, candidate: object) -> None:
        del variant, candidate
        self.events.emit("solo_candidate_found")

    def candidate_suppressed(self, reason: str, suppression_count: int) -> None:
        self.events.emit(
            "solo_candidate_suppressed",
            level="WARNING",
            fields={"reason": reason, "suppression_count": suppression_count},
        )

    def proposal_completed(self, outcome: object) -> None:
        accepted = getattr(outcome, "accepted", None)
        category = getattr(outcome, "category", None)
        if not isinstance(accepted, bool) or not isinstance(category, str):
            raise ValueError("proposal outcome is invalid")
        self.events.emit(
            "solo_block_proposal_completed",
            level="INFO" if accepted else "WARNING",
            fields={"accepted": accepted, "status_category": category},
        )

    def submission_completed(self, outcome: object) -> None:
        accepted = getattr(outcome, "accepted", None)
        category = getattr(outcome, "category", None)
        if not isinstance(accepted, bool) or not isinstance(category, str):
            raise ValueError("submission outcome is invalid")
        self.events.emit(
            "solo_block_submission_completed",
            level="INFO" if accepted else "WARNING",
            fields={"accepted": accepted, "status_category": category},
        )

    def completed(self, result: SoloMiningResult) -> None:
        self.result = result


class _Arguments(Protocol):
    command: str
    event_log: str | None


def run_bitcoin_command(
    arguments: Sequence[str],
    *,
    rpc_client_factory: RpcClientFactory = BitcoinCoreRpcClient,
    backend_selector: BackendSelector | None = None,
) -> int:
    """Parse and run one Bitcoin command without affecting ordinary CLI paths."""

    parser = _command_parser()
    try:
        parsed = parser.parse_args(list(arguments))
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2
    except argparse.ArgumentError as exc:
        print(f"Argument error: {exc}", file=sys.stderr)
        return 2
    if parsed.command == "bitcoin-core-check":
        return _with_events(
            parsed,
            lambda events: _run_readiness_check(events, rpc_client_factory),
        )
    if parsed.command == "solo-mine":
        return _with_events(
            parsed,
            lambda events: _run_solo_command(
                parsed,
                events,
                rpc_client_factory=rpc_client_factory,
                backend_selector=(
                    _default_backend_selector if backend_selector is None else backend_selector
                ),
            ),
        )
    return 2


def _command_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hashsphere", exit_on_error=False)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("bitcoin-core-check", exit_on_error=False)
    check.add_argument("--event-log")

    mine = commands.add_parser("solo-mine", exit_on_error=False)
    mine.add_argument("--profile")
    mine.add_argument("--backend")
    mine.add_argument("--workers", type=_positive_integer)
    mine.add_argument("--device", type=_nonnegative_integer)
    mine.add_argument("--devices")
    mine.add_argument("--threads-per-block", type=_positive_integer)
    mine.add_argument("--chunk-size", type=_positive_integer)
    mine.add_argument("--inter-range-delay-seconds", type=_nonnegative_float)
    mine.add_argument("--strategy", default=None)
    mine.add_argument("--start-nonce", type=_nonnegative_integer, default=0)
    mine.add_argument("--max-chunks", type=_positive_integer)
    mine.add_argument("--max-runtime-seconds", type=_positive_float)
    mine.add_argument("--template-poll-seconds", type=_positive_float, default=30.0)
    mine.add_argument("--max-time-roll-seconds", type=_nonnegative_integer, default=7_200)
    mine.add_argument("--event-log")
    return parser


def _with_events(arguments: _Arguments, operation: Callable[[EventSink], int]) -> int:
    try:
        events: EventSink = (
            NullEventSink()
            if arguments.event_log is None
            else JsonlEventSink(arguments.event_log, str(arguments.command))
        )
    except (EventLogError, TypeError, ValueError):
        print("Could not initialize structured event logging.", file=sys.stderr)
        return 2
    status = 1
    try:
        events.emit("command_started")
        status = operation(events)
    except EventLogError:
        print("Structured event logging failed.", file=sys.stderr)
        status = 1
    finally:
        try:
            events.close()
        except EventLogError:
            print("Could not close structured event logging cleanly.", file=sys.stderr)
            status = 1
    return status


def _run_readiness_check(events: EventSink, client_factory: RpcClientFactory) -> int:
    client: BitcoinCoreRpcClient | None = None
    try:
        load_dotenv()
        require_exact_opt_in(BITCOIN_RPC_CHECK_FLAG)
        command_settings = SoloCommandSettings.from_env()
        client = client_factory(BitcoinRpcSettings.from_env())
        chain_info = client.get_blockchain_info()
        if chain_info.initial_block_download:
            raise ValueError("Bitcoin Core initial block download is active")
        client.validate_address(command_settings.payout_address)
        template = parse_block_template(client.get_block_template())
        events.emit(
            "bitcoin_rpc_connected",
            fields={
                "chain": chain_info.chain,
                "initial_block_download": chain_info.initial_block_download,
            },
        )
        events.emit(
            "solo_template_received",
            fields={"template_identity": template.fingerprint, "replacement": False},
        )
        client.close()
        client = None
        events.emit("command_completed", fields={"outcome": "ready"})
    except (BitcoinRpcError, ValueError) as exc:
        category = _error_category(exc)
        _emit_command_failed(events, "readiness", category)
        print(f"Bitcoin Core readiness check failed ({category}).", file=sys.stderr)
        return 1
    finally:
        if client is not None:
            try:
                client.close()
            except BitcoinRpcError:
                print("Bitcoin Core RPC cleanup failed.", file=sys.stderr)
    print("Bitcoin Core readiness check passed.")
    print("RPC reachable: yes")
    print("Authentication accepted: yes")
    print(f"Chain: {chain_info.chain}")
    print(f"Initial block download: {'yes' if chain_info.initial_block_download else 'no'}")
    print("Template RPC: available")
    print("Payout destination: valid for connected chain")
    print("Proposal mode: required and checked only for a verified candidate")
    return 0


def _run_solo_command(
    arguments: argparse.Namespace,
    events: EventSink,
    *,
    rpc_client_factory: RpcClientFactory,
    backend_selector: BackendSelector,
) -> int:
    client: BitcoinCoreRpcClient | None = None
    backend: MiningComputeBackend | None = None
    signal_scope: _SoloSignalScope | None = None
    result: SoloMiningResult | None = None
    cleanup_failed = False
    try:
        load_dotenv()
        require_exact_opt_in(TRUE_SOLO_FLAG)
        require_exact_opt_in(BLOCK_SUBMISSION_FLAG)
        command_settings = SoloCommandSettings.from_env()
        plan, profile, strategy = _resolve_solo_policy(arguments)
        client = rpc_client_factory(BitcoinRpcSettings.from_env())
        chain_info = client.get_blockchain_info()
        if chain_info.initial_block_download:
            raise ValueError("Bitcoin Core initial block download is active")
        destination = client.validate_address(command_settings.payout_address)
        initial_template = parse_block_template(client.get_block_template())
        print(f"Bitcoin Core chain: {chain_info.chain}")
        print("Payout destination: configured and valid for this chain")
        print("Mining mode: direct Bitcoin Core true solo (no Stratum)")

        backend = backend_selector(
            profile.backend_name,
            worker_count=profile.worker_count or 2,
            cuda_device=profile.cuda_device or DEFAULT_CUDA_DEVICE,
            cuda_devices=(
                profile.cuda_devices
                or (
                    (profile.cuda_device,)
                    if profile.cuda_device is not None
                    else DEFAULT_CUDA_DEVICES
                )
            ),
            cuda_threads_per_block=(
                profile.cuda_threads_per_block or DEFAULT_CUDA_THREADS_PER_BLOCK
            ),
        )
        stop_controller = StopController(plan.max_runtime_seconds)
        signal_scope = _SoloSignalScope(stop_controller)
        signal_scope.install()
        events.emit(
            "bitcoin_rpc_connected",
            fields={
                "chain": chain_info.chain,
                "initial_block_download": chain_info.initial_block_download,
            },
        )
        _emit_profile(events, profile)
        _emit_backend(events, backend)
        _emit_strategy(events, strategy)
        observer = _SoloEventObserver(events)
        result = run_solo_mining(
            plan,
            chain=chain_info.chain,
            payout_script=destination.script_pub_key,
            initial_template=initial_template,
            backend=backend,
            strategy=strategy,
            stop_token=stop_controller,
            fetch_template=lambda: parse_block_template(client.get_block_template()),
            propose_block=client.propose_block,
            submit_block=client.submit_block,
            observer=observer,
        )
    except (BitcoinRpcError, RuntimeError, ValueError) as exc:
        category = _error_category(exc)
        _emit_command_failed(events, "solo_mining", category)
        print(f"Solo mining failed ({category}).", file=sys.stderr)
        return 1
    finally:
        if signal_scope is not None:
            try:
                signal_scope.restore()
            except _SignalError:
                cleanup_failed = True
        if backend is not None:
            try:
                close_compute_backend(backend)
            except RuntimeError:
                cleanup_failed = True
        if client is not None:
            try:
                client.close()
            except BitcoinRpcError:
                cleanup_failed = True
        if cleanup_failed and result is None:
            print("Solo command cleanup failed.", file=sys.stderr)

    if result is None:
        return 1
    if cleanup_failed:
        _emit_command_failed(events, "cleanup", "cleanup_failure")
        print("Solo command cleanup failed.", file=sys.stderr)
        return 1
    events.emit(
        "command_completed",
        fields={
            "outcome": result.outcome.value,
            "chunks_completed": result.chunks_completed,
            "templates_received": result.templates_received,
            "template_replacements": result.template_replacements,
            "work_variants": result.work_variants_used,
            "coinbase_extra_nonce_advances": result.coinbase_extra_nonce_advances,
            "timestamp_rolls": result.timestamp_rolls,
            "candidates": result.candidates_found,
            "candidate_suppressions": result.candidates_suppressed,
            "proposals": result.proposals_performed,
            "submissions": result.submissions_performed,
            "hashes_checked": result.total_hashes_checked,
        },
    )
    print(f"Solo mining outcome: {result.outcome.value}")
    print(f"Completed ranges: {result.chunks_completed}")
    print(f"Hashes checked: {result.total_hashes_checked}")
    return 0 if result.outcome in _SUCCESSFUL_SOLO_OUTCOMES else 1


def _resolve_solo_policy(
    arguments: argparse.Namespace,
) -> tuple[SoloMiningPlan, ResolvedComputeProfile, MiningSearchStrategy]:
    profile_name = parse_compute_profile(
        arguments.profile or os.getenv("HASHPHERE_COMPUTE_PROFILE", "auto")
    )
    cli_overrides = ComputeProfileOverrides(
        backend_name=arguments.backend,
        worker_count=arguments.workers,
        cuda_device=arguments.device,
        cuda_devices=(parse_cuda_devices(arguments.devices) if arguments.devices else None),
        cuda_threads_per_block=arguments.threads_per_block,
        chunk_size=arguments.chunk_size,
        inter_range_delay_seconds=arguments.inter_range_delay_seconds,
    )
    profile = resolve_compute_profile(
        profile_name,
        cli_overrides.merged_over(parse_compute_profile_overrides_from_env()),
        LocalComputeProfileCapabilities(),
    )
    strategy = select_search_strategy(
        arguments.strategy or os.getenv("HASHPHERE_SEARCH_STRATEGY", "sequential")
    )
    plan = SoloMiningPlan(
        start_nonce=arguments.start_nonce,
        chunk_size=profile.chunk_size,
        max_chunks=arguments.max_chunks,
        max_runtime_seconds=arguments.max_runtime_seconds,
        template_poll_seconds=arguments.template_poll_seconds,
        max_time_roll_seconds=arguments.max_time_roll_seconds,
        inter_range_delay_seconds=profile.inter_range_delay_seconds,
    )
    return plan, profile, strategy


def _emit_profile(events: EventSink, profile: ResolvedComputeProfile) -> None:
    events.emit(
        "compute_profile_resolved",
        fields={
            "requested_profile": profile.requested_profile,
            "effective_profile": profile.effective_profile,
            "effective_backend": profile.backend_name,
            "chunk_size": profile.chunk_size,
            "inter_range_delay_seconds": profile.inter_range_delay_seconds,
            "resolution_reason": profile.resolution_reason,
        },
    )


def _emit_backend(events: EventSink, backend: MiningComputeBackend) -> None:
    capabilities = backend.capabilities
    events.emit(
        "compute_backend_selected",
        fields={
            "backend_name": capabilities.backend_name,
            "backend_kind": capabilities.backend_kind,
            "implementation": capabilities.implementation,
            "supports_parallel_search": capabilities.supports_parallel_search,
            "supports_cooperative_cancellation": capabilities.supports_cooperative_cancellation,
        },
    )


def _default_backend_selector(
    backend_name: str,
    *,
    worker_count: int,
    cuda_device: int,
    cuda_devices: tuple[int, ...],
    cuda_threads_per_block: int,
) -> MiningComputeBackend:
    registry = builtin_compute_backend_registry(
        worker_count=worker_count,
        cuda_device=cuda_device,
        cuda_devices=cuda_devices,
        cuda_threads_per_block=cuda_threads_per_block,
        initialize_cuda=backend_name == "cuda",
        initialize_cuda_multi=backend_name == "cuda-multi",
    )
    return select_compute_backend(backend_name, registry)


def _emit_strategy(events: EventSink, strategy: MiningSearchStrategy) -> None:
    capabilities = strategy.capabilities
    events.emit(
        "search_strategy_selected",
        fields={
            "strategy_name": capabilities.strategy_name,
            "implementation": capabilities.implementation,
            "deterministic": capabilities.deterministic,
            "contiguous_parent_ranges": capabilities.contiguous_parent_ranges,
            "exhaustive": capabilities.exhaustive,
            "experimental": capabilities.experimental,
        },
    )


def _emit_command_failed(events: EventSink, stage: str, category: str) -> None:
    try:
        events.emit(
            "command_failed",
            level="ERROR",
            fields={"stage": stage, "error_category": category},
        )
    except EventLogError:
        pass


def _error_category(error: BaseException) -> str:
    if isinstance(error, BitcoinRpcError):
        return error.category
    if isinstance(error, _SignalError):
        return "signal_failure"
    name = type(error).__name__
    if "Compute" in name:
        return "compute_failure"
    if "Template" in name or "Coinbase" in name or "SoloBlock" in name:
        return "block_construction_failure"
    return "configuration_failure"


def _positive_integer(value: str) -> int:
    parsed = _nonnegative_integer(value)
    if parsed == 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def _nonnegative_integer(value: str) -> int:
    if (
        not value
        or not value.isascii()
        or not value.isdecimal()
        or (len(value) > 1 and value.startswith("0"))
    ):
        raise argparse.ArgumentTypeError("value must be an unpadded ASCII decimal integer")
    return int(value)


def _positive_float(value: str) -> float:
    parsed = _nonnegative_float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def _nonnegative_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("value must be a finite decimal") from exc
    if not math.isfinite(parsed) or parsed < 0:
        raise argparse.ArgumentTypeError("value must be a finite nonnegative decimal")
    return parsed
