"""Polite random delays (§3.1) — used from Phase 2 onward."""

from __future__ import annotations

import random

from scraper.core.interruptible import interruptible_sleep


def sleep_random(*, min_s: float = 3.0, max_s: float = 8.0) -> None:
    interruptible_sleep(random.uniform(min_s, max_s))
