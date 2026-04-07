"""Map CLI --site values to run functions."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Protocol

from playwright.sync_api import Page

from scraper.core.models import ContactRecord
from scraper.sites.cpa_australia import run_cpa_au_phase1


class SiteRunner(Protocol):
    def __call__(
        self,
        page: Page,
        out_csv: Path,
        *,
        location_query: str,
        search_seed: str,
    ) -> ContactRecord: ...


SITE_REGISTRY: dict[str, SiteRunner] = {
    "cpa_au": run_cpa_au_phase1,
}
