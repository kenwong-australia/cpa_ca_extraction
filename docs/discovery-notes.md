# Phase 0 — Discovery notes (Find a CPA)

**Date:** 2026-04-07  
**URL:** https://apps.cpaaustralia.com.au/find-a-cpa/  
**Method:** Static HTML fetch, response headers, automated browser load + Network capture (Cursor browser MCP), light `/_api/` probe.

---

## 1. Verdict (for implementation plan §3.0)

**Classification: *API fragile* — ship *browser-primary* (Playwright) for Phase 1–2.**

| Criterion | Finding |
|-----------|---------|
| Callable JSON for **search results / detail** | **Search list (§7.2):** same `GET /callaction?actionName=cpa_findacpa` with `EndpointName: "findacpa"` + bbox → JSON **array** of **account** rows (`accountid`, `accountnumber`, …). **Detail (§7.3):** opening a row triggers **more** `callaction` requests with other `EndpointName` values (e.g. `practicelanguages` + `RecordId`; may return `[]`) and possibly bbox-style calls (e.g. `financialplanners`). **Core contact fields** likely already on the list row; these are **enrichments**. **Caveats:** undocumented portal action, session/Cloudflare, `Content-Type` may be `text/html` for JSON bodies. |
| **Autocomplete** | **Google Maps JavaScript API** with **`places`** library (third-party). Predictions go to Google, not a simple CPA-owned JSON field. Replacing the widget with raw HTTP to Google would be a **separate** integration (API keys, Places ToS, billing) — **out of scope** for v1. |
| **Practical v1 path** | **Playwright:** country combobox → type seed → select Places suggestion (keyboard or click) → **FIND A CPA** → iterate list → open detail. |

**Revisit Phase 5 (HTTP optimisation)** only after §7 documents repeatable portal `/_api/` or `/_services/` calls with clear payloads and stable auth (often **anonymous portal session cookies**).

---

## 2. Platform stack (affects failure modes and selectors)

- **Microsoft Power Pages / Dynamics 365 Portal** (`CDSStarterPortal` in page bootstrapping).
- **Power Apps Component Framework (PCF)** bundles loaded from `content.powerapps.com/resource/powerappsportal/` (`pcf-loader`, `pcf.bundle`, control manifests, chunked hosts).
- **Cloudflare** in front (`cf-ray`, `cdn-cgi/challenge-platform` scripts observed). Expect occasional **bot challenges**; pure `curl`/headless datacenter IPs may behave differently from a normal desktop browser.
- **Session cookies:** `ARRAffinity*`, `Dynamics365PortalAnalytics`, `__cf_bm` (short-lived) — typical for this stack.

**Implication:** Prefer **real browser automation** with the same navigation a user would use; keep **screenshots + consecutive-failure abort** (plan §3.2) when the challenge or layout changes.

---

## 3. Network observations (initial page load)

**High-signal URLs (non-exhaustive):**

| URL / pattern | Role |
|---------------|------|
| `/_portal/1ea1ab5d-2957-ec11-8f8f-000d3ad11c4b/Resources/ResourceManager?lang=en-US` | Portal resource / string manager (**portal id** `1ea1ab5d-2957-ec11-8f8f-000d3ad11c4b` appears in paths). |
| `https://maps.googleapis.com/maps/api/js?...&libraries=geometry,places` | **Google Maps + Places** (autocomplete / map). |
| `https://content.powerapps.com/resource/powerappsportal/controls/...` | PCF control host chunks and manifests (`manifest-*.json`). |
| `https://us-mobile.events.data.microsoft.com/OneCollector/...` | Microsoft telemetry (not used for scraping). |
| `https://apps.cpaaustralia.com.au/cdn-cgi/challenge-platform/...` | Cloudflare challenge / instrumentation. |

**Google API key:** A **browser-restricted** Maps key is present in the script URL on the live page. **Do not** copy it into this repo or reuse it from server-side code — it is subject to **Google’s terms** and key restrictions. For automation, drive the **existing** widget via Playwright.

