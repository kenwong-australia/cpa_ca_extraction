# cpa_ca_extraction

Extract CPA practice contacts from [CPA Australia — Find a CPA](https://apps.cpaaustralia.com.au/find-a-cpa/). Implementation follows `docs/implementation-plan.md` (Playwright, phased rollout).

## Setup

**Two equivalent ways to get a working environment:**

| | |
|--|--|
| **Manual** (below) | You create `.venv`, activate it, install packages yourself. |
| **Scripts** ([macOS / Linux](#helper-scripts-macos--linux)) | `./scripts/setup.sh` does the same job; [`run_scraper.sh`](./run_scraper.sh) runs the scraper without activating the venv by hand. |

Manual install (any OS; use your Python 3.10+):

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .
python -m playwright install chromium
```

If `PLAYWRIGHT_BROWSERS_PATH` points at a cache from another machine or architecture, either clear it or run installs with `env -u PLAYWRIGHT_BROWSERS_PATH python -m playwright install chromium`.

### Helper scripts (macOS / Linux)

- **`scripts/setup.sh`** — creates `.venv` if missing, then `pip install -e .` and Playwright Chromium.
- **`run_scraper.sh`** — runs `python -m scraper` using that `.venv` so you do not have to `source .venv/bin/activate` yourself.

#### From a new terminal to a finished scrape (copy-paste)

Use this when you open Terminal and still see conda **`(base)`**, or any fresh shell. **Step 1:** go to **your** clone of this repo (the folder that contains `pyproject.toml` and `run_scraper.sh`). **Step 2:** optionally leave conda base in this tab only. **Step 3:** install the environment **only if** `.venv` does not exist yet. **Step 4:** run the scraper; when it exits, open the printed CSV path.

```bash
cd /path/to/cpa_ca_extraction   # e.g. cd ~/cpa_ca_extraction — must be this project’s root

conda deactivate 2>/dev/null || true   # optional; run again if your prompt still shows "(base)"

chmod +x scripts/setup.sh run_scraper.sh 2>/dev/null || true
[[ -d .venv ]] || ./scripts/setup.sh

./run_scraper.sh run --site cpa_au --out "data/run_$(date +%Y%m%d_%H%M).csv" --limit 1
```

This block is **not** a different app: `[[ -d .venv ]] || ./scripts/setup.sh` runs **`scripts/setup.sh`** only when `.venv` is missing (same steps as **Manual setup**). **`run_scraper.sh`** is a thin wrapper around **`python -m scraper`** using that `.venv`.

After the last line, you should see `Wrote 1 row(s) to …/data/run_….csv`. For more rows, change `--limit` or remove it for the full list (slow). To refresh dependencies later, run `./scripts/setup.sh` again.

## Phase 2 — full result list (one search)

By default, **`cpa_au` scrapes every practice row** for the chosen `--location` (one search, many CSV rows).

```bash
./run_scraper.sh run --site cpa_au --out data/run.csv
# Or, with venv activated: python -m scraper run --site cpa_au --out data/run.csv
```

Between rows, the scraper waits a **uniform random 5–15 seconds** (implementation plan §3.1) after returning to the list and before opening the next practice.

### Options

- `--location` — string typed into Google Places (default: `Sydney NSW, Australia`)
- `--seed` — value stored in CSV `search_seed` for provenance (default: `Sydney,NSW,2000`)
- `--limit N` — scrape at most **N** rows (e.g. `--limit 1` for a Phase‑1-style smoke test)
- `--max-consecutive-failures` — abort after this many **consecutive** row failures (default: `10`, §3.2)
- `--max-search-retries` — retries for the initial search / Places step (default: `3`, §3.2)
- `--wall-clock-seconds S` — stop after **S** seconds (optional, §3.2)
- `--headed` — show the browser (default is headless)

The CPA UI’s **Find** control is driven with a DOM `click()` so the portal handler runs reliably in headless Chromium.

### Browser “Know your location” (manual browsing)

Chrome may show **Use your location?** for `apps.cpaaustralia.com.au`. Choosing **Allow while visiting the site** is fine and lets the map use your position.

When you run **`python -m scraper`**, Playwright already **grants geolocation** for that origin and supplies a default point near **Sydney CBD**, so you should not see that permission bar during automation.

If the red line **“You can only search for either Australian or New Zealand address.”** appears while typing manually, it usually means the field does not yet have a **confirmed Google Places** value: pick a suggestion from the dropdown (keyboard or click), then try **FIND A CPA** again.

Phase 3 will add multi-location seeds and dedupe across runs.
