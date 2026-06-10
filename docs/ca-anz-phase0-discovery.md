# Phase 0 — Discovery notes (CA ANZ — Find a CA)

**Date:** 2026-05-03  
**Entry:** `https://www.charteredaccountantsanz.com/find-a-ca`  
**Results (from user screenshots):**  
`https://www.charteredaccountantsanz.com/find-a-ca/search-results-for-a-ca?country=Australia&selectedType=All&postcode=2000&formType=specifications&limit=20&firstName=&lastName=&business=`

**Method:** Automated `curl` + Playwright **headless vs headed**; manual Network capture still useful for §7.

---

## 1. Verdict (updated 2026-05-03 — headed automation)

**Classification: *browser-primary* (Playwright); use **`--headed`** for CA ANZ the same way you run CPA AU.**

| Criterion | Finding |
|-----------|---------|
| **Callable JSON vs HTML** | **Yes (§7.2):** `POST https://www.charteredaccountantsanz.com/api/FindACAV2/GetMembers` returns **JSON**. **Payload / reCAPTCHA** fields still need capture for a safe replay strategy. |
| **Skip the landing form?** | **Confirmed (headed):** `page.goto` to the **search-results** URL with `country=Australia`, `postcode=<seed>`, `formType=specifications`, `limit=20` loads the real results UI (title **`Search results for a CA | CA ANZ`**). Phase 1 can start with **URL construction**; keep form fallback if behaviour changes. |
| **Pagination** | **Load More** — `button.btn.btn-result`, label **Load More**. List is **not** a `<table>` (see §4). |
| **Headless vs headed** | **Headless:** Cloudflare block / challenge, no app DOM. **Headed:** real page and listing. Default CA ANZ runs should use **`--headed`**. |
| **Load more via automation** | **Fragile:** Programmatic **Load More** in stock Playwright **Chromium** has been observed to leave the UI on a **spinner** (no append). The same action **manually** works and triggers **`GetMembers`** XHR **200** quickly. **Likely cause:** **Google reCAPTCHA** on the flow (see §9) — token / risk-scoring path may not complete for `navigator.webdriver` sessions. |

---

## 2. Platform / blocking (automated probes — 2026-05-03)

- **Cloudflare** in front of `charteredaccountantsanz.com`.
- **`curl`** to `/find-a-ca` returned **HTTP 403** with HTML **"Sorry, you have been blocked"** (managed challenge / bot policy — not the real app HTML).
- **Playwright Chromium headless** loading both the **landing** and **search-results** URLs produced **`Attention Required! | Cloudflare`** (or block copy in `body`), **no** application DOM.
- **Playwright Chromium headed** (`headless=False`) on a developer machine loaded **search-results** successfully: correct **document title**, **"Showing 20 out of 2009 results for 2000"**, and listing content (names, firms, contacts) in the main region.

**Implication:** Treat **headless as flaky / unsupported** for CA ANZ unless you add mitigations (persistent profile, etc.). Use **headed** for Phase 1–2 and smoke tests. CI against this host will likely need **`--headed`** on a real display or be skipped.

**Recommendation for `rate_limit.py` (Phase 1):** Extend detection to include **`Sorry, you have been blocked`** / **`Attention Required! | Cloudflare`** when the hostname is CA ANZ, so runs fail loudly instead of scraping an empty DOM.

---

## 2.1 reCAPTCHA + Load More (2026-05-03 — manual Network capture)

When **Load More** is clicked **manually**, DevTools shows at least:

| Request | Notes |
|---------|--------|
| **`GetMembers`** | **XHR**, **200**, ~1–2 s, ~**2 kB** response. **Initiator:** bundle like **`find-ca-search-v2`** — this is the pagination / append call. |
| **`recaptcha__en.js`** | reCAPTCHA loader. |
| **`reload?k=…`** (Google) | Typical reCAPTCHA **token / challenge** traffic (see `k=` site key in URL — **do not commit** keys or full URLs with secrets). |

**Interpretation:** The app likely waits for a **successful reCAPTCHA / risk assessment** before firing or resolving **`GetMembers`**. **Stock Playwright** is easy to score as automated, so the client may **never get a token** or **block the request** → **infinite spinner** after `locator.click()`.

---

## 3. Flow to validate manually (matches your screenshots)

1. Open **Find a CA** landing (`/find-a-ca`).
2. **Step 1:** Country **Australia** (default).
3. **Step 2:** Mode **City, Suburb, Or Postcode** (not CA Name).
4. Enter **postcode only** (e.g. `2000`); leave **CA ANZ Specialisation** at **All**.
5. Click **Search** → land on **search-results** page.
6. Confirm table columns:
   - **Name / designation** — pattern like `First Last (Display), CA`.
   - **Company** — firm name (link) + address lines.
   - **Contact** — `tel:` / phone text, `mailto:` email.
