"""Tests for Stratum share-submission message construction and parsing."""

from __future__ import annotations

import json
from copy import deepcopy

import pytest

from hashorb.mining import NonceSearchMatch, PreparedMiningWork
from hashorb.network.stratum import (
    StratumMessageError,
    build_submit_request,
    parse_submit_result,
)


def _build_request(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "request_id": 7,
        "username": "bc1qexample.worker",
        "job_id": "job-A",
        "extra_nonce_2": "aB12",
        "network_time": "65F04aBc",
        "nonce": 0x12345678,
    }
    values.update(overrides)
    return build_submit_request(  # type: ignore[arg-type]
        values["request_id"],
        values["username"],
        values["job_id"],
        values["extra_nonce_2"],
        values["network_time"],
        values["nonce"],
    )


def test_build_submit_request_has_exact_shape_and_parameter_order() -> None:
    request = _build_request()

    assert request == {
        "id": 7,
        "method": "mining.submit",
        "params": [
            "bc1qexample.worker",
            "job-A",
            "aB12",
            "65F04aBc",
            "78563412",
        ],
    }


@pytest.mark.parametrize(
    ("nonce", "expected"),
    [
        (0, "00000000"),
        (1, "01000000"),
        (0x12345678, "78563412"),
        (0xFFFFFFFF, "ffffffff"),
    ],
)
def test_build_submit_request_serializes_nonce_little_endian(
    nonce: int,
    expected: str,
) -> None:
    request = _build_request(nonce=nonce)
    params = request["params"]

    assert isinstance(params, list)
    assert params[4] == expected
    assert params[4] == nonce.to_bytes(4, "little").hex()


def test_submit_nonce_is_not_direct_integer_formatting() -> None:
    nonce = 0x12345678
    params = _build_request(nonce=nonce)["params"]

    assert isinstance(params, list)
    assert params[4] != f"{nonce:08x}"


def test_build_submit_request_is_json_compatible_and_deterministic() -> None:
    first = _build_request()
    second = _build_request()

    assert first == second
    assert json.loads(json.dumps(first)) == first


def test_build_submit_request_preserves_inputs() -> None:
    inputs = {
        "request_id": 11,
        "username": "Account.Worker",
        "job_id": "Job-AbC",
        "extra_nonce_2": "AaBbCcDd",
        "network_time": "65AbCdEf",
        "nonce": 0x10203040,
    }
    original = inputs.copy()

    request = _build_request(**inputs)

    assert inputs == original
    assert request["id"] == inputs["request_id"]
    assert request["params"][:4] == [
        inputs["username"],
        inputs["job_id"],
        inputs["extra_nonce_2"],
        inputs["network_time"],
    ]


@pytest.mark.parametrize("request_id", [-1])
def test_build_submit_request_rejects_negative_request_id(request_id: int) -> None:
    with pytest.raises(ValueError, match="request_id"):
        _build_request(request_id=request_id)


@pytest.mark.parametrize("request_id", [True, 1.0, "1", None, b"1"])
def test_build_submit_request_rejects_invalid_request_id(request_id: object) -> None:
    with pytest.raises(TypeError, match="request_id"):
        _build_request(request_id=request_id)


@pytest.mark.parametrize("field", ["username", "job_id"])
@pytest.mark.parametrize("value", ["", " ", "\t\n"])
def test_build_submit_request_rejects_blank_text_fields(field: str, value: str) -> None:
    with pytest.raises(ValueError, match=field):
        _build_request(**{field: value})


@pytest.mark.parametrize("field", ["username", "job_id"])
@pytest.mark.parametrize("value", [1, None, b"value", True])
def test_build_submit_request_rejects_non_string_text_fields(
    field: str,
    value: object,
) -> None:
    with pytest.raises(TypeError, match=field):
        _build_request(**{field: value})


@pytest.mark.parametrize(
    "extra_nonce_2",
    ["", "0", "abc", "zz", "00 11", " 0011", "0x0011", "00-11"],
)
def test_build_submit_request_rejects_invalid_extra_nonce_2(
    extra_nonce_2: str,
) -> None:
    with pytest.raises(ValueError, match="extra_nonce_2"):
        _build_request(extra_nonce_2=extra_nonce_2)


