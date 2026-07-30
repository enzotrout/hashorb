"""Independent synthetic tests for Bitcoin transaction and template models."""

from __future__ import annotations

import hashlib
from dataclasses import FrozenInstanceError

import pytest

from hashphere.bitcoin.serialization import (
    BitcoinSerializationError,
    ByteReader,
    encode_compact_size,
)
from hashphere.bitcoin.template import (
    BlockTemplateError,
    calculate_hash_merkle_root,
    parse_block_template,
)
from hashphere.bitcoin.transaction import BitcoinTransactionError, parse_transaction
from hashphere.crypto import double_sha256
from hashphere.mining.target import decode_compact_target

_COMMITMENT_PREFIX = bytes.fromhex("6a24aa21a9ed")


def _legacy_transaction(*, marker: int = 0x11, value: int = 1_000) -> bytes:
    return b"".join(
        (
            (1).to_bytes(4, "little"),
            b"\x01",
            bytes((marker,)) * 32,
            (0).to_bytes(4, "little"),
            b"\x01\x51",
            (0xFFFFFFFF).to_bytes(4, "little"),
            b"\x01",
            value.to_bytes(8, "little"),
            b"\x01\x51",
            (0).to_bytes(4, "little"),
        )
    )


def _witness_transaction() -> bytes:
    return b"".join(
        (
            (2).to_bytes(4, "little"),
            b"\x00\x01",
            b"\x01",
            b"\x22" * 32,
            (1).to_bytes(4, "little"),
            b"\x00",
            (0xFFFFFFFE).to_bytes(4, "little"),
            b"\x01",
            (2_000).to_bytes(8, "little"),
            b"\x01\x51",
            b"\x02\x01\x01\x02\x02\x03",
            (0).to_bytes(4, "little"),
        )
    )


def _template_transactions(raw_transactions: list[bytes]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for raw in raw_transactions:
        parsed = parse_transaction(raw)
        result.append(
            {
                "data": raw.hex(),
                "txid": parsed.txid[::-1].hex(),
                "hash": parsed.wtxid[::-1].hex(),
                "depends": [],
                "fee": 100,
                "sigops": 1,
                "weight": parsed.weight,
            }
        )
    return result


def _witness_commitment(raw_transactions: list[bytes]) -> str:
    leaves = (bytes(32), *(parse_transaction(raw).wtxid for raw in raw_transactions))
    root = calculate_hash_merkle_root(leaves)
    return (_COMMITMENT_PREFIX + double_sha256(root + bytes(32))).hex()


def _valid_template(raw_transactions: list[bytes] | None = None) -> dict[str, object]:
    transactions = [] if raw_transactions is None else raw_transactions
    target = decode_compact_target("207fffff")
    return {
        "previousblockhash": "11" * 32,
        "version": 0x20000000,
        "bits": "207fffff",
        "target": f"{target:064x}",
        "height": 101,
        "curtime": 1_700_000_001,
        "mintime": 1_700_000_000,
        "transactions": _template_transactions(transactions),
        "coinbasevalue": 5_000_000_000,
        "coinbaseaux": {"flags": "062f503253482f"},
        "rules": ["csv", "segwit", "taproot"],
        "mutable": ["time", "transactions", "prevblock"],
        "noncerange": "00000000ffffffff",
        "sizelimit": 1_000_000,
        "weightlimit": 4_000_000,
        "default_witness_commitment": _witness_commitment(transactions),
        "longpollid": "synthetic-longpoll-identity",
    }


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, "00"),
        (252, "fc"),
        (253, "fdfd00"),
        (65_535, "fdffff"),
        (65_536, "fe00000100"),
        (0xFFFFFFFF, "feffffffff"),
        (0x1_0000_0000, "ff0000000001000000"),
    ],
)
def test_compact_size_boundaries(value: int, expected: str) -> None:
    encoded = encode_compact_size(value)
    reader = ByteReader(encoded)
    assert encoded.hex() == expected
    assert reader.read_compact_size(maximum=value) == value
    reader.require_end()


@pytest.mark.parametrize(
    "encoded",
    [b"\xfd\xfc\x00", b"\xfe\xff\xff\x00\x00", b"\xff" + b"\xff" * 4 + b"\x00" * 4],
)
def test_compact_size_rejects_noncanonical_encodings(encoded: bytes) -> None:
    with pytest.raises(BitcoinSerializationError, match="noncanonical"):
        ByteReader(encoded).read_compact_size(maximum=0xFFFFFFFFFFFFFFFF)


def test_legacy_transaction_parser_independently_derives_identifiers_and_weight() -> None:
    raw = _legacy_transaction()
    parsed = parse_transaction(raw)

    assert parsed.raw == parsed.stripped == raw
    assert parsed.txid == parsed.wtxid == hashlib.sha256(hashlib.sha256(raw).digest()).digest()
    assert parsed.input_count == parsed.output_count == 1
    assert parsed.weight == len(raw) * 4
    assert not parsed.has_witness
    assert raw.hex() not in repr(parsed)


def test_witness_transaction_parser_strips_marker_flag_and_witness_stack() -> None:
    raw = _witness_transaction()
    parsed = parse_transaction(raw)

    expected_stripped = raw[:4] + raw[6:59] + raw[-4:]
    assert parsed.stripped == expected_stripped
    assert parsed.txid != parsed.wtxid
    assert parsed.has_witness
    assert parsed.weight == len(expected_stripped) * 3 + len(raw)


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        (1).to_bytes(4, "little") + b"\x00\x02" + b"\x00" * 10,
        _legacy_transaction()[:-1],
        _legacy_transaction() + b"\x00",
    ],
)
def test_transaction_parser_rejects_malformed_or_trailing_bytes(raw: bytes) -> None:
    with pytest.raises((BitcoinTransactionError, BitcoinSerializationError)):
        parse_transaction(raw)


