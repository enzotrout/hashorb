"""Strict immutable model for the Bitcoin Core block-template subset HashOrb uses."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from hashorb.bitcoin.transaction import (
    MAX_BLOCK_BYTES,
    MAX_MONEY,
    ParsedTransaction,
    parse_transaction,
)
from hashorb.crypto import double_sha256
from hashorb.mining.target import TargetError, decode_compact_target

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
_DIAGNOSTIC_CATEGORIES = frozenset(
    {
        "duplicate_transaction",
        "inconsistent_fields",
        "invalid_encoding",
        "invalid_length",
        "invalid_optional_field",
        "invalid_structure",
        "invalid_transaction_data",
        "invalid_transaction_identity",
        "invalid_type",
        "missing_required_field",
        "out_of_range",
        "unsupported_mutation",
        "unsupported_rule",
    }
)
_DIAGNOSTIC_FIELD_PATHS = frozenset(
    {
        "bits",
        "bits_target",
        "coinbaseaux",
        "coinbaseaux.flags",
        "coinbasevalue",
        "curtime",
        "curtime_mintime",
        "default_witness_commitment",
        "height",
        "longpollid",
        "merkle_leaves",
        "mintime",
        "mutable[]",
        "noncerange",
        "previousblockhash",
        "rules[]",
        "signet_challenge",
        "sizelimit",
        "target",
        "template",
        "transactions",
        "transactions[].data",
        "transactions[].depends",
        "transactions[].fee",
        "transactions[].hash",
        "transactions[].sigops",
        "transactions[].txid",
        "transactions[].weight",
        "version",
        "weightlimit",
        "workid",
    }
)
_DIAGNOSTIC_EXPECTATIONS = frozenset(
    {
        "bounded array",
        "bounded integer",
        "bounded string",
        "canonical nonce range",
        "canonical transaction",
        "consistent fields",
        "lowercase hexadecimal string",
        "matching derived identity",
        "matching derived weight",
        "object",
        "required field",
        "SegWit commitment",
        "supported mutation",
        "supported rule",
        "unique transaction",
        "valid dependency array",
    }
)
_DIAGNOSTIC_CONDITIONS = frozenset(
    {
        "duplicate",
        "inconsistent",
        "invalid_encoding",
        "invalid_length",
        "malformed",
        "missing",
        "out_of_range",
        "unexpected_content",
        "unsupported",
        "wrong_type",
    }
)


class BlockTemplateError(ValueError):
    """Raised when a block template is malformed, unsupported, or contradictory."""

    def __init__(
        self,
        category: str,
        field_path: str,
        expected_kind: str,
        observed_condition: str,
    ) -> None:
        if (
            category not in _DIAGNOSTIC_CATEGORIES
            or field_path not in _DIAGNOSTIC_FIELD_PATHS
            or expected_kind not in _DIAGNOSTIC_EXPECTATIONS
            or observed_condition not in _DIAGNOSTIC_CONDITIONS
        ):
            category = "invalid_structure"
            field_path = "template"
            expected_kind = "object"
            observed_condition = "malformed"
        self.category = category
        self.field_path = field_path
        self.expected_kind = expected_kind
        self.observed_condition = observed_condition
        super().__init__(f"{category}: {field_path}")


@dataclass(frozen=True, slots=True)
class TemplateTransaction:
    """One exact template transaction with independently verified identities."""

    transaction: ParsedTransaction = field(repr=False)
    fee_satoshis: int | None
    sigops: int | None
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

    template = _object(value, "template")
    previous_block_hash = _hex_string(template, "previousblockhash", "previousblockhash", _HEX_64)
    version = _integer(template, "version", "version", minimum=0, maximum=0xFFFFFFFF)
    bits = _hex_string(template, "bits", "bits", _HEX_8)
    try:
        decoded_target = decode_compact_target(bits)
    except TargetError as exc:
        raise BlockTemplateError(
            "invalid_encoding",
            "bits",
            "lowercase hexadecimal string",
            "invalid_encoding",
        ) from exc
    target_text = template.get("target")
    if target_text is not None:
        if not isinstance(target_text, str):
            raise BlockTemplateError(
                "invalid_type", "target", "lowercase hexadecimal string", "wrong_type"
            )
        if len(target_text) != 64:
            raise BlockTemplateError(
                "invalid_length", "target", "lowercase hexadecimal string", "invalid_length"
            )
        if _HEX_64.fullmatch(target_text) is None:
            raise BlockTemplateError(
                "invalid_encoding",
                "target",
                "lowercase hexadecimal string",
                "invalid_encoding",
            )
        if int(target_text, 16) != decoded_target:
            raise BlockTemplateError(
                "inconsistent_fields", "bits_target", "consistent fields", "inconsistent"
            )

    height = _integer(template, "height", "height", minimum=1, maximum=0xFFFFFFFF)
    current_time = _integer(template, "curtime", "curtime", minimum=0, maximum=0xFFFFFFFF)
    minimum_time = _integer(template, "mintime", "mintime", minimum=0, maximum=0xFFFFFFFF)
    if current_time < minimum_time:
        raise BlockTemplateError(
            "inconsistent_fields", "curtime_mintime", "consistent fields", "inconsistent"
        )
    coinbase_value = _integer(
        template, "coinbasevalue", "coinbasevalue", minimum=0, maximum=MAX_MONEY
    )
    size_limit = _integer(template, "sizelimit", "sizelimit", minimum=81, maximum=MAX_BLOCK_BYTES)
    weight_limit = _integer(
        template, "weightlimit", "weightlimit", minimum=324, maximum=MAX_BLOCK_WEIGHT
    )
    rules = _string_tuple(template, "rules", "rules[]", required=True)
    normalized_rules = tuple(rule.removeprefix("!") for rule in rules)
    if any(rule in {"", "!"} or rule.startswith("!!") for rule in rules):
        raise BlockTemplateError(
            "invalid_encoding", "rules[]", "supported rule", "invalid_encoding"
        )
    if len(set(normalized_rules)) != len(normalized_rules):
        raise BlockTemplateError("inconsistent_fields", "rules[]", "supported rule", "inconsistent")
    if "segwit" not in normalized_rules:
        raise BlockTemplateError("unsupported_rule", "rules[]", "supported rule", "unsupported")
    unsupported_rules = set(normalized_rules) - _SUPPORTED_RULES
    if unsupported_rules:
        raise BlockTemplateError("unsupported_rule", "rules[]", "supported rule", "unsupported")
    mutable = _string_tuple(template, "mutable", "mutable[]", required=False)
    if set(mutable) - _SUPPORTED_MUTATIONS:
        raise BlockTemplateError(
            "unsupported_mutation", "mutable[]", "supported mutation", "unsupported"
        )
    if "signet_challenge" in template:
        raise BlockTemplateError(
            "unsupported_rule", "signet_challenge", "supported rule", "unsupported"
        )

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
    if value is None:
        raise BlockTemplateError(
            "missing_required_field", "transactions", "required field", "missing"
        )
    if not isinstance(value, list):
        raise BlockTemplateError("invalid_type", "transactions", "bounded array", "wrong_type")
    if len(value) > MAX_TEMPLATE_TRANSACTIONS:
        raise BlockTemplateError("out_of_range", "transactions", "bounded array", "out_of_range")
    result: list[TemplateTransaction] = []
    seen_txids: set[bytes] = set()
    seen_raw: set[bytes] = set()
    for index, item in enumerate(value):
        transaction = _object(item, "transactions")
        raw_text = _hex_string(
            transaction,
            "data",
            "transactions[].data",
            _HEX_EVEN,
            allow_empty=False,
        )
        try:
            parsed = parse_transaction(bytes.fromhex(raw_text))
        except ValueError as exc:
            raise BlockTemplateError(
                "invalid_transaction_data",
                "transactions[].data",
                "canonical transaction",
                "malformed",
            ) from exc
        expected_txid = _hex_string(transaction, "txid", "transactions[].txid", _HEX_64)
        expected_wtxid = _hex_string(transaction, "hash", "transactions[].hash", _HEX_64)
        if parsed.txid[::-1].hex() != expected_txid:
            raise BlockTemplateError(
                "invalid_transaction_identity",
                "transactions[].txid",
                "matching derived identity",
                "inconsistent",
            )
        if parsed.wtxid[::-1].hex() != expected_wtxid:
            raise BlockTemplateError(
                "invalid_transaction_identity",
                "transactions[].hash",
                "matching derived identity",
                "inconsistent",
            )
        if parsed.txid in seen_txids or parsed.raw in seen_raw:
            raise BlockTemplateError(
                "duplicate_transaction",
                "transactions",
                "unique transaction",
                "duplicate",
            )
        seen_txids.add(parsed.txid)
        seen_raw.add(parsed.raw)
        fee = _optional_integer(
            transaction,
            "fee",
            "transactions[].fee",
            minimum=0,
            maximum=MAX_MONEY,
        )
        sigops = _optional_integer(
            transaction,
            "sigops",
            "transactions[].sigops",
            minimum=0,
            maximum=MAX_BLOCK_WEIGHT,
        )
        weight = _integer(
            transaction,
            "weight",
            "transactions[].weight",
            minimum=1,
            maximum=MAX_BLOCK_WEIGHT,
        )
        if weight != parsed.weight:
            raise BlockTemplateError(
                "inconsistent_fields",
                "transactions[].weight",
                "matching derived weight",
                "inconsistent",
            )
        depends = _integer_tuple(transaction.get("depends"), "transactions[].depends")
        if any(dependency < 1 or dependency > index for dependency in depends):
            raise BlockTemplateError(
                "invalid_structure",
                "transactions[].depends",
                "valid dependency array",
                "malformed",
            )
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
        raise BlockTemplateError(
            "invalid_optional_field", "coinbaseaux", "object", "unexpected_content"
        )
    flags = coinbase_aux.get("flags", "")
    if not isinstance(flags, str):
        raise BlockTemplateError(
            "invalid_type",
            "coinbaseaux.flags",
            "lowercase hexadecimal string",
            "wrong_type",
        )
    if len(flags) % 2 != 0:
        raise BlockTemplateError(
            "invalid_length",
            "coinbaseaux.flags",
            "lowercase hexadecimal string",
            "invalid_length",
        )
    if _HEX_EVEN.fullmatch(flags) is None:
        raise BlockTemplateError(
            "invalid_encoding",
            "coinbaseaux.flags",
            "lowercase hexadecimal string",
            "invalid_encoding",
        )
    result = bytes.fromhex(flags)
    if len(result) > MAX_COINBASE_SCRIPT_BYTES:
        raise BlockTemplateError(
            "out_of_range", "coinbaseaux.flags", "bounded string", "out_of_range"
        )
    return result


def _parse_witness_commitment(value: object) -> bytes:
    if value is None:
        raise BlockTemplateError(
            "missing_required_field",
            "default_witness_commitment",
            "required field",
            "missing",
        )
    if not isinstance(value, str):
        raise BlockTemplateError(
            "invalid_type",
            "default_witness_commitment",
            "SegWit commitment",
            "wrong_type",
        )
    if _HEX_EVEN.fullmatch(value) is None:
        raise BlockTemplateError(
            "invalid_encoding",
            "default_witness_commitment",
            "SegWit commitment",
            "invalid_encoding",
        )
    result = bytes.fromhex(value)
    if len(result) != 38 or not result.startswith(_WITNESS_COMMITMENT_PREFIX):
        raise BlockTemplateError(
            "invalid_length",
            "default_witness_commitment",
            "SegWit commitment",
            "invalid_length",
        )
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
        raise BlockTemplateError(
            "inconsistent_fields",
            "default_witness_commitment",
            "SegWit commitment",
            "inconsistent",
        )


def calculate_hash_merkle_root(leaves: tuple[bytes, ...]) -> bytes:
    """Calculate an internal-order merkle root from exact raw 32-byte digests."""

    if not isinstance(leaves, tuple) or not leaves:
        raise BlockTemplateError("invalid_structure", "merkle_leaves", "bounded array", "malformed")
    if any(not isinstance(leaf, bytes) or len(leaf) != 32 for leaf in leaves):
        raise BlockTemplateError(
            "invalid_length", "merkle_leaves", "bounded array", "invalid_length"
        )
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


def _object(value: object, field_path: str) -> dict[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        safe_path = (
            field_path if field_path in {"template", "transactions", "coinbaseaux"} else "template"
        )
        raise BlockTemplateError("invalid_type", safe_path, "object", "wrong_type")
    return dict(value)


def _integer(
    value: Mapping[str, object],
    key: str,
    field_path: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    if key not in value:
        raise BlockTemplateError("missing_required_field", field_path, "required field", "missing")
    result = value[key]
    if not isinstance(result, int) or isinstance(result, bool):
        raise BlockTemplateError("invalid_type", field_path, "bounded integer", "wrong_type")
    if not minimum <= result <= maximum:
        raise BlockTemplateError("out_of_range", field_path, "bounded integer", "out_of_range")
    return result


def _optional_integer(
    value: Mapping[str, object],
    key: str,
    field_path: str,
    *,
    minimum: int,
    maximum: int,
) -> int | None:
    if key not in value:
        return None
    result = value[key]
    if not isinstance(result, int) or isinstance(result, bool):
        raise BlockTemplateError("invalid_type", field_path, "bounded integer", "wrong_type")
    if not minimum <= result <= maximum:
        raise BlockTemplateError("out_of_range", field_path, "bounded integer", "out_of_range")
    return result


def _hex_string(
    value: Mapping[str, object],
    key: str,
    field_path: str,
    pattern: re.Pattern[str],
    *,
    allow_empty: bool = True,
) -> str:
    if key not in value:
        raise BlockTemplateError("missing_required_field", field_path, "required field", "missing")
    result = value[key]
    if not isinstance(result, str):
        raise BlockTemplateError(
            "invalid_type", field_path, "lowercase hexadecimal string", "wrong_type"
        )
    expected_length = 64 if pattern is _HEX_64 else 8 if pattern is _HEX_8 else None
    if (
        (expected_length is not None and len(result) != expected_length)
        or (pattern is _HEX_EVEN and len(result) % 2 != 0)
        or (not allow_empty and not result)
    ):
        raise BlockTemplateError(
            "invalid_length",
            field_path,
            "lowercase hexadecimal string",
            "invalid_length",
        )
    if pattern.fullmatch(result) is None:
        raise BlockTemplateError(
            "invalid_encoding",
            field_path,
            "lowercase hexadecimal string",
            "invalid_encoding",
        )
    return result


def _string_tuple(
    value: Mapping[str, object], key: str, field_path: str, *, required: bool
) -> tuple[str, ...]:
    if key not in value:
        if required:
            raise BlockTemplateError(
                "missing_required_field", field_path, "required field", "missing"
            )
        return ()
    raw = value[key]
    if not isinstance(raw, list) or not all(isinstance(item, str) and item for item in raw):
        raise BlockTemplateError("invalid_type", field_path, "bounded array", "wrong_type")
    result = tuple(raw)
    if len(set(result)) != len(result):
        raise BlockTemplateError("invalid_structure", field_path, "bounded array", "duplicate")
    return result


def _integer_tuple(value: object, field_path: str) -> tuple[int, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise BlockTemplateError("invalid_type", field_path, "valid dependency array", "wrong_type")
    if not all(isinstance(item, int) and not isinstance(item, bool) for item in value):
        raise BlockTemplateError("invalid_type", field_path, "valid dependency array", "wrong_type")
    return tuple(value)


def _optional_bounded_string(value: object, name: str, maximum: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise BlockTemplateError("invalid_type", name, "bounded string", "wrong_type")
    if not value or len(value) > maximum:
        raise BlockTemplateError("invalid_length", name, "bounded string", "invalid_length")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        raise BlockTemplateError("invalid_encoding", name, "bounded string", "invalid_encoding")
    return value


def _validate_noncerange(value: object) -> None:
    if value is None:
        return
    if value != "00000000ffffffff":
        category = "invalid_type" if not isinstance(value, str) else "invalid_optional_field"
        condition = "wrong_type" if not isinstance(value, str) else "unsupported"
        raise BlockTemplateError(category, "noncerange", "canonical nonce range", condition)