**Probe:** `GET https://apps.cpaaustralia.com.au/_api/` returned **406** without a full OData `Accept` header — consistent with a portal that expects proper API negotiation, not a public open JSON root.

---

## 4. DOM / accessibility (landing state)

From an accessibility snapshot of the search screen:

| Role | Name / notes | Playwright hint |
|------|----------------|-----------------|
| `combobox` | **Australia** (options include New Zealand) | Select **Australia** once per session / confirm default. |
| `textbox` | **Enter address, suburb, city or region** | Fill with seed; trigger **Places** suggestions. |
| `button` | **FIND A CPA** | Submit after a resolved place. |

**Gap:** Google Places suggestion list **may not expose options** in the accessibility tree (custom overlay). Automation should plan for **ArrowDown / Enter** after typing, or **click** coordinates / `.pac-item` CSS (confirm in headed Playwright during Phase 1).

---

## 5. Identity / dedupe (Phase 0 status)

**On the wire (§7.2):** each search result object includes **`accountid`** (GUID) and **`accountnumber`** — use these as **primary `dedupe_key`** when the scraper can read the same values (e.g. from DOM/`data-*`, or from a controlled network listen — Phase 1 chooses how).

**Still to confirm in Phase 1 (DOM):** whether list/detail nodes expose those ids without parsing raw XHR; whether the address bar or hash changes when opening a practice.

Keep **normalised name+address** as **fallback** dedupe only (per implementation plan §5.2).

---

## 6. Follow-up (you or Phase 1 dev — ~15 minutes)

Full click-by-click steps: **§7.1**. Summary checklist — **Chrome DevTools → Network → Fetch/XHR** (with “Preserve log”):

1. Type a suburb (e.g. `Sydney`) → select **Sydney NSW, Australia** → **FIND A CPA**.
2. Note every request to **`apps.cpaaustralia.com.au`** (especially `/_api/`, `/_services/`, `/_portal/`) and status codes.
3. Open **one** practice detail (chevron/row).
4. Export or paste **one example** request URL + **redacted** response shape (table/entity name, record id fields).

Record redacted samples under **§7** below.

---

## 7. Captured XHR (manual)

Paste **redacted** examples here after you capture them (no live cookies, tokens, API keys, or PII).

### 7.1 How to capture (Chrome, step by step)

