"""Bitcoin network-target decoding and proof-of-work comparison."""

from __future__ import annotations

import string

_HEX_DIGITS = frozenset(string.hexdigits)
_COMPACT_HEX_LENGTH = 8
_HASH_BYTE_LENGTH = 32
_MAX_UINT256 = (1 << 256) - 1


class TargetError(Exception):
    """Base error for target-domain failures."""


class TargetValidationError(TargetError, ValueError):
    """Raised when target-domain input violates structural invariants."""


def decode_compact_target(network_bits: str) -> int:
    """Decode a valid Stratum compact target into a positive Python integer.

    This applies Bitcoin Core's compact sign and overflow rules without
    enforcing a network-specific proof-of-work limit.
    """

    _validate_network_bits(network_bits)
    compact = int(network_bits, 16)
    exponent = compact >> 24
    mantissa = compact & 0x007FFFFF
    negative = mantissa != 0 and (compact & 0x00800000) != 0

    if exponent <= 3:
        target = mantissa >> (8 * (3 - exponent))
    else:
        target = mantissa << (8 * (exponent - 3))

    overflow = mantissa != 0 and (
        exponent > 34
        or (mantissa > 0xFF and exponent > 33)
        or (mantissa > 0xFFFF and exponent > 32)
    )

    if negative:
        raise TargetValidationError("network_bits encodes a negative target")
    if overflow or target > _MAX_UINT256:
        raise TargetValidationError("network_bits target overflows 256 bits")
    if target == 0:
        raise TargetValidationError("network_bits must decode to a nonzero target")
    return target


def block_hash_to_int(block_hash: bytes) -> int:
    """Interpret a raw 32-byte block hash as an unsigned little-endian integer."""

    if not isinstance(block_hash, bytes):
        raise TargetValidationError("block_hash must be bytes")
    if len(block_hash) != _HASH_BYTE_LENGTH:
        raise TargetValidationError("block_hash must contain exactly 32 bytes")
    return int.from_bytes(block_hash, byteorder="little", signed=False)


def hash_meets_target(block_hash: bytes, target: int) -> bool:
    """Return whether a raw block hash is less than or equal to a target."""

    hash_value = block_hash_to_int(block_hash)
    if isinstance(target, bool) or not isinstance(target, int):
        raise TargetValidationError("target must be an integer")
    if not 1 <= target <= _MAX_UINT256:
        raise TargetValidationError("target must be between 1 and 2**256 - 1")
    return hash_value <= target


def _validate_network_bits(value: object) -> None:
    if not isinstance(value, str):
        raise TargetValidationError("network_bits must be a string")
    if not value:
        raise TargetValidationError("network_bits must not be empty")
    if len(value) != _COMPACT_HEX_LENGTH:
        raise TargetValidationError("network_bits must contain exactly 8 hexadecimal characters")
    if any(character not in _HEX_DIGITS for character in value):
        raise TargetValidationError("network_bits must contain only hexadecimal characters")
