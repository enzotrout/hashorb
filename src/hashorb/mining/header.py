"""Deterministic serialization and hashing of Bitcoin block headers."""

from __future__ import annotations

from hashorb.crypto import double_sha256
from hashorb.mining.job import MiningJob

_UINT32_MAX = 0xFFFFFFFF
_UINT32_BYTE_LENGTH = 4
_HASH_BYTE_LENGTH = 32
_HEADER_BYTE_LENGTH = 80


class BlockHeaderError(Exception):
    """Base error for block-header domain failures."""


class BlockHeaderValidationError(BlockHeaderError, ValueError):
    """Raised when block-header data violates a public contract."""


def serialize_block_header(
    job: MiningJob,
    merkle_root: bytes,
    nonce: int,
) -> bytes:
    """Serialize one immutable 80-byte Bitcoin block header.

    Stratum numeric strings become unsigned 32-bit little-endian fields. The
    Stratum previous hash is converted from its 32-bit-word-swapped form, while
    the raw Merkle digest is already in the internal order required by a header.
    """

    if not isinstance(job, MiningJob):
        raise BlockHeaderValidationError("job must be a MiningJob")
    _validate_merkle_root(merkle_root)
    nonce_bytes = _serialize_nonce(nonce)

    header = b"".join(
        (
            _stratum_uint32_to_header_bytes(job.version),
            _stratum_previous_hash_to_header_bytes(job.previous_block_hash),
            merkle_root,
            _stratum_uint32_to_header_bytes(job.network_time),
            _stratum_uint32_to_header_bytes(job.network_bits),
            nonce_bytes,
        )
    )
    if len(header) != _HEADER_BYTE_LENGTH:
        raise BlockHeaderError("serialized block header must contain exactly 80 bytes")
    return header


def hash_block_header(header: bytes) -> bytes:
    """Return the unchanged raw double-SHA256 digest of an 80-byte header."""

    if not isinstance(header, bytes):
        raise BlockHeaderValidationError("header must be bytes")
    if len(header) != _HEADER_BYTE_LENGTH:
        raise BlockHeaderValidationError("header must contain exactly 80 bytes")
    return double_sha256(header)


def _validate_merkle_root(value: object) -> None:
    if not isinstance(value, bytes):
        raise BlockHeaderValidationError("merkle_root must be bytes")
    if len(value) != _HASH_BYTE_LENGTH:
        raise BlockHeaderValidationError("merkle_root must contain exactly 32 bytes")


def _serialize_nonce(value: object) -> bytes:
    if isinstance(value, bool) or not isinstance(value, int):
        raise BlockHeaderValidationError("nonce must be an integer")
    if not 0 <= value <= _UINT32_MAX:
        raise BlockHeaderValidationError("nonce must be between 0 and 0xffffffff")
    return value.to_bytes(_UINT32_BYTE_LENGTH, byteorder="little", signed=False)


def _stratum_uint32_to_header_bytes(value: str) -> bytes:
    """Convert an eight-character Stratum integer to header byte order."""

    numeric_value = int(value, 16)
    return numeric_value.to_bytes(_UINT32_BYTE_LENGTH, byteorder="little", signed=False)


def _stratum_previous_hash_to_header_bytes(value: str) -> bytes:
    """Convert CKPool's word-swapped previous hash to internal header order."""

    stratum_bytes = bytes.fromhex(value)
    header_words = (
        int.from_bytes(
            stratum_bytes[offset : offset + _UINT32_BYTE_LENGTH],
            byteorder="big",
            signed=False,
        ).to_bytes(_UINT32_BYTE_LENGTH, byteorder="little", signed=False)
        for offset in range(0, _HASH_BYTE_LENGTH, _UINT32_BYTE_LENGTH)
    )
    return b"".join(header_words)