7. Note **header** text: “Showing *x* out of *y* results …” (for loop termination vs “Load more”).
8. Click **Load more** once — confirm **new rows append** and **x** increases.

**Parallel experiment:** In a **new tab**, paste a **full results URL** with a different `postcode` (e.g. `3000`) **without** visiting the form first. Record whether the table loads or you get an error/redirect.

**Multi-postcode automation (2026-06):** In the **same tab** after seed 1, **`page.goto`** to a new `postcode=` results URL often **does not** produce a fresh **`GetMembers`** call (Melbourne/Brisbane/Adelaide skipped in batched runs). The site expects the **on-page postcode field + red Search button** (`button.btn.btn-result`, label **Search**) — same control family as **Load More**. Phase 1 **`ca_anz.py`** uses form Search when already on `/find-a-ca`; cold loads still use results URL **`goto`**.

---

## 4. DOM / accessibility (headed probe — 2026-05-03)

In-page evaluation on **search-results** (`postcode=2000`, `limit=20`):

| Area | Notes | Playwright hint |
|------|--------|------------------|
| **Layout** | **No `<table>`** — `nTables: 0`, `nTr: 0`. Results are **div-based** (Bootstrap-style `row`, cards). | Do not rely on `table tbody tr`. |
| **Row / card** | Classes observed include **`ca-search-result-new`**, **`results-list`**, **`result-section`**, **`ca-result-card-mbl`**, **`card-section`**. | Start with **`.ca-search-result-new`** or **`.results-list .ca-search-result-new`**; confirm after “Load more” (duplicate classes). |
| **Column header row** | **`result-table-head`** (`d-none d-md-flex` — desktop header). | Useful anchor; data cards may be sibling structure. |
| **Load more** | **`button.btn.btn-result`**, visible text **Load More**. | `page.get_by_role("button", name="Load More")` |
| **Summary line** | Visible copy includes **`Showing 20 out of 2009 results for 2000`**. | Regex on main / hero text for `Showing (\d+) out of (\d+)` to cap pagination. |
| **Name / firm / contact** | Plain text blocks + links (emails/phones appear as text or `mailto`/`tel` — confirm per row in Phase 1). | Parse inner text lines or scoped locators under each card. |

### 4.1 Optional — your follow-up

Paste **one Network** row for **Load more** (§7.2) or note **framework** (e.g. Next data) when captured.

---

## 5. Identity / dedupe (Phase 0 direction)

Until an API exposes a stable **member id**:

- **Primary:** normalised **email** (if present).
- **Fallback:** **`dedupe_key_normalised(company_name, address, full name)`** or phone + name.

Revisit when §7 shows a **profile URL** or **id** in XHR JSON.

---

## 6. Network capture checklist (high value)

In **Chrome DevTools → Network → Fetch/XHR**, with **Preserve log**:

1. Load landing → search for `2000` → results visible.
2. Filter by **`charteredaccountantsanz.com`** (or show all and sort by domain).
3. Click **Load more** once.
4. For each interesting request, record: **method**, **URL** (path + query only), **status**, **content-type**, and whether **response** is JSON or HTML fragment.

**Paste redacted excerpts in §7** (no cookies, no PII).

---

## 7. Captured XHR / APIs (manual — redact)

### 7.1 Initial load vs Load more

Both use **`POST /api/FindACAV2/GetMembers`** with the **same JSON shape**; pagination is **`offset`** plus **`recordLimit`**.

| Call | Typical `offset` | Notes |
|------|------------------|--------|
| **First page** (after search / first paint) | **`0`** | Increase by **`recordLimit`** for each subsequent batch. |
| **Load more** (captured) | **`20`** | With **`recordLimit`: 20**, next batches use `40`, `60`, … until done. |

Confirm **`offset: 0`** on the **first** `GetMembers` in Network once; it should match the above.

### 7.2 GetMembers — request / response (2026-05-03)

**URL:** `https://www.charteredaccountantsanz.com/api/FindACAV2/GetMembers`  
**Method:** `POST`  
**Request body (`Content-Type: application/json`):**

```json
{
  "country": "Australia",
  "postcode": "3000",
  "memberType": "All",
  "name": "",
  "business": "",
  "firstName": "",
  "lastName": "",
  "latitude": "",
  "longitude": "",
  "is": true,
  "offset": 20,
  "recordLimit": 20,
  "sortBy": "lastName",
  "sortOrder": "asc",
  "token": "<long-lived client token; do not commit live values — likely tied to reCAPTCHA / site JS>"
}
```

