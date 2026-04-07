"""Dedupe helpers (§5.2). Phase 1: normalised name+address fallback only."""

from __future__ import annotations

import re


_ws_re = re.compile(r"\s+")


def normalise_for_dedupe(s: str) -> str:
    t = (s or "").strip().lower()
    t = _ws_re.sub(" ", t)
    return t


def dedupe_key_normalised(company_name: str, address: str) -> str:
    c = normalise_for_dedupe(company_name)
    a = normalise_for_dedupe(address)
    if not c and not a:
        return ""
    return f"{c}|{a}"
