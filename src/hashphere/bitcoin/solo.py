"""Bounded, stop-aware Bitcoin Core true-solo mining lifecycle."""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, runtime_checkable

from hashphere.bitcoin.block import SoloWorkVariant, assemble_solo_block, prepare_solo_work
from hashphere.bitcoin.coinbase import MAX_COINBASE_EXTRA_NONCE, next_coinbase_extra_nonce
from hashphere.bitcoin.rpc import BitcoinRpcError, ProposalOutcome, SubmissionOutcome
from hashphere.bitcoin.template import BlockTemplate
from hashphere.compute.backend import MiningComputeBackend
from hashphere.mining.continuous import MAX_RUNTIME_SECONDS, StopToken
from hashphere.mining.header import hash_block_header
from hashphere.mining.search import NonceSearchResult
from hashphere.mining.strategy import MiningSearchStrategy, validate_search_strategy_compatibility
from hashphere.mining.target import hash_meets_target

NONCE_LIMIT = 1 << 32
MAX_SOLO_CHUNKS = NONCE_LIMIT
MAX_TEMPLATE_POLL_SECONDS = 300.0
MAX_INTER_RANGE_DELAY_SECONDS = 60.0
_PACING_SLICE_SECONDS = 0.1

type TemplateFetcher = Callable[[], BlockTemplate]
type BlockProposer = Callable[[bytes], ProposalOutcome]
type BlockSubmitter = Callable[[bytes], SubmissionOutcome]
type TemplateRefresher = Callable[[], bool]
type StopAwareWaiter = Callable[[float, StopToken], None]


class SoloMiningError(RuntimeError):
    """Base error for true-solo lifecycle validation and execution."""


class SoloMiningValidationError(SoloMiningError, ValueError):
    """Raised when lifecycle input or a collaborator result is invalid."""


class SoloMiningOutcome(StrEnum):
    """One controlled terminal category for a bounded solo session."""

    STOPPED_BY_USER = "stopped_by_user"
    RUNTIME_LIMIT_REACHED = "runtime_limit_reached"
    CHUNK_LIMIT_REACHED = "chunk_limit_reached"
    SAFE_PROGRESSION_EXHAUSTED = "safe_progression_exhausted"
    PROPOSAL_REJECTED = "proposal_rejected"
    PROPOSAL_UNAVAILABLE = "proposal_unavailable"
    CANDIDATE_SUPPRESSED = "candidate_suppressed"
    CANDIDATE_FOUND_SUBMISSION_DISABLED = "candidate_found_submission_disabled"
    BLOCK_ACCEPTED = "block_accepted"
    BLOCK_REJECTED = "block_rejected"
    RPC_FAILURE = "rpc_failure"


@dataclass(frozen=True, slots=True)
class SoloMiningPlan:
    """Finite work and polling policy for one explicitly authorized run."""

    start_nonce: int
    chunk_size: int
    max_chunks: int | None = None
    max_runtime_seconds: float | None = None
    template_poll_seconds: float = 30.0
    max_time_roll_seconds: int = 7_200
    inter_range_delay_seconds: float = 0.0

    def __post_init__(self) -> None:
        """Require at least one session bound and prevent busy polling."""

        _bounded_integer(self.start_nonce, "start_nonce", minimum=0, maximum=NONCE_LIMIT - 1)
        _bounded_integer(self.chunk_size, "chunk_size", minimum=1, maximum=NONCE_LIMIT)
        if self.max_chunks is not None:
            _bounded_integer(self.max_chunks, "max_chunks", minimum=1, maximum=MAX_SOLO_CHUNKS)
        if self.max_runtime_seconds is not None:
            _bounded_number(
                self.max_runtime_seconds,
                "max_runtime_seconds",
                minimum_exclusive=0,
                maximum=MAX_RUNTIME_SECONDS,
            )
        if self.max_chunks is None and self.max_runtime_seconds is None:
            raise SoloMiningValidationError("solo mining requires a chunk or runtime limit")
        _bounded_number(
            self.template_poll_seconds,
            "template_poll_seconds",
            minimum_exclusive=0,
            maximum=MAX_TEMPLATE_POLL_SECONDS,
        )
        _bounded_integer(
            self.max_time_roll_seconds,
            "max_time_roll_seconds",
            minimum=0,
            maximum=7_200,
        )
        if (
            not isinstance(self.inter_range_delay_seconds, (int, float))
            or isinstance(self.inter_range_delay_seconds, bool)
            or not math.isfinite(self.inter_range_delay_seconds)
            or not 0 <= self.inter_range_delay_seconds <= MAX_INTER_RANGE_DELAY_SECONDS
        ):
            raise SoloMiningValidationError(
                "inter_range_delay_seconds must be finite and between 0 and 60"
            )


