"""Load locality seeds for Phase 3 multi-search runs."""

from __future__ import annotations

import csv
from pathlib import Path


def load_seed_placements(path: Path) -> list[tuple[str, str]]:
    """
    Read a CSV with at least `suburb` and `state` columns (optional `postcode`).

    Returns (location_query, search_seed) per row:
    - location_query: typed into Google Places (suburb + state + Australia).
    - search_seed: provenance for CSV (suburb,state,postcode).
    """
    rows: list[tuple[str, str]] = []
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            return rows
        fn = {h.strip().lower(): h for h in reader.fieldnames if h}

        def col(name: str) -> str | None:
            key = fn.get(name.lower())
            if not key:
                return None
            return key

        k_sub = col("suburb")
        k_state = col("state")
        k_pc = col("postcode")
        if not k_sub or not k_state:
            raise ValueError(
                f"Seed CSV {path} must include 'suburb' and 'state' columns (found: {reader.fieldnames!r})",
            )

        for raw in reader:
            suburb = (raw.get(k_sub) or "").strip()
            state = (raw.get(k_state) or "").strip()
            pc = (raw.get(k_pc) or "").strip() if k_pc else ""
            if not suburb or not state:
                continue
            location_query = f"{suburb} {state}, Australia"
            search_seed = f"{suburb},{state},{pc}" if pc else f"{suburb},{state}"
            rows.append((location_query, search_seed))
    return rows
