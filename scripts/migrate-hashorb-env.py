#!/usr/bin/env python3
"""Safely rename pre-release project prefixes in one private dotenv file."""

from __future__ import annotations

import argparse
import os
import re
import stat
import sys
import tempfile
from pathlib import Path

_LEGACY_PREFIXES = (b"HASHSPHERE_", b"HASHPHERE_")
_CURRENT_PREFIX = b"HASHORB_"
_KEY_LINE = re.compile(rb"^([A-Za-z_][A-Za-z0-9_]*)=")
_MAX_ENV_BYTES = 1 << 20


class MigrationError(ValueError):
    """The dotenv file cannot be migrated without ambiguity or data loss."""


def _read_private_file(path: Path) -> tuple[bytes, int]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise MigrationError("configuration file could not be opened safely") from exc
    try:
        metadata = os.fstat(descriptor)
        mode = stat.S_IMODE(metadata.st_mode)
        if not stat.S_ISREG(metadata.st_mode):
            raise MigrationError("configuration file must be a regular file")
        if hasattr(os, "geteuid") and metadata.st_uid != os.geteuid():
            raise MigrationError("configuration file must be owned by the current user")
        if mode & 0o077:
            raise MigrationError("configuration file permissions must exclude group and other")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            content = stream.read(_MAX_ENV_BYTES + 1)
        if len(content) > _MAX_ENV_BYTES:
            raise MigrationError("configuration file is too large")
        return content, mode
    finally:
        os.close(descriptor)


def _key(line: bytes) -> bytes | None:
    match = _KEY_LINE.match(line)
    return None if match is None else match.group(1)


def _renamed_key(key: bytes) -> bytes | None:
    for prefix in _LEGACY_PREFIXES:
        if key.startswith(prefix):
            return _CURRENT_PREFIX + key[len(prefix) :]
    return None


def _migration(content: bytes) -> tuple[bytes, tuple[bytes, ...]]:
    existing_current: set[bytes] = set()
    targets: set[bytes] = set()
    legacy_keys: list[bytes] = []
    lines = content.splitlines(keepends=True)
    for line in lines:
        key = _key(line)
        if key is None:
            continue
        if key.startswith(_CURRENT_PREFIX):
            existing_current.add(key)
        target = _renamed_key(key)
        if target is None:
            continue
        if target in targets:
            raise MigrationError("legacy configuration keys would map to the same HashOrb key")
        targets.add(target)
        legacy_keys.append(key)
    if targets & existing_current:
        raise MigrationError("a resulting HashOrb configuration key already exists")

    migrated: list[bytes] = []
    for line in lines:
        key = _key(line)
        target = None if key is None else _renamed_key(key)
        migrated.append(line if target is None else target + line[len(key) :])
    return b"".join(migrated), tuple(legacy_keys)


def _write_all(descriptor: int, content: bytes) -> None:
    offset = 0
    while offset < len(content):
        offset += os.write(descriptor, content[offset:])


def _write_backup(path: Path, content: bytes, mode: int) -> Path:
    backup = path.with_name(f"{path.name}.pre-hashorb")
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(backup, flags, mode)
    except FileExistsError as exc:
        raise MigrationError("configuration backup already exists") from exc
    except OSError as exc:
        raise MigrationError("configuration backup could not be created safely") from exc
    try:
        os.fchmod(descriptor, mode)
        _write_all(descriptor, content)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return backup


def _atomic_replace(path: Path, content: bytes, mode: int) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.hashorb-", dir=path.parent)
    try:
        os.fchmod(descriptor, mode)
        _write_all(descriptor, content)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary_name, path)
        directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass


def migrate(path: Path) -> int:
    """Migrate one private dotenv file without displaying any value."""

    if os.name == "nt":
        print("Environment migration requires a POSIX system.", file=sys.stderr)
        return 1

    content, mode = _read_private_file(path)
    migrated, legacy_keys = _migration(content)
    if not legacy_keys:
        print("No legacy configuration keys found; no file was changed.")
        return 0
    _write_backup(path, content, mode)
    _atomic_replace(path, migrated, mode)
    print(f"Migrated {len(legacy_keys)} legacy configuration key(s); values were not displayed.")
    return 0


def verify(path: Path) -> int:
    """Print only legacy key names that remain in one private dotenv file."""

    if os.name == "nt":
        print("Environment migration requires a POSIX system.", file=sys.stderr)
        return 1

    content, _ = _read_private_file(path)
    _, legacy_keys = _migration(content)
    for key in legacy_keys:
        print(key.decode("ascii"))
    return 1 if legacy_keys else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true", help="print only remaining legacy names")
    parser.add_argument("file", type=Path, help="private dotenv file to inspect or migrate")
    arguments = parser.parse_args(argv)
    try:
        return verify(arguments.file) if arguments.verify else migrate(arguments.file)
    except (MigrationError, OSError):
        print(
            "Environment migration failed without changing configuration values.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
