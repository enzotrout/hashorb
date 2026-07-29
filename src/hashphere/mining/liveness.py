"""Opt-in monotonic Stratum activity and active-job liveness tracking."""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from hashphere.network.stratum.messages import (
    MiningNotifyNotification,
    SetDifficultyNotification,
)

MAX_LIVENESS_SECONDS = 31_536_000.0


class StratumLivenessError(ValueError):
    """Raised when liveness policy or clock data is invalid."""


class StratumStaleReason(StrEnum):
    """Stable, sanitized reasons for declaring one session stale."""

    SERVER_SILENCE = "server_silence"
    JOB_AGE = "job_age"


@dataclass(frozen=True, slots=True)
class StratumLivenessPolicy:
    """Disabled-by-default operator thresholds for one active session."""

    max_server_silence_seconds: float | None = None
    max_job_age_seconds: float | None = None

    def __post_init__(self) -> None:
        _validate_optional_limit(self.max_server_silence_seconds, "max_server_silence_seconds")
        _validate_optional_limit(self.max_job_age_seconds, "max_job_age_seconds")

    @property
    def enabled(self) -> bool:
        """Return whether either explicit operator threshold is active."""

        return self.max_server_silence_seconds is not None or self.max_job_age_seconds is not None


@dataclass(frozen=True, slots=True)
class StratumLivenessViolation:
    """One sanitized threshold crossing sampled from the monotonic clock."""

    reason: StratumStaleReason
    threshold_seconds: float
    elapsed_seconds: float


class StratumLivenessTracker:
    """Track server, job, and completed-work activity as separate clocks."""

    __slots__ = (
        "_clock",
        "_last_job_at",
        "_last_server_at",
        "_last_work_at",
        "_policy",
    )

    def __init__(
        self,
        policy: StratumLivenessPolicy,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not isinstance(policy, StratumLivenessPolicy):
            raise StratumLivenessError("policy must be a StratumLivenessPolicy")
        if not callable(clock):
            raise StratumLivenessError("clock must be callable")
        now = _read_clock(clock)
        self._policy = policy
        self._clock = clock
        self._last_server_at = now
        self._last_job_at = now
        self._last_work_at = now

    def session_replaced(self) -> None:
        """Reset session-specific activity after fresh usable work is installed."""

        now = _read_clock(self._clock)
        self._last_server_at = now
        self._last_job_at = now
        self._last_work_at = now

    def notification_received(
        self,
        notification: SetDifficultyNotification | MiningNotifyNotification,
    ) -> None:
        """Refresh server activity for every supported complete notification."""

        if not isinstance(notification, (SetDifficultyNotification, MiningNotifyNotification)):
            raise StratumLivenessError("notification must be a supported Stratum notification")
        now = _read_clock(self._clock)
        self._last_server_at = now
        if isinstance(notification, MiningNotifyNotification):
            self._last_job_at = now

    def range_completed(self) -> None:
        """Refresh work activity without changing server or job activity."""

        self._last_work_at = _read_clock(self._clock)

    def violation(self) -> StratumLivenessViolation | None:
        """Return the first configured threshold crossed at the sampled instant."""

        if not self._policy.enabled:
            return None
        now = _read_clock(self._clock)
        silence_limit = self._policy.max_server_silence_seconds
        if silence_limit is not None:
            elapsed = max(0.0, now - self._last_server_at)
            if elapsed >= silence_limit:
                return StratumLivenessViolation(
                    StratumStaleReason.SERVER_SILENCE,
                    silence_limit,
                    elapsed,
                )
        job_limit = self._policy.max_job_age_seconds
        if job_limit is not None:
            elapsed = max(0.0, now - self._last_job_at)
            if elapsed >= job_limit:
                return StratumLivenessViolation(
                    StratumStaleReason.JOB_AGE,
                    job_limit,
                    elapsed,
                )
        return None

    @property
    def work_idle_seconds(self) -> float:
        """Return elapsed time since the last completed range or session reset."""

        return max(0.0, _read_clock(self._clock) - self._last_work_at)


def _validate_optional_limit(value: object, name: str) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StratumLivenessError(f"{name} must be a number")
    parsed = float(value)
    if not math.isfinite(parsed) or not 0 < parsed <= MAX_LIVENESS_SECONDS:
        raise StratumLivenessError(
            f"{name} must be finite and between 0 and {int(MAX_LIVENESS_SECONDS)}"
        )


def _read_clock(clock: Callable[[], float]) -> float:
    value = clock()
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StratumLivenessError("clock must return a number")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise StratumLivenessError("clock must return a finite number")
    return parsed
