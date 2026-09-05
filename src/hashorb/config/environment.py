"""Project environment loading with pre-release legacy-name rejection."""

from __future__ import annotations

import os

from dotenv import find_dotenv, load_dotenv

_LEGACY_ENVIRONMENT_PREFIXES = ("HASHSPHERE_", "HASHPHERE_")
_LEGACY_ENVIRONMENT_ERROR = "Legacy project configuration detected; rename keys to HASHORB_."


def load_hashorb_environment() -> None:
    """Load the nearest ``.env`` from the working directory and reject old prefixes."""

    dotenv_path = find_dotenv(usecwd=True)
    if dotenv_path:
        load_dotenv(dotenv_path=dotenv_path)

    if any(name.startswith(_LEGACY_ENVIRONMENT_PREFIXES) for name in os.environ):
        raise ValueError(_LEGACY_ENVIRONMENT_ERROR)
