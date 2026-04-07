# cpa_ca_extraction

Extract CPA practice contacts from [CPA Australia — Find a CPA](https://apps.cpaaustralia.com.au/find-a-cpa/). Implementation follows `docs/implementation-plan.md` (Playwright, phased rollout).

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .
playwright install chromium
```

If `PLAYWRIGHT_BROWSERS_PATH` points at a cache from another machine or architecture, either clear it or run installs with `env -u PLAYWRIGHT_BROWSERS_PATH playwright install chromium`.

## Phase 1 — one row (vertical slice)

```bash
python -m scraper run --site cpa_au --out data/run.csv
```

Options:

- `--location` — string typed into Google Places (default: `Sydney NSW, Australia`)
- `--seed` — value stored in CSV `search_seed` for provenance (default: `Sydney,NSW,2000`)
- `--headed` — show the browser (default is headless)

The CPA UI’s **Find** control is driven with a DOM `click()` so the portal handler runs reliably in headless Chromium.

### Browser “Know your location” (manual browsing)

Chrome may show **Use your location?** for `apps.cpaaustralia.com.au`. Choosing **Allow while visiting the site** is fine and lets the map use your position.

When you run **`python -m scraper`**, Playwright already **grants geolocation** for that origin and supplies a default point near **Sydney CBD**, so you should not see that permission bar during automation.

If the red line **“You can only search for either Australian or New Zealand address.”** appears while typing manually, it usually means the field does not yet have a **confirmed Google Places** value: pick a suggestion from the dropdown (keyboard or click), then try **FIND A CPA** again.

After Phase 1, extend to full result lists (Phase 2) using the same module.
