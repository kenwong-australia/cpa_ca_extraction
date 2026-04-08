"""Map CLI --site values to run functions."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from playwright.sync_api import Page

from scraper.core.models import ContactRecord
from scraper.core.safety import SafetyBrakes
from scraper.sites.cpa_australia import run_cpa_au_cli


class SiteRunner(Protocol):
    def __call__(
        self,
        page: Page,
        out_csv: Path,
        *,
        location_query: str,
        search_seed: str,
        limit: int | None = None,
        max_consecutive_failures: int = 10,
        max_search_retries: int = 3,
        wall_clock_seconds: float | None = None,
        dedupe_seen: set[str] | None = None,
        brakes: SafetyBrakes | None = None,
        jitter_min_s: float = 3.0,
        jitter_max_s: float = 8.0,
    ) -> list[ContactRecord]: ...


SITE_REGISTRY: dict[str, SiteRunner] = {
    "cpa_au": run_cpa_au_cli,
}
