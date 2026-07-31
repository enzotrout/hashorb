"""Strict Bitcoin compact-size and bounded byte parsing helpers."""

from __future__ import annotations


class BitcoinSerializationError(ValueError):
    """Raised when serialized Bitcoin data is malformed or noncanonical."""


def encode_compact_size(value: int) -> bytes:
    """Encode one unsigned integer using Bitcoin's canonical compact-size form."""

    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not 0 <= value <= 0xFFFFFFFFFFFFFFFF
    ):
        raise BitcoinSerializationError("compact-size value must be an unsigned 64-bit integer")
    if value < 0xFD:
        return bytes((value,))
    if value <= 0xFFFF:
        return b"\xfd" + value.to_bytes(2, "little")
    if value <= 0xFFFFFFFF:
        return b"\xfe" + value.to_bytes(4, "little")
    return b"\xff" + value.to_bytes(8, "little")


class ByteReader:
    """Forward-only exact reader for one bounded serialized object."""

    __slots__ = ("_data", "_offset")

    def __init__(self, data: bytes) -> None:
        if not isinstance(data, bytes):
            raise BitcoinSerializationError("serialized data must be bytes")
        self._data = data
        self._offset = 0

    @property
    def offset(self) -> int:
        """Return the next unread byte offset."""

        return self._offset

    @property
    def remaining(self) -> int:
        """Return the exact unread byte count."""

        return len(self._data) - self._offset

    def read(self, length: int) -> bytes:
        """Read exactly ``length`` bytes or reject truncation."""

        if not isinstance(length, int) or isinstance(length, bool) or length < 0:
            raise BitcoinSerializationError("read length must be a nonnegative integer")
        stop = self._offset + length
        if stop > len(self._data):
            raise BitcoinSerializationError("serialized data is truncated")
        result = self._data[self._offset : stop]
        self._offset = stop
        return result

    def read_compact_size(self, *, maximum: int) -> int:
        """Read a canonical compact-size integer not exceeding ``maximum``."""

        if not isinstance(maximum, int) or isinstance(maximum, bool) or maximum < 0:
            raise BitcoinSerializationError("compact-size maximum must be nonnegative")
        prefix = self.read(1)[0]
        if prefix < 0xFD:
            value = prefix
        elif prefix == 0xFD:
            value = int.from_bytes(self.read(2), "little")
            if value < 0xFD:
                raise BitcoinSerializationError("compact-size integer is noncanonical")
        elif prefix == 0xFE:
            value = int.from_bytes(self.read(4), "little")
            if value <= 0xFFFF:
                raise BitcoinSerializationError("compact-size integer is noncanonical")
        else:
            value = int.from_bytes(self.read(8), "little")
            if value <= 0xFFFFFFFF:
                raise BitcoinSerializationError("compact-size integer is noncanonical")
        if value > maximum:
            raise BitcoinSerializationError("compact-size integer exceeds its limit")
        return value

    def require_end(self) -> None:
        """Reject trailing bytes."""

        if self.remaining != 0:
            raise BitcoinSerializationError("serialized data has trailing bytes")
