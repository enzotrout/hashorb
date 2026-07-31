"""Pure assembly of coinbase transaction bytes from a validated mining job."""

from __future__ import annotations

import string

from hashorb.crypto import double_sha256
from hashorb.mining.job import MiningJob

_HEX_DIGITS = frozenset(string.hexdigits)


class CoinbaseError(Exception):
    """Base error for coinbase transaction assembly failures."""


class CoinbaseValidationError(CoinbaseError, ValueError):
    """Raised when coinbase assembly input violates an invariant."""


def build_coinbase_transaction(job: MiningJob, extra_nonce_2: str) -> bytes:
    """Decode the four protocol components into immutable transaction bytes."""

    if not isinstance(job, MiningJob):
        raise CoinbaseValidationError("job must be a MiningJob")
    _validate_extra_nonce_2(extra_nonce_2, job.extra_nonce_2_size)

    transaction_hex = job.coinbase_part_1 + job.extra_nonce_1 + extra_nonce_2 + job.coinbase_part_2
    return bytes.fromhex(transaction_hex)


def hash_coinbase_transaction(transaction: bytes) -> bytes:
    """Return the raw double-SHA256 digest of nonempty transaction bytes."""

    if not isinstance(transaction, bytes):
        raise CoinbaseValidationError("transaction must be bytes")
    if not transaction:
        raise CoinbaseValidationError("transaction must not be empty")
    return double_sha256(transaction)


def _validate_extra_nonce_2(value: object, expected_byte_length: int) -> None:
    if not isinstance(value, str):
        raise CoinbaseValidationError("extra_nonce_2 must be a string")
    if not value:
        raise CoinbaseValidationError("extra_nonce_2 must not be empty")
    if any(character not in _HEX_DIGITS for character in value):
        raise CoinbaseValidationError("extra_nonce_2 must contain only hexadecimal characters")
    if len(value) % 2 != 0:
        raise CoinbaseValidationError("extra_nonce_2 must contain a whole number of bytes")

    expected_character_length = expected_byte_length * 2
    if len(value) != expected_character_length:
        raise CoinbaseValidationError(
            f"extra_nonce_2 must contain exactly {expected_character_length} hexadecimal characters"
        )
