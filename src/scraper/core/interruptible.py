"""Chunk long waits so Python can handle SIGINT between Playwright steps."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import Locator, Page


def interruptible_sleep(seconds: float, *, chunk_s: float = 0.25) -> None:
    """Sleep in short slices so Ctrl+C is handled sooner than one long `time.sleep`."""
    deadline = time.monotonic() + seconds
    while True:
        left = deadline - time.monotonic()
        if left <= 0:
            return
        time.sleep(min(chunk_s, left))


def interruptible_page_wait_ms(page: Page, total_ms: int, *, chunk_ms: int = 400) -> None:
    """Replace a single long `page.wait_for_timeout` with shorter pumps."""
    remaining = total_ms
    while remaining > 0:
        step = min(chunk_ms, remaining)
        page.wait_for_timeout(step)
        remaining -= step


def locator_wait_visible_interruptible(
    locator: Locator,
    *,
    total_timeout_ms: int,
    chunk_ms: int = 400,
) -> None:
    """Poll visibility with short timeouts so SIGINT can run between attempts."""
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    deadline = time.monotonic() + total_timeout_ms / 1000.0
    last: BaseException | None = None
    while time.monotonic() < deadline:
        remaining_s = deadline - time.monotonic()
        step = int(min(chunk_ms, max(1.0, remaining_s * 1000)))
        try:
            locator.wait_for(state="visible", timeout=step)
            return
        except PlaywrightTimeoutError as e:
            last = e
            continue
    if last is not None:
        raise last
    raise RuntimeError("locator_wait_visible_interruptible: deadline exceeded")
