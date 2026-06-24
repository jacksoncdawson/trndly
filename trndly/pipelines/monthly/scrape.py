"""Run all retail scrapers sequentially.

Replaces the prior bash orchestrator at ``pipelines/collectors/run_all.sh``.
Each scraper is invoked as a subprocess (their ``main()`` functions own
``sys.argv`` and ``asyncio.run`` so importing in-process risks event-loop
collisions).

Building the live cubes from the scraped ``items_*.csv`` is a SEPARATE stage
(``build_cube``) in the monthly tick — it used to run inside this stage but is
now its own step between ``scrape`` and ``aggregate``. Run it after scraping:
``python -m pipelines.monthly build_cube``.

Outputs:
    data/raw/items/items_<retailer>_<YYYY-MM>.csv  (one per retailer, per month)

Wall-clock with --enrich-pdp (default):
    gap            ~17s   (~5,200 rows)
    uniqlo         ~30s   (~3,000 rows)
    american_eagle ~3min  (Playwright JWT bootstrap + Akamai-throttled fan-out)
    hollister      ~5min  (~21,000 rows; ~250s for ~2,200 PDP enrichments)
    ─────────
    total          ~9-10 min

Drop ``--no-enrich-pdp`` for ~3x speedup at the cost of ~14% material unknown.

Usage:
    python -m pipelines.monthly.scrape
    python -m pipelines.monthly.scrape --retailers gap,uniqlo
    python -m pipelines.monthly.scrape --no-enrich-pdp
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from pipelines.paths import RAW_ITEMS_DIR, items_csv_path_for

logger = logging.getLogger(__name__)

SCRAPERS: tuple[str, ...] = ("gap", "uniqlo", "american_eagle", "hollister")

COLLECTORS_DIR: Path = Path(__file__).resolve().parents[1] / "collectors"

# Scrape-completeness guard (the 2026-06 Hollister incident: a 403/marker-drift
# made the scraper write a header-only CSV and exit 0, so a retailer that was
# ~66% of the catalog silently vanished and the tick published anyway). A scrape
# yielding < this fraction of the prior month's rows aborts the tick. Override
# via env for a legitimately large seasonal drop; 0 rows is ALWAYS fatal.
MIN_SCRAPE_RETAIN_FRAC: float = float(
    os.environ.get("TRNDLY_MIN_SCRAPE_RETAIN_FRAC", "0.6")
)
_ITEMS_MONTH_RE = re.compile(r"^items_.+_(\d{4}-\d{2})\.csv$")


def _count_data_rows(path: Path) -> int:
    """Number of data rows (lines minus the header) in an items CSV; 0 if absent
    or header-only."""
    if not path.exists():
        return 0
    with path.open(newline="") as fh:
        n_lines = sum(1 for _ in fh)
    return max(0, n_lines - 1)


def _prior_month_rows(retailer: str, current_month: str) -> tuple[str, int] | None:
    """Row count of ``retailer``'s most recent items CSV strictly before
    ``current_month`` (``'YYYY-MM'``), or ``None`` if there is no prior month."""
    best_month: str | None = None
    best_path: Path | None = None
    for p in RAW_ITEMS_DIR.glob(f"items_{retailer}_*.csv"):
        if p.name.endswith("_partial.csv"):
            continue  # in-progress StreamingItemWriter resume file
        m = _ITEMS_MONTH_RE.match(p.name)
        if not m:
            continue
        month = m.group(1)
        if month >= current_month:
            continue
        if best_month is None or month > best_month:
            best_month, best_path = month, p
    if best_path is None:
        return None
    return best_month, _count_data_rows(best_path)


def _check_scrape_completeness(retailer: str) -> None:
    """Fail the tick if a retailer's freshly-scraped CSV is empty or collapsed
    vs the prior month — a silent header-only scrape must never reach publish."""
    out_path = items_csv_path_for(retailer)
    month_m = _ITEMS_MONTH_RE.match(out_path.name)
    current_month = month_m.group(1) if month_m else ""
    rows = _count_data_rows(out_path)

    if rows == 0:
        raise RuntimeError(
            f"{retailer}: scrape produced 0 data rows ({out_path.name} is "
            f"header-only) — aborting the tick. A retailer scrape must not "
            f"silently collapse to empty (the 2026-06 Hollister incident); "
            f"investigate the scraper, then re-run."
        )

    prior = _prior_month_rows(retailer, current_month)
    if prior is not None:
        prev_month, prev_rows = prior
        if prev_rows > 0 and rows < MIN_SCRAPE_RETAIN_FRAC * prev_rows:
            raise RuntimeError(
                f"{retailer}: scrape produced {rows} rows, below "
                f"{MIN_SCRAPE_RETAIN_FRAC:.0%} of the prior month "
                f"({prev_month}: {prev_rows}) — likely a partial/blocked "
                f"scrape. Aborting the tick; investigate, then re-run (or set "
                f"TRNDLY_MIN_SCRAPE_RETAIN_FRAC if the drop is real)."
            )
        logger.info(
            "    %s completeness OK: %d rows (prior %s: %d)",
            retailer, rows, prev_month, prev_rows,
        )
    else:
        logger.info(
            "    %s completeness OK: %d rows (no prior month to compare)",
            retailer, rows,
        )


def _run_one(script: str, *, extra_args: list[str] | None = None) -> None:
    cmd = [sys.executable, str(COLLECTORS_DIR / script), *(extra_args or [])]
    logger.info(">>> %s", " ".join(cmd))
    t0 = time.time()
    proc = subprocess.run(cmd, check=False)
    dt = time.time() - t0
    if proc.returncode != 0:
        raise RuntimeError(f"{script} exited {proc.returncode} after {dt:.1f}s")
    logger.info("    done in %.1fs", dt)


def run_scrape(
    retailers: list[str] | None = None,
    *,
    enrich_pdp: bool = True,
) -> None:
    """Run named retailers' scrapers.

    Args:
        retailers: subset of SCRAPERS to run. None or empty means all.
        enrich_pdp: passed as ``--enrich-pdp`` (True) or ``--no-enrich-pdp``.

    Building the live cubes is the separate ``build_cube`` stage — run
    ``pipelines.monthly.build_cube`` (or the ``run`` chain) afterwards.
    """
    selected = list(retailers) if retailers else list(SCRAPERS)
    unknown = set(selected) - set(SCRAPERS)
    if unknown:
        raise ValueError(
            f"unknown retailer(s): {sorted(unknown)}; known: {SCRAPERS}"
        )

    extra = ["--enrich-pdp"] if enrich_pdp else ["--no-enrich-pdp"]

    logger.info("scrape start: retailers=%s enrich_pdp=%s", selected, enrich_pdp)
    overall_t0 = time.time()
    for r in selected:
        _run_one(f"{r}_scraper.py", extra_args=extra)
        # Fail loud on a collapsed/empty scrape before it can poison the cube.
        _check_scrape_completeness(r)

    logger.info("scrape end: total %.1fs", time.time() - overall_t0)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Run all retail scrapers. Build the live cubes separately via "
            "`python -m pipelines.monthly build_cube`."
        )
    )
    p.add_argument(
        "--retailers",
        default=",".join(SCRAPERS),
        help=f"comma-separated subset of {SCRAPERS}; default: all",
    )
    pdp = p.add_mutually_exclusive_group()
    pdp.add_argument(
        "--enrich-pdp",
        dest="enrich_pdp",
        action="store_true",
        default=True,
        help="enable PDP material enrichment (default; full coverage but slower)",
    )
    pdp.add_argument(
        "--no-enrich-pdp",
        dest="enrich_pdp",
        action="store_false",
        help="skip PDP material enrichment for ~3x speedup (~14%% material unknown)",
    )
    return p.parse_args()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    args = parse_args()
    retailers = [r.strip() for r in args.retailers.split(",") if r.strip()]
    run_scrape(
        retailers=retailers,
        enrich_pdp=args.enrich_pdp,
    )


if __name__ == "__main__":
    main()
