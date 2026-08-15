"""Mining-domain models, deterministic primitives, and mining orchestration."""

from hashorb.mining.chunks import (
    ChunkedMiningError,
    ChunkedMiningObserver,
    ChunkedMiningPlan,
    ChunkedMiningResult,
    ChunkedMiningValidationError,
    NullChunkedMiningObserver,
    run_chunked_mining,
)
from hashorb.mining.coinbase import (
    CoinbaseError,
    CoinbaseValidationError,
    build_coinbase_transaction,
    hash_coinbase_transaction,
)
from hashorb.mining.continuous import (
    MAX_RUNTIME_SECONDS,
    ContinuousMiningError,
    ContinuousMiningObserver,
    ContinuousMiningOutcome,
    ContinuousMiningPlan,
    ContinuousMiningResult,
    ContinuousMiningValidationError,
    NullContinuousMiningObserver,
    StopController,
    StopToken,
    run_continuous_mining,
)
from hashorb.mining.fibonacci import (
    FibonacciBounceSearchCursor,
    FibonacciBounceSearchStrategy,
    fibonacci_bounce_offset,
    fibonacci_coprime_stride,
)
from hashorb.mining.header import (
    BlockHeaderError,
    BlockHeaderValidationError,
    hash_block_header,
    serialize_block_header,
)
from hashorb.mining.job import (
    MiningJob,
    MiningJobAssembler,
    MiningJobError,
    MiningJobStateError,
    MiningJobValidationError,
)
from hashorb.mining.liveness import (
    MAX_LIVENESS_SECONDS,
    StratumLivenessError,
    StratumLivenessPolicy,
    StratumLivenessTracker,
    StratumLivenessViolation,
    StratumStaleReason,
)
from hashorb.mining.merkle import (
    MerkleError,
    MerkleValidationError,
    calculate_merkle_root,
)
from hashorb.mining.progression import (
    MiningJobContextIdentity,
    MiningWorkCursor,
    MiningWorkIdentity,
    MiningWorkProgress,
    MiningWorkProgressionError,
    MiningWorkProgressionValidationError,
    MiningWorkVariant,
    mining_job_context_identity,
    mining_work_identity,
    prepare_work_variant,
)
from hashorb.mining.recovery import (
    MAX_RECONNECT_ATTEMPTS,
    BackoffWaiter,
    ExtraNonceSeedFactory,
    NullStratumSessionRecoveryObserver,
    ReconnectPolicy,
    SessionRecoveryError,
    SessionRecoveryExhaustedError,
    SessionRecoveryValidationError,
    StratumClientFactory,
    StratumMiningSession,
    StratumRecoveryStage,
    StratumRecoveryStatistics,
    StratumSessionClient,
    StratumSessionRecovery,
    StratumSessionRecoveryObserver,
    is_recoverable_stratum_error,
    wait_for_reconnect_delay,
)
from hashorb.mining.search import (
    NonceSearchError,
    NonceSearchMatch,
    NonceSearchResult,
    NonceSearchValidationError,
    PreparedMiningWork,
    prepare_mining_work,
    search_nonce_range,
)
from hashorb.mining.strategy import (
    MiningSearchStrategy,
    OrbitingBitSearchCursor,
    OrbitingBitSearchStrategy,
    SearchAssignment,
    SearchBackendCapabilities,
    SearchStrategyCapabilities,
    SearchStrategyCompatibilityError,
    SearchStrategyCursor,
    SearchStrategyError,
    SearchStrategyExecutionError,
    SearchStrategyRegistry,
    SearchStrategySelectionError,
    SearchStrategyValidationError,
    SequentialSearchCursor,
    SequentialSearchStrategy,
    calculate_orbiting_range_count,
    list_search_strategies as _list_search_strategies,
    next_power_of_two,
    reverse_bits,
    select_search_strategy as _select_search_strategy,
    validate_search_strategy_compatibility,
)
from hashorb.mining.target import (
    TargetError,
    TargetValidationError,
    block_hash_to_int,
    decode_compact_target,
    difficulty_to_share_target,
    hash_meets_target,
)


def builtin_search_strategy_registry() -> SearchStrategyRegistry:
    """Create a fresh registry containing every built-in search strategy."""

    return SearchStrategyRegistry(
        (
            FibonacciBounceSearchStrategy(),
            OrbitingBitSearchStrategy(),
            SequentialSearchStrategy(),
        )
    )


