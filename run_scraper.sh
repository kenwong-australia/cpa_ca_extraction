#!/usr/bin/env bash
# Run the scraper from the project venv (no need to source .venv yourself).
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
exec python -m scraper "$@"
