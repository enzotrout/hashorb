"""True-solo merkle, header, prepared-work, and complete-block assembly."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from hashorb.bitcoin.coinbase import (
    SoloCoinbase,
    build_solo_coinbase,
    encode_bip34_height,
)
from hashorb.bitcoin.serialization import ByteReader, encode_compact_size
from hashorb.bitcoin.template import BlockTemplate, calculate_hash_merkle_root
from hashorb.bitcoin.transaction import parse_transaction
from hashorb.mining.header import hash_block_header
from hashorb.mining.search import PreparedMiningWork
from hashorb.mining.target import hash_meets_target

BLOCK_HEADER_BYTES = 80
HEADER_PREFIX_BYTES = 76
NONCE_LIMIT = 1 << 32
_SUPPORTED_CHAINS = frozenset({"main", "test", "testnet4", "signet", "regtest"})


class SoloBlockError(ValueError):
    """Raised when solo work or a complete block is inconsistent."""


@dataclass(frozen=True, slots=True)
class SoloWorkVariant:
    """One immutable effective header variant and its private construction state."""

    chain: str
    template: BlockTemplate = field(repr=False)
    coinbase: SoloCoinbase = field(repr=False)
    timestamp: int
    merkle_root: bytes = field(repr=False)
    prepared_work: PreparedMiningWork = field(repr=False)
    identity: str


@dataclass(frozen=True, slots=True)
class SoloBlockCandidate:
    """One independently verified complete candidate block."""

    serialized_block: bytes = field(repr=False)
    header: bytes = field(repr=False)
    nonce: int = field(repr=False)
    block_hash: bytes = field(repr=False)
    transaction_count: int
    size: int
    weight: int
    work_identity: str


def serialize_solo_header(
    *,
    version: int,
    previous_block_hash: str,
    merkle_root: bytes,
    timestamp: int,
    bits: str,
    nonce: int,
) -> bytes:
    """Serialize the exact Core-display inputs into one 80-byte Bitcoin header."""

    _uint32(version, "version")
    _uint32(timestamp, "timestamp")
    _uint32(nonce, "nonce")
    if (
        not isinstance(previous_block_hash, str)
        or len(previous_block_hash) != 64
        or not _is_lower_hex(previous_block_hash)
    ):
        raise SoloBlockError("previous block hash must be 64 lowercase hexadecimal characters")
    if not isinstance(merkle_root, bytes) or len(merkle_root) != 32:
        raise SoloBlockError("merkle root must contain exactly 32 bytes")
    if not isinstance(bits, str) or len(bits) != 8 or not _is_lower_hex(bits):
        raise SoloBlockError("compact bits must be 8 lowercase hexadecimal characters")
    header = b"".join(
        (
            version.to_bytes(4, "little"),
            bytes.fromhex(previous_block_hash)[::-1],
            merkle_root,
            timestamp.to_bytes(4, "little"),
            int(bits, 16).to_bytes(4, "little"),
            nonce.to_bytes(4, "little"),
        )
    )
    if len(header) != BLOCK_HEADER_BYTES:
        raise SoloBlockError("serialized block header must contain exactly 80 bytes")
    return header


def prepare_solo_work(
    *,
    chain: str,
    template: BlockTemplate,
    payout_script: bytes,
    coinbase_extra_nonce: int,
    timestamp: int | None = None,
) -> SoloWorkVariant:
    """Construct coinbase, merkle root, header prefix, and network-only work."""

    if chain not in _SUPPORTED_CHAINS:
        raise SoloBlockError("chain is unsupported")
    if not isinstance(template, BlockTemplate):
        raise SoloBlockError("template must be a BlockTemplate")
    selected_time = template.current_time if timestamp is None else timestamp
    _uint32(selected_time, "timestamp")
    if selected_time < template.minimum_time:
        raise SoloBlockError("timestamp is below the template minimum")
    if selected_time != template.current_time and "time" not in template.mutable:
        raise SoloBlockError("template does not permit timestamp mutation")

    coinbase = build_solo_coinbase(template, payout_script, coinbase_extra_nonce)
    merkle_root = calculate_hash_merkle_root(
        (coinbase.transaction.txid, *(item.transaction.txid for item in template.transactions))
    )
    header = serialize_solo_header(
        version=template.version,
        previous_block_hash=template.previous_block_hash,
        merkle_root=merkle_root,
        timestamp=selected_time,
        bits=template.bits,
        nonce=0,
    )
    identity = _work_identity(
        chain=chain,
        template=template,
        coinbase=coinbase,
        timestamp=selected_time,
        payout_script=payout_script,
    )
    prepared = PreparedMiningWork(
        job_id=f"solo-{identity}",
        extra_nonce_2=coinbase_extra_nonce.to_bytes(8, "little").hex(),
        network_time=f"{selected_time:08x}",
        header_prefix=header[:HEADER_PREFIX_BYTES],
        network_target=template.target,
        share_target=template.target,
    )
    return SoloWorkVariant(
        chain=chain,
        template=template,
        coinbase=coinbase,
        timestamp=selected_time,
        merkle_root=merkle_root,
        prepared_work=prepared,
        identity=identity,
    )


def assemble_solo_block(variant: SoloWorkVariant, nonce: int) -> SoloBlockCandidate:
    """Reconstruct and independently verify one complete network-target candidate."""

    if not isinstance(variant, SoloWorkVariant):
        raise SoloBlockError("variant must be SoloWorkVariant")
    _uint32(nonce, "nonce")
    header = serialize_solo_header(
        version=variant.template.version,
        previous_block_hash=variant.template.previous_block_hash,
        merkle_root=variant.merkle_root,
        timestamp=variant.timestamp,
        bits=variant.template.bits,
        nonce=nonce,
    )
    block_hash = hash_block_header(header)
    if not hash_meets_target(block_hash, variant.template.target):
        raise SoloBlockError("candidate header does not meet the network target")

    transaction_raw = (
        variant.coinbase.transaction.raw,
        *(item.transaction.raw for item in variant.template.transactions),
    )
    _verify_coinbase_height_prefix(transaction_raw[0], variant.template.height)
    reparsed = tuple(parse_transaction(raw) for raw in transaction_raw)
    merkle_root = calculate_hash_merkle_root(tuple(item.txid for item in reparsed))
    if merkle_root != variant.merkle_root or header[36:68] != merkle_root:
        raise SoloBlockError("candidate merkle root does not match the serialized transactions")
    count = len(transaction_raw)
    prefix = header + encode_compact_size(count)
    serialized_block = prefix + b"".join(transaction_raw)
    size = len(serialized_block)
    weight = len(prefix) * 4 + sum(item.weight for item in reparsed)
    if size > variant.template.size_limit:
        raise SoloBlockError("candidate block exceeds the template size limit")
    if weight > variant.template.weight_limit:
        raise SoloBlockError("candidate block exceeds the template weight limit")
    return SoloBlockCandidate(
        serialized_block=serialized_block,
        header=header,
        nonce=nonce,
        block_hash=block_hash,
        transaction_count=count,
        size=size,
        weight=weight,
        work_identity=variant.identity,
    )


def _verify_coinbase_height_prefix(raw: bytes, height: int) -> None:
    """Independently read the coinbase input and enforce Core's BIP34 prefix."""

    reader = ByteReader(raw)
    reader.read(4)
    if reader.read(2) != b"\x00\x01":
        raise SoloBlockError("candidate coinbase lacks the required witness serialization")
    if reader.read_compact_size(maximum=1) != 1:
        raise SoloBlockError("candidate coinbase must contain exactly one input")
    if reader.read(32) != bytes(32) or int.from_bytes(reader.read(4), "little") != 0xFFFFFFFF:
        raise SoloBlockError("candidate coinbase input outpoint is invalid")
    script_length = reader.read_compact_size(maximum=100)
    script_sig = reader.read(script_length)
    if not script_sig.startswith(encode_bip34_height(height)):
        raise SoloBlockError("candidate coinbase height prefix is invalid")


def _work_identity(
    *,
    chain: str,
    template: BlockTemplate,
    coinbase: SoloCoinbase,
    timestamp: int,
    payout_script: bytes,
) -> str:
    material = b"\x00".join(
        (
            chain.encode("ascii"),
            template.fingerprint.encode("ascii"),
            template.version.to_bytes(4, "little"),
            bytes.fromhex(template.bits),
            timestamp.to_bytes(4, "little"),
            coinbase.transaction.txid,
            hashlib.sha256(payout_script).digest(),
        )
    )
    return hashlib.sha256(material).hexdigest()[:16]


def _uint32(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value < NONCE_LIMIT:
        raise SoloBlockError(f"{name} must be an unsigned 32-bit integer")
    return value


def _is_lower_hex(value: str) -> bool:
    return all(character in "0123456789abcdef" for character in value)
