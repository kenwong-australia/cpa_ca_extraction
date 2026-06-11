"""CA ANZ — Find a CA: first batch from GetMembers (navigation); further pages via **Load more**."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote

from playwright.sync_api import Page, Response

from scraper.core.csv_sink import append_contact_row
from scraper.core.dedupe import dedupe_key_normalised
from scraper.core.delays import sleep_random
from scraper.core.interruptible import interruptible_page_wait_ms
from scraper.core.models import ContactRecord, RunContext
from scraper.core.rate_limit import raise_if_rate_limited
from scraper.core.safety import SafetyBrakes

# Must match `new_browser_context` default in `core/browser.py`.
_PLAYWRIGHT_PAGE_DEFAULT_TIMEOUT_MS = 60_000

FIND_CA_LANDING_URL = "https://www.charteredaccountantsanz.com/find-a-ca"

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
    *,
    preferred_name: str = "",
) -> tuple[str, str, str, str]:
    """
    Return (first_name, last_name, display_name, designation).
    Designation usually comes from API; name may be 'First Last (Display)'.
    If there is no parenthetical display name, uses PreferredName when present.
    """
    raw = (name_raw or "").strip()
    desig = (designation or "").strip()
    pref = (preferred_name or "").strip()
    m = _NAME_WITH_DISPLAY_RE.match(raw)
    if m:
        fn = m.group(1).strip()
        ln = m.group(2).strip()
        disp = m.group(3).strip()
    else:
        parts = raw.split()
        if len(parts) >= 2:
            fn, ln, disp = parts[0], parts[-1], ""
        elif raw:
            fn, ln, disp = raw, "", ""
        else:
            fn, ln, disp = "", "", ""
    if not disp and pref:
        disp = pref
    return fn, ln, disp, desig


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


def _parse_getmembers_response(resp: Response) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        first_data = resp.json()
    except Exception as e:
        raise RuntimeError("GetMembers response was not JSON.") from e
    try:
        template = json.loads(resp.request.post_data or "{}")
    except json.JSONDecodeError as e:
        raise RuntimeError("GetMembers request body was not JSON.") from e
    return first_data, template


def _attach_getmembers_logger(page: Page, seen: list[tuple[int, str]]) -> Callable[[Response], None]:
    def _on(resp: Response) -> None:
        try:
            if resp.request.method != "POST" or "GetMembers" not in resp.url:
                return
            seen.append((resp.status, resp.url))
            if resp.status != 200:
                snippet = ""
                try:
                    snippet = resp.text()[:120].replace("\n", " ")
                except Exception:
                    pass
                msg = f"GetMembers HTTP {resp.status} (need 200)"
                if snippet:
                    msg += f": {snippet!r}"
                print(msg, flush=True)
        except Exception:
            pass

    page.on("response", _on)
    return _on


def _wait_for_getmembers(
    page: Page,
    trigger: Callable[[], None],
    *,
    timeout_ms: float = 120_000,
    label: str = "navigation",
) -> Response:
    seen: list[tuple[int, str]] = []
    listener = _attach_getmembers_logger(page, seen)
    try:
        with page.expect_response(_getmembers_response_ok, timeout=timeout_ms) as info:
            trigger()
        return info.value
    except Exception as exc:
        if seen:
            codes = ", ".join(f"HTTP {s}" for s, _ in seen)
            raise RuntimeError(
                f"GetMembers after {label} returned {codes} (need HTTP 200). "
                "Close any survey, wait for the results list to finish loading, then re-run."
            ) from exc
        title = ""
        try:
            title = page.title()
        except Exception:
            pass
        raise RuntimeError(
            f"No GetMembers HTTP 200 within {int(timeout_ms / 1000)}s after {label}. "
            f"Page title: {title!r}. "
            "Common causes: Qualtrics survey still open, reCAPTCHA not passed, infinite spinner, "
            "or rate-limit. Confirm you see 'Showing … results for …' before Resume, then re-run."
        ) from exc
    finally:
        try:
            page.remove_listener("response", listener)
        except Exception:
            pass


def _on_ca_anz_find_ca_page(page: Page) -> bool:
    u = page.url or ""
    return "charteredaccountantsanz.com" in u and "find-a-ca" in u


def _on_ca_anz_landing_form(page: Page) -> bool:
    u = page.url or ""
    return "charteredaccountantsanz.com" in u and u.rstrip("/").endswith("/find-a-ca")


# Site button label is "Search →" (not plain "Search").
_SEARCH_BTN_RE = re.compile(r"search", re.I)
_LOAD_MORE_BTN_RE = re.compile(r"load\s*more", re.I)
_QUALTRICS_SURVEY_RE = re.compile(
    r"still browsing|satisfied with your experience|share your feedback",
    re.I,
)


def _try_click_visible(page: Page, locator, *, timeout_ms: float = 2_000) -> bool:
    try:
        if locator.count() == 0:
            return False
        target = locator.first
        if not target.is_visible(timeout=500):
            return False
        target.click(timeout=timeout_ms)
        interruptible_page_wait_ms(page, 300)
        return True
    except Exception:
        return False


def _dismiss_ca_anz_overlays(page: Page) -> bool:
    """
    Close Qualtrics satisfaction surveys and similar modals that block Search / Load more.

    CA ANZ sometimes shows: "How satisfied are you with your experience…" (Qualtrics).
    """
    closed = False

    qualtrics = page.locator('[class*="QSIWebResponsive"]')
    if qualtrics.count():
        for sel in (
            '[class*="QSIWebResponsive"] [role="button"][aria-label*="close" i]',
            '[class*="QSIWebResponsive"] button[aria-label*="close" i]',
            '[class*="QSIWebResponsive"] [class*="close-button"]',
            '[class*="QSIWebResponsive"] [class*="CloseButton"]',
            '[class*="QSIWebResponsive"] button:has-text("×")',
            '[class*="QSIWebResponsive"] button:has-text("Close")',
        ):
            if _try_click_visible(page, page.locator(sel)):
                closed = True
                break

    if page.get_by_text(_QUALTRICS_SURVEY_RE).count():
        dialog = page.locator('[class*="QSIWebResponsive"], [role="dialog"]').filter(
            has_text=_QUALTRICS_SURVEY_RE,
        )
        if dialog.count():
            for sel in (
                dialog.locator('button[aria-label*="close" i]'),
                dialog.locator("button").filter(has_text=re.compile(r"^×$|^close$", re.I)),
            ):
                if _try_click_visible(page, sel):
                    closed = True
                    break

    for frame in page.frames:
        try:
            frame_url = frame.url or ""
        except Exception:
            continue
        if "qualtrics" not in frame_url.lower():
            continue
        for sel in (
            'button[aria-label*="Close" i]',
            "button.close",
            '[class*="close"]',
        ):
            try:
                loc = frame.locator(sel)
                if _try_click_visible(page, loc):
                    closed = True
            except Exception:
                pass

    try:
        page.keyboard.press("Escape")
    except Exception:
        pass

    if closed:
        print("Dismissed CA ANZ feedback survey overlay.", flush=True)
    return closed


def _prepare_ca_anz_page(page: Page) -> None:
    """Clear blocking overlays before clicks or manual gate."""
    _dismiss_ca_anz_overlays(page)
    raise_if_rate_limited(page)


def _postcode_input_locator(page: Page):
    for sel in (
        'input[name="postcode"]',
        'input[id="postcode"]',
        'input[id*="postcode" i]',
        'input[placeholder*="postcode" i]',
        'input[aria-label*="postcode" i]',
        'input[placeholder*="City, Suburb" i]',
    ):
        loc = page.locator(sel).first
        if loc.count() and loc.is_visible():
            return loc
    skip = page.locator(
        'input[name="firstName"], input[name="lastName"], input[name="business"]',
    )
    candidates = page.locator('input[type="text"]:visible').filter(has_not=skip)
    if candidates.count():
        return candidates.first
    return page.locator('input[type="text"]').first


def _search_button_locator(page: Page):
    for loc in (
        page.get_by_role("button", name=_SEARCH_BTN_RE),
        page.locator("button.btn.btn-result").filter(has_text=_SEARCH_BTN_RE),
        page.locator("button").filter(has_text=_SEARCH_BTN_RE),
    ):
        if loc.count():
            btn = loc.filter(has_not_text=_LOAD_MORE_BTN_RE).first
            if btn.count():
                return btn
    return page.locator("button.btn.btn-result").filter(has_text=_SEARCH_BTN_RE).first


def _fill_postcode_field(page: Page, postcode: str) -> None:
    inp = _postcode_input_locator(page)
    if inp.count() == 0:
        raise RuntimeError("Could not find postcode field on CA ANZ page.")
    inp.scroll_into_view_if_needed()
    inp.click()
    inp.fill("")
    inp.fill((postcode or "").strip())


def _capture_via_results_url(page: Page, url: str) -> tuple[dict[str, Any], dict[str, Any]]:
    resp = _wait_for_getmembers(
        page,
        lambda: page.goto(url, wait_until="domcontentloaded", timeout=120_000),
        label="results URL goto",
    )
    _prepare_ca_anz_page(page)
    return _parse_getmembers_response(resp)


def _capture_postcode_via_landing_reset(
    page: Page,
    url: str,
    postcode: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    Reset SPA state via the landing form URL, then open the results URL.

    Avoids clicking Search after a long session — manual/automated Search clicks can
    trigger ``RangeError: Maximum call stack size exceeded`` in findca-searchV2.min.js.
    """
    print(
        f"Postcode change: landing reset, then results URL for {postcode!r} "
        f"(skipping Search click — site JS can stack-overflow after long runs).",
        flush=True,
    )
    page.goto(FIND_CA_LANDING_URL, wait_until="domcontentloaded", timeout=120_000)
    interruptible_page_wait_ms(page, 1_500)
    _prepare_ca_anz_page(page)
    return _capture_via_results_url(page, url)


