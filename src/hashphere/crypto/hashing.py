"""Reusable Bitcoin hashing primitives."""

from __future__ import annotations

import hashlib


class HashingError(Exception):
    """Base error for cryptographic hashing failures."""


class HashingValidationError(HashingError, TypeError):
    """Raised when hashing input has an unsupported type."""


def double_sha256(data: bytes) -> bytes:
    """Return the raw digest of applying SHA-256 twice to immutable bytes."""

    if not isinstance(data, bytes):
        raise HashingValidationError("data must be bytes")
    return hashlib.sha256(hashlib.sha256(data).digest()).digest()
