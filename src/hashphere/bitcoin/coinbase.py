"""Deterministic BIP34 and SegWit coinbase construction for true solo mining."""

from __future__ import annotations

from dataclasses import dataclass, field

from hashphere.bitcoin.serialization import encode_compact_size
from hashphere.bitcoin.template import BlockTemplate
from hashphere.bitcoin.transaction import MAX_MONEY, ParsedTransaction, parse_transaction

COINBASE_TRANSACTION_VERSION = 2
COINBASE_SEQUENCE = 0xFFFFFFFF
COINBASE_EXTRA_NONCE_BYTES = 8
MAX_COINBASE_EXTRA_NONCE = (1 << (8 * COINBASE_EXTRA_NONCE_BYTES)) - 1
MIN_COINBASE_SCRIPT_BYTES = 2
MAX_COINBASE_SCRIPT_BYTES = 100
WITNESS_RESERVED_VALUE = bytes(32)
HASHPHERE_COINBASE_MARKER = b"/Hashsphere/"


class CoinbaseConstructionError(ValueError):
    """Raised when a safe exact coinbase cannot be constructed."""


@dataclass(frozen=True, slots=True)
class SoloCoinbase:
    """Exact coinbase serializations and identities with sensitive fields hidden."""

    transaction: ParsedTransaction = field(repr=False)
    script_sig: bytes = field(repr=False)
    payout_script: bytes = field(repr=False)
    coinbase_extra_nonce: int = field(repr=False)
    output_value: int


def encode_script_number(value: int) -> bytes:
    """Encode one nonnegative integer as minimal Bitcoin Script number bytes."""

    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise CoinbaseConstructionError("script number must be a nonnegative integer")
    if value == 0:
        return b""
    result = bytearray()
    remaining = value
    while remaining:
        result.append(remaining & 0xFF)
        remaining >>= 8
    if result[-1] & 0x80:
        result.append(0)
    return bytes(result)


def encode_push_data(data: bytes) -> bytes:
    """Encode one bounded minimal direct data push."""

    if not isinstance(data, bytes) or len(data) > 75:
        raise CoinbaseConstructionError("direct push data must contain at most 75 bytes")
    return bytes((len(data),)) + data


def encode_bip34_height(height: int) -> bytes:
    """Match Bitcoin Core's consensus ``CScript() << height`` serialization."""

    if not isinstance(height, int) or isinstance(height, bool) or height < 0:
        raise CoinbaseConstructionError("block height must be a nonnegative integer")
    if 1 <= height <= 16:
        return bytes((0x50 + height,))
    return encode_push_data(encode_script_number(height))


def build_solo_coinbase(
    template: BlockTemplate,
    payout_script: bytes,
    coinbase_extra_nonce: int,
) -> SoloCoinbase:
    """Build one exact two-output SegWit coinbase from a validated template."""

    if not isinstance(template, BlockTemplate):
        raise CoinbaseConstructionError("template must be a BlockTemplate")
    if not isinstance(payout_script, bytes) or not 2 <= len(payout_script) <= 100:
        raise CoinbaseConstructionError("payout script must contain between 2 and 100 bytes")
    if (
        not isinstance(coinbase_extra_nonce, int)
        or isinstance(coinbase_extra_nonce, bool)
        or not 0 <= coinbase_extra_nonce <= MAX_COINBASE_EXTRA_NONCE
    ):
        raise CoinbaseConstructionError("coinbase extra nonce must be an unsigned 64-bit integer")
    if not 0 <= template.coinbase_value <= MAX_MONEY:
        raise CoinbaseConstructionError("coinbase value exceeds the money limit")

    script_sig = b"".join(
        (
            encode_bip34_height(template.height),
            template.coinbase_aux_flags,
            HASHPHERE_COINBASE_MARKER,
            coinbase_extra_nonce.to_bytes(COINBASE_EXTRA_NONCE_BYTES, "little"),
        )
    )
    if not MIN_COINBASE_SCRIPT_BYTES <= len(script_sig) <= MAX_COINBASE_SCRIPT_BYTES:
        raise CoinbaseConstructionError(
            "constructed coinbase script length is outside consensus bounds"
        )

    version = COINBASE_TRANSACTION_VERSION.to_bytes(4, "little")
    input_bytes = b"".join(
        (
            bytes(32),
            (0xFFFFFFFF).to_bytes(4, "little"),
            encode_compact_size(len(script_sig)),
            script_sig,
            COINBASE_SEQUENCE.to_bytes(4, "little"),
        )
    )
    payout_output = b"".join(
        (
            template.coinbase_value.to_bytes(8, "little"),
            encode_compact_size(len(payout_script)),
            payout_script,
        )
    )
    commitment_output = b"".join(
        (
            bytes(8),
            encode_compact_size(len(template.witness_commitment)),
            template.witness_commitment,
        )
    )
    transaction_body = b"".join(
        (
            b"\x01",
            input_bytes,
            b"\x02",
            payout_output,
            commitment_output,
        )
    )
    locktime = bytes(4)
    stripped = version + transaction_body + locktime
    raw = b"".join(
        (
            version,
            b"\x00\x01",
            transaction_body,
            b"\x01\x20",
            WITNESS_RESERVED_VALUE,
            locktime,
        )
    )
    parsed = parse_transaction(raw)
    if parsed.stripped != stripped:
        raise CoinbaseConstructionError("coinbase stripped serialization is inconsistent")
    return SoloCoinbase(
        transaction=parsed,
        script_sig=script_sig,
        payout_script=payout_script,
        coinbase_extra_nonce=coinbase_extra_nonce,
        output_value=template.coinbase_value,
    )


def next_coinbase_extra_nonce(value: int) -> int | None:
    """Advance one bounded extra nonce, returning ``None`` after exact wrap."""

    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not 0 <= value <= MAX_COINBASE_EXTRA_NONCE
    ):
        raise CoinbaseConstructionError("coinbase extra nonce must be an unsigned 64-bit integer")
    return None if value == MAX_COINBASE_EXTRA_NONCE else value + 1
