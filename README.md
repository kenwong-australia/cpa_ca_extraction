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
- **`run_scraper.sh`** — runs `python -m scraper` using that `.venv`. For subcommand **`run`**, it adds **`--headed`** by default so Chromium opens on your screen (good for testing). For **headless** runs: `CPA_SCRAPER_HEADLESS=1 ./run_scraper.sh run …` or use `python -m scraper run …` with the venv activated and no `--headed`.

#### From a new terminal to a finished scrape (copy-paste)

Use this when you open Terminal and still see conda **`(base)`**, or any fresh shell. **Step 1:** go to **your** clone of this repo (the folder that contains `pyproject.toml` and `run_scraper.sh`). **Step 2:** optionally leave conda base in this tab only. **Step 3:** install the environment **only if** `.venv` does not exist yet. **Step 4:** run the scraper; when it exits, open the printed CSV path.

```bash
cd /path/to/cpa_ca_extraction   # e.g. cd ~/cpa_ca_extraction — must be this project’s root

conda deactivate 2>/dev/null || true   # optional; run again if your prompt still shows "(base)"

chmod +x scripts/setup.sh run_scraper.sh 2>/dev/null || true
[[ -d .venv ]] || ./scripts/setup.sh

./run_scraper.sh run --site cpa_au --out "data/run_$(date +%Y%m%d_%H%M).csv" --limit 1
```

This block is **not** a different app: `[[ -d .venv ]] || ./scripts/setup.sh` runs **`scripts/setup.sh`** only when `.venv` is missing (same steps as **Manual setup**). **`run_scraper.sh`** wraps **`python -m scraper`** and turns on **visible browser** for `run` (see bullet above).

After the last line, you should see `Wrote 1 row(s) to …/data/run_….csv`. For more rows, change `--limit` or remove it for the full list (slow). To refresh dependencies later, run `./scripts/setup.sh` again.

**Output CSV naming:** Prefer a **datetime-stamped** `--out` path (as above: `run_$(date +%Y%m%d_%H%M).csv`) so each run has its own file and checkpoints stay tied to that file. Plain names like `data/run.csv` are fine for quick tests.

## Phase 2 — full result list (one search)

By default, **`cpa_au` scrapes every practice row** for the chosen `--location` (one search, many CSV rows).

```bash
OUT="data/run_$(date +%Y%m%d_%H%M).csv"
./run_scraper.sh run --site cpa_au --out "$OUT"
# Headless (no window): CPA_SCRAPER_HEADLESS=1 ./run_scraper.sh run --site cpa_au --out "$OUT"
# Or with venv activated: python -m scraper run --site cpa_au --out "$OUT"   # add --headed to see browser
```

Between rows, the scraper waits a **uniform random pause** after returning to the list and before opening the next practice. **Defaults** are **3–8 seconds** (implementation plan §3.1). Override with **`--jitter-min-seconds`** and **`--jitter-max-seconds`** (same bounds apply between Phase 3 seed rows and between listing rows).

### Options

- `--location` — string typed into Google Places (default: `Sydney NSW, Australia`)
- `--seed` — value stored in CSV `search_seed` for provenance (default: `Sydney,NSW,2000`)
- `--limit N` — scrape at most **N** rows (e.g. `--limit 1` for a Phase‑1-style smoke test)
- `--max-consecutive-failures` — abort after this many **consecutive** row failures (default: `10`, §3.2)
- `--max-search-retries` — retries for the initial search / Places step (default: `3`, §3.2)
- `--wall-clock-seconds S` — stop after **S** seconds (optional, §3.2)
- `--jitter-min-seconds S` / **`--jitter-max-seconds S`** — min/max **uniform random delay** (seconds) between Phase 3 seeds and between practice rows within a search (defaults: `3` and `8`). Use **wider** ranges (e.g. `10`–`25`) if you hit Cloudflare rate limits; `min` must be ≤ `max`.
- `--headed` — show the browser (`python -m scraper` default is headless; **`./run_scraper.sh run` adds this for you** unless `CPA_SCRAPER_HEADLESS=1`)

The CPA UI’s **Find** control is driven with a DOM `click()` so the portal handler runs reliably in headless Chromium.

### Stopping the run (Ctrl+C)

Playwright’s synchronous browser calls can hold the process until the current step finishes, so **Ctrl+C may not return immediately**. The scraper **chunks long waits** so interrupts are picked up sooner; if it still feels stuck, press **Ctrl+C again within about two seconds** to **force exit**, or from another terminal run `kill -INT <pid>` / `kill -9 <pid>` on the Python process.

### Rate limits (Cloudflare Error 1015)

