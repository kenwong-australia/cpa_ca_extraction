"""Append contact rows to CSV with a fixed header."""

from __future__ import annotations

import csv
from pathlib import Path

from scraper.core.models import CSV_FIELDNAMES, ContactRecord


def append_contact_row(path: Path, record: ContactRecord) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES, extrasaction="ignore")
        if write_header:
            w.writeheader()
        w.writerow(record.as_csv_dict())
