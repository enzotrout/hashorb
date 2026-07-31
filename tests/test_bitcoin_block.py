"""Synthetic correctness tests for coinbase, header, merkle, and block assembly."""

from __future__ import annotations

from dataclasses import replace

import pytest

from hashorb.bitcoin.block import (
    SoloBlockError,
    assemble_solo_block,
    prepare_solo_work,
    serialize_solo_header,
)
from hashorb.bitcoin.coinbase import (
    COINBASE_EXTRA_NONCE_BYTES,
    HASHORB_COINBASE_MARKER,
    MAX_COINBASE_SCRIPT_BYTES,
    MAX_COINBASE_EXTRA_NONCE,
    WITNESS_RESERVED_VALUE,
    CoinbaseConstructionError,
    build_solo_coinbase,
    encode_script_number,
    next_coinbase_extra_nonce,
)
from hashorb.bitcoin.serialization import ByteReader
from hashorb.bitcoin.template import calculate_hash_merkle_root, parse_block_template
from hashorb.bitcoin.transaction import parse_transaction
from hashorb.crypto import double_sha256
from hashorb.mining import search_nonce_range
from hashorb.mining.target import decode_compact_target

_PAYOUT_SCRIPT = bytes.fromhex("0014" + "42" * 20)
_COMMITMENT_PREFIX = bytes.fromhex("6a24aa21a9ed")


def _transaction(marker: int = 0x11) -> bytes:
    return b"".join(
        (
            (1).to_bytes(4, "little"),
            b"\x01",
            bytes((marker,)) * 32,
            bytes(4),
            b"\x01\x51",
            (0xFFFFFFFF).to_bytes(4, "little"),
            b"\x01",
            (1_000).to_bytes(8, "little"),
            b"\x01\x51",
            bytes(4),
        )
    )


def _template(
    *,
    transactions: tuple[bytes, ...] = (),
    flags: bytes = b"\x51",
    height: int = 128,
):
    parsed_transactions = tuple(parse_transaction(raw) for raw in transactions)
    witness_root = calculate_hash_merkle_root(
        (bytes(32), *(transaction.wtxid for transaction in parsed_transactions))
    )
    commitment = _COMMITMENT_PREFIX + double_sha256(witness_root + bytes(32))
    target = decode_compact_target("207fffff")
    return parse_block_template(
        {
            "previousblockhash": bytes(range(32)).hex(),
            "version": 0x20000000,
            "bits": "207fffff",
            "target": f"{target:064x}",
            "height": height,
            "curtime": 1_700_000_001,
            "mintime": 1_700_000_000,
            "transactions": [
                {
                    "data": transaction.raw.hex(),
                    "txid": transaction.txid[::-1].hex(),
                    "hash": transaction.wtxid[::-1].hex(),
                    "depends": [],
                    "fee": 100,
                    "sigops": 1,
                    "weight": transaction.weight,
                }
                for transaction in parsed_transactions
            ],
            "coinbasevalue": 5_000_000_000,
            "coinbaseaux": {"flags": flags.hex()},
            "rules": ["csv", "segwit", "taproot"],
            "mutable": ["time", "transactions", "prevblock"],
            "sizelimit": 1_000_000,
            "weightlimit": 4_000_000,
            "default_witness_commitment": commitment.hex(),
        }
    )


@pytest.mark.parametrize(
    ("height", "expected"),
    [
        (0, ""),
        (1, "01"),
        (127, "7f"),
        (128, "8000"),
        (255, "ff00"),
        (256, "0001"),
        (32_767, "ff7f"),
        (32_768, "008000"),
        (0xFFFFFFFF, "ffffffff00"),
    ],
)
def test_script_number_height_boundaries(height: int, expected: str) -> None:
    assert encode_script_number(height).hex() == expected


@pytest.mark.parametrize(
    ("height", "expected_prefix"),
    [
        (1, b"\x51"),
        (2, b"\x52"),
        (16, b"\x60"),
        (17, b"\x01\x11"),
        (128, b"\x02\x80\x00"),
    ],
)
def test_coinbase_bip34_prefix_matches_core_script_integer_encoding(
    height: int, expected_prefix: bytes
) -> None:
    coinbase = build_solo_coinbase(_template(height=height), _PAYOUT_SCRIPT, 0)

    prefix_matches = coinbase.script_sig.startswith(expected_prefix)
    assert prefix_matches


