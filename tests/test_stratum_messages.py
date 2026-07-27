"""Tests for Stratum message construction and parsing."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from hashphere.network.stratum import (
    MiningNotifyNotification,
    SetDifficultyNotification,
    StratumError,
    StratumMessageError,
    SubscribeResult,
    build_authorize_request,
    build_subscribe_request,
    parse_authorize_result,
    parse_mining_notify,
    parse_set_difficulty,
    parse_stratum_error,
    parse_subscribe_result,
)


def test_build_subscribe_request() -> None:
    assert build_subscribe_request(0, "Hashphere/0.1") == {
        "id": 0,
        "method": "mining.subscribe",
        "params": ["Hashphere/0.1"],
    }


def test_build_authorize_request_accepts_conventional_password() -> None:
    assert build_authorize_request(7, "worker.1", "x") == {
        "id": 7,
        "method": "mining.authorize",
        "params": ["worker.1", "x"],
    }


@pytest.mark.parametrize("request_id", [-1, True, 1.5])
def test_request_builders_reject_invalid_request_ids(request_id: object) -> None:
    with pytest.raises((TypeError, ValueError), match="request_id"):
        build_subscribe_request(request_id, "Hashphere/0.1")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("builder", "arguments", "field"),
    [
        (build_subscribe_request, (1, ""), "user_agent"),
        (build_subscribe_request, (1, "   "), "user_agent"),
        (build_authorize_request, (1, "", "x"), "username"),
        (build_authorize_request, (1, "worker.1", ""), "password"),
    ],
)
def test_request_builders_reject_empty_values(
    builder: object,
    arguments: tuple[object, ...],
    field: str,
) -> None:
    with pytest.raises(ValueError, match=field):
        builder(*arguments)  # type: ignore[operator]


def test_parse_subscribe_result() -> None:
    message: dict[str, object] = {
        "id": 1,
        "result": [
            [
                ["mining.set_difficulty", "deadbeef"],
                ["mining.notify", "cafebabe"],
            ],
            "08000002",
            4,
        ],
        "error": None,
    }

    assert parse_subscribe_result(message) == SubscribeResult(
        subscriptions=(
            ("mining.set_difficulty", "deadbeef"),
            ("mining.notify", "cafebabe"),
        ),
        extra_nonce_1="08000002",
        extra_nonce_2_size=4,
    )


@pytest.mark.parametrize(
    ("message", "error"),
    [
        ({}, "missing required field"),
        ({"result": None}, "must be an array"),
        ({"result": [[], "abcd"]}, "exactly three"),
        ({"result": [None, "abcd", 4]}, "result\\[0\\] must be an array"),
        ({"result": [[["mining.notify"]], "abcd", 4]}, "exactly two"),
        ({"result": [[[1, "id"]], "abcd", 4]}, "must be a string"),
        ({"result": [[[]], "abcd", 4]}, "exactly two"),
        ({"result": [[], 123, 4]}, "must be a string"),
        ({"result": [[], "abcd", True]}, "must be an integer"),
        ({"result": [[], "abcd", 0]}, "greater than zero"),
    ],
)
def test_parse_subscribe_result_rejects_malformed_messages(
    message: dict[str, object],
    error: str,
) -> None:
    with pytest.raises(StratumMessageError, match=error):
        parse_subscribe_result(message)


@pytest.mark.parametrize("result", [True, False])
def test_parse_authorize_result_accepts_only_booleans(result: bool) -> None:
    assert parse_authorize_result({"result": result}) is result


@pytest.mark.parametrize("message", [{}, {"result": 1}, {"result": "true"}])
def test_parse_authorize_result_rejects_malformed_messages(
    message: dict[str, object],
) -> None:
    with pytest.raises(StratumMessageError):
        parse_authorize_result(message)


@pytest.mark.parametrize("difficulty", [1, 4096.5])
def test_parse_set_difficulty(difficulty: int | float) -> None:
    assert parse_set_difficulty(
        {
            "id": None,
            "method": "mining.set_difficulty",
            "params": [difficulty],
        }
    ) == SetDifficultyNotification(difficulty=difficulty)


@pytest.mark.parametrize(
    "message",
    [
        {},
        {"method": "mining.notify", "params": [1]},
        {"method": "mining.set_difficulty"},
        {"method": "mining.set_difficulty", "params": 1},
        {"method": "mining.set_difficulty", "params": []},
        {"method": "mining.set_difficulty", "params": [True]},
        {"method": "mining.set_difficulty", "params": ["1"]},
        {"method": "mining.set_difficulty", "params": [0]},
        {"method": "mining.set_difficulty", "params": [-1.0]},
        {"method": "mining.set_difficulty", "params": [float("inf")]},
        {"method": "mining.set_difficulty", "params": [float("nan")]},
    ],
)
def test_parse_set_difficulty_rejects_malformed_messages(
    message: dict[str, object],
) -> None:
    with pytest.raises(StratumMessageError):
        parse_set_difficulty(message)


def valid_notify_message() -> dict[str, object]:
    """Return a fresh valid mining notification."""

    return {
        "id": None,
        "method": "mining.notify",
        "params": [
            "job-1",
            "00aabbcc",
            "01000000",
            "ffffffff",
            ["11223344", "aabbccdd"],
            "20000000",
            "170fffff",
            "65f04abc",
            True,
        ],
    }


def test_parse_mining_notify_preserves_hexadecimal_strings() -> None:
    assert parse_mining_notify(valid_notify_message()) == MiningNotifyNotification(
        job_id="job-1",
        previous_block_hash="00aabbcc",
        coinbase_part_1="01000000",
        coinbase_part_2="ffffffff",
        merkle_branches=("11223344", "aabbccdd"),
        version="20000000",
        network_bits="170fffff",
        network_time="65f04abc",
        clean_jobs=True,
    )


@pytest.mark.parametrize(
    ("index", "value"),
    [
        (0, 1),
        (1, None),
        (2, []),
        (3, 3),
        (4, "not-an-array"),
        (5, 2),
        (6, False),
        (7, None),
        (8, 1),
    ],
)
def test_parse_mining_notify_rejects_invalid_parameter_types(
    index: int,
    value: object,
) -> None:
    message = valid_notify_message()
    params = message["params"]
    assert isinstance(params, list)
    params[index] = value

    with pytest.raises(StratumMessageError):
        parse_mining_notify(message)


def test_parse_mining_notify_rejects_wrong_method_and_parameter_count() -> None:
    with pytest.raises(StratumMessageError, match="method"):
        parse_mining_notify({"method": "mining.set_difficulty", "params": []})

    with pytest.raises(StratumMessageError, match="exactly nine"):
        parse_mining_notify({"method": "mining.notify", "params": []})


def test_parse_mining_notify_rejects_invalid_merkle_branch() -> None:
    message = valid_notify_message()
    params = message["params"]
    assert isinstance(params, list)
    params[4] = ["11223344", 123]

    with pytest.raises(StratumMessageError, match="must be a string"):
        parse_mining_notify(message)


def test_parse_stratum_error() -> None:
    assert parse_stratum_error([20, "Other/Unknown", {"retry": False}]) == StratumError(
        code=20,
        message="Other/Unknown",
        data={"retry": False},
    )
    assert parse_stratum_error(None) is None


@pytest.mark.parametrize(
    "value",
    [
        "error",
        [],
        [20, "message"],
        [True, "message", None],
        [20.0, "message", None],
        [20, 123, None],
    ],
)
def test_parse_stratum_error_rejects_malformed_values(value: object) -> None:
    with pytest.raises(StratumMessageError):
        parse_stratum_error(value)


def test_message_dataclasses_are_immutable() -> None:
    result = SetDifficultyNotification(difficulty=1)

    with pytest.raises(FrozenInstanceError):
        result.difficulty = 2  # type: ignore[misc]
