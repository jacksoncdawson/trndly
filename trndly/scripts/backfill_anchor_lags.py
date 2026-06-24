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

The output is written **in place** to the current tick's
``ticks/<MONTH>/merged_univariate.parquet`` and ``merged_fingerprint.parquet`` so
`pipelines.monthly.predict` will naturally pick the live month as the anchor on
its next run. Backfilled rows are marked with `source = 'backfill'` (added as a
new category) so they're traceable and can be filtered out later.

Re-running `pipelines.monthly.aggregate` rebuilds the tick's merged cubes from
raw sources and will clobber this backfill — that's intended. The backfill is a
one-time hack to keep the UI honest until real live history accumulates.

``--month`` selects which tick's merged cubes to operate on (default: the most
recent tick on disk, else the current calendar month); ``--target`` is the anchor
month to enable.

Usage:
  python -m scripts.backfill_anchor_lags                   # auto-detects latest isolated live month
  python -m scripts.backfill_anchor_lags --target 2026-05  # explicit anchor
  python -m scripts.backfill_anchor_lags --month 2026-06   # explicit tick
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
    current_tick_month,
    discover_ticks,
    resolve_tick_month,
    tick_merged_path,
)

logger = logging.getLogger(__name__)

LAGS_NEEDED: tuple[int, ...] = (1, 2, 3)  # months prior to anchor we need to fill


def _month_shift(ts: pd.Timestamp, months: int) -> pd.Timestamp:
    """Return ts + months (positive = forward, negative = backward). Normalized
    to the first of the month."""
    return (ts + pd.DateOffset(months=months)).normalize().replace(day=1)


def _detect_isolated_anchor(cube: pd.DataFrame, lags: int = 3) -> pd.Timestamp | None:
    """Return the latest month of REAL data in `cube` that lacks `lags`
    contiguous real prior months — i.e. the month a backfill would enable
    as the anchor. Backfill rows from previous runs are excluded so the
    detection is stable across re-runs.
    """
    real = cube
    if "source" in cube.columns:
        real = cube[cube["source"] != "backfill"]
    months = sorted(pd.to_datetime(real["month"]).unique(), reverse=True)
    months_set = set(months)
    for m in months:
        m = pd.Timestamp(m)
        needed = {_month_shift(m, -k) for k in range(1, lags + 1)}
        if not needed.issubset(months_set):
            return m
    return None


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


def backfill_univariate(target: pd.Timestamp, dry_run: bool, merged_path: Path) -> None:
    cube = pd.read_parquet(merged_path)
    cube["month"] = pd.to_datetime(cube["month"])
    # Drop any prior backfill so re-running is idempotent.
    if "source" in cube.columns and (cube["source"] == "backfill").any():
        prior = (cube["source"] == "backfill").sum()
        logger.info("dropping %d prior backfill rows from merged_univariate", prior)
        cube = cube[cube["source"] != "backfill"].copy()

    # `source` is a Categorical — extend its categories to include 'backfill'.
    if isinstance(cube["source"].dtype, pd.CategoricalDtype):
        cube["source"] = cube["source"].cat.add_categories(["backfill"])

    logger.info("backfilling merged_univariate for anchor=%s", target.strftime("%Y-%m"))
    synthetic = _build_backfill_rows(
        cube,
        anchor=target,
        key_cols=["dimension", "level_id"],
        extra_cols={},
    )
    out = pd.concat([cube, synthetic], ignore_index=True)
    # Normalize so each (synthetic_month, dimension) sums to 1.0 again.
    out = _normalize_shares(out, group_cols=["month", "dimension"])
    out = out.sort_values(["dimension", "level_id", "month"]).reset_index(drop=True)

    if dry_run:
        logger.info("dry-run: would write %d rows (%d synthetic) to %s",
                    len(out), len(synthetic), merged_path)
        return
    out.to_parquet(merged_path, index=False)
    logger.info("wrote %s (%d rows, +%d synthetic backfill)",
                merged_path, len(out), len(synthetic))


def backfill_fingerprint(target: pd.Timestamp, dry_run: bool, merged_path: Path) -> None:
    cube = pd.read_parquet(merged_path)
    cube["month"] = pd.to_datetime(cube["month"])
    if "source" in cube.columns and (cube["source"] == "backfill").any():
        prior = (cube["source"] == "backfill").sum()
        logger.info("dropping %d prior backfill rows from merged_fingerprint", prior)
        cube = cube[cube["source"] != "backfill"].copy()
    if isinstance(cube["source"].dtype, pd.CategoricalDtype):
        cube["source"] = cube["source"].cat.add_categories(["backfill"])

    key_cols = [
        "product_type_id", "gender_id", "color_master_id",
        "graphical_appearance_id", "material_id",
    ]
    logger.info("backfilling merged_fingerprint for anchor=%s", target.strftime("%Y-%m"))
    synthetic = _build_backfill_rows(
        cube,
        anchor=target,
        key_cols=key_cols,
        extra_cols={"avg_price": np.nan},
    )
    out = pd.concat([cube, synthetic], ignore_index=True)
    out = _normalize_shares(out, group_cols=["month"])
    out = out.sort_values(key_cols + ["month"]).reset_index(drop=True)

    if dry_run:
        logger.info("dry-run: would write %d rows (%d synthetic) to %s",
                    len(out), len(synthetic), merged_path)
        return
    out.to_parquet(merged_path, index=False)
    logger.info("wrote %s (%d rows, +%d synthetic backfill)",
                merged_path, len(out), len(synthetic))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target", type=str, default=None,
        help="Anchor month to enable as 'YYYY-MM'. Default: auto-detect the latest "
             "live month that lacks 3 prior lags.",
    )
    parser.add_argument(
        "--month", type=str, default=None,
        help="Tick month ('YYYY-MM') whose merged cubes to operate on. Default: "
             "the most recent tick on disk, else the current calendar month.",
    )
    parser.add_argument("--dry-run", action="store_true", help="print plan, don't write")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.month:
        tick_month = resolve_tick_month(args.month)
    else:
        ticks = discover_ticks()
        tick_month = resolve_tick_month(ticks[-1].name) if ticks else current_tick_month()
    logger.info("operating on tick %s", tick_month.strftime("%Y-%m"))

    merged_uv_path = tick_merged_path(tick_month, "univariate")
    merged_fp_path = tick_merged_path(tick_month, "fingerprint")

    if args.target:
        target = pd.Timestamp(args.target + "-01")
    else:
        # Auto-detect on the univariate cube; the same anchor should be the
        # right one for the fingerprint cube too (both produced by the same
        # monthly tick).
        uv = pd.read_parquet(merged_uv_path)
        target = _detect_isolated_anchor(uv)
        if target is None:
            logger.warning("no isolated anchor detected — nothing to backfill")
            return 0
        logger.info("auto-detected target anchor: %s", target.strftime("%Y-%m"))

    backfill_univariate(target, args.dry_run, merged_uv_path)
    backfill_fingerprint(target, args.dry_run, merged_fp_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
