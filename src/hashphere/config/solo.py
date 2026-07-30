"""Environment-only sensitive configuration for Bitcoin Core solo commands."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from dotenv import load_dotenv

BITCOIN_RPC_CHECK_FLAG = "HASHPHERE_ENABLE_BITCOIN_RPC_CHECK"
TRUE_SOLO_FLAG = "HASHPHERE_ENABLE_TRUE_SOLO"
BLOCK_SUBMISSION_FLAG = "HASHPHERE_ENABLE_BLOCK_SUBMISSION"
SOLO_PAYOUT_ADDRESS = "HASHPHERE_SOLO_PAYOUT_ADDRESS"
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True, slots=True)
class SoloCommandSettings:
    """Validated payout destination hidden from object representations."""

    payout_address: str = field(repr=False)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.payout_address, str)
            or not self.payout_address
            or self.payout_address != self.payout_address.strip()
            or len(self.payout_address) > 128
            or _CONTROL_CHARACTERS.search(self.payout_address) is not None
        ):
            raise ValueError("solo payout address is invalid")

    @classmethod
    def from_env(cls) -> SoloCommandSettings:
        """Load the required destination without touching Stratum settings."""

        load_dotenv()
        value = os.getenv(SOLO_PAYOUT_ADDRESS)
        if value is None:
            raise ValueError(f"{SOLO_PAYOUT_ADDRESS} is required")
        return cls(payout_address=value)


def require_exact_opt_in(name: str) -> None:
    """Require the exact literal ``1`` for one named safety boundary."""

    if os.getenv(name) != "1":
        raise ValueError(f"{name}=1 is required")
