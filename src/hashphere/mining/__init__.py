"""Mining-domain models and assembly services."""

from hashphere.mining.coinbase import (
    CoinbaseError,
    CoinbaseValidationError,
    build_coinbase_transaction,
    hash_coinbase_transaction,
)
from hashphere.mining.job import (
    MiningJob,
    MiningJobAssembler,
    MiningJobError,
    MiningJobStateError,
    MiningJobValidationError,
)

__all__ = [
    "CoinbaseError",
    "CoinbaseValidationError",
    "MiningJob",
    "MiningJobAssembler",
    "MiningJobError",
    "MiningJobStateError",
    "MiningJobValidationError",
    "build_coinbase_transaction",
    "hash_coinbase_transaction",
]
