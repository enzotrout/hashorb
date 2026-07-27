"""Structural decoding of Bitcoin compact network targets."""

from __future__ import annotations

import string

_HEX_DIGITS = frozenset(string.hexdigits)
_COMPACT_HEX_LENGTH = 8
_MAX_UINT256 = (1 << 256) - 1


class TargetError(Exception):
    """Base error for target-domain failures."""


class TargetValidationError(TargetError, ValueError):
    """Raised when a compact target violates structural invariants."""


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


def _validate_network_bits(value: object) -> None:
    if not isinstance(value, str):
        raise TargetValidationError("network_bits must be a string")
    if not value:
        raise TargetValidationError("network_bits must not be empty")
    if len(value) != _COMPACT_HEX_LENGTH:
        raise TargetValidationError("network_bits must contain exactly 8 hexadecimal characters")
    if any(character not in _HEX_DIGITS for character in value):
        raise TargetValidationError("network_bits must contain only hexadecimal characters")