If the site returns a **Cloudflare / rate-limit page**, the run **stops with exit code 3** and a short message. **Wait** until `find-a-cpa` loads normally in a normal browser on **your network** (cool-down can be minutes to hours), then **re-run the same command** with the **same `--out`**; with **`--input`**, the seed **checkpoint is not advanced** for the row that was blocked, so that suburb will be tried again on resume.

**Reducing how often you hit 1015 (no guarantees — Cloudflare also weighs IP and traffic patterns):**

- **Slow down:** pass **`--jitter-min-seconds`** / **`--jitter-max-seconds`** with higher values (e.g. `10` and `25`, or `15` and `35`) so there are fewer requests per minute.
- **Shorter sessions:** stop after a handful of suburbs (Ctrl+C), wait, then resume — checkpoints and dedupe support that workflow.
- **Fewer detail opens:** **`--limit N`** caps rows **per** suburb if you do not need the full list every time.
- **Network:** residential access often behaves better than VPN/datacenter IPs.

### Browser “Know your location” (manual browsing)

Chrome may show **Use your location?** for `apps.cpaaustralia.com.au`. Choosing **Allow while visiting the site** is fine and lets the map use your position.

When you run **`python -m scraper`**, Playwright already **grants geolocation** for that origin and supplies a default point near **Sydney CBD**, so you should not see that permission bar during automation.

If the red line **“You can only search for either Australian or New Zealand address.”** appears while typing manually, it usually means the field does not yet have a **confirmed Google Places** value: pick a suggestion from the dropdown (keyboard or click), then try **FIND A CPA** again.

## Phase 3 — many locations (seed CSV)

Use **`--input`** with a CSV that has **`suburb`**, **`state`**, and optional **`postcode`** (extra columns are ignored). Each row becomes a Places query **`{suburb} {state}, Australia`** and provenance **`{suburb},{state},{postcode}`**.

- **Between locations:** same **random delay** as between practices (§3.1): defaults **3–8 s**, overridable with **`--jitter-min-seconds`** / **`--jitter-max-seconds`**.
- **Checkpoint prompt vs browser:** for **`--input`**, the **full run vs resume** prompt runs **before** Chromium starts, so you are not left waiting at `Enter choice` while a **headless** browser is already running. After launch, stderr prints **`Browser: headed …`** or **`Browser: headless …`** so you can confirm the mode.
- **Dedupe:** rows whose `dedupe_key` (or normalised fallback) was **already in the output file** or written earlier in this run are skipped (no duplicate CSV lines).
- **Progress:** each seed row logs a line such as `Progress: seed 12/168 (of seed CSV) search_seed=…`. A **`skipped:`** line means that row did not complete — the **on-disk checkpoint does not advance**, so the next run will **try that seed again** after resume.
- **Checkpoints:** after each **completed** seed row, progress is saved in a **sidecar file** next to your output CSV: **`{your-out}.csv.seed_checkpoint.json`** (for example `data/run_20260408_1430.csv.seed_checkpoint.json`). Re-running with the **same** `--input`, **`--out`**, and seed row count (including **`--max-locations`**) lets you resume safely.
- **Interrupted runs (Ctrl+C):** completed seeds are already checkpointed; run the same command again to continue.
- **Fresh run vs resume**
  - **Resume:** run again **without** `--fresh` — on an interactive terminal you get a prompt: **`1`** = full run from seed 1 (clears checkpoint), **`2`** = resume from the last checkpoint. If stdin is **not** a TTY (scripts/CI), the tool **resumes automatically**; use **`--fresh`** there to force a full run.
  - **Fresh run from seed 1:** add **`--fresh`** (or choose **`1`** at the prompt).

```bash
OUT="data/run_$(date +%Y%m%d_%H%M).csv"
./run_scraper.sh run --site cpa_au --out "$OUT" --input data/seeds.168.csv --max-locations 2
# Same command later to resume (same $OUT path), or add --fresh to restart from seed 1
# Slower pacing if Cloudflare rate-limits you (same resume command, same $OUT):
# ./run_scraper.sh run --site cpa_au --out "$OUT" --input data/seeds.168.csv \
#   --jitter-min-seconds 10 --jitter-max-seconds 25
```

`--limit` applies **per location**. `--wall-clock-seconds` applies to the **whole** multi-location run. Omit `--max-locations` to process every seed row. **`--fresh`** (Phase 3 only) resets the seed checkpoint as described above.

**Tip:** Keep the same **`--out`** path across resume attempts so the CSV and **`*.seed_checkpoint.json`** stay paired. If you use a new datetime in the filename each time, each run is a separate file with no inherited checkpoint (by design).

## Repository

Source and issues: [github.com/kenwong-australia/cpa_ca_extraction](https://github.com/kenwong-australia/cpa_ca_extraction).