@dataclass(frozen=True, slots=True)
class SoloMiningResult:
    """Sanitized aggregate state for one and only one terminal outcome."""

    outcome: SoloMiningOutcome
    chain: str
    final_template_fingerprint: str
    proposal_category: str | None
    submission_category: str | None
    chunks_completed: int
    templates_received: int
    template_replacements: int
    work_variants_used: int
    coinbase_extra_nonce_advances: int
    timestamp_rolls: int
    candidates_found: int
    candidates_suppressed: int
    proposals_performed: int
    submissions_performed: int
    total_hashes_checked: int
    total_elapsed_ns: int


@runtime_checkable
class SoloMiningObserver(Protocol):
    """Passive sanitized lifecycle notifications."""

    def template_received(self, template: BlockTemplate, replacement: bool) -> None:
        """Observe one strict template response."""

    def template_replaced(
        self, previous: BlockTemplate, current: BlockTemplate, reason: str
    ) -> None:
        """Observe effective work replacement."""

    def work_variant_started(
        self,
        variant: SoloWorkVariant,
        variant_index: int,
        coinbase_advance_count: int,
        timestamp_roll_count: int,
    ) -> None:
        """Observe first use of an effective header variant."""

    def range_completed(self, variant: SoloWorkVariant, result: NonceSearchResult) -> None:
        """Observe one exact backend result."""

    def coinbase_extra_nonce_advanced(self, advance_count: int) -> None:
        """Observe a safe cumulative progression counter."""

    def timestamp_rolled(self, roll_count: int) -> None:
        """Observe a safe cumulative timestamp-roll counter."""

    def candidate_found(self, variant: SoloWorkVariant, submission_enabled: bool) -> None:
        """Observe one locally verified current candidate."""

    def candidate_suppressed(self, reason: str, suppression_count: int) -> None:
        """Observe one stale or stopped candidate suppression."""

    def proposal_completed(self, outcome: ProposalOutcome) -> None:
        """Observe one strict proposal category."""

    def submission_completed(self, outcome: SubmissionOutcome) -> None:
        """Observe one strict submission category."""

    def completed(self, result: SoloMiningResult) -> None:
        """Observe the single terminal result."""


class NullSoloMiningObserver:
    """No-op observer preserving the same call contract."""

    def template_received(self, template: BlockTemplate, replacement: bool) -> None:
        del template, replacement

    def template_replaced(
        self, previous: BlockTemplate, current: BlockTemplate, reason: str
    ) -> None:
        del previous, current, reason

    def work_variant_started(
        self,
        variant: SoloWorkVariant,
        variant_index: int,
        coinbase_advance_count: int,
        timestamp_roll_count: int,
    ) -> None:
        del variant, variant_index, coinbase_advance_count, timestamp_roll_count

    def range_completed(self, variant: SoloWorkVariant, result: NonceSearchResult) -> None:
        del variant, result

    def coinbase_extra_nonce_advanced(self, advance_count: int) -> None:
        del advance_count

    def timestamp_rolled(self, roll_count: int) -> None:
        del roll_count

    def candidate_found(self, variant: SoloWorkVariant, submission_enabled: bool) -> None:
        del variant, submission_enabled

    def candidate_suppressed(self, reason: str, suppression_count: int) -> None:
        del reason, suppression_count

    def proposal_completed(self, outcome: ProposalOutcome) -> None:
        del outcome

    def submission_completed(self, outcome: SubmissionOutcome) -> None:
        del outcome

    def completed(self, result: SoloMiningResult) -> None:
        del result


