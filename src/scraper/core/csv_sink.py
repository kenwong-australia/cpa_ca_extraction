"""Append contact rows to CSV with a fixed header."""

from __future__ import annotations

import csv
from pathlib import Path

from scraper.core.models import CSV_FIELDNAMES, ContactRecord


def read_existing_dedupe_keys(path: Path) -> set[str]:
    """Collect non-empty dedupe_key and dedupe_key_normalised from an existing export (Phase 3 resume)."""
    if not path.exists():
        return set()
    keys: set[str] = set()
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            return keys
        for row in reader:
            for col in ("dedupe_key", "dedupe_key_normalised"):
                v = (row.get(col) or "").strip()
                if v:
                    keys.add(v)
    return keys


def append_contact_row(path: Path, record: ContactRecord) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES, extrasaction="ignore")
        if write_header:
            w.writeheader()
        w.writerow(record.as_csv_dict())
