"""CPA Australia — Find a CPA (Playwright). Phase 1: one row; Phase 2: full list + §3.1 / §3.2."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from playwright.sync_api import Locator, Page, Response

from scraper.core.csv_sink import append_contact_row
from scraper.core.dedupe import dedupe_key_normalised
from scraper.core.delays import sleep_random
from scraper.core.interruptible import (
    interruptible_page_wait_ms,
    interruptible_sleep,
    locator_wait_visible_interruptible,
)
from scraper.core.rate_limit import RateLimitedError, raise_if_rate_limited
from scraper.core.models import ContactRecord, RunContext
from scraper.core.safety import SafetyBrakes

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


def _record_dedupe_identity(record: ContactRecord) -> str:
    k = (record.dedupe_key or "").strip()
    if k:
        return k
    return (record.dedupe_key_normalised or "").strip()


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
    interruptible_page_wait_ms(page, 2_000)
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
            interruptible_page_wait_ms(page, 200)
            box.press("Enter")
    interruptible_page_wait_ms(page, 800)
    try:
        box.press("Tab")
    except Exception:
        pass
    interruptible_page_wait_ms(page, 300)


def _register_findacpa_capture(page: Page, buckets: list[list[dict[str, Any]]]) -> None:
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


def _practice_items(page: Page) -> Locator:
    return page.get_by_role("listitem").filter(has_text=re.compile(r"\d+\.\d+\s*km"))


def _selected_place_label(page: Page, location_query: str) -> str:
    try:
        h = page.get_by_role("heading", name=re.compile(re.escape(location_query.split(",")[0]), re.I))
        if h.count():
            return h.first.inner_text().strip()
    except Exception:
        pass
    try:
        return page.locator("h3").first.inner_text(timeout=3_000).strip()
    except Exception:
        return location_query


def _run_search_flow(
    page: Page,
    buckets: list[list[dict[str, Any]]],
    location_query: str,
    search_seed: str,
    brakes: SafetyBrakes,
) -> tuple[RunContext, str, list[dict[str, Any]]]:
    """One attempt: load site, search, wait for results. Mutates buckets (cleared by caller)."""
    brakes.check_wall_clock()
    page.goto(FIND_A_CPA_URL, wait_until="domcontentloaded")
    interruptible_page_wait_ms(page, 4_000)
    raise_if_rate_limited(page)
    _ensure_country_australia(page)
    _set_location_via_places(page, location_query)

    find_btn = page.get_by_role("button", name=re.compile(r"FIND A CPA", re.I))
    locator_wait_visible_interruptible(find_btn, total_timeout_ms=30_000)
    find_btn.evaluate("el => el.click()")

    practice_hint = _practice_items(page)
    modify_btn = page.get_by_role("button", name=re.compile(r"Modify Search", re.I))
    locator_wait_visible_interruptible(
        modify_btn.or_(practice_hint.first),
        total_timeout_ms=90_000,
    )
    raise_if_rate_limited(page)

    if page.get_by_text(re.compile(r"No results found near", re.I)).is_visible():
        raise RuntimeError(
            "Search returned no results (Places selection may have failed in headless). "
            "Retry with --headed or a more specific --location string.",
        )

    selected_label = _selected_place_label(page, location_query)
    ctx = RunContext.now(
        site_id="cpa_au",
        search_seed=search_seed,
        search_query=location_query,
    )
    ctx.selected_place_label = selected_label

    api_rows = max(buckets, key=len) if buckets else []
    return ctx, selected_label, api_rows


def _click_back_to_results(page: Page) -> None:
    back = page.get_by_role("button", name=re.compile(r"BACK TO RESULT", re.I))
    locator_wait_visible_interruptible(back, total_timeout_ms=30_000)
    back.evaluate("el => el.click()")
    locator_wait_visible_interruptible(_practice_items(page).first, total_timeout_ms=30_000)
    raise_if_rate_limited(page)


def _append_row_for_listing(
    ctx: RunContext,
    out_csv: Path,
    *,
    list_preview: str,
    api_rows: list[dict[str, Any]],
    d_phone: str,
    d_email: str,
    d_web: str,
    dedupe_seen: set[str] | None = None,
) -> ContactRecord | None:
    api0 = _best_account_row(api_rows, list_preview) if api_rows else {}
    api_fields = _fields_from_account_row(api0) if api0 else {}

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
        selected_place_label=ctx.selected_place_label,
        listing_id=listing_id,
        listing_url="",
        raw_listing_token=raw_token,
        dedupe_key=dedupe,
        dedupe_key_normalised=norm,
    )
    ident = _record_dedupe_identity(record)
    if dedupe_seen is not None and ident and ident in dedupe_seen:
        return None
    append_contact_row(out_csv, record)
    if dedupe_seen is not None and ident:
        dedupe_seen.add(ident)
    return record


def _scrape_open_detail(
    page: Page,
    ctx: RunContext,
    out_csv: Path,
    practice_row: Locator,
    api_rows: list[dict[str, Any]],
    dedupe_seen: set[str] | None = None,
) -> ContactRecord | None:
    locator_wait_visible_interruptible(practice_row, total_timeout_ms=60_000)
    try:
        practice_row.scroll_into_view_if_needed()
    except Exception:
        pass

    list_preview = ""
    try:
        list_preview = practice_row.inner_text().strip()
    except Exception:
        pass

    practice_row.evaluate("el => el.click()")

    locator_wait_visible_interruptible(
        page.get_by_role("button", name=re.compile(r"BACK TO RESULT", re.I)),
        total_timeout_ms=30_000,
    )
    raise_if_rate_limited(page)

    d_phone, d_email, d_web = _detail_links(page)
    return _append_row_for_listing(
        ctx,
        out_csv,
        list_preview=list_preview,
        api_rows=api_rows,
        d_phone=d_phone,
        d_email=d_email,
        d_web=d_web,
        dedupe_seen=dedupe_seen,
    )


def run_cpa_au(
    page: Page,
    out_csv: Path,
    *,
    location_query: str,
    search_seed: str,
    limit: int | None = None,
    brakes: SafetyBrakes | None = None,
    dedupe_seen: set[str] | None = None,
) -> list[ContactRecord]:
    """
    Search one location; scrape one row (limit=1) or every practice row (limit=None).

    Between rows: §3.1 random 3–8 s after returning to the list. Uses §3.2 safety brakes.
    """
    brakes = brakes or SafetyBrakes()
    buckets: list[list[dict[str, Any]]] = []
    _register_findacpa_capture(page, buckets)

    last_err: BaseException | None = None
    ctx: RunContext | None = None
    api_rows: list[dict[str, Any]] = []

    for attempt in range(brakes.max_retries_per_location):
        buckets.clear()
        try:
            ctx, _selected_label, api_rows = _run_search_flow(
                page,
                buckets,
                location_query,
                search_seed,
                brakes,
            )
            break
        except RateLimitedError:
            raise
        except Exception as e:
            last_err = e
            if attempt + 1 >= brakes.max_retries_per_location:
                raise RuntimeError(
                    f"Search failed after {brakes.max_retries_per_location} attempt(s).",
                ) from last_err
            backoff_s = min(120.0, 10.0 * (2**attempt))
            interruptible_sleep(backoff_s)
    else:
        raise RuntimeError("Search failed.") from last_err

    assert ctx is not None
    brakes.on_success()

    items = _practice_items(page)
    locator_wait_visible_interruptible(items.first, total_timeout_ms=30_000)
    n = items.count()
    if n == 0:
        raise RuntimeError("No practice list rows found.")

    total = n if limit is None else min(n, limit)
    records: list[ContactRecord] = []

    for i in range(total):
        brakes.check_wall_clock()
        if i > 0:
            sleep_random()

        raise_if_rate_limited(page)
        items = _practice_items(page)
        row = items.nth(i)
        try:
            rec = _scrape_open_detail(page, ctx, out_csv, row, api_rows, dedupe_seen)
            brakes.on_success()
            if rec is not None:
                records.append(rec)
        except RateLimitedError:
            raise
        except Exception:
            brakes.on_failure()
            try:
                back = page.get_by_role("button", name=re.compile(r"BACK TO RESULT", re.I))
                if back.is_visible():
                    _click_back_to_results(page)
            except Exception:
                pass
            continue

        if i < total - 1:
            _click_back_to_results(page)

    return records


def run_phase1_vertical_slice(
    page: Page,
    out_csv: Path,
    *,
    location_query: str = "Sydney NSW, Australia",
    search_seed: str = "Sydney,NSW,2000",
) -> ContactRecord:
    rows = run_cpa_au(
        page,
        out_csv,
        location_query=location_query,
        search_seed=search_seed,
        limit=1,
    )
    return rows[0]


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


def run_cpa_au_cli(
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
) -> list[ContactRecord]:
    """CLI entry: builds `SafetyBrakes` from flags unless `brakes` is passed (Phase 3 multi-run)."""
    brakes = brakes or SafetyBrakes(
        max_consecutive_failures=max_consecutive_failures,
        max_retries_per_location=max_search_retries,
        wall_clock_budget_s=wall_clock_seconds,
    )
    return run_cpa_au(
        page,
        out_csv,
        location_query=location_query,
        search_seed=search_seed,
        limit=limit,
        brakes=brakes,
        dedupe_seen=dedupe_seen,
    )