@dataclass(frozen=True, slots=True)
class CandidatePolicyResult:
    """Sanitized terminal decision returned by one candidate capability."""

    outcome: SoloMiningOutcome
    proposal_category: str | None = None
    submission_category: str | None = None
    suppressions: int = 0
    suppression_reason: str | None = None
    proposals: int = 0
    submissions: int = 0


@runtime_checkable
class SoloCandidatePolicy(Protocol):
    """Capability boundary applied only after independent candidate verification."""

    def handle_verified_candidate(
        self,
        variant: SoloWorkVariant,
        nonce: int,
        *,
        refresh_template: TemplateRefresher,
        stop_token: StopToken,
        observer: SoloMiningObserver,
    ) -> CandidatePolicyResult:
        """Return one terminal decision without exposing candidate material."""


@dataclass(frozen=True, slots=True)
class HashOnlyCandidatePolicy:
    """Stop on a verified current candidate without block or RPC capabilities."""

    def handle_verified_candidate(
        self,
        variant: SoloWorkVariant,
        nonce: int,
        *,
        refresh_template: TemplateRefresher,
        stop_token: StopToken,
        observer: SoloMiningObserver,
    ) -> CandidatePolicyResult:
        del nonce, refresh_template, stop_token
        observer.candidate_found(variant, False)
        return CandidatePolicyResult(SoloMiningOutcome.CANDIDATE_FOUND_SUBMISSION_DISABLED)


@dataclass(frozen=True, slots=True)
class ProposalSubmissionCandidatePolicy:
    """Own the only proposal and submission capabilities in the solo lifecycle."""

    propose_block: BlockProposer = field(repr=False)
    submit_block: BlockSubmitter = field(repr=False)

    def __post_init__(self) -> None:
        if not callable(self.propose_block) or not callable(self.submit_block):
            raise SoloMiningValidationError("submission policy callables are invalid")

    def handle_verified_candidate(
        self,
        variant: SoloWorkVariant,
        nonce: int,
        *,
        refresh_template: TemplateRefresher,
        stop_token: StopToken,
        observer: SoloMiningObserver,
    ) -> CandidatePolicyResult:
        candidate = assemble_solo_block(variant, nonce)
        observer.candidate_found(variant, True)
        try:
            proposal = self.propose_block(candidate.serialized_block)
        except BitcoinRpcError as exc:
            if getattr(exc, "code_category", None) == "method_unavailable":
                return CandidatePolicyResult(SoloMiningOutcome.PROPOSAL_UNAVAILABLE)
            return CandidatePolicyResult(
                SoloMiningOutcome.RPC_FAILURE,
                suppressions=1,
                suppression_reason="rpc_invalidated",
            )
        if not isinstance(proposal, ProposalOutcome):
            raise SoloMiningValidationError("propose_block must return ProposalOutcome")
        observer.proposal_completed(proposal)
        if not proposal.accepted:
            return CandidatePolicyResult(
                SoloMiningOutcome.PROPOSAL_REJECTED,
                proposal_category=proposal.category,
                proposals=1,
            )
        try:
            changed = refresh_template()
        except BitcoinRpcError:
            return CandidatePolicyResult(
                SoloMiningOutcome.RPC_FAILURE,
                proposal_category=proposal.category,
                suppressions=1,
                suppression_reason="rpc_invalidated",
                proposals=1,
            )
        if changed:
            return CandidatePolicyResult(
                SoloMiningOutcome.CANDIDATE_SUPPRESSED,
                proposal_category=proposal.category,
                suppressions=1,
                suppression_reason="template_replaced",
                proposals=1,
            )
        if stop_token.stop_requested:
            return CandidatePolicyResult(
                _stop_outcome(stop_token),
                proposal_category=proposal.category,
                suppressions=1,
                suppression_reason="stop_requested",
                proposals=1,
            )
        try:
            submission = self.submit_block(candidate.serialized_block)
        except BitcoinRpcError:
            return CandidatePolicyResult(
                SoloMiningOutcome.RPC_FAILURE,
                proposal_category=proposal.category,
                proposals=1,
            )
        if not isinstance(submission, SubmissionOutcome):
            raise SoloMiningValidationError("submit_block must return SubmissionOutcome")
        observer.submission_completed(submission)
        return CandidatePolicyResult(
            (
                SoloMiningOutcome.BLOCK_ACCEPTED
                if submission.accepted
                else SoloMiningOutcome.BLOCK_REJECTED
            ),
            proposal_category=proposal.category,
            submission_category=submission.category,
            proposals=1,
            submissions=1,
        )


