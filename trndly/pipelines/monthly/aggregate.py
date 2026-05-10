"""Merge historical + live cubes into ``merged_*.parquet``.

Was notebook ``1b_scrape_aggregate_live.ipynb`` (sections 6 + 7).

Reads:
    data/processed/historical_{fingerprint,univariate}.parquet  (immutable)
    data/processed/live_{fingerprint,univariate}_<YYYY-MM>.parquet  (per-month)

Writes:
    data/processed/merged_fingerprint.parquet
    data/processed/merged_univariate.parquet

Always rebuilds — no ``.bak`` because ``historical_*`` is never overwritten.
Dedup keys:
    fingerprint: (month, *FINGERPRINT_COLS, source) keep='last'
    univariate:  (month, dimension, level_id, source)  keep='last'

Usage:
    python -m pipelines.monthly.aggregate
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from pipelines.paths import (
    HISTORICAL_FINGERPRINT_PARQUET,
    HISTORICAL_UNIVARIATE_PARQUET,
    MERGED_FINGERPRINT_PARQUET,
    MERGED_UNIVARIATE_PARQUET,
    discover_live_fingerprint_parquets,
    discover_live_univariate_parquets,
)
from pipelines.cube_slicing import FINGERPRINT_COLS

logger = logging.getLogger(__name__)


def _merge_one(
    *,
    historical_path: Path,
    live_paths: list[Path],
    dup_cols: list[str],
    out_path: Path,
    label: str,
) -> int:
    """Concat historical + every live parquet, dedup, write. Return row count."""
    if not historical_path.exists():
        raise FileNotFoundError(
            f"missing {label} historical at {historical_path} — run notebook 1 first."
        )

    hist = pd.read_parquet(historical_path)
    hist["month"] = pd.to_datetime(hist["month"]).dt.as_unit("ns")

    if live_paths:
        live_frames = []
        for p in live_paths:
            f = pd.read_parquet(p)
            f["month"] = pd.to_datetime(f["month"]).dt.as_unit("ns")
            live_frames.append(f)
            logger.info("  loaded %d rows from %s", len(f), p.name)
        live = pd.concat(live_frames, ignore_index=True)
    else:
        logger.info("no live %s parquets found — merged cube will be historical-only.", label)
        live = pd.DataFrame(columns=hist.columns)

    merged = pd.concat([hist, live], ignore_index=True)
    merged = merged.drop_duplicates(subset=dup_cols, keep="last")
    merged.to_parquet(out_path, index=False)
    logger.info("wrote %s | rows=%d", out_path, len(merged))
    return len(merged)


def run_aggregate() -> dict[str, int]:
    """Merge historical + live cubes for both fingerprint and univariate.

    Returns a {target: row_count} summary.
    """
    logger.info("aggregate: merging fingerprint cubes")
    fp_rows = _merge_one(
        historical_path=HISTORICAL_FINGERPRINT_PARQUET,
        live_paths=discover_live_fingerprint_parquets(),
        dup_cols=["month", *FINGERPRINT_COLS, "source"],
        out_path=MERGED_FINGERPRINT_PARQUET,
        label="fingerprint",
    )
    logger.info("aggregate: merging univariate cubes")
    uv_rows = _merge_one(
        historical_path=HISTORICAL_UNIVARIATE_PARQUET,
        live_paths=discover_live_univariate_parquets(),
        dup_cols=["month", "dimension", "level_id", "source"],
        out_path=MERGED_UNIVARIATE_PARQUET,
        label="univariate",
    )
    return {"merged_fingerprint": fp_rows, "merged_univariate": uv_rows}


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    summary = run_aggregate()
    logger.info("aggregate summary: %s", summary)


if __name__ == "__main__":
    main()