def _search_postcode_via_form(page: Page, postcode: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    Submit postcode on the landing form. Prefer Enter over Search click when possible.
    """
    pc = (postcode or "").strip()
    if not pc:
        raise RuntimeError("postcode is empty.")
    raise_if_rate_limited(page)
    if not _on_ca_anz_landing_form(page):
        page.goto(FIND_CA_LANDING_URL, wait_until="domcontentloaded", timeout=120_000)
        interruptible_page_wait_ms(page, 1_000)
        raise_if_rate_limited(page)
    _fill_postcode_field(page, pc)
    search = _search_button_locator(page)
    with page.expect_response(_getmembers_response_ok, timeout=120_000) as info:
        try:
            inp = _postcode_input_locator(page)
            inp.press("Enter")
        except Exception:
            if search.count() == 0:
                raise RuntimeError("Could not find Search button on CA ANZ page.") from None
            search.scroll_into_view_if_needed()
            search.click()
    return _parse_getmembers_response(info.value)


def _capture_first_getmembers(
    page: Page,
    url: str,
    postcode: str,
    *,
    manual_gate: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    Arm response listener and capture the first GetMembers batch.

    Fresh tab: navigate to the results URL (works headed on cold load).
    Already on find-a-ca (multi-postcode run): use the on-page Search button —
    URL-only goto often does not fire GetMembers for the new postcode.
    """
    if manual_gate:
        page.goto(url, wait_until="domcontentloaded", timeout=120_000)
        interruptible_page_wait_ms(page, 1_500)
        _prepare_ca_anz_page(page)
        print(
            "Manual gate: dismiss any survey, wait until results show "
            "('Showing … out of … results for …'), then Resume ▶ in Playwright Inspector. "
            "The page will reload to capture GetMembers.",
            flush=True,
        )
        page.pause()
        interruptible_page_wait_ms(page, 500)
        _prepare_ca_anz_page(page)
        try:
            resp = _wait_for_getmembers(
                page,
                lambda: page.reload(wait_until="domcontentloaded", timeout=120_000),
                label="manual-gate reload",
            )
        except RuntimeError as reload_exc:
            print(
                f"Manual gate reload did not capture GetMembers ({reload_exc}); "
                "trying fresh results URL…",
                flush=True,
            )
            _prepare_ca_anz_page(page)
            resp = _wait_for_getmembers(
                page,
                lambda: page.goto(url, wait_until="domcontentloaded", timeout=120_000),
                label="manual-gate fresh goto",
            )
        _prepare_ca_anz_page(page)
        return _parse_getmembers_response(resp)

    if _on_ca_anz_find_ca_page(page):
        try:
            return _capture_postcode_via_landing_reset(page, url, postcode)
        except Exception as reset_exc:
            print(
                f"Landing reset failed ({reset_exc}); trying form submit.",
                flush=True,
            )
        try:
            return _search_postcode_via_form(page, postcode)
        except Exception as form_exc:
            print(
                f"Form submit failed ({form_exc}); falling back to results URL navigation.",
                flush=True,
            )

    return _capture_via_results_url(page, url)


def _find_load_more_button(page: Page, *, wait_ms: float = 10_000):
    btn = page.get_by_role("button", name=_LOAD_MORE_BTN_RE)
    if btn.count() == 0:
        return None
    try:
        btn.first.wait_for(state="visible", timeout=wait_ms)
        return btn.first
    except Exception:
        return None


def _fetch_next_batch_via_load_more(
    page: Page,
    *,
    timeout_ms: float = 180_000,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Click **Load more**, return (response_json, request_body_dict) from the GetMembers XHR."""
    _prepare_ca_anz_page(page)
    target = _find_load_more_button(page)
    if target is None:
        raise RuntimeError(
            "Load more button not visible (Qualtrics survey, spinner, or end of list). "
            "Dismiss overlays in the browser and re-run."
        )
    with page.expect_response(_getmembers_response_ok, timeout=timeout_ms) as info:
        target.scroll_into_view_if_needed(timeout=10_000)
        target.click(timeout=10_000)
    resp = info.value

    try:
        data = resp.json()
    except Exception as e:
        raise RuntimeError("Load more GetMembers response was not JSON.") from e
    try:
        template = json.loads(resp.request.post_data or "{}")
    except json.JSONDecodeError as e:
        raise RuntimeError("Load more GetMembers request body was not JSON.") from e
    return data, template


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
    jitter_min_s: float = 10.0,
    jitter_max_s: float = 25.0,
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
            postcode,
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
    if not (template.get("token") or "").strip():
        raise RuntimeError(
            "Empty GetMembers token — try --headed, --manual-gate, or complete any checks in the browser.",
        )

    total_count = int(first_data.get("totalCount") or 0)
    if dedupe_seen is not None and out_csv.exists():
        try:
            import csv as _csv

            with out_csv.open(encoding="utf-8", newline="") as f:
                existing_here = sum(
                    1
                    for row in _csv.DictReader(f)
                    if (row.get("search_query") or "").strip() == postcode
                )
            if existing_here:
                pages = (existing_here + 19) // 20
                print(
                    f"CSV already has {existing_here} row(s) for postcode {postcode} "
                    f"(~{pages} pages to skip before new rows can appear).",
                    flush=True,
                )
        except Exception:
            pass

    records_out: list[ContactRecord] = []
    written = 0
    skipped_dupes = 0

    def process_batch(batch: list[dict[str, Any]]) -> None:
        nonlocal written, skipped_dupes
        for row in batch:
            brakes.check_wall_clock()
            if limit is not None and written >= limit:
                return
            fn, ln, disp, desig = parse_member_name_fields(
                str(row.get("Name") or ""),
                str(row.get("Designation") or ""),
                preferred_name=str(row.get("PreferredName") or ""),
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
                skipped_dupes += 1
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
    rows_delivered = len(batch0)

    # Further pages: only **Load more** (replaying token+offset via fetch often yields 403/500).
    load_more_round = 0
    while rows_delivered < total_count and (limit is None or written < limit):
        brakes.check_wall_clock()
        btn = page.get_by_role("button", name=re.compile(r"load\s*more", re.I))
        if btn.count() == 0:
            break
        try:
            if not btn.first.is_visible(timeout=3_000):
                break
        except Exception:
            break
        if load_more_round > 0:
            sleep_random(min_s=jitter_min_s, max_s=jitter_max_s)
        _prepare_ca_anz_page(page)
        data: dict[str, Any] | None = None
        refreshed: dict[str, Any] | None = None
        last_exc: Exception | None = None
        for attempt in range(2):
            try:
                data, refreshed = _fetch_next_batch_via_load_more(page, timeout_ms=180_000)
                last_exc = None
                break
            except Exception as exc:
                last_exc = exc
                if attempt == 0:
                    print(
                        f"Load more attempt {attempt + 1} failed ({exc}); retrying once…",
                        flush=True,
                    )
                    _prepare_ca_anz_page(page)
                    try:
                        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    except Exception:
                        pass
                    interruptible_page_wait_ms(page, 2_000)
                    sleep_random(min_s=jitter_min_s, max_s=jitter_max_s)
        if last_exc is not None:
            print(f"Load more failed or timed out: {last_exc}", flush=True)
            break
        assert data is not None and refreshed is not None
        batch = data.get("searchDetails") or []
        if not batch:
            break
        process_batch(batch)
        rows_delivered += len(batch)
        load_more_round += 1
        if skipped_dupes and written == 0 and rows_delivered % 100 < 20:
            print(
                f"Resume scan: {rows_delivered}/{total_count} on site, "
                f"{skipped_dupes} already in CSV (no new rows yet — keep re-running until Load more passes your existing block).",
                flush=True,
            )
        elif written:
            print(
                f"Progress: {written} new row(s) this run, {rows_delivered}/{total_count} scanned on site.",
                flush=True,
            )
        template.update(refreshed)
        template["postcode"] = postcode

    if written == 0 and skipped_dupes:
        print(
            f"No new rows written ({skipped_dupes} duplicate(s) skipped). "
            f"Scanned {rows_delivered}/{total_count} on site before stop. "
            "Re-run the same command to continue past already-exported contacts.",
            flush=True,
        )

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
    jitter_min_s: float = 10.0,
    jitter_max_s: float = 25.0,
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