@dataclass(slots=True)
class _SoloProgression:
    """Small deterministic state machine beyond one 32-bit nonce domain."""

    coinbase_extra_nonce: int
    timestamp: int

    def advance(
        self,
        template: BlockTemplate,
        wall_time: float,
        max_time_roll_seconds: int,
    ) -> tuple[bool, bool] | None:
        """Advance extra nonce, then time on wrap, or require a new template."""

        next_value = next_coinbase_extra_nonce(self.coinbase_extra_nonce)
        if next_value is not None:
            self.coinbase_extra_nonce = next_value
            return True, False
        if (
            "time" not in template.mutable
            or not math.isfinite(wall_time)
            or self.timestamp >= int(wall_time)
            or self.timestamp == 0xFFFFFFFF
            or self.timestamp - template.current_time >= max_time_roll_seconds
        ):
            return None
        self.coinbase_extra_nonce = 0
        self.timestamp += 1
        return True, True


def run_solo_mining(
    plan: SoloMiningPlan,
    *,
    chain: str,
    payout_script: bytes,
    initial_template: BlockTemplate,
    backend: MiningComputeBackend,
    strategy: MiningSearchStrategy,
    stop_token: StopToken,
    fetch_template: TemplateFetcher,
    candidate_policy: SoloCandidatePolicy,
    observer: SoloMiningObserver | None = None,
    monotonic_clock: Callable[[], float] = time.monotonic,
    wall_clock: Callable[[], float] = time.time,
    wait: StopAwareWaiter | None = None,
    initial_coinbase_extra_nonce: int = 0,
    nonce_limit: int = NONCE_LIMIT,
) -> SoloMiningResult:
    """Search bounded solo work and delegate only verified current candidates."""

    _validate_run_inputs(
        plan,
        chain,
        payout_script,
        initial_template,
        backend,
        strategy,
        stop_token,
        fetch_template,
        candidate_policy,
        monotonic_clock,
        wall_clock,
        initial_coinbase_extra_nonce,
        nonce_limit,
    )
    selected_observer: SoloMiningObserver = (
        NullSoloMiningObserver() if observer is None else observer
    )
    if not isinstance(selected_observer, SoloMiningObserver):
        raise SoloMiningValidationError("observer must implement SoloMiningObserver")
    selected_wait = _wait_stop_aware if wait is None else wait
    if not callable(selected_wait):
        raise SoloMiningValidationError("wait must be callable")

    current_template = initial_template
    progression = _SoloProgression(initial_coinbase_extra_nonce, initial_template.current_time)
    current_variant = prepare_solo_work(
        chain=chain,
        template=current_template,
        payout_script=payout_script,
        coinbase_extra_nonce=progression.coinbase_extra_nonce,
        timestamp=progression.timestamp,
    )
    strategy_cursor = strategy.create_cursor(
        plan.start_nonce, plan.chunk_size, nonce_limit=nonce_limit
    )
    last_poll = _clock_value(monotonic_clock, "monotonic clock")
    templates_received = 1
    replacements = 0
    work_variants = 0
    coinbase_advances = 0
    timestamp_rolls = 0
    candidates = 0
    suppressions = 0
    proposals = 0
    submissions = 0
    chunks = 0
    total_hashes = 0
    total_elapsed_ns = 0
    variant_started = False
    seen_variant_identities: set[str] = set()
    selected_observer.template_received(current_template, False)

    def finish(
        outcome: SoloMiningOutcome,
        *,
        proposal_category: str | None = None,
        submission_category: str | None = None,
    ) -> SoloMiningResult:
        result = SoloMiningResult(
            outcome=outcome,
            chain=chain,
            final_template_fingerprint=current_template.fingerprint,
            proposal_category=proposal_category,
            submission_category=submission_category,
            chunks_completed=chunks,
            templates_received=templates_received,
            template_replacements=replacements,
            work_variants_used=work_variants,
            coinbase_extra_nonce_advances=coinbase_advances,
            timestamp_rolls=timestamp_rolls,
            candidates_found=candidates,
            candidates_suppressed=suppressions,
            proposals_performed=proposals,
            submissions_performed=submissions,
            total_hashes_checked=total_hashes,
            total_elapsed_ns=total_elapsed_ns,
        )
        selected_observer.completed(result)
        return result

    def replace_template(new_template: BlockTemplate) -> None:
        nonlocal current_template, current_variant, progression, strategy_cursor
        nonlocal replacements, variant_started
        previous = current_template
        reason = (
            "previous_block_changed"
            if previous.previous_block_hash != new_template.previous_block_hash
            else "template_changed"
        )
        current_template = new_template
        progression = _SoloProgression(0, new_template.current_time)
        current_variant = prepare_solo_work(
            chain=chain,
            template=new_template,
            payout_script=payout_script,
            coinbase_extra_nonce=0,
            timestamp=new_template.current_time,
        )
        strategy_cursor = strategy.create_cursor(
            plan.start_nonce, plan.chunk_size, nonce_limit=nonce_limit
        )
        replacements += 1
        variant_started = False
        selected_observer.template_replaced(previous, new_template, reason)

    def refresh_template(*, force: bool) -> bool:
        nonlocal templates_received, last_poll
        now = _clock_value(monotonic_clock, "monotonic clock")
        if not force and now - last_poll < plan.template_poll_seconds:
            return False
        fresh = fetch_template()
        if not isinstance(fresh, BlockTemplate):
            raise SoloMiningValidationError("fetch_template must return a BlockTemplate")
        templates_received += 1
        last_poll = now
        changed = fresh.fingerprint != current_template.fingerprint
        selected_observer.template_received(fresh, changed)
        if changed:
            replace_template(fresh)
        return changed

    while True:
        if stop_token.stop_requested:
            return finish(_stop_outcome(stop_token))
        if plan.max_chunks is not None and chunks >= plan.max_chunks:
            return finish(SoloMiningOutcome.CHUNK_LIMIT_REACHED)

        try:
            refresh_template(force=False)
        except BitcoinRpcError:
            return finish(SoloMiningOutcome.RPC_FAILURE)

        assignment = strategy_cursor.next_assignment()
        if assignment is None:
            try:
                advanced = progression.advance(
                    current_template,
                    _clock_value(wall_clock, "wall clock"),
                    plan.max_time_roll_seconds,
                )
            except ValueError as exc:
                raise SoloMiningValidationError("solo progression failed") from exc
            if advanced is None:
                try:
                    changed = refresh_template(force=True)
                except BitcoinRpcError:
                    return finish(SoloMiningOutcome.RPC_FAILURE)
                if not changed:
                    return finish(SoloMiningOutcome.SAFE_PROGRESSION_EXHAUSTED)
                continue
            advanced_extra_nonce, rolled_time = advanced
            if advanced_extra_nonce:
                coinbase_advances += 1
                selected_observer.coinbase_extra_nonce_advanced(coinbase_advances)
            if rolled_time:
                timestamp_rolls += 1
                selected_observer.timestamp_rolled(timestamp_rolls)
            current_variant = prepare_solo_work(
                chain=chain,
                template=current_template,
                payout_script=payout_script,
                coinbase_extra_nonce=progression.coinbase_extra_nonce,
                timestamp=progression.timestamp,
            )
            strategy_cursor = strategy.create_cursor(
                plan.start_nonce, plan.chunk_size, nonce_limit=nonce_limit
            )
            variant_started = False
            continue

        if not variant_started:
            if current_variant.identity in seen_variant_identities:
                raise SoloMiningError("solo progression produced duplicate effective work")
            seen_variant_identities.add(current_variant.identity)
            work_variants += 1
            variant_started = True
            selected_observer.work_variant_started(
                current_variant, work_variants, coinbase_advances, timestamp_rolls
            )

        result = backend.search_nonce_range(
            current_variant.prepared_work, assignment.start_nonce, assignment.stop_nonce
        )
        _validate_search_result(result, assignment.start_nonce, assignment.stop_nonce)
        chunks += 1
        total_hashes += result.hashes_checked
        total_elapsed_ns += result.elapsed_ns
        selected_observer.range_completed(current_variant, result)

        if stop_token.stop_requested:
            if result.match is not None:
                suppressions += 1
                selected_observer.candidate_suppressed("stop_requested", suppressions)
            return finish(_stop_outcome(stop_token))

        if result.match is not None:
            _verify_backend_candidate(current_variant, result)
            try:
                changed = refresh_template(force=True)
            except BitcoinRpcError:
                suppressions += 1
                selected_observer.candidate_suppressed("rpc_invalidated", suppressions)
                return finish(SoloMiningOutcome.RPC_FAILURE)
            if changed:
                suppressions += 1
                selected_observer.candidate_suppressed("template_replaced", suppressions)
                continue
            if stop_token.stop_requested:
                suppressions += 1
                selected_observer.candidate_suppressed("stop_requested", suppressions)
                return finish(_stop_outcome(stop_token))
            candidates += 1
            decision = candidate_policy.handle_verified_candidate(
                current_variant,
                result.match.nonce,
                refresh_template=lambda: refresh_template(force=True),
                stop_token=stop_token,
                observer=selected_observer,
            )
            if not isinstance(decision, CandidatePolicyResult):
                raise SoloMiningValidationError(
                    "candidate policy must return CandidatePolicyResult"
                )
            suppressions += decision.suppressions
            if decision.suppression_reason is not None:
                selected_observer.candidate_suppressed(decision.suppression_reason, suppressions)
            proposals += decision.proposals
            submissions += decision.submissions
            return finish(
                decision.outcome,
                proposal_category=decision.proposal_category,
                submission_category=decision.submission_category,
            )

        if plan.max_chunks is not None and chunks >= plan.max_chunks:
            return finish(SoloMiningOutcome.CHUNK_LIMIT_REACHED)
        if plan.inter_range_delay_seconds > 0:
            selected_wait(plan.inter_range_delay_seconds, stop_token)