1. **Open the site** in Chrome: [Find a CPA](https://apps.cpaaustralia.com.au/find-a-cpa/).
2. **Open DevTools:** `View` → `Developer` → `Developer Tools`, or `⌥⌘I` (Mac) / `F12` (Windows).
3. **Network panel:** click the **Network** tab.
4. **Filter to XHR/fetch:** click **Fetch/XHR** (so you only see API-style requests, not every image or script).
5. **Preserve log:** check **Preserve log** so the list is not cleared on full navigations or soft reloads.
6. **Optional — clear first:** click **Clear** (circle-with-slash) so you only see this run.
7. **Run the search flow on the page:**
   - Choose country if needed (e.g. **Australia**).
   - In the address field, type a suburb (e.g. `Sydney`) and **select a full Places suggestion** (e.g. Sydney NSW, Australia).
   - Click **FIND A CPA** and wait until results appear.
8. **Note `apps.cpaaustralia.com.au` calls:** In the Network table, focus rows whose **Name** or **Request URL** is under `apps.cpaaustralia.com.au`. For **Find a CPA** search results, look for **`callaction?actionName=cpa_findacpa`**. Also watch **`/_api/`**, **`/_services/`**, **`/_portal/`** if present. For each interesting row, note **Method**, **Status**, and open **Headers** → **Request URL** (and **Query String Parameters** if any).
9. **Inspect payloads:** Select a request → **Headers** (request headers — redact `Cookie`, `Authorization`, etc.) → **Payload** for **query parameters** (many portal calls use **GET** with a `parameters=` JSON blob) or POST body → **Response** or **Preview** for JSON shape.
10. **Open one detail:** Click a result to open **one** practice/detail view. Again note new **Fetch/XHR** rows to `apps.cpaaustralia.com.au` (detail often triggers extra `/_api/` calls).
11. **Copy for this doc:** For **one search-related** and **one detail-related** call (or the single call that serves both if that is how the app works), copy:
    - Request URL (path + query only is fine; strip or redact secrets).
    - Method.
    - **Redacted** request body (if JSON, replace IDs/names with placeholders).
    - **Redacted** response excerpt (entity/table name, field names, record id keys — no real person or firm names if avoidable).

### 7.2 Search flow — sample (2026-04-07 manual capture)

**Yes — this is the right request** (not `OneCollector` / `GetViewportInfo`).

```text
Method: GET
Host:   apps.cpaaustralia.com.au
Path:   /callaction
Query:  actionName=cpa_findacpa
        parameters=<URL-encoded JSON; decode below>
Status: 200 OK

Decoded `parameters` (structure only; numeric values vary with map/search area):
{
  "EndpointName": "findacpa",
  "UpperLatitude": <number>,
  "LowerLatitude": <number>,
  "UpperLongitude": <number>,
  "LowerLongitude": <number>
}

Response (shape):
- Body parses as a JSON array of objects.
- Confirmed fields include: accountid (GUID), accountnumber (string).
- Additional keys match Dataverse / Dynamics **account** (organisation) attributes;
  redact firm names, addresses, phones, emails in any notes or commits.
- Observed Content-Type may be text/html; treat body as JSON text when implementing.
```

### 7.3 Detail flow — sample (2026-04-07 manual capture)

Opening a firm’s **detail** panel does **not** switch to a different host or action name: it is still **`GET /callaction?actionName=cpa_findacpa`**, with **`parameters`** encoding a JSON object. The difference is **`EndpointName`** and, for record-scoped calls, **`RecordId`** (GUID aligned with **`accountid`** from the search list).

**Example — supplementary fetch (languages):**

```text
Method: GET
Path:   /callaction
Query:  actionName=cpa_findacpa
        parameters={"EndpointName":"practicelanguages","RecordId":"<account-guid>"}
Status: 200 OK

Response:
- JSON array; observed case returned [] (no language rows for that practice).
```

**Other `EndpointName` values seen around detail / map (structure only):** e.g. `financialplanners` with the same **bounding-box** lat/long keys as search — likely extra attributes or map-related data, not the core contact block.

**Implication for automation:** Treat **search response rows** as the **canonical source** for name, address, phone, email, website when those fields appear there. Treat per-record **`callaction`** calls as **optional enrichments** (may be empty). **Dedupe key:** prefer **`accountid`** (and/or **`accountnumber`**) from the list payload.

---

## 8. Implications for Phases 1–3 (summary)

| Area | Decision |
|------|----------|
| **Stack** | **Playwright** first; autocomplete via **Places widget** interaction. |
| **HTTP shortcut** | **§7** documents `GET /callaction?actionName=cpa_findacpa` (bbox search + record-scoped `EndpointName` / `RecordId`). Still **defer implementing direct HTTP** until the **Playwright vertical slice** works — undocumented portal action, session cookies, Cloudflare, and `Content-Type: text/html` quirks make **browser-primary** the right Phase 1–2 default. **Phase 5** can revisit cookie-authenticated replay if stable. |
| **Dedupe** | **Prefer `accountid` / `accountnumber`** from search JSON when obtainable (§5, §7.2); Phase 1 confirms whether the DOM exposes them or whether a **network listener** in Playwright is needed. |
| **Risk** | **Cloudflare** + portal upgrades may break runs; keep **safety brakes** and screenshots. |

---

*Phase 0: **complete** for moving to Phase 1 — verdict, DOM hints, dedupe direction, and §7.2–§7.3 `callaction` patterns recorded.*
