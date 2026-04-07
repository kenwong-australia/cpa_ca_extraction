"""CLI: python -m scraper run --site cpa_au --out path.csv"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

from scraper.core.browser import new_browser_context
from scraper.registry import SITE_REGISTRY


def _cmd_run(args: argparse.Namespace) -> int:
    site = args.site
    if site not in SITE_REGISTRY:
        print(f"Unknown site {site!r}. Known: {', '.join(sorted(SITE_REGISTRY))}", file=sys.stderr)
        return 2

    out = Path(args.out).resolve()
    runner = SITE_REGISTRY[site]

    with sync_playwright() as p:
        browser, _ctx, page = new_browser_context(p, headless=not args.headed)
        try:
            runner(
                page,
                out,
                location_query=args.location,
                search_seed=args.seed,
            )
        finally:
            browser.close()

    print(f"Wrote 1 row to {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scraper", description="Contact extraction CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="Run a site scraper")
    run_p.add_argument("--site", required=True, help="Site id (e.g. cpa_au)")
    run_p.add_argument("--out", required=True, help="Output CSV path")
    run_p.add_argument(
        "--location",
        default="Sydney NSW, Australia",
        help="Full place string for Google Places field (default: Sydney NSW, Australia)",
    )
    run_p.add_argument(
        "--seed",
        default="Sydney,NSW,2000",
        help="Provenance label stored in CSV search_seed (default: Sydney,NSW,2000)",
    )
    run_p.add_argument("--headed", action="store_true", help="Show browser (default: headless)")
    run_p.set_defaults(func=_cmd_run)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
