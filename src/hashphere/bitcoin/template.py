"""Strict immutable model for the Bitcoin Core block-template subset Hashsphere uses."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from hashphere.bitcoin.transaction import (
    MAX_BLOCK_BYTES,
    MAX_MONEY,
    ParsedTransaction,
    parse_transaction,
)
from hashphere.crypto import double_sha256
from hashphere.mining.target import TargetError, decode_compact_target

MAX_BLOCK_WEIGHT = 4_000_000
MAX_TEMPLATE_TRANSACTIONS = 1_000_000
MAX_COINBASE_SCRIPT_BYTES = 100
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_HEX_8 = re.compile(r"^[0-9a-f]{8}$")
_HEX_EVEN = re.compile(r"^(?:[0-9a-f]{2})*$")
_SUPPORTED_RULES = frozenset({"csv", "segwit", "taproot"})
_SUPPORTED_MUTATIONS = frozenset({"time", "transactions", "prevblock"})
_WITNESS_COMMITMENT_PREFIX = bytes.fromhex("6a24aa21a9ed")
_ZERO_WITNESS_RESERVED_VALUE = bytes(32)


class BlockTemplateError(ValueError):
    """Raised when a block template is malformed, unsupported, or contradictory."""


@dataclass(frozen=True, slots=True)
class TemplateTransaction:
    """One exact template transaction with independently verified identities."""

    transaction: ParsedTransaction = field(repr=False)
    fee_satoshis: int
    sigops: int
    depends: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class BlockTemplate:
    """Validated template state needed for deterministic solo block construction."""

    previous_block_hash: str = field(repr=False)
    version: int
    bits: str = field(repr=False)
    target: int = field(repr=False)
    height: int
    current_time: int
    minimum_time: int
    transactions: tuple[TemplateTransaction, ...] = field(repr=False)
    coinbase_value: int
    coinbase_aux_flags: bytes = field(repr=False)
    rules: tuple[str, ...]
    mutable: tuple[str, ...]
    witness_commitment: bytes = field(repr=False)
    size_limit: int
    weight_limit: int
    longpoll_id: str | None = field(default=None, repr=False)
    work_id: str | None = field(default=None, repr=False)
    fingerprint: str = ""

    @property
    def transaction_count(self) -> int:
        """Return the non-coinbase transaction count."""

        return len(self.transactions)


def parse_block_template(value: object) -> BlockTemplate:
    """Validate a Core template without normalizing malformed fields."""

    template = _object(value, "block template")
    previous_block_hash = _hex_string(template, "previousblockhash", _HEX_64)
    version = _integer(template, "version", minimum=0, maximum=0xFFFFFFFF)
    bits = _hex_string(template, "bits", _HEX_8)
    try:
        decoded_target = decode_compact_target(bits)
    except TargetError as exc:
        raise BlockTemplateError("block template bits are invalid") from exc
    target_text = template.get("target")
    if target_text is not None:
        if not isinstance(target_text, str) or _HEX_64.fullmatch(target_text) is None:
            raise BlockTemplateError("block template target is invalid")
        if int(target_text, 16) != decoded_target:
            raise BlockTemplateError("block template target contradicts compact bits")

    height = _integer(template, "height", minimum=1, maximum=0xFFFFFFFF)
    current_time = _integer(template, "curtime", minimum=0, maximum=0xFFFFFFFF)
    minimum_time = _integer(template, "mintime", minimum=0, maximum=0xFFFFFFFF)
    if current_time < minimum_time:
        raise BlockTemplateError("block template current time is below minimum time")
    coinbase_value = _integer(template, "coinbasevalue", minimum=0, maximum=MAX_MONEY)
    size_limit = _integer(template, "sizelimit", minimum=81, maximum=MAX_BLOCK_BYTES)
    weight_limit = _integer(template, "weightlimit", minimum=324, maximum=MAX_BLOCK_WEIGHT)
    rules = _string_tuple(template, "rules", required=True)
    normalized_rules = tuple(rule.removeprefix("!") for rule in rules)
    if any(rule in {"", "!"} or rule.startswith("!!") for rule in rules):
        raise BlockTemplateError("block template rule syntax is invalid")
    if len(set(normalized_rules)) != len(normalized_rules):
        raise BlockTemplateError("block template rules are contradictory")
    if "segwit" not in normalized_rules:
        raise BlockTemplateError("block template does not declare SegWit support")
    unsupported_rules = set(normalized_rules) - _SUPPORTED_RULES
    if unsupported_rules:
        raise BlockTemplateError("block template requires an unsupported rule")
    mutable = _string_tuple(template, "mutable", required=False)
    if set(mutable) - _SUPPORTED_MUTATIONS:
        raise BlockTemplateError("block template declares an unsupported mutation")
    if "signet_challenge" in template:
        raise BlockTemplateError("signet block construction is not supported")

    aux_flags = _parse_coinbase_aux(template.get("coinbaseaux"))
    transactions = _parse_template_transactions(template.get("transactions"))
    witness_commitment = _parse_witness_commitment(template.get("default_witness_commitment"))
    _verify_witness_commitment(witness_commitment, transactions)
    _validate_noncerange(template.get("noncerange"))
    longpoll_id = _optional_bounded_string(template.get("longpollid"), "longpollid", 1024)
    work_id = _optional_bounded_string(template.get("workid"), "workid", 1024)

    fingerprint = _template_fingerprint(
        previous_block_hash=previous_block_hash,
        version=version,
        bits=bits,
        height=height,
        current_time=current_time,
        transactions=transactions,
        coinbase_value=coinbase_value,
        coinbase_aux_flags=aux_flags,
        rules=rules,
        mutable=mutable,
        witness_commitment=witness_commitment,
        size_limit=size_limit,
        weight_limit=weight_limit,
    )
    return BlockTemplate(
        previous_block_hash=previous_block_hash,
        version=version,
        bits=bits,
        target=decoded_target,
        height=height,
        current_time=current_time,
        minimum_time=minimum_time,
        transactions=transactions,
        coinbase_value=coinbase_value,
        coinbase_aux_flags=aux_flags,
        rules=rules,
        mutable=mutable,
        witness_commitment=witness_commitment,
        size_limit=size_limit,
        weight_limit=weight_limit,
        longpoll_id=longpoll_id,
        work_id=work_id,
        fingerprint=fingerprint,
    )


def _parse_template_transactions(value: object) -> tuple[TemplateTransaction, ...]:
    if not isinstance(value, list) or len(value) > MAX_TEMPLATE_TRANSACTIONS:
        raise BlockTemplateError("block template transactions must be a bounded array")
    result: list[TemplateTransaction] = []
    seen_txids: set[bytes] = set()
    seen_raw: set[bytes] = set()
    for index, item in enumerate(value):
        transaction = _object(item, "template transaction")
        raw_text = _hex_string(transaction, "data", _HEX_EVEN, allow_empty=False)
        try:
            parsed = parse_transaction(bytes.fromhex(raw_text))
        except ValueError as exc:
            raise BlockTemplateError("block template transaction data is malformed") from exc
        expected_txid = _hex_string(transaction, "txid", _HEX_64)
        expected_wtxid = _hex_string(transaction, "hash", _HEX_64)
        if parsed.txid[::-1].hex() != expected_txid:
            raise BlockTemplateError("block template transaction txid is inconsistent")
        if parsed.wtxid[::-1].hex() != expected_wtxid:
            raise BlockTemplateError("block template transaction witness hash is inconsistent")
        if parsed.txid in seen_txids or parsed.raw in seen_raw:
            raise BlockTemplateError("block template contains a duplicate transaction")
        seen_txids.add(parsed.txid)
        seen_raw.add(parsed.raw)
        fee = _integer(transaction, "fee", minimum=0, maximum=MAX_MONEY)
        sigops = _integer(transaction, "sigops", minimum=0, maximum=MAX_BLOCK_WEIGHT)
        weight = _integer(transaction, "weight", minimum=1, maximum=MAX_BLOCK_WEIGHT)
        if weight != parsed.weight:
            raise BlockTemplateError("block template transaction weight is inconsistent")
        depends = _integer_tuple(transaction.get("depends"), "depends")
        if any(dependency < 1 or dependency > index for dependency in depends):
            raise BlockTemplateError("block template transaction dependency is invalid")
        if len(set(depends)) != len(depends):
            raise BlockTemplateError("block template transaction dependencies are duplicated")
        result.append(
            TemplateTransaction(
                transaction=parsed,
                fee_satoshis=fee,
                sigops=sigops,
                depends=depends,
            )
        )
    return tuple(result)


def _parse_coinbase_aux(value: object) -> bytes:
    if value is None:
        return b""
    coinbase_aux = _object(value, "coinbaseaux")
    if set(coinbase_aux) - {"flags"}:
        raise BlockTemplateError("block template coinbase auxiliary fields are unsupported")
    flags = coinbase_aux.get("flags", "")
    if not isinstance(flags, str) or _HEX_EVEN.fullmatch(flags) is None:
        raise BlockTemplateError("block template coinbase auxiliary flags are invalid")
    result = bytes.fromhex(flags)
    if len(result) > MAX_COINBASE_SCRIPT_BYTES:
        raise BlockTemplateError("block template coinbase auxiliary flags are too long")
    return result


def _parse_witness_commitment(value: object) -> bytes:
    if not isinstance(value, str) or _HEX_EVEN.fullmatch(value) is None:
        raise BlockTemplateError("SegWit block template lacks a valid witness commitment")
    result = bytes.fromhex(value)
    if len(result) != 38 or not result.startswith(_WITNESS_COMMITMENT_PREFIX):
        raise BlockTemplateError("SegWit witness commitment script has an invalid shape")
    return result


def _verify_witness_commitment(
    witness_commitment: bytes, transactions: tuple[TemplateTransaction, ...]
) -> None:
    leaves = [bytes(32), *(item.transaction.wtxid for item in transactions)]
    witness_root = calculate_hash_merkle_root(tuple(leaves))
    expected = _WITNESS_COMMITMENT_PREFIX + double_sha256(
        witness_root + _ZERO_WITNESS_RESERVED_VALUE
    )
    if witness_commitment != expected:
        raise BlockTemplateError("SegWit witness commitment is inconsistent with transactions")


def calculate_hash_merkle_root(leaves: tuple[bytes, ...]) -> bytes:
    """Calculate an internal-order merkle root from exact raw 32-byte digests."""

    if not isinstance(leaves, tuple) or not leaves:
        raise BlockTemplateError("merkle leaves must be a nonempty tuple")
    if any(not isinstance(leaf, bytes) or len(leaf) != 32 for leaf in leaves):
        raise BlockTemplateError("every merkle leaf must contain exactly 32 bytes")
    level = leaves
    while len(level) > 1:
        if len(level) % 2:
            level = (*level, level[-1])
        level = tuple(
            double_sha256(level[index] + level[index + 1]) for index in range(0, len(level), 2)
        )
    return level[0]


def _template_fingerprint(
    *,
    previous_block_hash: str,
    version: int,
    bits: str,
    height: int,
    current_time: int,
    transactions: tuple[TemplateTransaction, ...],
    coinbase_value: int,
    coinbase_aux_flags: bytes,
    rules: tuple[str, ...],
    mutable: tuple[str, ...],
    witness_commitment: bytes,
    size_limit: int,
    weight_limit: int,
) -> str:
    material = {
        "previous": previous_block_hash,
        "version": version,
        "bits": bits,
        "height": height,
        "time": current_time,
        "txids": [item.transaction.txid.hex() for item in transactions],
        "coinbase_value": coinbase_value,
        "coinbase_aux": coinbase_aux_flags.hex(),
        "rules": rules,
        "mutable": mutable,
        "commitment": witness_commitment.hex(),
        "size_limit": size_limit,
        "weight_limit": weight_limit,
    }
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()[:16]


def _object(value: object, name: str) -> dict[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise BlockTemplateError(f"{name} must be an object")
    return dict(value)


def _integer(value: Mapping[str, object], key: str, *, minimum: int, maximum: int) -> int:
    result = value.get(key)
    if not isinstance(result, int) or isinstance(result, bool) or not minimum <= result <= maximum:
        raise BlockTemplateError(f"block template field {key} is invalid")
    return result


def _hex_string(
    value: Mapping[str, object], key: str, pattern: re.Pattern[str], *, allow_empty: bool = True
) -> str:
    result = value.get(key)
    if (
        not isinstance(result, str)
        or (not allow_empty and not result)
        or pattern.fullmatch(result) is None
    ):
        raise BlockTemplateError(f"block template field {key} is invalid")
    return result


def _string_tuple(value: Mapping[str, object], key: str, *, required: bool) -> tuple[str, ...]:
    raw = value.get(key)
    if raw is None and not required:
        return ()
    if not isinstance(raw, list) or not all(isinstance(item, str) and item for item in raw):
        raise BlockTemplateError(f"block template field {key} is invalid")
    result = tuple(raw)
    if len(set(result)) != len(result):
        raise BlockTemplateError(f"block template field {key} contains duplicates")
    return result


def _integer_tuple(value: object, name: str) -> tuple[int, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise BlockTemplateError(f"block template transaction {name} is invalid")
    if not all(isinstance(item, int) and not isinstance(item, bool) for item in value):
        raise BlockTemplateError(f"block template transaction {name} is invalid")
    return tuple(value)


def _optional_bounded_string(value: object, name: str, maximum: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise BlockTemplateError(f"block template field {name} is invalid")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        raise BlockTemplateError(f"block template field {name} is invalid")
    return value


def _validate_noncerange(value: object) -> None:
    if value is None:
        return
    if value != "00000000ffffffff":
        raise BlockTemplateError("block template nonce range is unsupported")
