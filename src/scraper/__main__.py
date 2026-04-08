"""CLI: python -m scraper run --site cpa_au --out path.csv"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from scraper.core.browser import new_browser_context
from scraper.core.checkpoint import (
    CHECKPOINT_VERSION,
    SeedCheckpoint,
    checkpoint_path_for_output,
    delete_seed_checkpoint,
    explain_non_interactive_resume,
    is_checkpoint_valid_for_run,
    load_seed_checkpoint,
    prompt_full_or_resume,
    save_seed_checkpoint,
)
from scraper.core.csv_sink import read_existing_dedupe_keys
from scraper.core.delays import sleep_random
from scraper.core.seeds import load_seed_placements
from scraper.core.rate_limit import RateLimitedError
from scraper.core.safety import SafetyBrakes
from scraper.registry import SITE_REGISTRY

_SIGINT_PREV_TIME: float | None = None

# Phase 3 bundle built before Playwright so checkpoint prompts do not sit behind a silent headless window.
_Phase3PreBrowser = tuple[
    list[tuple[str, str]],
    set[str],
    SafetyBrakes,
    int,
    Path,
    int,
]


def _install_sigint_second_forces_exit():
    """Second Ctrl+C within ~2s calls os._exit(130) (Playwright can delay the first)."""

    def _handler(signum: int, frame: object | None) -> None:  # noqa: ARG001
        global _SIGINT_PREV_TIME
        now = time.monotonic()
        if _SIGINT_PREV_TIME is not None and (now - _SIGINT_PREV_TIME) <= 2.0:
            os._exit(130)
        _SIGINT_PREV_TIME = now
        raise KeyboardInterrupt

    return signal.signal(signal.SIGINT, _handler)


def _cmd_run(args: argparse.Namespace) -> int:
    site = args.site
    if site not in SITE_REGISTRY:
        print(f"Unknown site {site!r}. Known: {', '.join(sorted(SITE_REGISTRY))}", file=sys.stderr)
        return 2

    out = Path(args.out).resolve()
    runner = SITE_REGISTRY[site]

    wall_clock = args.wall_clock_seconds
    if wall_clock is not None and wall_clock <= 0:
        print("--wall-clock-seconds must be positive", file=sys.stderr)
        return 2
    if args.limit is not None and args.limit < 1:
        print("--limit must be >= 1 when set", file=sys.stderr)
        return 2
    if args.max_locations is not None and args.max_locations < 1:
        print("--max-locations must be >= 1 when set", file=sys.stderr)
        return 2

    input_path = Path(args.input).resolve() if args.input else None
    if input_path is not None and not input_path.is_file():
        print(f"--input not found: {input_path}", file=sys.stderr)
        return 2

    rows: list = []

    phase3_pre: _Phase3PreBrowser | None = None
    if input_path is not None:
        placements = load_seed_placements(input_path)
        if args.max_locations is not None:
            placements = placements[: args.max_locations]
        if not placements:
            print("No seed rows to process (check CSV suburb/state).", file=sys.stderr)
            return 2
        seen = read_existing_dedupe_keys(out)
        shared = SafetyBrakes(
            max_consecutive_failures=args.max_consecutive_failures,
            max_retries_per_location=args.max_search_retries,
            wall_clock_budget_s=wall_clock,
        )
        total_seeds = len(placements)
        cp_path = checkpoint_path_for_output(out)
        cp_state = load_seed_checkpoint(cp_path)
        start_index = 0

        if args.fresh:
            delete_seed_checkpoint(cp_path)
        elif cp_state is not None:
            valid = is_checkpoint_valid_for_run(
                cp_state,
                input_path=input_path,
                total_seeds=total_seeds,
            )
            if not valid or cp_state.next_index < 0:
                delete_seed_checkpoint(cp_path)
            elif cp_state.next_index >= total_seeds:
                delete_seed_checkpoint(cp_path)
            elif cp_state.next_index > 0:
                if sys.stdin.isatty():
                    if prompt_full_or_resume(
                        completed=cp_state.next_index,
                        total=total_seeds,
                        next_human=cp_state.next_index + 1,
                        checkpoint_file=cp_path,
                    ):
                        delete_seed_checkpoint(cp_path)
                    else:
                        start_index = cp_state.next_index
                else:
                    start_index = cp_state.next_index
                    explain_non_interactive_resume(
                        next_human=start_index + 1,
                        total=total_seeds,
                    )

        phase3_pre = (placements, seen, shared, total_seeds, cp_path, start_index)

    with sync_playwright() as p:
        print(
            "Browser: headed (Chromium window should appear)"
            if args.headed
            else "Browser: headless (no window — use ./run_scraper.sh run … or pass --headed)",
            file=sys.stderr,
            flush=True,
        )
        browser, _ctx, page = new_browser_context(p, headless=not args.headed)
        try:
            if phase3_pre is not None:
                placements, seen, shared, total_seeds, cp_path, start_index = phase3_pre

                all_rows: list = []
                try:
                    for i in range(start_index, total_seeds):
                        loc, seed = placements[i]
                        shared.check_wall_clock()
                        if i > 0:
                            sleep_random()
                        print(
                            f"Progress: seed {i + 1}/{total_seeds} (of seed CSV) "
                            f"search_seed={seed!r}",
                            flush=True,
                        )
                        try:
                            batch = runner(
                                page,
                                out,
                                location_query=loc,
                                search_seed=seed,
                                limit=args.limit,
                                dedupe_seen=seen,
                                brakes=shared,
                            )
                        except RateLimitedError:
                            raise
                        except Exception as exc:
                            print(
                                f"  skipped: {exc} "
                                f"(checkpoint not advanced — this seed row will retry on resume)",
                                file=sys.stderr,
                                flush=True,
                            )
                            continue

                        all_rows.extend(batch)
                        save_seed_checkpoint(
                            cp_path,
                            SeedCheckpoint(
                                version=CHECKPOINT_VERSION,
                                input_path=str(input_path.resolve()),
                                total_seeds=total_seeds,
                                next_index=i + 1,
                            ),
                        )
                except KeyboardInterrupt:
                    print(
                        "\nInterrupted. Progress saved for completed seeds; "
                        "re-run to choose resume or use --fresh.",
                        file=sys.stderr,
                        flush=True,
                    )
                    return 130

                last_cp = load_seed_checkpoint(cp_path)
                if (
                    last_cp is not None
                    and last_cp.next_index >= total_seeds
                    and is_checkpoint_valid_for_run(
                        last_cp,
                        input_path=input_path,
                        total_seeds=total_seeds,
                    )
                ):
                    delete_seed_checkpoint(cp_path)

                rows = all_rows
            else:
                rows = runner(
                    page,
                    out,
                    location_query=args.location,
                    search_seed=args.seed,
                    limit=args.limit,
                    max_consecutive_failures=args.max_consecutive_failures,
                    max_search_retries=args.max_search_retries,
                    wall_clock_seconds=wall_clock,
                )
        except RateLimitedError as exc:
            print(f"\n{exc}", file=sys.stderr, flush=True)
            print(
                "Exit 3: wait until the site is reachable again, then re-run with the same "
                "command; your seed checkpoint is unchanged for the blocked row.",
                file=sys.stderr,
                flush=True,
            )
            return 3
        finally:
            browser.close()

    n = len(rows)
    print(f"Wrote {n} row(s) to {out}")
    return 0


def _main_impl(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scraper", description="Contact extraction CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="Run a site scraper")
    run_p.add_argument("--site", required=True, help="Site id (e.g. cpa_au)")
    run_p.add_argument("--out", required=True, help="Output CSV path")
    run_p.add_argument(
        "--input",
        default=None,
        metavar="PATH",
        help="Phase 3: CSV with suburb,state,postcode columns; run all seeds in one session",
    )
    run_p.add_argument(
        "--max-locations",
        type=int,
        default=None,
        metavar="N",
        help="With --input: process only the first N seed rows",
    )
    run_p.add_argument(
        "--location",
        default="Sydney NSW, Australia",
        help="Full place string for Google Places field (default: Sydney NSW, Australia); ignored if --input",
    )
    run_p.add_argument(
        "--seed",
        default="Sydney,NSW,2000",
        help="Provenance label stored in CSV search_seed (default: Sydney,NSW,2000); ignored if --input",
    )
    run_p.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Scrape at most N rows per location (default: all rows for each search)",
    )
    run_p.add_argument(
        "--max-consecutive-failures",
        type=int,
        default=10,
        help="Abort after this many row failures in a row (default: 10)",
    )
    run_p.add_argument(
        "--max-search-retries",
        type=int,
        default=3,
        help="Retries for the initial search/autocomplete step (default: 3)",
    )
    run_p.add_argument(
        "--wall-clock-seconds",
        type=float,
        default=None,
        metavar="S",
        help="Stop cleanly after S seconds (optional; shared across all seeds when using --input)",
    )
    run_p.add_argument("--headed", action="store_true", help="Show browser (default: headless)")
    run_p.add_argument(
        "--fresh",
        action="store_true",
        help="With --input: ignore/delete seed checkpoint and start from seed 1",
    )
    run_p.set_defaults(func=_cmd_run)

    args = parser.parse_args(argv)
    return int(args.func(args))


def main(argv: list[str] | None = None) -> int:
    old = _install_sigint_second_forces_exit()
    try:
        return _main_impl(argv)
    finally:
        signal.signal(signal.SIGINT, old)


if __name__ == "__main__":
    raise SystemExit(main())
