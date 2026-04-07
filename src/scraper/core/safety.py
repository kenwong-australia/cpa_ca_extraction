"""Circuit breakers (§3.2) for Phase 2+."""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class SafetyBrakes:
    max_consecutive_failures: int = 10
    max_retries_per_location: int = 3
    max_locations: int | None = None
    wall_clock_budget_s: float | None = None

    consecutive_failures: int = field(default=0, init=False)
    _started_monotonic: float = field(default_factory=time.monotonic, init=False)

    def check_wall_clock(self) -> None:
        if self.wall_clock_budget_s is None:
            return
        if time.monotonic() - self._started_monotonic > self.wall_clock_budget_s:
            msg = f"Wall clock budget ({self.wall_clock_budget_s}s) exceeded"
            raise RuntimeError(msg)

    def on_success(self) -> None:
        self.consecutive_failures = 0

    def on_failure(self) -> None:
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.max_consecutive_failures:
            msg = (
                f"Aborted after {self.max_consecutive_failures} consecutive failures "
                "(timeouts, missing UI, or scrape errors)."
            )
            raise RuntimeError(msg)
