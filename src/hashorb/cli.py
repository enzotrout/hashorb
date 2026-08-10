"""Installed HashOrb command wrapper with the read-only dashboard command."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from decimal import Decimal, InvalidOperation

from hashorb.__main__ import main as legacy_main
from hashorb.dashboard import DashboardLogError, run_dashboard

_DASHBOARD_USAGE = "Usage: hashorb dashboard --log-file PATH [--refresh-seconds SECONDS] [--once]"


def main(argv: Sequence[str] | None = None) -> int:
    """Run dashboard locally or delegate every existing command unchanged."""

    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == "dashboard":
        return _dashboard_main(arguments[1:])
    status = legacy_main(arguments)
    if arguments in (["--help"], ["-h"]):
        print(_DASHBOARD_USAGE)
    return status


def _dashboard_main(arguments: Sequence[str]) -> int:
    if arguments in (["--help"], ["-h"]):
        print(_DASHBOARD_USAGE)
        print("Read-only live view of an existing sanitized HashOrb JSONL mining log.")
        print("Ctrl-C exits live mode; --once renders one deterministic snapshot.")
        return 0
    try:
        log_file, refresh_seconds, once = _parse_dashboard_arguments(arguments)
    except ValueError as exc:
        print(f"Argument error: {exc}", file=sys.stderr)
        print(_DASHBOARD_USAGE, file=sys.stderr)
        return 2
    try:
        return run_dashboard(log_file, refresh_seconds=refresh_seconds, once=once)
    except DashboardLogError as exc:
        print(f"Dashboard failed: {exc}", file=sys.stderr)
        return 1


def _parse_dashboard_arguments(arguments: Sequence[str]) -> tuple[str, float, bool]:
    log_file: str | None = None
    refresh_seconds = 1.0
    refresh_seen = False
    once = False
    index = 0
    while index < len(arguments):
        option = arguments[index]
        if option == "--once":
            if once:
                raise ValueError("--once may be supplied only once")
            once = True
            index += 1
            continue
        if option not in {"--log-file", "--refresh-seconds"}:
            raise ValueError("unsupported dashboard argument")
        if index + 1 >= len(arguments) or arguments[index + 1].startswith("--"):
            raise ValueError(f"{option} requires a value")
        value = arguments[index + 1]
        if option == "--log-file":
            if log_file is not None:
                raise ValueError("--log-file may be supplied only once")
            if not value.strip():
                raise ValueError("--log-file requires a nonblank path")
            log_file = value
        else:
            if refresh_seen:
                raise ValueError("--refresh-seconds may be supplied only once")
            refresh_seconds = _parse_refresh_seconds(value)
            refresh_seen = True
        index += 2
    if log_file is None:
        raise ValueError("--log-file is required")
    return log_file, refresh_seconds, once


def _parse_refresh_seconds(value: str) -> float:
    if not value or value != value.strip():
        raise ValueError("--refresh-seconds must be between 0.1 and 60")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("--refresh-seconds must be between 0.1 and 60") from exc
    if not parsed.is_finite() or not Decimal("0.1") <= parsed <= Decimal("60"):
        raise ValueError("--refresh-seconds must be between 0.1 and 60")
    converted = float(parsed)
    if not 0.1 <= converted <= 60.0:
        raise ValueError("--refresh-seconds must be between 0.1 and 60")
    return converted
