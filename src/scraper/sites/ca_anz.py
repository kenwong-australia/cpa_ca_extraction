"""CA ANZ — Find a CA via POST /api/FindACAV2/GetMembers (Playwright + APIRequestContext)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

from playwright.sync_api import Page, Response

from scraper.core.csv_sink import append_contact_row
from scraper.core.dedupe import dedupe_key_normalised
from scraper.core.delays import sleep_random
from scraper.core.interruptible import interruptible_page_wait_ms
from scraper.core.models import ContactRecord, RunContext
from scraper.core.rate_limit import RateLimitedError, raise_if_rate_limited
from scraper.core.safety import SafetyBrakes

GET_MEMBERS_URL = "https://www.charteredaccountantsanz.com/api/FindACAV2/GetMembers"

_NAME_WITH_DISPLAY_RE = re.compile(
    r"^(.+?)\s+(.+?)\s+\(([^)]*)\)\s*$",
)


def results_page_url(*, postcode: str) -> str:
    pc = quote((postcode or "").strip(), safe="")
    return (
        "https://www.charteredaccountantsanz.com/find-a-ca/search-results-for-a-ca"
        f"?country=Australia&selectedType=All&postcode={pc}"
        "&formType=specifications&limit=20&firstName=&lastName=&business="
)


def parse_member_name_fields(
    name_raw: str,
    designation: str,
) -> tuple[str, str, str, str]:
    """
    Return (first_name, last_name, display_name, designation).
    Designation usually comes from API; name may be 'First Last (Display)'.
    """
    raw = (name_raw or "").strip()
    desig = (designation or "").strip()
    m = _NAME_WITH_DISPLAY_RE.match(raw)
    if m:
        return m.group(1).strip(), m.group(2).strip(), m.group(3).strip(), desig
    parts = raw.split()
    if len(parts) >= 2:
        return parts[0], parts[-1], "", desig
    if raw:
        return raw, "", "", desig
    return "", "", "", desig


def _listing_id_from_row(row: dict[str, Any]) -> str:
    for k in (
        "MemberId",
        "MemberID",
        "memberId",
        "Id",
        "id",
        "ProfileId",
        "profileId",
    ):
        if k in row and row[k] not in (None, ""):
            return str(row[k]).strip()
    return ""


def _getmembers_response_ok(resp: Response) -> bool:
    try:
        return (
            resp.request.method == "POST"
            and "GetMembers" in resp.url
            and resp.status == 200
        )
    except Exception:
        return False


def _capture_first_getmembers(
    page: Page,
    url: str,
    *,
    manual_gate: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Arm response listener, navigate or reload, return (response_json, request_body_dict)."""
    if manual_gate:
        page.goto(url, wait_until="domcontentloaded", timeout=120_000)
        interruptible_page_wait_ms(page, 1_500)
        raise_if_rate_limited(page)
        print(
            "Manual gate: use the browser if needed, then Resume ▶ in the Playwright Inspector. "
            "The page will reload to capture GetMembers.",
            flush=True,
        )
        page.pause()
        interruptible_page_wait_ms(page, 500)
        raise_if_rate_limited(page)
        with page.expect_response(_getmembers_response_ok, timeout=120_000) as info:
            page.reload(wait_until="domcontentloaded", timeout=120_000)
        resp = info.value
    else:
        with page.expect_response(_getmembers_response_ok, timeout=120_000) as info:
            page.goto(url, wait_until="domcontentloaded", timeout=120_000)
        resp = info.value

    try:
        first_data = resp.json()
    except Exception as e:
        raise RuntimeError("GetMembers response was not JSON.") from e
    try:
        template = json.loads(resp.request.post_data or "{}")
    except json.JSONDecodeError as e:
        raise RuntimeError("GetMembers request body was not JSON.") from e
    return first_data, template


def _post_getmembers(
    page: Page,
    payload: dict[str, Any],
    *,
    timeout_ms: float = 120_000,
) -> dict[str, Any]:
    ctx = page.context
    # Playwright API: timeout is in **milliseconds** (see APIRequestContext.post docs).
    r = ctx.request.post(
        GET_MEMBERS_URL,
        data=json.dumps(payload),
        headers={"content-type": "application/json;charset=UTF-8"},
        timeout=timeout_ms,
    )
    if r.status == 429:
        raise RateLimitedError(
            "Rate limited or blocked (HTTP 429 on GetMembers). "
            "Wait and re-run with the same --out to resume from checkpoint.",
        )
    if r.status != 200:
        raise RuntimeError(f"GetMembers failed: HTTP {r.status} {r.status_text!r}")
    try:
        return r.json()
    except Exception as e:
        raise RuntimeError("GetMembers body was not JSON.") from e


