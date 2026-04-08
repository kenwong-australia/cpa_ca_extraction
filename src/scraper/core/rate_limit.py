"""Detect Cloudflare / origin rate-limit pages (e.g. Error 1015) and stop cleanly."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import Page


class RateLimitedError(RuntimeError):
    """Site returned a rate-limit / block page instead of the portal."""


def raise_if_rate_limited(page: Page) -> None:
    """
    Inspect visible HTML for Cloudflare Error 1015 and similar rate-limit pages.

    Call after navigations and other steps where the edge may replace the app UI.
    """
    if not _page_looks_rate_limited(page):
        return
    raise RateLimitedError(
        "Rate limited or blocked (e.g. Cloudflare Error 1015). "
        "Wait until the site loads normally in a browser, then re-run the scraper "
        "with the same --out to resume from the last checkpoint.",
    )


def _page_looks_rate_limited(page: Page) -> bool:
    parts: list[str] = []
    try:
        parts.append(page.title())
    except Exception:
        pass
    try:
        body = page.locator("body").inner_text(timeout=8_000)
        parts.append(body[:200_000])
    except Exception:
        try:
            parts.append(page.content()[:200_000])
        except Exception:
            return False
    blob = "\n".join(parts).lower()
    if "error 1015" in blob:
        return True
    if "you are being rate limited" in blob:
        return True
    if "banned you temporarily" in blob and "cloudflare" in blob:
        return True
    if "cloudflare" in blob and "rate limit" in blob and "ray id" in blob:
        return True
    return False
