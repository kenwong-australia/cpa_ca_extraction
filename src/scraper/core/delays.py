"""Polite random delays (§3.1) — used from Phase 2 onward."""

from __future__ import annotations

import random
import time


def sleep_random(*, min_s: float = 5.0, max_s: float = 15.0) -> None:
    time.sleep(random.uniform(min_s, max_s))