def run_ca_anz(
    page: Page,
    out_csv: Path,
    *,
    location_query: str,
    search_seed: str,
    limit: int | None = None,
    manual_gate: bool = False,
    brakes: SafetyBrakes | None = None,
    dedupe_seen: set[str] | None = None,
    jitter_min_s: float = 5.0,
    jitter_max_s: float = 15.0,
) -> list[ContactRecord]:
    brakes = brakes or SafetyBrakes()
    postcode = (location_query or "").strip()
    if not postcode:
        raise RuntimeError("location_query (postcode) is empty.")

    url = results_page_url(postcode=postcode)
    ctx = RunContext.now(
        site_id="ca_anz",
        search_seed=search_seed,
        search_query=postcode,
    )

    try:
        first_data, template = _capture_first_getmembers(
            page,
            url,
            manual_gate=manual_gate,
        )
    except Exception as e:
        raise RuntimeError(
            "Did not capture GetMembers — use --headed (required for CA ANZ) and optionally "
            "--manual-gate for a warm-up pause.",
        ) from e

    interruptible_page_wait_ms(page, 300)
    raise_if_rate_limited(page)

    template = dict(template)
    template["postcode"] = postcode
    template["country"] = template.get("country") or "Australia"
    token = (template.get("token") or "").strip()
    if not token:
        raise RuntimeError(
            "Empty GetMembers token — try --headed, --manual-gate, or complete any checks in the browser.",
        )

    total_count = int(first_data.get("totalCount") or 0)
    records_out: list[ContactRecord] = []
    written = 0

    def process_batch(batch: list[dict[str, Any]]) -> None:
        nonlocal written
        for row in batch:
            brakes.check_wall_clock()
            if limit is not None and written >= limit:
                return
            fn, ln, disp, desig = parse_member_name_fields(
                str(row.get("Name") or ""),
                str(row.get("Designation") or ""),
            )
            company = str(row.get("Company") or "").strip()
            address = str(row.get("BusinessAddress") or "").strip()
            phone = str(row.get("Phone") or "").strip()
            email = str(row.get("Email") or "").strip()
            website = str(row.get("CompanyWebsite") or "").strip()
            lid = _listing_id_from_row(row)
            raw_tok = json.dumps(row, sort_keys=True)[:500] if row else ""
            dedupe = (email.strip().lower() if email else "") or lid
            if not dedupe:
                dedupe = dedupe_key_normalised(company, address) + "|" + f"{fn}|{ln}".lower()
            norm = dedupe_key_normalised(company, address)
            rec = ContactRecord.from_run(
                ctx,
                company_name=company,
                address=address,
                phone=phone,
                email=email,
                website=website,
                first_name=fn,
                last_name=ln,
                display_name=disp,
                designation=desig,
                selected_place_label=f"{total_count} total",
                listing_id=lid,
                listing_url="",
                raw_listing_token=raw_tok,
                dedupe_key=dedupe,
                dedupe_key_normalised=norm,
            )
            ident = (rec.dedupe_key or rec.dedupe_key_normalised or "").strip()
            if dedupe_seen is not None and ident and ident in dedupe_seen:
                continue
            append_contact_row(out_csv, rec)
            if dedupe_seen is not None and ident:
                dedupe_seen.add(ident)
            records_out.append(rec)
            brakes.on_success()
            written += 1
            if limit is not None and written >= limit:
                return

    batch0 = first_data.get("searchDetails") or []
    if not isinstance(batch0, list):
        batch0 = []
    process_batch(batch0)
    next_offset = int(template.get("offset") or 0) + len(batch0)

    api_calls = 0
    while next_offset < total_count and (limit is None or written < limit):
        brakes.check_wall_clock()
        if api_calls > 0:
            sleep_random(min_s=jitter_min_s, max_s=jitter_max_s)
        payload = {**template, "offset": next_offset, "postcode": postcode, "token": token}
        data = _post_getmembers(page, payload)
        batch = data.get("searchDetails") or []
        if not batch:
            break
        process_batch(batch)
        next_offset += len(batch)
        api_calls += 1

    return records_out


def run_ca_anz_cli(
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
    jitter_min_s: float = 5.0,
    jitter_max_s: float = 15.0,
    manual_gate: bool = False,
) -> list[ContactRecord]:
    brakes = brakes or SafetyBrakes(
        max_consecutive_failures=max_consecutive_failures,
        max_retries_per_location=max_search_retries,
        wall_clock_budget_s=wall_clock_seconds,
    )
    return run_ca_anz(
        page,
        out_csv,
        location_query=location_query,
        search_seed=search_seed,
        limit=limit,
        manual_gate=manual_gate,
        brakes=brakes,
        dedupe_seen=dedupe_seen,
        jitter_min_s=jitter_min_s,
        jitter_max_s=jitter_max_s,
    )
