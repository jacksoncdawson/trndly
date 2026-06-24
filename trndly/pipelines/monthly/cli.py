"""Single CLI for the monthly tick.

Usage:
    python -m pipelines.monthly run                  # full chain (current tick month)
    python -m pipelines.monthly scrape               # individual stages
    python -m pipelines.monthly build_cube
    python -m pipelines.monthly aggregate
    python -m pipelines.monthly features
    python -m pipelines.monthly train
    python -m pipelines.monthly evaluate
    python -m pipelines.monthly predict
    python -m pipelines.monthly publish

    python -m pipelines.monthly run --month 2026-06     # explicit tick month
    python -m pipelines.monthly run --force             # re-run a completed tick
    python -m pipelines.monthly run --skip-scrape       # already have items_*.csv
    python -m pipelines.monthly run --skip-build-cube   # already have live_*.parquet

Stage order in ``run``:
    scrape → build_cube → aggregate → features → train → evaluate → predict → publish

The tick is idempotent per month: ``run`` is a no-op when the tick's ``_SUCCESS``
marker exists, unless ``--force``. On a successful full run the manifest is
written and ``_SUCCESS`` is touched LAST (so a crash never leaves a tick marked
complete). Individual stage subcommands always run for the current tick month
(no idempotency guard).

Non-zero exit on any stage failure.
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import time
from datetime import datetime, timezone

from pipelines.paths import (
    resolve_tick_month,
    tick_is_complete,
    tick_manifest_json,
    tick_success_marker,
)

logger = logging.getLogger(__name__)

# Each stage's run_<stage>(month) returns a summary dict (None for scrape /
# build_cube, which operate on shared inputs and ignore the tick month).
# Imports are lazy inside ``_call_stage`` so a bad import in one stage doesn't
# prevent invoking the others (e.g., a partial venv).


def _call_stage(name: str, month) -> object:
    if name == "scrape":
        from pipelines.monthly.scrape import run_scrape
        return run_scrape()
    if name == "build_cube":
        from pipelines.collectors.build_live_cube import run_build_cube
        return run_build_cube()
    if name == "aggregate":
        from pipelines.monthly.aggregate import run_aggregate
        return run_aggregate(month)
    if name == "features":
        from pipelines.monthly.features import run_features
        return run_features(month)
    if name == "train":
        from pipelines.monthly.train import run_train
        return run_train(month)
    if name == "evaluate":
        from pipelines.monthly.evaluate import run_evaluate
        return run_evaluate(month)
    if name == "predict":
        from pipelines.monthly.predict import run_predict
        return run_predict(month)
    if name == "publish":
        from pipelines.monthly.publish import run_publish
        return run_publish(month)
    raise ValueError(f"unknown stage: {name!r}")


FULL_ORDER: tuple[str, ...] = (
    "scrape", "build_cube", "aggregate", "features",
    "train", "evaluate", "predict", "publish",
)


def _git_sha() -> str | None:
    """Best-effort short git SHA of the working tree (None if unavailable)."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5, check=True,
        )
        return out.stdout.strip() or None
    except Exception:
        return None


def _write_manifest(month, summary: dict) -> None:
    """Write the tick manifest (git sha, per-stage summaries, anchor, decisions)."""
    predict_summary = summary.get("predict")
    evaluate_summary = summary.get("evaluate")
    publish_summary = summary.get("publish")

    anchor = None
    if isinstance(publish_summary, dict):
        anchor = publish_summary.get("anchor_month")

    decisions = None
    if isinstance(evaluate_summary, dict):
        decisions = evaluate_summary.get("decisions")

    manifest = {
        "month": resolve_tick_month(month).strftime("%Y-%m"),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_sha": _git_sha(),
        "anchor_month": anchor,
        "champion_decisions": decisions,
        "stage_summaries": {
            k: v for k, v in summary.items() if not isinstance(v, BaseException)
        },
    }
    path = tick_manifest_json(month)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(manifest, f, indent=2, default=str)
    logger.info("wrote %s", path)


def run_full(
    month=None,
    *,
    force: bool = False,
    skip_scrape: bool = False,
    skip_build_cube: bool = False,
) -> dict:
    """Run all stages in order for tick ``month`` (default: current tick month).

    No-op when the tick's ``_SUCCESS`` marker already exists, unless ``force``.
    On a fully-successful run the manifest is written and ``_SUCCESS`` touched
    LAST; on any stage exception ``_SUCCESS`` is NOT written (the error
    propagates). Returns {stage: summary} (or ``{"skipped": month}`` on a no-op).
    """
    month = resolve_tick_month(month)
    month_str = month.strftime("%Y-%m")

    if tick_is_complete(month) and not force:
        logger.info(
            "tick %s already complete; pass --force to re-run", month_str
        )
        return {"skipped": month_str}

    # We are (re-)running this tick: clear any prior _SUCCESS marker up front.
    # Stages overwrite the checkpoint in place, so on a --force re-run a crash
    # mid-chain would otherwise leave a half-overwritten tick still marked
    # complete (the guard + serving would then trust it). _SUCCESS is re-touched
    # LAST, only after every stage succeeds.
    tick_success_marker(month).unlink(missing_ok=True)

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
        logger.info("=== stage: %s (tick %s) ===", stage, month_str)
        t0 = time.time()
        summary[stage] = _call_stage(stage, month)
        logger.info("=== stage %s done in %.1fs ===", stage, time.time() - t0)

    # All stages succeeded: write the manifest, then touch _SUCCESS LAST.
    _write_manifest(month, summary)
    marker = tick_success_marker(month)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.touch()
    logger.info("tick %s complete in %.1fs (wrote %s)",
                month_str, time.time() - overall_t0, marker)
    return summary


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="trndly monthly tick CLI")
    sub = p.add_subparsers(dest="command", required=True)

    full = sub.add_parser("run", help="run the full chain end-to-end")
    full.add_argument(
        "--month", type=str, default=None,
        help="tick month as 'YYYY-MM' (default: current calendar month)",
    )
    full.add_argument(
        "--force", action="store_true",
        help="re-run even if this tick's _SUCCESS marker exists",
    )
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
                month=args.month,
                force=args.force,
                skip_scrape=args.skip_scrape,
                skip_build_cube=args.skip_build_cube,
            )
        else:
            # Individual stages run for the current tick month, no guard.
            _call_stage(args.command, resolve_tick_month(None))
    except Exception:
        logger.exception("monthly tick failed at command=%s", args.command)
        sys.exit(1)


if __name__ == "__main__":
    main()