- **`token`:** Required in live traffic. **Never commit** real values. Phase 1 must learn how **find-ca-search-v2** supplies it (often **reCAPTCHA execute** output or similar).
- **`memberType`:** **All** in UI maps to **`"All"`** in this capture.

**Response body (`application/json`) — structure (values illustrative):**

```json
{
  "totalCount": 905,
  "searchDetails": [
    {
      "Name": "…",
      "Company": "…",
      "BusinessAddress": "…",
      "Phone": "…",
      "Email": "…",
      "CompanyWebsite": "…",
      "Designation": "CA",
      "Specialties": null,
      "SpecialConditions": null,
      "Specialisation": null,
      "Longitude": null,
      "Latitude": null
    }
  ]
}
```

- **`totalCount`:** Matches UI “out of *N* results”.
- **`searchDetails`:** Length ≤ **`recordLimit`**; may be shorter on the last page.
- **CSV mapping (Phase 1):** Parse **`Name`** into first / last / display + **`Designation`**; map **`Company`**, **`BusinessAddress`**, **`Phone`**, **`Email`**, **`CompanyWebsite`**; scan full JSON for a stable **`MemberId`/`id`** if present for `listing_id` / dedupe.

**Phase 1 angle:** Loop **`page.request.post(GetMembers, …)`** with the tab’s **cookies** and a valid **`token`** per policy of the front-end. If **`token`** is only issued after human trust, combine **headed `page.goto`** with **`page.evaluate`** hooking the same function the app uses, or fall back to **Load more** + DOM.

### 7.3 Optional — direct results URL

**Headed Playwright (2026-05-03):** Direct GET to `search-results-for-a-ca?...&postcode=2000&...` showed **full listing text in the DOM** after `wait_until=networkidle` + short settle. **Hydrating XHR** may still occur; capture in §7.1 if you want an API shortcut.

---

## 8. Phase 0 “done” criteria

- [x] **Direct results URL** works under **headed** Playwright (real listing + “Showing *x* out of *y*”).
- [x] **Row + Load more** selector hints documented (§4) — refine in Phase 1 if markup shifts.
- [ ] **Form → results** still worth a quick manual check if URL-only ever fails (cookie/session edge case).
- [x] **§7** request/response shapes captured (`GetMembers`, **`offset`**/**`recordLimit`**, **`searchDetails`** fields); **`token`** sourcing remains a Phase 1 task.

**Batched runs / resume (operational — spelled out in README in Phase 1):** One **browser session** per invocation; **manual warm-up at most once per session** (start of run), not before every postcode. If the run **stops**, start again → **new session** → **warm up once** → **same `--out`** so the existing **seed checkpoint** sidecar resumes the next unfinished row. Implementation: Phase 1 CLI (`ca_anz`, `--input`, optional `--manual-gate`).

When you are satisfied with §7 (or accept HTML-only scraping), proceed to **Phase 1** (`sites/ca_anz.py`: `goto` results URL, card loop, **Load more** + jitter).

---

## 9. Phase 1 options when Load More spins under automation

Try in order of **simplicity** (all **headed**; re-test **`GetMembers`** appears after each click):

1. **Real Chrome instead of bundled Chromium:** `channel="chrome"` (or **`launch_persistent_context`** with a normal user data dir) so the browser fingerprint is closer to daily use.
2. **Human-like gap before click:** scroll the **Load More** button into view, **`sleep_random`** several seconds, then click — sometimes enough for **reCAPTCHA v3** to settle.
3. **Confirm the XHR:** register `page.on("request", …)` / `response` and log whether **`GetMembers`** fires at all after an automated click. If **no request** → client blocked before fetch; if **pending / 4xx** → inspect status and body.
4. **Semi-automated runs:** `page.pause()` before first **Load More**, operator clicks **Load More** in the visible window until done, then script resumes scraping **DOM** (acceptable only for small jobs).
5. **Legal / policy check:** If the site offers **official data access** or **API**, prefer that over bypassing abuse controls.
6. **API-first inside the browser session:** After **`page.goto`** results URL, use **`APIRequestContext` / `page.request.post`** to call **`/api/FindACAV2/GetMembers`** with the same **storage state** as the tab, using the **payload pattern** from §7.2 (avoids **Load More** clicks if the body does not require a fresh client-only token each time — **verify**).

Avoid committing **reCAPTCHA site keys**, cookies, or full **`GetMembers`** payloads with PII into the repo.

---

*Phase 0 **automated** slice: **headed first paint OK**; **Load more** pagination **confirmed manually** via **`GetMembers`**; **programmatic Load more** may hang until reCAPTCHA / browser fingerprint issues are mitigated (§9). Headless remains blocked at the edge.*
