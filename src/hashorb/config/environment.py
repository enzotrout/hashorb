"""Project environment loading with pre-release legacy-name rejection."""

from __future__ import annotations

import os

from dotenv import load_dotenv

_LEGACY_ENVIRONMENT_PREFIXES = ("HASHSPHERE_", "HASHPHERE_")
_LEGACY_ENVIRONMENT_ERROR = "Legacy project configuration detected; rename keys to HASHORB_."


def load_hashorb_environment() -> None:
    """Load ``.env`` and reject old project prefixes without reflecting input."""

    load_dotenv()
    if any(name.startswith(_LEGACY_ENVIRONMENT_PREFIXES) for name in os.environ):
        raise ValueError(_LEGACY_ENVIRONMENT_ERROR)
