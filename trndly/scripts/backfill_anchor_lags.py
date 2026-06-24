"""Seasonal backfill for an isolated live month so it can be the anchor.

⚠ THIS IS A SYNTHETIC-DATA STOPGAP. ⚠

The forecaster requires 4 contiguous months (anchor + 3 lags) before a month
can be the prediction anchor. The shipped data has a ~5-year gap between the
historical block (2018-10 → 2020-08) and the first live scrape (2026-05), so
out of the box the anchor is forced back to 2020-08. The chart labels then
read "NOW" against May 2020 numbers — visually correct but temporally lying.

This script manufactures the three missing prior months for the latest live
month so it can serve as the anchor. For each (dim, level_id) and each 5-D
fingerprint that exists in the live snapshot:

  lag_value = share_t_live * mean_over_years(hist[lag_month] / hist[live_month])

Concretely, for 2026-05 (the typical case today):
  lag3 (Feb 2026)  = share_t_2026-05 * mean({2019,2020}: hist[Feb] / hist[May])
  lag2 (Mar 2026)  = share_t_2026-05 * mean({2019,2020}: hist[Mar] / hist[May])
  lag1 (Apr 2026)  = share_t_2026-05 * mean({2019,2020}: hist[Apr] / hist[May])

The ratios capture historical seasonality (the Feb→May progression);
multiplying by current share_t pegs them to the present absolute scale. We
explicitly do NOT use historical absolute values because the historical
catalog (H&M Kaggle) lives on a different scale than the multi-retailer live
scrape — directly substituting them would manufacture huge spurious "rising"
forecasts everywhere.

When historical doesn't carry a key (or the corresponding live anchor month),
we fall back to a global ratio across all keys for that month. When even
that isn't available, we clone share_t backwards (assume flat past).

PERSISTENCE (ADR 0002): this is a ONE-TIME generator. The synthetic rows
(marked ``source = 'backfill'``) are written to a STANDALONE artifact —
``data/processed/backfill_{univariate,fingerprint}.parquet`` — which
``pipelines.monthly.aggregate`` then UNIONS into every tick's merged cube
(historical ∪ live ∪ backfill). The priors are no longer patched in place into a
tick's ``merged_*`` (which ``aggregate`` rebuilt and clobbered each tick — the
root cause of the 2026-06 anchor=2020-08 incident). They self-retire once 4 real
contiguous live months exist. Pegged to the first live scrape (e.g. 2026-05).

``--target`` is the month to peg the priors to (default: auto-detect the start of
the latest contiguous live run).

Usage:
  python -m scripts.backfill_anchor_lags                   # auto-detect the peg month
  python -m scripts.backfill_anchor_lags --target 2026-05  # explicit peg month
  python -m scripts.backfill_anchor_lags --dry-run         # print plan, don't write
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# Allow running as `python scripts/backfill_anchor_lags.py` from the package root.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pipelines.paths import (
    BACKFILL_FINGERPRINT_PARQUET,
    BACKFILL_UNIVARIATE_PARQUET,
    HISTORICAL_FINGERPRINT_PARQUET,
    HISTORICAL_UNIVARIATE_PARQUET,
    discover_live_fingerprint_parquets,
    discover_live_univariate_parquets,
)

logger = logging.getLogger(__name__)

LAGS_NEEDED: tuple[int, ...] = (1, 2, 3)  # months prior to anchor we need to fill


def _month_shift(ts: pd.Timestamp, months: int) -> pd.Timestamp:
    """Return ts + months (positive = forward, negative = backward). Normalized
    to the first of the month."""
    return (ts + pd.DateOffset(months=months)).normalize().replace(day=1)


def _detect_backfill_anchor(cube: pd.DataFrame, lags: int = 3) -> pd.Timestamp | None:
    """Month to peg the synthetic priors to: the **start of the latest
    contiguous run of real months**, when that run's latest month still lacks
    ``lags`` contiguous real priors.

    Pegging at the run-start (e.g. 2026-05, the first live scrape after the
    ~5-year gap) makes that month AND every later contiguous month eligible as
    the anchor, and the priors self-retire once ``lags`` real months accumulate.
    Returns ``None`` when the latest real month already has ``lags`` real priors
    (no backfill needed) or the cube has no real rows. Backfill rows are excluded
    so detection is stable across re-runs.
    """
    real = cube
    if "source" in cube.columns:
        real = cube[cube["source"] != "backfill"]
    months_set = {pd.Timestamp(m) for m in pd.to_datetime(real["month"]).unique()}
    if not months_set:
        return None
    latest = max(months_set)
    # If the latest month already has `lags` contiguous real priors, the cube
    # anchors correctly on its own — nothing to backfill.
    if {_month_shift(latest, -k) for k in range(1, lags + 1)}.issubset(months_set):
        return None
    # Walk back to the first month of the contiguous run ending at `latest`.
    start = latest
    while _month_shift(start, -1) in months_set:
        start = _month_shift(start, -1)
    return start


def _seasonal_ratios(
    cube: pd.DataFrame,
    key_cols: list[str],
    *,
    target_month_num: int,
    lag_month_num: int,
) -> tuple[pd.DataFrame, float]:
    """Compute per-key ratios `hist[lag_month] / hist[target_month]`,
    averaged across all historical years that carry both values.

    Returns:
        (per_key_ratios, global_ratio_fallback). per_key_ratios is a frame
        with key_cols + a 'ratio' column. global_ratio_fallback is the mean
        across all keys, used when a specific key didn't have both months.
    """
    hist = cube[cube["source"] == "historical"].copy()
    hist["_mo"] = pd.to_datetime(hist["month"]).dt.month
    target_rows = hist[hist["_mo"] == target_month_num]
    lag_rows    = hist[hist["_mo"] == lag_month_num]
    if target_rows.empty or lag_rows.empty:
        return (
            pd.DataFrame(columns=key_cols + ["ratio"]),
            float("nan"),
        )

    # Mean across years for each key + month.
    tg = (
        target_rows.groupby(key_cols, as_index=False, observed=False)["share_articles"]
        .mean()
        .rename(columns={"share_articles": "_target"})
    )
    lg = (
        lag_rows.groupby(key_cols, as_index=False, observed=False)["share_articles"]
        .mean()
        .rename(columns={"share_articles": "_lag"})
    )
    merged = tg.merge(lg, on=key_cols, how="inner")
    merged = merged[merged["_target"] > 0].copy()
    merged["ratio"] = merged["_lag"] / merged["_target"]
    per_key = merged[key_cols + ["ratio"]]

    # Global fallback ratio: simple mean of all matching keys' ratios.
    if per_key.empty:
        return per_key, float("nan")
    return per_key, float(per_key["ratio"].mean())


def _build_backfill_rows(
    cube: pd.DataFrame,
    *,
    anchor: pd.Timestamp,
    key_cols: list[str],
    extra_cols: dict[str, object],
) -> pd.DataFrame:
    """For a single anchor month, build synthetic rows for anchor-1, -2, -3
    using rescaled historical seasonality (or share_t-clone fallback).

    `extra_cols` is a literal column → value mapping for columns the cube
    has but that we don't compute (e.g. `avg_price = NaN` for fingerprint).
    """
    live_rows = cube[cube["month"] == anchor].copy()
    if live_rows.empty:
        raise RuntimeError(
            f"target month {anchor.strftime('%Y-%m')} has no rows in the cube — "
            "nothing to backfill against. Pass --target YYYY-MM with a month "
            "that actually exists in merged_*.parquet (typically the latest "
            "live scrape)."
        )

    anchor_month_num = int(pd.Timestamp(anchor).month)
    backfilled: list[pd.DataFrame] = []

    for lag in LAGS_NEEDED:
        lag_dt = _month_shift(anchor, -lag)
        lag_month_num = int(lag_dt.month)
        per_key, global_ratio = _seasonal_ratios(
            cube, key_cols,
            target_month_num=anchor_month_num,
            lag_month_num=lag_month_num,
        )

        rows = live_rows.merge(per_key, on=key_cols, how="left")
        # Per-key ratio if available, else global, else 1.0 (clone share_t).
        rows["ratio"] = rows["ratio"].fillna(global_ratio).fillna(1.0)
        rows["share_articles"] = rows["share_articles"] * rows["ratio"]
        rows["month"] = lag_dt
        rows["month_of_year"] = lag_month_num
        # New source category so backfilled rows are traceable.
        rows["source"] = "backfill"
        for col, val in extra_cols.items():
            rows[col] = val
        rows = rows.drop(columns=["ratio"])

        # n_articles is a required-non-null column. Use a synthetic count
        # proportional to the share so downstream consumers that don't care
        # about exact counts still get sensible values.
        if "n_articles" in rows.columns:
            rows["n_articles"] = (rows["share_articles"] * 10000).round().astype("int64")

        logger.info(
            "  lag=%d (target=%s): per-key ratios for %d/%d rows; "
            "global fallback ratio=%.4f",
            lag, lag_dt.strftime("%Y-%m"),
            per_key.shape[0] if not per_key.empty else 0,
            len(rows),
            global_ratio if pd.notna(global_ratio) else float("nan"),
        )
        backfilled.append(rows[cube.columns.tolist()])

    return pd.concat(backfilled, ignore_index=True)


def _normalize_shares(cube: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    """Rescale `share_articles` within each `group_cols` group to sum to 1.0.

    Applies only to rows with source='backfill' (we don't touch historical
    or live data). This keeps the cube's per-month share-sum invariant
    intact for the synthetic months.
    """
    out = cube.copy()
    mask = out["source"] == "backfill"
    if not mask.any():
        return out
    sums = (
        out[mask].groupby(group_cols, observed=False)["share_articles"]
        .transform("sum")
    )
    out.loc[mask, "share_articles"] = out.loc[mask, "share_articles"] / sums.values
    return out


def _load_working_cube(historical_path: Path, live_paths: list[Path]) -> pd.DataFrame:
    """Concat historical + every live parquet into the working cube the seasonal
    ratios + anchor rows are derived from. ``source`` is coerced to object so the
    'backfill' value can be added without Categorical gymnastics."""
    hist = pd.read_parquet(historical_path)
    hist["month"] = pd.to_datetime(hist["month"])
    frames = [hist]
    for p in live_paths:
        f = pd.read_parquet(p)
        f["month"] = pd.to_datetime(f["month"])
        frames.append(f)
    cube = pd.concat(frames, ignore_index=True)
    if "source" in cube.columns:
        cube["source"] = cube["source"].astype("object")
    return cube


def generate_univariate(target: pd.Timestamp, dry_run: bool) -> None:
    """Build the synthetic univariate priors for ``target`` and write ONLY those
    rows to the standalone backfill artifact (ADR 0002). ``aggregate`` unions it."""
    cube = _load_working_cube(
        HISTORICAL_UNIVARIATE_PARQUET, discover_live_univariate_parquets()
    )
    logger.info("generating univariate backfill priors for anchor=%s", target.strftime("%Y-%m"))
    synthetic = _build_backfill_rows(
        cube, anchor=target, key_cols=["dimension", "level_id"], extra_cols={}
    )
    # Renormalize so each (synthetic_month, dimension) sums to 1.0.
    synthetic = _normalize_shares(synthetic, group_cols=["month", "dimension"])
    synthetic = synthetic.sort_values(["dimension", "level_id", "month"]).reset_index(drop=True)

    if dry_run:
        logger.info("dry-run: would write %d synthetic rows to %s",
                    len(synthetic), BACKFILL_UNIVARIATE_PARQUET)
        return
    synthetic.to_parquet(BACKFILL_UNIVARIATE_PARQUET, index=False)
    logger.info("wrote %s (%d synthetic backfill rows, months %s)",
                BACKFILL_UNIVARIATE_PARQUET, len(synthetic),
                sorted({pd.Timestamp(m).strftime("%Y-%m") for m in synthetic["month"].unique()}))


def generate_fingerprint(target: pd.Timestamp, dry_run: bool) -> None:
    """Build the synthetic fingerprint priors for ``target`` and write ONLY those
    rows to the standalone backfill artifact (ADR 0002)."""
    cube = _load_working_cube(
        HISTORICAL_FINGERPRINT_PARQUET, discover_live_fingerprint_parquets()
    )
    key_cols = [
        "product_type_id", "gender_id", "color_master_id",
        "graphical_appearance_id", "material_id",
    ]
    logger.info("generating fingerprint backfill priors for anchor=%s", target.strftime("%Y-%m"))
    synthetic = _build_backfill_rows(
        cube, anchor=target, key_cols=key_cols, extra_cols={"avg_price": np.nan}
    )
    synthetic = _normalize_shares(synthetic, group_cols=["month"])
    synthetic = synthetic.sort_values(key_cols + ["month"]).reset_index(drop=True)

    if dry_run:
        logger.info("dry-run: would write %d synthetic rows to %s",
                    len(synthetic), BACKFILL_FINGERPRINT_PARQUET)
        return
    synthetic.to_parquet(BACKFILL_FINGERPRINT_PARQUET, index=False)
    logger.info("wrote %s (%d synthetic backfill rows, months %s)",
                BACKFILL_FINGERPRINT_PARQUET, len(synthetic),
                sorted({pd.Timestamp(m).strftime("%Y-%m") for m in synthetic["month"].unique()}))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target", type=str, default=None,
        help="Month to peg the synthetic priors to, as 'YYYY-MM' (typically the "
             "first live scrape after the historical gap). Default: auto-detect "
             "the start of the latest contiguous live run.",
    )
    parser.add_argument("--dry-run", action="store_true", help="print plan, don't write")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.target:
        target = pd.Timestamp(args.target + "-01")
    else:
        # Auto-detect on the univariate working cube (historical + live); the
        # same peg is right for fingerprint (same months).
        uv = _load_working_cube(
            HISTORICAL_UNIVARIATE_PARQUET, discover_live_univariate_parquets()
        )
        target = _detect_backfill_anchor(uv)
        if target is None:
            logger.warning(
                "no backfill needed — the latest live month already has 3 "
                "contiguous real priors (or no live data found)."
            )
            return 0
        logger.info("auto-detected peg month: %s", target.strftime("%Y-%m"))

    generate_univariate(target, args.dry_run)
    generate_fingerprint(target, args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
