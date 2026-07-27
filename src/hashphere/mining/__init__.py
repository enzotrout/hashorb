"""Mining-domain models and assembly services."""

from hashphere.mining.coinbase import (
    CoinbaseError,
    CoinbaseValidationError,
    build_coinbase_transaction,
    hash_coinbase_transaction,
)
from hashphere.mining.header import (
    BlockHeaderError,
    BlockHeaderValidationError,
    serialize_block_header,
)
from hashphere.mining.job import (
    MiningJob,
    MiningJobAssembler,
    MiningJobError,
    MiningJobStateError,
    MiningJobValidationError,
)
from hashphere.mining.merkle import (
    MerkleError,
    MerkleValidationError,
    calculate_merkle_root,
)

__all__ = [
    "BlockHeaderError",
    "BlockHeaderValidationError",
    "CoinbaseError",
    "CoinbaseValidationError",
    "MiningJob",
    "MiningJobAssembler",
    "MiningJobError",
    "MiningJobStateError",
    "MiningJobValidationError",
    "MerkleError",
    "MerkleValidationError",
    "build_coinbase_transaction",
    "calculate_merkle_root",
    "hash_coinbase_transaction",
    "serialize_block_header",
]
