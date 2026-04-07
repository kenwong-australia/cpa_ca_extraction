#!/usr/bin/env bash
# One-time (or occasional) dev environment: venv, editable install, Playwright Chromium.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -x /opt/homebrew/bin/python3.12 ]]; then
  PY=/opt/homebrew/bin/python3.12
elif command -v python3.12 >/dev/null 2>&1; then
  PY="$(command -v python3.12)"
else
  PY=python3
fi

echo "Using Python: $PY"
"$PY" -V

if [[ ! -d .venv ]]; then
  "$PY" -m venv .venv
fi

# shellcheck source=/dev/null
source .venv/bin/activate

python -m pip install -U pip
pip install -e .
python -m playwright install chromium

echo ""
echo "Setup finished. Run the scraper without manual activate, e.g.:"
echo "  ./run_scraper.sh run --site cpa_au --out \"data/run_\$(date +%Y%m%d_%H%M).csv\" --limit 1"
