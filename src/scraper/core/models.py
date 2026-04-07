"""Shared row shape for CSV output (implementation plan §7)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from typing import Any


CSV_FIELDNAMES: list[str] = [
    "company_name",
    "address",
    "phone",
    "email",
    "website",
    "site_id",
    "run_date",
    "run_timestamp_utc",
    "search_seed",
    "search_query",
    "selected_place_label",
    "listing_id",
    "listing_url",
    "raw_listing_token",
    "dedupe_key",
    "dedupe_key_normalised",
]


@dataclass
class RunContext:
    """Metadata attached to every scraped row."""

    site_id: str
    search_seed: str
    search_query: str
    selected_place_label: str = ""

    @classmethod
    def now(cls, *, site_id: str, search_seed: str, search_query: str) -> RunContext:
        return cls(
            site_id=site_id,
            search_seed=search_seed,
            search_query=search_query,
        )


@dataclass
class ContactRecord:
    company_name: str = ""
    address: str = ""
    phone: str = ""
    email: str = ""
    website: str = ""
    site_id: str = ""
    run_date: str = ""
    run_timestamp_utc: str = ""
    search_seed: str = ""
    search_query: str = ""
    selected_place_label: str = ""
    listing_id: str = ""
    listing_url: str = ""
    raw_listing_token: str = ""
    dedupe_key: str = ""
    dedupe_key_normalised: str = ""

    @staticmethod
    def _utc_now() -> tuple[str, str]:
        now = datetime.now(timezone.utc)
        return now.date().isoformat(), now.isoformat()

    @classmethod
    def from_run(
        cls,
        ctx: RunContext,
        *,
        company_name: str = "",
        address: str = "",
        phone: str = "",
        email: str = "",
        website: str = "",
        listing_id: str = "",
        listing_url: str = "",
        raw_listing_token: str = "",
        dedupe_key: str = "",
        dedupe_key_normalised: str = "",
        selected_place_label: str = "",
    ) -> ContactRecord:
        run_date, run_ts = cls._utc_now()
        return cls(
            company_name=company_name,
            address=address,
            phone=phone,
            email=email,
            website=website,
            site_id=ctx.site_id,
            run_date=run_date,
            run_timestamp_utc=run_ts,
            search_seed=ctx.search_seed,
            search_query=ctx.search_query,
            selected_place_label=selected_place_label or ctx.selected_place_label,
            listing_id=listing_id,
            listing_url=listing_url,
            raw_listing_token=raw_listing_token,
            dedupe_key=dedupe_key,
            dedupe_key_normalised=dedupe_key_normalised,
        )

    def as_csv_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return {k: d.get(k, "") for k in CSV_FIELDNAMES}
