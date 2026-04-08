# CPA Australia “Find a CPA” — Extraction Implementation Plan

This document records the **stack decision** (Playwright), a **phased implementation** you can stop after any phase, and a **lightweight layout** so future scrapers for other sites can plug in without rewriting shared plumbing.

Target: Australian practice contact details from [Find a CPA](https://apps.cpaaustralia.com.au/find-a-cpa/) → local CSV including run date metadata.

---

## 1. How the site behaves (from your screenshots and typical SPA patterns)

| Observation | Implication |
|---------------|-------------|
| Country is a dropdown (Australia / New Zealand) | Set Australia once per session; no NZ loop needed. |
| Location field uses autocomplete (suburb/postcode work; state alone does not) | Automation must **select a concrete suggestion** (keyboard + Enter or click), not only type free text. |
| After search: list + map | Content is **rendered by JavaScript**; Playwright (or discovered APIs) is required. |
| Row chevron opens a detail panel | **Simulate clicks** in the browser, or call **discovered APIs** if Phase 0 shows stable JSON/XHR (see §3.0). |

---

## 2. Stack decision: Playwright (Python)

**We are standardising on [Playwright for Python](https://playwright.dev/python/).**

- Matches the SPA flow: autocomplete, waits, list iteration, detail panel, optional screenshots for debugging.
- Single language (Python), local CSV output, straightforward CLI.

**Phasing out alternatives (for this repo):**

- **Selenium:** Not the default path; only revisit if Playwright is blocked by organisational policy.
- **Cloudflare Browser Rendering / other hosted browsers:** Defer unless you hit **IP or environment** constraints (no local Chromium, datacenter blocks). If needed later, the **site-specific** module stays the same in spirit; only **browser launch** (connect to remote CDP vs local) changes in shared plumbing.
- **Pure `requests`/`httpx`:** Optional **Phase 5** only if **Phase 0** documents viable endpoints; ship Playwright first unless discovery proves a safe shortcut.

---

## 3. Phased implementation (stop when good enough)

Each phase delivers something runnable. Later phases **extend** earlier code rather than replacing it.

| Phase | Goal | Done when |
|-------|------|-----------|
| **0 — Discovery** | **Timeboxed** (e.g. half-day): decide **browser-primary vs callable API** for results + detail; map DOM; capture IDs/tokens for dedupe. See §3.0. | Written verdict (*API viable* / *API fragile* / *browser-only*) + notes in repo; implications for selectors, dedupe, and failure handling documented. |
| **1 — Vertical slice (MVP)** | One fixed location (e.g. “Sydney NSW, Australia”): search → open **one** listing → parse detail → append **one** row to CSV with `run_date` / timestamp. | `python -m ...` produces a CSV row without manual clicks. |
| **2 — Full listing for one search** | Same as Phase 1 but iterate **all** rows for that search (handle back-to-list if needed). Apply **timing policy** (§3.1) and **safety brakes** (§3.2) between steps. | All contacts for one place in one CSV run. |
| **3 — Outer loop + dedupe** | Feed seeds from §5; skip duplicates via **dedupe strategy** (§5.2); apply §3.1 + §3.2; basic logging. | Multi-location run; no duplicate rows for same listing identity. |
| **4 — Hardening (optional)** | Checkpoint/resume, config file (delay bounds, **safety brake** thresholds), failure screenshots, headless flag. | Long runs recover from crashes. |
| **5 — Optimisation (optional)** | If Phase 0 was *API viable* (or *fragile* with documented workarounds): replace hot paths with direct HTTP where safe. | Faster runs; keep Playwright fallback if API breaks. |

### 3.0 Phase 0 — Discovery (mandatory depth)

Phase 0 is **not** “pick a few selectors.” It must answer whether the app exposes **listing and detail data** outside the DOM in a way you can rely on (even if v1 still uses Playwright).

**Timebox:** Cap wall-clock (e.g. half a day) so discovery does not block shipping; still produce a **clear written outcome**.

**Do:**

1. **Network (Fetch/XHR):** During autocomplete, search, list load, and detail open, record URLs, methods, status codes, and **sample JSON** (redact secrets). Note required headers, cookies, CSRF, or one-time tokens.
2. **Verdict — choose one label for the team:**
   - **API viable:** Stable-ish JSON (or GraphQL) for search results and/or detail; reproducible with Playwright-provided cookies or simple headers. Plan Phase 5 to use it for speed; Phase 1–2 may still be Playwright-only.
   - **API fragile:** Data exists on the wire but is obfuscated, versioned unpredictably, or needs brittle signing — treat **DOM as primary**; optional HTTP experiments later.
   - **Browser-only:** No practical direct calls; **DOM + Playwright** is the long-term approach.
3. **DOM:** Draft selectors / role-based locators and **wait conditions** anyway (needed for autocomplete, login, or API fallback).
4. **Identity for dedupe:** In Network + DOM, note **site-provided IDs**, `data-*` attributes, detail **URL patterns**, or listing tokens (§5.2).

**Deliverable:** Short doc in repo (e.g. `docs/discovery-notes.md`) with the verdict, 1–2 example requests if relevant, and “implications for Phase 1–3” (e.g. “prefer `listing_id` from JSON when present”).

Early API knowledge **changes** how aggressively you invest in DOM-only selectors, how you define `dedupe_key`, and how you handle failures (retry vs “API returned empty”).

### 3.1 Timing policy — polite pacing (Phases 2+)

Apply a **uniform random wait** between **5 and 15 seconds** (inclusive) before each **site-driving** step that follows meaningful work — i.e. after finishing one unit of interaction and before starting the next that hits the app/network. Concretely:

- **Phase 2:** After closing or leaving a detail view (or before opening the next row), wait **3–8 s** at random before the next row action.
- **Phase 3:** Same between rows; additionally, after completing all rows for one location (or after a search that yields none), wait **3–8 s** at random before starting the **next** location search.

Phase 1 may omit inter-step delays if it is a single one-off chain; once the flow repeats (Phase 2+), use the policy above.

**Polite throughput (v1):** Do **not** add an artificial “max searches per hour” or “max actions per run” **for politeness** — only this per-step **3–8 s** jitter. (This is separate from **safety brakes** in §3.2.)

Configurable (Phase 4): env or config can override `min_seconds` / `max_seconds` while keeping **5 / 15** as defaults.

### 3.2 Safety brakes — circuit breakers (Phases 2+)

These limits exist to stop **bugs and poisoned state** (infinite loops, broken navigation, retry storms), **not** to throttle the site politely. Defaults below are **starting points** — tune in config/env (Phase 4).

| Brake | Purpose | **Suggested default (v1)** |
|-------|---------|----------------------------|
| **Max consecutive failures** | After *N* failures in a row (timeout, missing selector, unexpected DOM, failed navigation), **abort the run** with a clear log. | **10** — tolerates a few flaky steps; if 10 in a row fail, the build or site likely changed. |
| **Max retries per location** | Retries **per seed** for the same location step (e.g. transient timeout, autocomplete not ready) before **logging and skipping** that seed. | **3** — standard retry budget without hammering one bad row. |
| **Max locations per run** | Cap how many seeds to process in **this** invocation (split work across runs or smoke-test). | **`None` (unlimited)** for full production runs; use **`50`** or **`100`** for dry runs / CI smoke tests. |
| **Wall-clock budget (seconds)** | Stop cleanly after *T* seconds so a job cannot run forever if logic regresses. | **`None` (unlimited)** for interactive use; use **`14_400` (4 h)** or **`28_800` (8 h)** for unattended schedules — pick based on host policy. |

**Reset rule:** Reset the **consecutive failure** counter after any **successful** step (e.g. one full listing scraped, or one successful search load) so transient blips do not accumulate across an otherwise healthy run.

Implement from Phase 2 onward. Phase 4 surfaces all thresholds in config/env.

**Rule of thumb:** Ship Phase 1–2 before investing in Phase 4–5. Do **not** build abstract “plugin systems” until Phase 2 works; the folder layout below is enough foresight.

---

## 4. Extensibility for other websites (keep it simple now)

You may later scrape **another domain** with different UI/UX. Avoid a heavy framework; use **one shared core** and **one module per site**.

### 4.1 Shared core (site-agnostic)

Keep these **free of CPA-specific selectors**:

- **Browser session:** create context, optional storage state, headless/slow-mo flags.
- **Run metadata:** `run_date`, `run_timestamp_utc`, optional `site_id` string.
- **Output:** write rows to CSV (stdlib `csv` is fine) using a **single shared row schema** or a small `TypedDict` / dataclass that all sites map into (e.g. `company_name`, `address`, `phone`, `email`, `website`, plus metadata columns).
- **Utilities:** normalise phone/email, **`sleep_random(min_s=3, max_s=8)`** for §3.1, **circuit-breaker counters** for §3.2, dedupe helper that respects **primary vs fallback keys** (§5.2).

### 4.2 Site-specific adapter (CPA now; others later)

For each website, implement a **narrow surface** so the runner does not care about UI details:

- **Inputs:** e.g. `location_query: str`, or a path to a seed file — defined per site.
- **Behaviour:** functions or one small class with methods such as:
  - navigate to start URL;
  - perform search for one location;
  - yield or collect **contact records** (already normalised to the shared schema).

CPA Australia logic (selectors, “pick first autocomplete”, chevron clicks) lives **only** in e.g. `sites/cpa_australia.py` (name as you prefer).

### 4.3 Wiring the runner

- **CLI:** e.g. `python -m scraper run --site cpa_au --input seeds.txt` where `--site` chooses which module to import.
- **Registration:** a tiny dict `SITE_REGISTRY = {"cpa_au": run_cpa_australia}` avoids package sprawl; upgrade to entry points only if you publish multiple packages.

### 4.4 What *not* to do yet

- No generic “workflow DSL” or visual builder.
- No shared base class with dozens of optional hooks — start with **plain functions** and extract a base class only if a second site copies the same method names.

This keeps the **first** deliverable small (CPA only) while the **folder names** make a second site a copy-paste-adapt of one file plus a registry line.

---

## 5. Geographic coverage and seed strategy (Australia only)

You cannot search by state alone; you need **many concrete locations**. This choice drives **runtime, completeness, and autocomplete behaviour** — decide explicitly before scaling Phase 3.

### 5.0 Default approach (v1)

1. **Primary seed source:** Start from a **clean Australian suburb/locality list** (or postcode-to-suburb expansion) from a **trusted open or ABS-aligned** dataset. One row per seed: include **suburb + state** (and postcode if available) so seeds are unambiguous.
2. **Coverage check before full loop:** Run a **small evaluation set** (mixed metro/regional postcodes) and compare listing counts or spot-checks against expectations. **Document gaps** (e.g. new developments, naming mismatches).
3. **Postcode seeding as a second wave:** Add **postcode-driven seeds** (or extra localities) **only where testing shows missing coverage** — not a blind **0000–9999** loop on day one. A full postcode sweep is noisy, slow, and duplicate-heavy without strong dedupe.

### 5.1 Autocomplete ambiguity

The UI can return **multiple suggestions** for the same typed string (e.g. similar suburb names, or postcode matching several streets). **Pick one reproducible policy** and document it in `docs/discovery-notes.md` or README, for example:

- Prefer the suggestion whose **label matches** the seed row (suburb + state + postcode if present); or  
- If multiple match, **prefer the first** that includes the seed state; or  
- If still ambiguous, **log and skip** the seed (or require manual seed file fix).

Same suburb name **across states** is the main pitfall — **always carry state (and postcode when possible)** in the seed file.

### 5.2 Dedupe keys and provenance

Normalised `(company_name, address)` alone is **not** sufficient as the **primary** strategy: names vary, addresses format differently, and one firm may appear in multiple listings.

**Order of preference for `dedupe_key`:**

1. **Stable site-provided identifier** from API response, detail URL path, query token, or `data-*` / hidden field (whatever Phase 0 finds).
2. If no ID: store **`listing_url`** or **raw listing token** when discoverable, and use a **hash or composite** of that as primary key.
3. **Fallback:** `dedupe_key_normalised` from normalised name + address (and optionally postcode), for merge/debug only — keep **raw** name/address columns unchanged.

**CSV / model:** Capture **provenance** where cheap: e.g. `listing_id`, `listing_url`, `raw_listing_token`, `dedupe_key` (best available), `dedupe_key_normalised` (fallback), plus `search_seed`, `selected_place_label` so duplicate rows from overlapping searches can be explained.

Same legal entity with **multiple distinct listings** (e.g. branches) may legitimately yield **multiple keys** — do not over-merge without human rules.

### 5.3 Seed data — what you need to provide

**You do not need to hand-compile a national list before starting.** Use a layered approach:

| Item | Who provides it | Notes |
|------|-----------------|--------|
| **Template + examples** | Repo ships **`data/seeds.example.csv`** (suburb, state, optional postcode). Copy to e.g. `data/seeds.csv` and grow, or point the CLI at your path. | Enough for Phase 1–2 and early Phase 3 tests. |
| **Full suburb/locality coverage (primary strategy, §5.0)** | **You or your org** obtain a dataset that fits your **licensing** needs (e.g. ABS / government open data, or a commercial address file). The scraper only reads **your** CSV — it does not embed copyrighted postcode products. | We do **not** ship a complete official AU postcode file in-repo (Australia Post and similar data are **restricted**). |
| **Postcode-only wave (secondary, §5.0)** | Optional: generate programmatically (e.g. all 4-digit strings `0000`–`9999`, or a filtered list) via a small script or one-liner when you intentionally accept noise, dedupe cost, and runtime. | Use **only after** evaluation shows suburb seeds miss coverage. |

**Summary:** No prerequisite for you to paste thousands of postcodes on day one. Start from **`data/seeds.example.csv`**, add rows as you validate autocomplete behaviour, then plug in a **proper locality list** when you scale — sourced under whatever licence your project allows.

---

## 6. CPA-specific automation checklist (Playwright)

For each search location (implemented in the site module):

1. Navigate to `https://apps.cpaaustralia.com.au/find-a-cpa/`.
2. Set country to **Australia**.
3. Fill location; wait for suggestions; select match per **§5.1**.
4. Click **FIND A CPA**; wait for list/map.
5. For each result: open detail (chevron/row); read name, address, phone, email, website; return to list if required.
6. Handle **load more** / pagination **only if** the search results list uses them.

**Observed UI (Find a CPA, 2026-04-08):** the **search results** list has **no** pagination and **no** “load more” control for the current portal — practices for that search appear in one scrollable list. Phase 2 can iterate every visible row without an extra paging loop. If CPA changes the UI later, revisit this step.

**Robustness:** `wait_for_selector`, retries, screenshot on failure (Phase 4).

---

## 7. CSV output

- **Core contact:** `company_name`, `address`, `phone`, `email`, `website` (when present).
- **Run metadata:** `site_id`, `run_date`, `run_timestamp_utc`, `search_seed` / `search_query`, `selected_place_label`.
- **Identity / dedupe (§5.2):** `listing_id` (if any), `listing_url`, `raw_listing_token` (if any), `dedupe_key` (best available), `dedupe_key_normalised` (fallback for debugging/merge).
- **Filename:** CLI **`--out`** chooses the path. In practice, use a **datetime-stamped** name (e.g. `data/run_YYYYMMDD_HHMM.csv`) so runs do not overwrite each other and **seed checkpoints** (Phase 3) stay bound to one export file.
- **Seed-run checkpoint sidecar:** with **`--input`**, the runner writes **`{--out basename}.seed_checkpoint.json`** beside the CSV (same directory), updated after each completed seed row; **`--fresh`** clears it. **Run metadata** on every row as requested.

---

## 8. Operational and compliance hygiene

- **Polite pacing:** **3–8 s** uniform random jitter between site-driving steps (§3.1). **No artificial cap** on total successful searches or detail opens *for politeness* — only jitter between steps.
- **Safety brakes:** **Always** implement §3.2 (consecutive failures, retries per location, optional location count / wall-clock) so selector bugs or stuck states cannot burn unlimited time.
- **Terms / robots:** Review site terms and `robots.txt`; this document is not legal advice.
- **Privacy:** Minimise stored fields; treat contact details as sensitive.

---

## 9. Suggested project layout (supports multi-site later)

```
cpa_ca_extraction/
  docs/
    implementation-plan.md
  src/
    scraper/
      __init__.py
      __main__.py              # CLI: run --site …
      core/
        __init__.py
        browser.py             # launch / context (no CPA selectors)
        csv_sink.py            # append rows, run metadata
        checkpoint.py          # Phase 3 seed-row checkpoint JSON (--input)
        models.py              # ContactRecord, run info
        dedupe.py              # primary + fallback keys (§5.2)
        delays.py              # random sleep 3–8 s (§3.1); overridable in Phase 4
        safety.py              # consecutive failures, retries, optional budgets (§3.2)
      sites/
        __init__.py
        cpa_australia.py       # all Find-a-CPA selectors + flow
        # future_site.py       # add + register when needed
      registry.py              # SITE_REGISTRY map
  data/
    .gitkeep
    seeds.example.csv    # template seeds (§5.3); copy to seeds.csv for real runs
  requirements.txt             # playwright
  README.md                    # playwright install chromium
```

Implement **CPA first** inside `sites/cpa_australia.py`; keep `core/` dumb and reusable.

---

## 10. Next steps

1. **Phase 0:** Complete §3.0 (Network + DOM + **API vs browser verdict**); write `docs/discovery-notes.md`; note listing IDs/URLs for §5.2.  
2. **Phase 1:** Scaffold `requirements.txt`, package layout above (minimal: `core` + `sites/cpa_australia` + CLI stub), one end-to-end row.  
3. **Phase 2:** Full list iteration for **one** search; **§3.1** pacing between rows; **§3.2** brakes; CSV + `dedupe_key` on each row. **List UI:** no pagination / load more on current site (§6) — iterate all practice rows in the list.  
4. **Phase 3:** Outer loop over a **seed file** (**§5.0**, **`data/seeds.example.csv`** / §5.3); reproducible autocomplete policy (**§5.1**); **skip duplicate listings** across the run using **`dedupe_key`** (and fallbacks per **§5.2**); **§3.1** jitter **between** locations after each search finishes (or after a zero-result search); optional **`max_locations`** cap (§3.2); basic logging; **seed-row checkpoints** and **`--fresh`** (see §10.1).

### 10.1 Phase 3 CLI (implemented)

| Piece | Notes |
|-------|--------|
| **Seed input** | `--input path.csv` — `suburb`, `state`, optional `postcode`; maps to Places query + `search_seed`. |
| **Seen-set dedupe** | `dedupe_key` / normalised keys already in **`--out`** or written earlier in the run are skipped. |
| **Between-location delay** | Same **3–8 s** uniform random wait as §3.1 after each location (including empty result). |
| **`--max-locations`** | Process only the first *N* seed rows (must match between resume attempts for a valid checkpoint). |
| **Checkpoints** | Sidecar **`{--out}.seed_checkpoint.json`**; updated after each **successful** seed row; interactive **full vs resume** prompt when appropriate; non-TTY auto-resume; **`--fresh`** clears checkpoint; **`KeyboardInterrupt`** leaves last good checkpoint. |
| **Progress logging** | e.g. `Progress: seed i/n (of seed CSV) search_seed=…`; **`skipped:`** means no checkpoint advance for that row (retried on resume). |

---

*Document version: Playwright committed; Phase 0 API/browser verdict; 3–8 s polite jitter; safety brakes (§3.2); seed data (§5.3); dedupe + geo strategy; Find a CPA results list — no pagination observed (§6, 2026-04-08); Phase 3 checkpoints + dated `--out` convention (§7, 2026-04-08); extensible layout.*