def select_search_strategy(
    strategy_name: str,
    registry: SearchStrategyRegistry | None = None,
) -> MiningSearchStrategy:
    """Select one strategy using the complete HashOrb built-in registry."""

    selected_registry = builtin_search_strategy_registry() if registry is None else registry
    return _select_search_strategy(strategy_name, selected_registry)


def list_search_strategies(
    registry: SearchStrategyRegistry | None = None,
) -> tuple[SearchStrategyCapabilities, ...]:
    """List strategy capabilities using the complete HashOrb built-in registry."""

    selected_registry = builtin_search_strategy_registry() if registry is None else registry
    return _list_search_strategies(selected_registry)


__all__ = [
    "BackoffWaiter",
    "BlockHeaderError",
    "BlockHeaderValidationError",
    "CoinbaseError",
    "CoinbaseValidationError",
    "ChunkedMiningError",
    "ChunkedMiningObserver",
    "ChunkedMiningPlan",
    "ChunkedMiningResult",
    "ChunkedMiningValidationError",
    "ContinuousMiningError",
    "ContinuousMiningObserver",
    "ContinuousMiningOutcome",
    "ContinuousMiningPlan",
    "ContinuousMiningResult",
    "ContinuousMiningValidationError",
    "ExtraNonceSeedFactory",
    "FibonacciBounceSearchCursor",
    "FibonacciBounceSearchStrategy",
    "MAX_RECONNECT_ATTEMPTS",
    "MAX_LIVENESS_SECONDS",
    "MAX_RUNTIME_SECONDS",
    "MiningJob",
    "MiningJobAssembler",
    "MiningJobContextIdentity",
    "MiningJobError",
    "MiningJobStateError",
    "MiningJobValidationError",
    "MiningSearchStrategy",
    "MiningWorkCursor",
    "MiningWorkIdentity",
    "MiningWorkProgress",
    "MiningWorkProgressionError",
    "StratumLivenessError",
    "StratumLivenessPolicy",
    "StratumLivenessTracker",
    "StratumLivenessViolation",
    "StratumStaleReason",
    "MiningWorkProgressionValidationError",
    "MiningWorkVariant",
    "MerkleError",
    "MerkleValidationError",
    "NonceSearchError",
    "NonceSearchMatch",
    "NonceSearchResult",
    "NonceSearchValidationError",
    "NullChunkedMiningObserver",
    "NullContinuousMiningObserver",
    "NullStratumSessionRecoveryObserver",
    "OrbitingBitSearchCursor",
    "OrbitingBitSearchStrategy",
    "PreparedMiningWork",
    "ReconnectPolicy",
    "SearchAssignment",
    "SearchBackendCapabilities",
    "SearchStrategyCapabilities",
    "SearchStrategyCompatibilityError",
    "SearchStrategyCursor",
    "SearchStrategyError",
    "SearchStrategyExecutionError",
    "SearchStrategyRegistry",
    "SearchStrategySelectionError",
    "SearchStrategyValidationError",
    "SessionRecoveryError",
    "SessionRecoveryExhaustedError",
    "SessionRecoveryValidationError",
    "SequentialSearchCursor",
    "SequentialSearchStrategy",
    "StopController",
    "StopToken",
    "StratumMiningSession",
    "StratumClientFactory",
    "StratumRecoveryStage",
    "StratumRecoveryStatistics",
    "StratumSessionClient",
    "StratumSessionRecovery",
    "StratumSessionRecoveryObserver",
    "TargetError",
    "TargetValidationError",
    "block_hash_to_int",
    "builtin_search_strategy_registry",
    "build_coinbase_transaction",
    "calculate_merkle_root",
    "calculate_orbiting_range_count",
    "decode_compact_target",
    "difficulty_to_share_target",
    "fibonacci_bounce_offset",
    "fibonacci_coprime_stride",
    "hash_block_header",
    "hash_coinbase_transaction",
    "hash_meets_target",
    "is_recoverable_stratum_error",
    "list_search_strategies",
    "mining_job_context_identity",
    "mining_work_identity",
    "next_power_of_two",
    "prepare_mining_work",
    "prepare_work_variant",
    "run_chunked_mining",
    "run_continuous_mining",
    "reverse_bits",
    "search_nonce_range",
    "select_search_strategy",
    "serialize_block_header",
    "wait_for_reconnect_delay",
    "validate_search_strategy_compatibility",
]