def _verify_backend_candidate(variant: SoloWorkVariant, result: NonceSearchResult) -> None:
    match = result.match
    if match is None:
        raise SoloMiningValidationError("candidate result is missing a match")
    header = variant.prepared_work.header_prefix + match.nonce.to_bytes(4, "little")
    block_hash = hash_block_header(header)
    if (
        match.block_hash != block_hash
        or not match.meets_network_target
        or not hash_meets_target(block_hash, variant.template.target)
    ):
        raise SoloMiningError("compute backend candidate failed independent verification")


def _validate_search_result(result: object, start_nonce: int, stop_nonce: int) -> None:
    if not isinstance(result, NonceSearchResult):
        raise SoloMiningValidationError("compute backend returned an invalid search result")
    if result.start_nonce != start_nonce or result.stop_nonce != stop_nonce:
        raise SoloMiningValidationError("compute backend returned a different nonce range")


def _validate_run_inputs(
    plan: object,
    chain: object,
    payout_script: object,
    initial_template: object,
    backend: object,
    strategy: object,
    stop_token: object,
    fetch_template: object,
    candidate_policy: object,
    monotonic_clock: object,
    wall_clock: object,
    initial_coinbase_extra_nonce: object,
    nonce_limit: object,
) -> None:
    if not isinstance(plan, SoloMiningPlan):
        raise SoloMiningValidationError("plan must be SoloMiningPlan")
    if chain not in {"main", "test", "testnet4", "signet", "regtest"}:
        raise SoloMiningValidationError("chain is unsupported")
    if not isinstance(payout_script, bytes) or not 2 <= len(payout_script) <= 100:
        raise SoloMiningValidationError("payout script is invalid")
    if not isinstance(initial_template, BlockTemplate):
        raise SoloMiningValidationError("initial_template must be BlockTemplate")
    if not isinstance(backend, MiningComputeBackend):
        raise SoloMiningValidationError("backend must implement MiningComputeBackend")
    if not isinstance(strategy, MiningSearchStrategy):
        raise SoloMiningValidationError("strategy must implement MiningSearchStrategy")
    validate_search_strategy_compatibility(strategy, backend.capabilities)
    if not isinstance(stop_token, StopToken):
        raise SoloMiningValidationError("stop_token must implement StopToken")
    if not isinstance(candidate_policy, SoloCandidatePolicy):
        raise SoloMiningValidationError("candidate_policy must implement SoloCandidatePolicy")
    for callback, name in (
        (fetch_template, "fetch_template"),
        (monotonic_clock, "monotonic_clock"),
        (wall_clock, "wall_clock"),
    ):
        if not callable(callback):
            raise SoloMiningValidationError(f"{name} must be callable")
    _bounded_integer(
        initial_coinbase_extra_nonce,
        "initial_coinbase_extra_nonce",
        minimum=0,
        maximum=MAX_COINBASE_EXTRA_NONCE,
    )
    parsed_nonce_limit = _bounded_integer(
        nonce_limit, "nonce_limit", minimum=1, maximum=NONCE_LIMIT
    )
    if plan.start_nonce >= parsed_nonce_limit:
        raise SoloMiningValidationError("nonce_limit must be greater than start_nonce")


def _stop_outcome(stop_token: StopToken) -> SoloMiningOutcome:
    return (
        SoloMiningOutcome.RUNTIME_LIMIT_REACHED
        if getattr(stop_token, "runtime_limit_reached", False)
        else SoloMiningOutcome.STOPPED_BY_USER
    )


def _wait_stop_aware(seconds: float, stop_token: StopToken) -> None:
    deadline = time.monotonic() + seconds
    while not stop_token.stop_requested:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(remaining, _PACING_SLICE_SECONDS))


def _clock_value(clock: Callable[[], float], name: str) -> float:
    value = clock()
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise SoloMiningValidationError(f"{name} must return a finite number")
    return float(value)


def _bounded_integer(value: object, name: str, *, minimum: int, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise SoloMiningValidationError(f"{name} must be between {minimum} and {maximum}")
    return value


def _bounded_number(
    value: object,
    name: str,
    *,
    minimum_exclusive: float,
    maximum: float,
) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
        or not minimum_exclusive < value <= maximum
    ):
        raise SoloMiningValidationError(
            f"{name} must be finite, greater than {minimum_exclusive}, and at most {maximum}"
        )
    return float(value)
