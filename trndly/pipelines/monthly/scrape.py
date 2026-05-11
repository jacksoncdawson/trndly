"""Run all retail scrapers sequentially, then build the live cubes.

Replaces the prior bash orchestrator at ``pipelines/collectors/run_all.sh``.
Each scraper is invoked as a subprocess (their ``main()`` functions own
``sys.argv`` and ``asyncio.run`` so importing in-process risks event-loop
collisions). After all named scrapers finish, ``build_live_cube.py`` is
subprocessed to aggregate ``items_*.csv`` → ``live_*_<YYYY-MM>.parquet``.

Outputs:
    data/raw/items/items_<retailer>.csv  (one per retailer)
    data/processed/live_fingerprint_<YYYY-MM>.parquet
    data/processed/live_univariate_<YYYY-MM>.parquet

Wall-clock with --enrich-pdp (default):
    gap            ~17s   (~5,200 rows)
    uniqlo         ~30s   (~3,000 rows)
    american_eagle ~3min  (Playwright JWT bootstrap + Akamai-throttled fan-out)
    hollister      ~5min  (~21,000 rows; ~250s for ~2,200 PDP enrichments)
    build_cube     ~3s
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
import subprocess
import sys
import time
from pathlib import Path

logger = logging.getLogger(__name__)

SCRAPERS: tuple[str, ...] = ("gap", "uniqlo", "american_eagle", "hollister")

COLLECTORS_DIR: Path = Path(__file__).resolve().parents[1] / "collectors"


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
    skip_build_cube: bool = False,
) -> None:
    """Run named retailers' scrapers, then build_live_cube.

    Args:
        retailers: subset of SCRAPERS to run. None or empty means all.
        enrich_pdp: passed as ``--enrich-pdp`` (True) or ``--no-enrich-pdp``.
        skip_build_cube: skip the trailing ``build_live_cube.py`` invocation.
            Useful when iterating on a single scraper.
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

    if not skip_build_cube:
        _run_one("build_live_cube.py")

    logger.info("scrape end: total %.1fs", time.time() - overall_t0)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Run all retail scrapers + build_live_cube."
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
    p.add_argument(
        "--skip-build-cube",
        action="store_true",
        help="skip the trailing build_live_cube.py invocation",
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
        skip_build_cube=args.skip_build_cube,
    )


if __name__ == "__main__":
    main()