def test_coinbase_exact_shape_value_outputs_and_witness_reserved_value() -> None:
    template = _template()
    coinbase = build_solo_coinbase(template, _PAYOUT_SCRIPT, 7)
    reader = ByteReader(coinbase.transaction.raw)

    assert int.from_bytes(reader.read(4), "little") == 2
    assert reader.read(2) == b"\x00\x01"
    assert reader.read_compact_size(maximum=1) == 1
    assert reader.read(32) == bytes(32)
    assert int.from_bytes(reader.read(4), "little") == 0xFFFFFFFF
    script_length = reader.read_compact_size(maximum=100)
    script_sig = reader.read(script_length)
    assert script_sig.startswith(bytes.fromhex("02800051") + HASHORB_COINBASE_MARKER)
    assert script_sig.endswith((7).to_bytes(8, "little"))
    assert int.from_bytes(reader.read(4), "little") == 0xFFFFFFFF
    assert reader.read_compact_size(maximum=2) == 2
    assert int.from_bytes(reader.read(8), "little") == template.coinbase_value
    assert reader.read(reader.read_compact_size(maximum=100)) == _PAYOUT_SCRIPT
    assert int.from_bytes(reader.read(8), "little") == 0
    assert reader.read(reader.read_compact_size(maximum=100)) == template.witness_commitment
    assert reader.read_compact_size(maximum=1) == 1
    assert reader.read(reader.read_compact_size(maximum=32)) == WITNESS_RESERVED_VALUE
    assert reader.read(4) == bytes(4)
    reader.require_end()
    assert coinbase.output_value == template.coinbase_value
    assert coinbase.transaction.input_count == 1
    assert coinbase.transaction.output_count == 2
    assert coinbase.transaction.has_witness


def test_coinbase_txid_wtxid_and_stripped_serialization_are_independent() -> None:
    coinbase = build_solo_coinbase(_template(), _PAYOUT_SCRIPT, 0)

    assert coinbase.transaction.txid == double_sha256(coinbase.transaction.stripped)
    assert coinbase.transaction.wtxid == double_sha256(coinbase.transaction.raw)
    assert coinbase.transaction.txid != coinbase.transaction.wtxid
    assert coinbase.transaction.raw.hex() not in repr(coinbase)
    assert _PAYOUT_SCRIPT.hex() not in repr(coinbase)


def test_coinbase_auxiliary_flags_and_script_length_limits() -> None:
    flags = bytes(range(16))
    coinbase = build_solo_coinbase(_template(flags=flags), _PAYOUT_SCRIPT, 0)
    assert coinbase.script_sig.startswith(bytes.fromhex("028000") + flags)

    height_prefix = bytes.fromhex("028000")
    oversized_length = (
        MAX_COINBASE_SCRIPT_BYTES
        - len(height_prefix)
        - len(HASHORB_COINBASE_MARKER)
        - COINBASE_EXTRA_NONCE_BYTES
        + 1
    )
    oversized_flags = bytes(range(oversized_length))
    with pytest.raises(CoinbaseConstructionError, match="script length"):
        build_solo_coinbase(_template(flags=oversized_flags), _PAYOUT_SCRIPT, 0)


def test_coinbase_extra_nonce_progression_is_bounded_and_exact() -> None:
    assert next_coinbase_extra_nonce(0) == 1
    assert next_coinbase_extra_nonce(MAX_COINBASE_EXTRA_NONCE - 1) == MAX_COINBASE_EXTRA_NONCE
    assert next_coinbase_extra_nonce(MAX_COINBASE_EXTRA_NONCE) is None


def test_solo_header_has_exact_core_byte_order_and_length() -> None:
    previous = bytes(range(32)).hex()
    merkle = bytes(range(32, 64))
    header = serialize_solo_header(
        version=0x01020304,
        previous_block_hash=previous,
        merkle_root=merkle,
        timestamp=0x11121314,
        bits="1d00ffff",
        nonce=0x21222324,
    )

    assert len(header) == 80
    assert header[:4] == bytes.fromhex("04030201")
    assert header[4:36] == bytes(range(32))[::-1]
    assert header[36:68] == merkle
    assert header[68:72] == bytes.fromhex("14131211")
    assert header[72:76] == bytes.fromhex("ffff001d")
    assert header[76:] == bytes.fromhex("24232221")