def test_minimal_segwit_template_is_strict_immutable_and_sanitized() -> None:
    parsed = parse_block_template(_valid_template())

    assert parsed.height == 101
    assert parsed.transaction_count == 0
    assert parsed.target == decode_compact_target("207fffff")
    assert parsed.coinbase_aux_flags == bytes.fromhex("062f503253482f")
    assert len(parsed.fingerprint) == 16
    assert "11" * 32 not in repr(parsed)
    assert parsed.witness_commitment.hex() not in repr(parsed)
    with pytest.raises(FrozenInstanceError):
        parsed.height = 102  # type: ignore[misc]


def test_template_accepts_core_required_segwit_marker_but_rejects_signet() -> None:
    required_segwit = _valid_template()
    required_segwit["rules"] = ["csv", "!segwit", "taproot"]
    assert "!segwit" in parse_block_template(required_segwit).rules

    signet = _valid_template()
    signet["rules"] = ["csv", "!segwit", "taproot", "!signet"]
    signet["signet_challenge"] = "51"
    with pytest.raises(BlockTemplateError, match="unsupported rule|signet"):
        parse_block_template(signet)


def test_template_preserves_transaction_order_and_validates_witness_commitment() -> None:
    first = _legacy_transaction(marker=0x31)
    second = _witness_transaction()
    template = _valid_template([first, second])
    parsed = parse_block_template(template)

    assert [item.transaction.raw for item in parsed.transactions] == [first, second]
    assert parsed.transactions[0].transaction.txid != parsed.transactions[1].transaction.txid


def test_template_fingerprint_is_stable_and_changes_with_transaction_set() -> None:
    template = _valid_template()
    first = parse_block_template(template)
    second = parse_block_template(dict(template))
    changed = parse_block_template(_valid_template([_legacy_transaction()]))

    assert first.fingerprint == second.fingerprint
    assert first.fingerprint != changed.fingerprint


@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    [
        ("previousblockhash", "11", "previousblockhash"),
        ("bits", "zzzzzzzz", "bits"),
        ("target", "00" * 32, "contradicts"),
        ("height", -1, "height"),
        ("coinbasevalue", -1, "coinbasevalue"),
        ("coinbasevalue", 21_000_000 * 100_000_000 + 1, "coinbasevalue"),
        ("rules", ["segwit", "unknown-active-rule"], "unsupported rule"),
        ("rules", ["csv"], "SegWit"),
        ("mutable", ["coinbase"], "unsupported mutation"),
        ("sizelimit", 4_000_001, "sizelimit"),
        ("weightlimit", 4_000_001, "weightlimit"),
        ("noncerange", "01000000ffffffff", "nonce range"),
    ],
)
def test_template_rejects_malformed_unsupported_or_excessive_fields(
    field: str, replacement: object, message: str
) -> None:
    template = _valid_template()
    template[field] = replacement

    with pytest.raises(BlockTemplateError, match=message):
        parse_block_template(template)


def test_template_rejects_missing_or_inconsistent_witness_commitment() -> None:
    missing = _valid_template()
    del missing["default_witness_commitment"]
    with pytest.raises(BlockTemplateError, match="commitment"):
        parse_block_template(missing)

    inconsistent = _valid_template()
    inconsistent["default_witness_commitment"] = _COMMITMENT_PREFIX.hex() + "00" * 32
    with pytest.raises(BlockTemplateError, match="inconsistent"):
        parse_block_template(inconsistent)


def test_template_rejects_duplicate_malformed_or_inconsistent_transactions() -> None:
    raw = _legacy_transaction()
    duplicate = _valid_template([raw, raw])
    with pytest.raises(BlockTemplateError, match="duplicate"):
        parse_block_template(duplicate)

    malformed = _valid_template()
    malformed["transactions"] = [
        {
            "data": "00",
            "txid": "00" * 32,
            "hash": "00" * 32,
            "depends": [],
            "fee": 1,
            "sigops": 1,
            "weight": 4,
        }
    ]
    with pytest.raises(BlockTemplateError, match="malformed"):
        parse_block_template(malformed)

    wrong_txid = _valid_template([raw])
    transactions = wrong_txid["transactions"]
    assert isinstance(transactions, list)
    transactions[0]["txid"] = "00" * 32
    with pytest.raises(BlockTemplateError, match="txid"):
        parse_block_template(wrong_txid)


def test_merkle_root_vectors_cover_odd_duplication_and_byte_order() -> None:
    leaves = (
        bytes(range(32)),
        bytes(range(32, 64)),
        bytes(range(64, 96)),
    )
    parent_left = double_sha256(leaves[0] + leaves[1])
    parent_right = double_sha256(leaves[2] + leaves[2])
    expected = double_sha256(parent_left + parent_right)

    assert calculate_hash_merkle_root((leaves[0],)) == leaves[0]
    assert calculate_hash_merkle_root(leaves) == expected
    assert calculate_hash_merkle_root(tuple(reversed(leaves))) != expected
    assert calculate_hash_merkle_root(tuple(leaf[::-1] for leaf in leaves)) != expected
