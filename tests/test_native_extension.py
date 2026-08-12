"""Direct contract tests for the optional portable native C extension."""

from __future__ import annotations

import pytest

from hashorb.mining import block_hash_to_int, hash_block_header

native_extension = pytest.importorskip("hashorb.compute._native")

_PREFIX = bytes(range(76))
_MAX_TARGET = (2**256 - 1).to_bytes(32, byteorder="little", signed=False)
_MIN_TARGET = (1).to_bytes(32, byteorder="little", signed=False)


def native_search(
    start_nonce: int,
    stop_nonce: int,
    *,
    share_target: bytes = _MIN_TARGET,
    network_target: bytes = _MIN_TARGET,
) -> tuple[object, ...]:
    """Invoke the deliberately narrow extension boundary."""

    result = native_extension.search_nonce_range(
        _PREFIX,
        share_target,
        network_target,
        start_nonce,
        stop_nonce,
    )
    assert isinstance(result, tuple)
    return result


def digest_for_nonce(nonce: int) -> bytes:
    """Return the Python reference digest for one synthetic header."""

    return hash_block_header(_PREFIX + nonce.to_bytes(4, byteorder="little", signed=False))


def best_nonce_for_range(start_nonce: int, stop_nonce: int) -> int:
    """Return the Python-reference lowest numerical hash nonce for one small range."""

    return min(
        range(start_nonce, stop_nonce),
        key=lambda nonce: (block_hash_to_int(digest_for_nonce(nonce)), nonce),
    )


def test_native_extension_exhausts_exact_range_without_mutating_inputs() -> None:
    prefix_before = bytes(_PREFIX)
    share_before = bytes(_MIN_TARGET)

    result = native_search(0, 2)

    assert result == (None, None, False, False, 2, best_nonce_for_range(0, 2))
    assert _PREFIX == prefix_before
    assert _MIN_TARGET == share_before


@pytest.mark.parametrize(
    ("share_target", "network_target", "expected_flags"),
    [
        (_MAX_TARGET, _MIN_TARGET, (True, False)),
        (_MIN_TARGET, _MAX_TARGET, (False, True)),
        (_MAX_TARGET, _MAX_TARGET, (True, True)),
    ],
)
def test_native_extension_returns_raw_python_reference_digest_and_flags(
    share_target: bytes,
    network_target: bytes,
    expected_flags: tuple[bool, bool],
) -> None:
    result = native_search(
        0,
        1,
        share_target=share_target,
        network_target=network_target,
    )

    assert result == (0, digest_for_nonce(0), *expected_flags, 1, 0)


def test_native_extension_stops_at_first_record_low_candidate() -> None:
    target = block_hash_to_int(digest_for_nonce(2)).to_bytes(
        32,
        byteorder="little",
        signed=False,
    )

    assert native_search(0, 8, share_target=target) == (
        2,
        digest_for_nonce(2),
        True,
        False,
        3,
        2,
    )


def test_native_extension_supports_range_ending_at_nonce_limit() -> None:
    final_nonce = 0xFFFFFFFF
    target = block_hash_to_int(digest_for_nonce(final_nonce)).to_bytes(
        32,
        byteorder="little",
        signed=False,
    )

    assert native_search(final_nonce - 1, 2**32, network_target=target) == (
        final_nonce,
        digest_for_nonce(final_nonce),
        False,
        True,
        2,
        final_nonce,
    )


@pytest.mark.parametrize(
    "arguments",
    [
        (bytes(75), _MIN_TARGET, _MIN_TARGET, 0, 1),
        (bytes(77), _MIN_TARGET, _MIN_TARGET, 0, 1),
        (_PREFIX, bytes(31), _MIN_TARGET, 0, 1),
        (_PREFIX, _MIN_TARGET, bytes(33), 0, 1),
        (_PREFIX, bytes(32), _MIN_TARGET, 0, 1),
        (_PREFIX, _MIN_TARGET, bytes(32), 0, 1),
        (_PREFIX, _MIN_TARGET, _MIN_TARGET, True, 1),
        (_PREFIX, _MIN_TARGET, _MIN_TARGET, 0, False),
        (_PREFIX, _MIN_TARGET, _MIN_TARGET, -1, 1),
        (_PREFIX, _MIN_TARGET, _MIN_TARGET, 0, 0),
        (_PREFIX, _MIN_TARGET, _MIN_TARGET, 1, 1),
        (_PREFIX, _MIN_TARGET, _MIN_TARGET, 0, 2**32 + 1),
    ],
)
def test_native_extension_rejects_malformed_boundaries(
    arguments: tuple[object, object, object, object, object],
) -> None:
    with pytest.raises((TypeError, ValueError, OverflowError)):
        native_extension.search_nonce_range(*arguments)


@pytest.mark.parametrize(
    "arguments",
    [
        (bytearray(76), _MIN_TARGET, _MIN_TARGET, 0, 1),
        (_PREFIX, memoryview(_MIN_TARGET), _MIN_TARGET, 0, 1),
        (_PREFIX, _MIN_TARGET, "01", 0, 1),
        (_PREFIX, _MIN_TARGET, _MIN_TARGET, "0", 1),
    ],
)
def test_native_extension_rejects_coercible_input_types(
    arguments: tuple[object, object, object, object, object],
) -> None:
    with pytest.raises(TypeError):
        native_extension.search_nonce_range(*arguments)
