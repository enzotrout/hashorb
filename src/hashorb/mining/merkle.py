"""Raw Bitcoin Merkle-root calculation for validated branch data."""

from __future__ import annotations

import string

from hashorb.crypto import double_sha256

_HEX_DIGITS = frozenset(string.hexdigits)
_HASH_BYTE_LENGTH = 32
_BRANCH_HEX_LENGTH = _HASH_BYTE_LENGTH * 2


class MerkleError(Exception):
    """Base error for Merkle-root calculation failures."""


class MerkleValidationError(MerkleError, ValueError):
    """Raised when Merkle-root input violates the public contract."""


def calculate_merkle_root(
    coinbase_hash: bytes,
    merkle_branches: tuple[str, ...],
) -> bytes:
    """Return the raw Merkle root for a coinbase hash and ordered branches.

    The inputs and intermediate digests remain in raw internal byte order. No
    operand, intermediate digest, or final root is reversed for display.
    """

    _validate_coinbase_hash(coinbase_hash)
    if not isinstance(merkle_branches, tuple):
        raise MerkleValidationError("merkle_branches must be a tuple")

    current_hash = coinbase_hash
    for index, branch in enumerate(merkle_branches):
        branch_bytes = _decode_branch(branch, index)
        current_hash = double_sha256(current_hash + branch_bytes)
    return current_hash


def _validate_coinbase_hash(value: object) -> None:
    if not isinstance(value, bytes):
        raise MerkleValidationError("coinbase_hash must be bytes")
    if len(value) != _HASH_BYTE_LENGTH:
        raise MerkleValidationError("coinbase_hash must contain exactly 32 bytes")


def _decode_branch(value: object, index: int) -> bytes:
    field = f"merkle_branches[{index}]"
    if not isinstance(value, str):
        raise MerkleValidationError(f"{field} must be a hexadecimal string")
    if not value:
        raise MerkleValidationError(f"{field} must not be empty")
    if any(character not in _HEX_DIGITS for character in value):
        raise MerkleValidationError(f"{field} must contain only hexadecimal characters")
    if len(value) % 2 != 0:
        raise MerkleValidationError(f"{field} must contain a whole number of bytes")
    if len(value) != _BRANCH_HEX_LENGTH:
        raise MerkleValidationError(f"{field} must contain exactly 64 hexadecimal characters")

    branch_bytes = bytes.fromhex(value)
    if len(branch_bytes) != _HASH_BYTE_LENGTH:
        raise MerkleValidationError(f"{field} must decode to exactly 32 bytes")
    return branch_bytes
