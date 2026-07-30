"""Independent strict parsing and hashing of template transactions."""

from __future__ import annotations

from dataclasses import dataclass, field

from hashphere.bitcoin.serialization import ByteReader, encode_compact_size
from hashphere.crypto import double_sha256

MAX_BLOCK_BYTES = 4_000_000
MAX_TRANSACTION_COMPONENTS = 1_000_000
MAX_MONEY = 21_000_000 * 100_000_000


class BitcoinTransactionError(ValueError):
    """Raised when transaction bytes violate the supported consensus shape."""


@dataclass(frozen=True, slots=True)
class ParsedTransaction:
    """Immutable exact transaction bytes and independently derived identities."""

    raw: bytes = field(repr=False)
    stripped: bytes = field(repr=False)
    txid: bytes = field(repr=False)
    wtxid: bytes = field(repr=False)
    input_count: int
    output_count: int
    total_size: int
    stripped_size: int
    weight: int
    has_witness: bool


def parse_transaction(raw: bytes) -> ParsedTransaction:
    """Parse one complete transaction and compute txid, wtxid, size, and weight."""

    if not isinstance(raw, bytes) or not 10 <= len(raw) <= MAX_BLOCK_BYTES:
        raise BitcoinTransactionError("transaction bytes have an invalid length")
    reader = ByteReader(raw)
    version = reader.read(4)
    has_witness = reader.remaining >= 2 and raw[reader.offset : reader.offset + 2] == b"\x00\x01"
    if has_witness:
        reader.read(2)
    elif reader.remaining >= 1 and raw[reader.offset] == 0:
        raise BitcoinTransactionError("transaction has an invalid witness marker")

    input_count = _read_nonzero_count(reader, "input")
    input_start = reader.offset
    for _ in range(input_count):
        reader.read(36)
        script_length = reader.read_compact_size(maximum=MAX_BLOCK_BYTES)
        reader.read(script_length)
        reader.read(4)
    input_bytes = raw[input_start : reader.offset]

    output_count = _read_nonzero_count(reader, "output")
    output_start = reader.offset
    for _ in range(output_count):
        value = int.from_bytes(reader.read(8), "little")
        if value > MAX_MONEY:
            raise BitcoinTransactionError("transaction output value exceeds the money limit")
        script_length = reader.read_compact_size(maximum=MAX_BLOCK_BYTES)
        reader.read(script_length)
    output_bytes = raw[output_start : reader.offset]

    if has_witness:
        any_witness_item = False
        for _ in range(input_count):
            item_count = reader.read_compact_size(maximum=MAX_TRANSACTION_COMPONENTS)
            for _ in range(item_count):
                item_length = reader.read_compact_size(maximum=MAX_BLOCK_BYTES)
                reader.read(item_length)
                any_witness_item = True
        if not any_witness_item:
            raise BitcoinTransactionError("witness serialization must contain witness data")

    locktime = reader.read(4)
    reader.require_end()
    stripped = b"".join(
        (
            version,
            encode_compact_size(input_count),
            input_bytes,
            encode_compact_size(output_count),
            output_bytes,
            locktime,
        )
    )
    stripped_size = len(stripped)
    total_size = len(raw)
    return ParsedTransaction(
        raw=raw,
        stripped=stripped,
        txid=double_sha256(stripped),
        wtxid=double_sha256(raw),
        input_count=input_count,
        output_count=output_count,
        total_size=total_size,
        stripped_size=stripped_size,
        weight=stripped_size * 3 + total_size,
        has_witness=has_witness,
    )


def _read_nonzero_count(reader: ByteReader, name: str) -> int:
    try:
        count = reader.read_compact_size(maximum=MAX_TRANSACTION_COMPONENTS)
    except ValueError as exc:
        raise BitcoinTransactionError(f"transaction {name} count is invalid") from exc
    if count == 0:
        raise BitcoinTransactionError(f"transaction must have at least one {name}")
    return count