def test_prepared_solo_work_reuses_backend_contract_without_share_semantics() -> None:
    variant = prepare_solo_work(
        chain="regtest",
        template=_template(transactions=(_transaction(),)),
        payout_script=_PAYOUT_SCRIPT,
        coinbase_extra_nonce=9,
    )

    assert len(variant.prepared_work.header_prefix) == 76
    assert variant.prepared_work.network_target == variant.prepared_work.share_target
    assert variant.prepared_work.job_id == f"solo-{variant.identity}"
    assert len(variant.identity) == 16
    representation = repr(variant.prepared_work)
    assert variant.prepared_work.extra_nonce_2 not in representation
    assert variant.prepared_work.header_prefix.hex() not in representation


def test_coinbase_change_changes_merkle_header_and_work_identity() -> None:
    template = _template(transactions=(_transaction(),))
    first = prepare_solo_work(
        chain="regtest",
        template=template,
        payout_script=_PAYOUT_SCRIPT,
        coinbase_extra_nonce=0,
    )
    second = prepare_solo_work(
        chain="regtest",
        template=template,
        payout_script=_PAYOUT_SCRIPT,
        coinbase_extra_nonce=1,
    )

    assert first.coinbase.transaction.txid != second.coinbase.transaction.txid
    assert first.merkle_root != second.merkle_root
    assert first.prepared_work.header_prefix != second.prepared_work.header_prefix
    assert first.identity != second.identity


def test_complete_block_assembly_preserves_order_count_merkle_size_and_weight() -> None:
    raws = (_transaction(0x21), _transaction(0x31), _transaction(0x41))
    variant = prepare_solo_work(
        chain="regtest",
        template=_template(transactions=raws),
        payout_script=_PAYOUT_SCRIPT,
        coinbase_extra_nonce=0,
    )
    result = search_nonce_range(variant.prepared_work, 0, 128)
    assert result.match is not None
    candidate = assemble_solo_block(variant, result.match.nonce)
    reader = ByteReader(candidate.serialized_block[80:])

    assert candidate.transaction_count == 4
    assert reader.read_compact_size(maximum=4) == 4
    assert candidate.serialized_block.endswith(b"".join(raws))
    assert candidate.header[36:68] == variant.merkle_root
    assert candidate.size == len(candidate.serialized_block)
    assert candidate.weight <= variant.template.weight_limit
    assert candidate.serialized_block.hex() not in repr(candidate)
    assert candidate.block_hash.hex() not in repr(candidate)


def test_block_assembly_rejects_non_candidate_and_template_limits() -> None:
    variant = prepare_solo_work(
        chain="regtest",
        template=_template(),
        payout_script=_PAYOUT_SCRIPT,
        coinbase_extra_nonce=0,
    )
    failing_nonce = next(
        nonce
        for nonce in range(128)
        if int.from_bytes(
            double_sha256(variant.prepared_work.header_prefix + nonce.to_bytes(4, "little")),
            "little",
        )
        > variant.template.target
    )
    with pytest.raises(SoloBlockError, match="network target"):
        assemble_solo_block(variant, failing_nonce)

    result = search_nonce_range(variant.prepared_work, 0, 128)
    assert result.match is not None
    constrained = replace(
        variant,
        template=replace(variant.template, size_limit=81),
    )
    with pytest.raises(SoloBlockError, match="size limit"):
        assemble_solo_block(constrained, result.match.nonce)


def test_timestamp_must_obey_minimum_and_declared_mutability() -> None:
    template = _template()
    with pytest.raises(SoloBlockError, match="minimum"):
        prepare_solo_work(
            chain="regtest",
            template=template,
            payout_script=_PAYOUT_SCRIPT,
            coinbase_extra_nonce=0,
            timestamp=template.minimum_time - 1,
        )

    fixed = replace(template, mutable=())
    with pytest.raises(SoloBlockError, match="permit"):
        prepare_solo_work(
            chain="regtest",
            template=fixed,
            payout_script=_PAYOUT_SCRIPT,
            coinbase_extra_nonce=0,
            timestamp=template.current_time + 1,
        )
