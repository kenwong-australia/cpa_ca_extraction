#!/usr/bin/env bash
# Run the scraper from the project venv (no need to source .venv yourself).
# For subcommand "run", passes --headed by default so you can see the browser while testing.
# Unattended / CI: CPA_SCRAPER_HEADLESS=1 ./run_scraper.sh run ...
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

if [[ ! -f .venv/bin/activate ]]; then
  echo "No .venv found. Run once from this folder:" >&2
  echo "  chmod +x scripts/setup.sh && ./scripts/setup.sh" >&2
  exit 1
fi

# shellcheck source=/dev/null
source .venv/bin/activate

if [[ "${1:-}" == "run" && "${CPA_SCRAPER_HEADLESS:-0}" != "1" ]]; then
  has_headed=0
  for a in "${@:2}"; do
    if [[ "$a" == "--headed" ]]; then
      has_headed=1
      break
    fi
  done
  if [[ "$has_headed" -eq 0 ]]; then
    shift
    exec python -m scraper run --headed "$@"
  fi
fi
exec python -m scraper "$@"
