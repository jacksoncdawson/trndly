"""Single CLI for the monthly tick.

Usage:
    python -m pipelines.monthly run                  # full chain
    python -m pipelines.monthly scrape               # individual stages
    python -m pipelines.monthly build_cube
    python -m pipelines.monthly aggregate
    python -m pipelines.monthly features
    python -m pipelines.monthly train
    python -m pipelines.monthly evaluate
    python -m pipelines.monthly predict

    python -m pipelines.monthly run --skip-scrape       # already have items_*.csv
    python -m pipelines.monthly run --skip-build-cube   # already have live_*.parquet

Stage order in ``run``:
    scrape → build_cube → aggregate → features → train → evaluate → predict

Non-zero exit on any stage failure.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from typing import Callable

logger = logging.getLogger(__name__)

# Each stage's run_<stage>() returns a summary dict (None for scrape).
# Imports are lazy inside ``_call_stage`` so a bad import in one stage
# doesn't prevent invoking the others (e.g., a partial venv).


def _call_stage(name: str) -> object:
    if name == "scrape":
        from pipelines.monthly.scrape import run_scrape
        return run_scrape()
    if name == "build_cube":
        from pipelines.collectors.build_live_cube import run_build_cube
        return run_build_cube()
    if name == "aggregate":
        from pipelines.monthly.aggregate import run_aggregate
        return run_aggregate()
    if name == "features":
        from pipelines.monthly.features import run_features
        return run_features()
    if name == "train":
        from pipelines.monthly.train import run_train
        return run_train()
    if name == "evaluate":
        from pipelines.monthly.evaluate import run_evaluate
        return run_evaluate()
    if name == "predict":
        from pipelines.monthly.predict import run_predict
        return run_predict()
    raise ValueError(f"unknown stage: {name!r}")


FULL_ORDER: tuple[str, ...] = (
    "scrape", "build_cube", "aggregate", "features", "train", "evaluate", "predict",
)


def run_full(*, skip_scrape: bool = False, skip_build_cube: bool = False) -> dict:
    """Run all stages in order. Returns {stage: summary}."""
    skips: set[str] = set()
    if skip_scrape:
        skips.add("scrape")
    if skip_build_cube:
        skips.add("build_cube")

    summary: dict[str, object] = {}
    overall_t0 = time.time()
    for stage in FULL_ORDER:
        if stage in skips:
            logger.info("skipping stage: %s", stage)
            continue
        logger.info("=== stage: %s ===", stage)
        t0 = time.time()
        summary[stage] = _call_stage(stage)
        logger.info("=== stage %s done in %.1fs ===", stage, time.time() - t0)
    logger.info("monthly tick complete in %.1fs", time.time() - overall_t0)
    return summary


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="trndly monthly tick CLI")
    sub = p.add_subparsers(dest="command", required=True)

    full = sub.add_parser("run", help="run the full chain end-to-end")
    full.add_argument(
        "--skip-scrape", action="store_true",
        help="skip the scrape stage (use existing items_*.csv)",
    )
    full.add_argument(
        "--skip-build-cube", action="store_true",
        help="skip the build_cube stage (use existing live_*_<YYYY-MM>.parquet)",
    )

    for stage in FULL_ORDER:
        sub.add_parser(stage, help=f"run only the {stage} stage")

    return p.parse_args()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    args = parse_args()
    try:
        if args.command == "run":
            run_full(
                skip_scrape=args.skip_scrape,
                skip_build_cube=args.skip_build_cube,
            )
        else:
            _call_stage(args.command)
    except Exception:
        logger.exception("monthly tick failed at command=%s", args.command)
        sys.exit(1)


if __name__ == "__main__":
    main()