@pytest.mark.parametrize("extra_nonce_2", [1, None, b"0011", True])
def test_build_submit_request_rejects_non_string_extra_nonce_2(
    extra_nonce_2: object,
) -> None:
    with pytest.raises(TypeError, match="extra_nonce_2"):
        _build_request(extra_nonce_2=extra_nonce_2)


@pytest.mark.parametrize(
    "network_time",
    ["", "1234567", "123456789", "zzzzzzzz", "1234 678", " 12345678", "0x12345678"],
)
def test_build_submit_request_rejects_invalid_network_time(network_time: str) -> None:
    with pytest.raises(ValueError, match="network_time"):
        _build_request(network_time=network_time)


@pytest.mark.parametrize("network_time", [1, None, b"12345678", True])
def test_build_submit_request_rejects_non_string_network_time(
    network_time: object,
) -> None:
    with pytest.raises(TypeError, match="network_time"):
        _build_request(network_time=network_time)


@pytest.mark.parametrize("nonce", [-1, 0x100000000])
def test_build_submit_request_rejects_out_of_range_nonce(nonce: int) -> None:
    with pytest.raises(ValueError, match="nonce"):
        _build_request(nonce=nonce)


@pytest.mark.parametrize(
    "nonce",
    [True, False, 1.0, "1", b"1", bytearray(b"1"), memoryview(b"1"), None, object()],
)
def test_build_submit_request_rejects_non_integer_nonce(nonce: object) -> None:
    with pytest.raises(TypeError, match="nonce"):
        _build_request(nonce=nonce)


@pytest.mark.parametrize("result", [True, False])
def test_parse_submit_result_accepts_actual_boolean(result: bool) -> None:
    assert parse_submit_result({"id": 9, "result": result, "error": None}) is result


def test_parse_submit_result_accepts_false_with_reject_reason() -> None:
    message: dict[str, object] = {
        "id": 9,
        "result": False,
        "error": None,
        "reject-reason": "low difficulty share",
    }

    assert parse_submit_result(message) is False


@pytest.mark.parametrize("result", [1, 0, "true", "false", None, [], {}])
def test_parse_submit_result_rejects_non_boolean_result(result: object) -> None:
    with pytest.raises(StratumMessageError, match="result must be a boolean"):
        parse_submit_result({"id": 9, "result": result, "error": None})


@pytest.mark.parametrize("message", [{}, {"id": 9}, {"id": 9, "error": None}])
def test_parse_submit_result_rejects_missing_result(
    message: dict[str, object],
) -> None:
    with pytest.raises(StratumMessageError, match="missing required field: result"):
        parse_submit_result(message)


def test_parse_submit_result_does_not_mutate_message() -> None:
    message: dict[str, object] = {
        "id": 9,
        "result": False,
        "error": None,
        "reject-reason": "stale",
    }
    original = deepcopy(message)

    parse_submit_result(message)

    assert message == original


def test_submit_request_uses_prepared_work_and_match_nonce_bytes() -> None:
    work = PreparedMiningWork(
        job_id="synthetic-job",
        extra_nonce_2="A1b2C3d4",
        network_time="65F04aBc",
        header_prefix=bytes(range(76)),
        network_target=1,
        share_target=2,
    )
    match = NonceSearchMatch(
        nonce=0x12345678,
        block_hash=bytes(32),
        meets_share_target=True,
        meets_network_target=False,
    )

    request = build_submit_request(
        request_id=12,
        username="account.worker",
        job_id=work.job_id,
        extra_nonce_2=work.extra_nonce_2,
        network_time=work.network_time,
        nonce=match.nonce,
    )
    params = request["params"]
    candidate_header = work.header_prefix + match.nonce.to_bytes(4, "little")

    assert isinstance(params, list)
    assert params[1:4] == [work.job_id, work.extra_nonce_2, work.network_time]
    assert params[4] == candidate_header[76:80].hex()
    assert bytes.fromhex(params[4]) == candidate_header[76:80]
