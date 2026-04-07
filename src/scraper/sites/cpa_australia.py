"""CPA Australia — Find a CPA (Playwright). Phase 1: one location, one listing, one CSV row."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from playwright.sync_api import Page, Response

from scraper.core.csv_sink import append_contact_row
from scraper.core.dedupe import dedupe_key_normalised
from scraper.core.models import ContactRecord, RunContext

FIND_A_CPA_URL = "https://apps.cpaaustralia.com.au/find-a-cpa/"

_SKIP_WEBSITE_SUBSTR = (
    "google.com",
    "maps.google",
    "gstatic.com",
    "cpaaustralia.com.au",
    "microsoft.com",
    "data.microsoft",
    "googletagmanager.com",
    "doubleclick.net",
)


def _parse_portal_json_list(raw: str) -> list[dict[str, Any]] | None:
    """Portal `callaction` bodies may be prefixed with CR/LF noise and JSON-double-encoded."""
    t = raw.strip()
    if not t:
        return None
    if "[" in t:
        sub = t[t.index("[") :]
        try:
            dec: Any = json.loads(sub)
        except json.JSONDecodeError:
            dec = None
    else:
        dec = None
    if dec is None:
        try:
            dec = json.loads(t)
        except json.JSONDecodeError:
            return None
    if isinstance(dec, str):
        try:
            dec = json.loads(dec)
        except json.JSONDecodeError:
            return None
    if isinstance(dec, list) and dec and isinstance(dec[0], dict):
        return dec
    return None


def _parse_callaction_params(url: str) -> dict[str, Any] | None:
    try:
        q = parse_qs(urlparse(url).query)
        raw = (q.get("parameters") or [""])[0]
        if not raw:
            return None
        return json.loads(unquote(raw))
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def _pick(d: dict[str, Any], *keys: str) -> str:
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return str(d[k]).strip()
    lower_map = {str(a).lower(): a for a in d}
    for k in keys:
        lk = k.lower()
        if lk in lower_map:
            v = d[lower_map[lk]]
            if v not in (None, ""):
                return str(v).strip()
    return ""


def _address_from_account(r: dict[str, Any]) -> str:
    line1 = _pick(r, "address1_line1", "Address1_Line1")
    city = _pick(r, "address1_city", "Address1_City")
    state = _pick(r, "address1_stateorprovince", "Address1_StateOrProvince")
    pc = _pick(r, "address1_postalcode", "Address1_PostalCode")
    parts = [p for p in (line1, city, state, pc) if p]
    if parts:
        return ", ".join(parts)
    return _pick(r, "address1_composite", "Address1_Composite")


def _best_account_row(rows: list[dict[str, Any]], list_preview: str) -> dict[str, Any]:
    """Pick the API row that matches the clicked list row (orders can differ)."""
    if not rows:
        return {}
    first = list_preview.splitlines()[0].strip().lower() if list_preview else ""
    norm_line = _ws_collapse(first)
    if not norm_line:
        return rows[0]
    best = rows[0]
    best_score = -1
    for r in rows:
        n = _ws_collapse(_pick(r, "name", "Name").lower())
        if not n:
            continue
        score = sum(1 for w in n.split() if len(w) > 2 and w in norm_line)
        if score > best_score:
            best, best_score = r, score
    return best


def _ws_collapse(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def _fields_from_account_row(r: dict[str, Any]) -> dict[str, str]:
    return {
        "company_name": _pick(r, "name", "Name", "accountname", "AccountName"),
        "address": _address_from_account(r),
        "phone": _pick(r, "telephone1", "Telephone1", "mobilephone", "MobilePhone"),
        "email": _pick(r, "emailaddress1", "EmailAddress1"),
        "website": _pick(r, "websiteurl", "WebSiteURL", "website", "WebSite"),
        "listing_id": _pick(r, "accountid", "AccountId"),
        "accountnumber": _pick(r, "accountnumber", "AccountNumber"),
    }


def _is_user_website(href: str) -> bool:
    h = href.lower()
    if not h.startswith("http"):
        return False
    for s in _SKIP_WEBSITE_SUBSTR:
        if s in h:
            return False
    return True


def _detail_links(page: Page) -> tuple[str, str, str]:
    phone, email, website = "", "", ""
    for link in page.get_by_role("link").all():
        href = (link.get_attribute("href") or "").strip()
        if not href:
            continue
        if href.lower().startswith("tel:"):
            phone = href.split(":", 1)[1].split("?", 1)[0].strip()
        elif href.lower().startswith("mailto:"):
            email = unquote(href.split(":", 1)[1].split("?", 1)[0]).strip()
        elif _is_user_website(href):
            if not website:
                website = href.strip()
    return phone, email, website


def _ensure_country_australia(page: Page) -> None:
    combo = page.get_by_role("combobox", name=re.compile(r"Australia", re.I))
    if not combo.count():
        return
    try:
        combo.select_option(value="au", timeout=5_000)
    except Exception:
        pass


def _set_location_via_places(page: Page, location_query: str) -> None:
    box = page.get_by_role(
        "textbox",
        name=re.compile(r"address|suburb|city|region", re.I),
    )
    box.click()
    box.fill("")
    box.press_sequentially(location_query, delay=55)
    page.wait_for_timeout(2_000)
    first_token = location_query.split()[0] if location_query.strip() else ""
    pac = page.locator(".pac-item").filter(has_text=re.compile(re.escape(first_token), re.I)).first
    if pac.is_visible(timeout=5_000):
        pac.click()
    else:
        pac_fallback = page.locator(".pac-item").first
        if pac_fallback.is_visible(timeout=2_000):
            pac_fallback.click()
        else:
            box.press("ArrowDown")
            page.wait_for_timeout(200)
            box.press("Enter")
    page.wait_for_timeout(800)
    try:
        box.press("Tab")
    except Exception:
        pass
    page.wait_for_timeout(300)


def _capture_findacpa_lists(page: Page) -> list[list[dict[str, Any]]]:
    buckets: list[list[dict[str, Any]]] = []

    def on_response(response: Response) -> None:
        try:
            if response.request.method != "GET":
                return
            if response.status != 200:
                return
            u = response.url
            if "callaction" not in u or "cpa_findacpa" not in u:
                return
            params = _parse_callaction_params(response.request.url) or _parse_callaction_params(
                u,
            )
            if not params or str(params.get("EndpointName", "")).lower() != "findacpa":
                return
            lat_keys = {k.lower() for k in params}
            if "upperlatitude" not in lat_keys:
                return
            text = response.text()
            data = _parse_portal_json_list(text)
            if not data:
                return
            row0 = data[0]
            if not any(str(k).lower() == "accountid" for k in row0):
                return
            buckets.append(data)
        except Exception:
            return

    page.on("response", on_response)
    return buckets


def run_phase1_vertical_slice(
    page: Page,
    out_csv: Path,
    *,
    location_query: str = "Sydney NSW, Australia",
    search_seed: str = "Sydney,NSW,2000",
) -> ContactRecord:
    """
    Search one place, open the first practice row, merge wire/DOM fields, append one CSV row.
    """
    ctx = RunContext.now(
        site_id="cpa_au",
        search_seed=search_seed,
        search_query=location_query,
    )

    buckets = _capture_findacpa_lists(page)
    page.goto(FIND_A_CPA_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(4_000)
    _ensure_country_australia(page)
    _set_location_via_places(page, location_query)

    find_btn = page.get_by_role("button", name=re.compile(r"FIND A CPA", re.I))
    find_btn.wait_for(state="visible", timeout=30_000)
    # Native Playwright click often misses the PCF handler; DOM click reaches it reliably.
    find_btn.evaluate("el => el.click()")

    practice_hint = page.get_by_role("listitem").filter(
        has_text=re.compile(r"\d+\.\d+\s*km"),
    )
    modify_btn = page.get_by_role("button", name=re.compile(r"Modify Search", re.I))
    modify_btn.or_(practice_hint.first).wait_for(state="visible", timeout=90_000)

    if page.get_by_text(re.compile(r"No results found near", re.I)).is_visible():
        raise RuntimeError(
            "Search returned no results (Places selection may have failed in headless). "
            "Retry with --headed or a more specific --location string.",
        )

    selected_label = ""
    try:
        h = page.get_by_role("heading", name=re.compile(re.escape(location_query.split(",")[0]), re.I))
        if h.count():
            selected_label = h.first.inner_text().strip()
    except Exception:
        pass
    if not selected_label:
        try:
            selected_label = page.locator("h3").first.inner_text(timeout=3_000).strip()
        except Exception:
            selected_label = location_query

    ctx.selected_place_label = selected_label

    practice_row = page.get_by_role("listitem").filter(
        has_text=re.compile(r"\d+\.\d+\s*km"),
    ).first
    practice_row.wait_for(state="visible", timeout=60_000)

    list_preview = ""
    try:
        list_preview = practice_row.inner_text().strip()
    except Exception:
        pass

    api_rows = max(buckets, key=len) if buckets else []
    api0 = _best_account_row(api_rows, list_preview) if api_rows else {}
    api_fields = _fields_from_account_row(api0) if api0 else {}

    practice_row.evaluate("el => el.click()")

    page.get_by_role("button", name=re.compile(r"BACK TO RESULT", re.I)).wait_for(
        state="visible",
        timeout=30_000,
    )

    d_phone, d_email, d_web = _detail_links(page)

    company = api_fields.get("company_name") or ""
    address = api_fields.get("address") or ""
    if (not company or not address) and list_preview:
        lines = [ln.strip() for ln in list_preview.splitlines() if ln.strip()]
        if lines:
            if not company:
                company = lines[0]
            if not address and len(lines) > 1:
                body_lines = lines[1:]
                if body_lines and re.match(r"^\d+\.\d+\s*km$", body_lines[-1]):
                    body_lines = body_lines[:-1]
                address = ", ".join(body_lines).strip()

    phone = api_fields.get("phone") or d_phone
    email = api_fields.get("email") or d_email
    website = api_fields.get("website") or d_web

    listing_id = api_fields.get("listing_id") or ""
    anum = api_fields.get("accountnumber") or ""
    raw_token = anum or (list_preview[:200] if list_preview else "")

    dedupe = listing_id or anum or ""
    if not dedupe:
        dedupe = dedupe_key_normalised(company, address)

    norm = dedupe_key_normalised(company, address)

    record = ContactRecord.from_run(
        ctx,
        company_name=company,
        address=address,
        phone=phone,
        email=email,
        website=website,
        selected_place_label=selected_label,
        listing_id=listing_id,
        listing_url="",
        raw_listing_token=raw_token,
        dedupe_key=dedupe,
        dedupe_key_normalised=norm,
    )
    append_contact_row(out_csv, record)
    return record


def run_cpa_au_phase1(
    page: Page,
    out_csv: Path,
    *,
    location_query: str,
    search_seed: str,
) -> ContactRecord:
    return run_phase1_vertical_slice(
        page,
        out_csv,
        location_query=location_query,
        search_seed=search_seed,
    )
