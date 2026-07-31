#!/usr/bin/env python3
"""Inspect CPU distribution archives for metadata and local-data leaks."""

from __future__ import annotations

import argparse
import re
import sys
import tarfile
import zipfile
from collections.abc import Iterable, Iterator
from pathlib import Path, PurePosixPath

_FORBIDDEN_PARTS = frozenset({".git", ".venv", "credentials", "logs", "secrets"})
_CUDA_BINARY = re.compile(
    rb"(?:^|/)hashphere/compute/_cuda[^/]*\.(?:dll|dylib|pyd|so)$",
    re.IGNORECASE,
)
_LOCAL_DATA_MARKERS = (
    b"/" + b"home" + b"/",
    b"\\" + b"Users" + b"\\",
    b"C:/" + b"Users" + b"/",
)


class DistributionVerificationError(ValueError):
    """A built archive violates the CPU distribution contract."""


def _members(path: Path) -> Iterator[tuple[str, bytes]]:
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            for name in archive.namelist():
                if not name.endswith("/"):
                    yield name, archive.read(name)
        return
    if path.name.endswith(".tar.gz"):
        with tarfile.open(path, mode="r:gz") as archive:
            for member in archive.getmembers():
                if member.isfile():
                    stream = archive.extractfile(member)
                    if stream is not None:
                        yield member.name, stream.read()
        return
    raise DistributionVerificationError(f"unsupported distribution type: {path.name}")


def verify_distribution(path: Path) -> None:
    """Verify one wheel or sdist without extracting or executing it."""

    members = list(_members(path))
    if not members:
        raise DistributionVerificationError(f"empty distribution: {path.name}")
    names = [name for name, _ in members]
    _verify_member_names(names)
    _verify_bytes(members)
    if path.suffix == ".whl":
        _verify_wheel_metadata(members)
    else:
        _verify_sdist_contents(names)


def _verify_member_names(names: Iterable[str]) -> None:
    for name in names:
        normalized = PurePosixPath(name.replace("\\", "/"))
        if normalized.is_absolute() or ".." in normalized.parts:
            raise DistributionVerificationError("archive contains an unsafe path")
        if any(part in _FORBIDDEN_PARTS for part in normalized.parts):
            raise DistributionVerificationError("archive contains local runtime data")
        if normalized.name == ".env":
            raise DistributionVerificationError("archive contains a private environment file")
        if _CUDA_BINARY.search(name.encode("utf-8", errors="ignore")):
            raise DistributionVerificationError("CPU archive contains a CUDA binary")


def _verify_bytes(members: Iterable[tuple[str, bytes]]) -> None:
    for _, content in members:
        if any(marker in content for marker in _LOCAL_DATA_MARKERS):
            raise DistributionVerificationError("archive contains a local personal path")


def _verify_wheel_metadata(members: Iterable[tuple[str, bytes]]) -> None:
    content_by_name = dict(members)
    metadata_names = [name for name in content_by_name if name.endswith(".dist-info/METADATA")]
    entry_names = [name for name in content_by_name if name.endswith(".dist-info/entry_points.txt")]
    if len(metadata_names) != 1 or len(entry_names) != 1:
        raise DistributionVerificationError("wheel metadata files are missing or ambiguous")
    metadata_text = content_by_name[metadata_names[0]].decode("utf-8")
    entry_text = content_by_name[entry_names[0]].decode("utf-8")
    required = (
        "Name: hashphere\n",
        "Version: 0.1.0\n",
        "Requires-Python: <3.14,>=3.13\n",
    )
    if not all(item in metadata_text for item in required):
        raise DistributionVerificationError("wheel release metadata does not match the contract")
    if "hashsphere = hashphere.__main__:main" not in entry_text:
        raise DistributionVerificationError("wheel console entry point is missing")


def _verify_sdist_contents(names: Iterable[str]) -> None:
    basenames = {PurePosixPath(name).name for name in names}
    required = {
        ".env.example",
        "15-security-audit.md",
        "MANIFEST.in",
        "README.md",
        "SECURITY.md",
        "pyproject.toml",
        "setup.py",
    }
    if not required <= basenames:
        raise DistributionVerificationError("sdist is missing required source metadata")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("distributions", nargs="+", type=Path)
    arguments = parser.parse_args(argv)
    distributions: list[Path] = []
    for candidate in arguments.distributions:
        if candidate.is_dir():
            distributions.extend(sorted(candidate.glob("*.whl")))
            distributions.extend(sorted(candidate.glob("*.tar.gz")))
        else:
            distributions.append(candidate)
    try:
        if not distributions:
            raise DistributionVerificationError("no distributions found")
        for distribution in distributions:
            verify_distribution(distribution)
            print(f"verified {distribution.name}")
    except (DistributionVerificationError, OSError, tarfile.TarError, zipfile.BadZipFile) as exc:
        print(f"distribution verification failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
