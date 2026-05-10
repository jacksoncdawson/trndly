"""Build calendar-strict training tables from the merged cubes.

Was notebook ``2_feature_processing.ipynb``.

Reads:
    data/processed/merged_univariate.parquet
    data/processed/merged_fingerprint.parquet

Writes:
    data/processed/training_univariate.parquet
    data/processed/training_fingerprint.parquet
    data/processed/training_run.json   (sample-weight + split contract for nb 3)

Eligibility:
    For anchor month ``t``, require cube rows on every calendar month in
    ``t-3 … t+6`` (10 months: three lags, anchor, six horizons).
    No reindex / zero-fill — rows that don't qualify are dropped.

Features:
    month_of_year, share_t, share_lag1, share_lag2, share_lag3
Targets:
    y_h1, y_h2, y_h3, y_h4, y_h5, y_h6  (share_articles at t+1..t+6)

Splits:
    split_group ∈ {train, val, holdout} via tail ranks on each table's
    distinct ``anchor_month`` values (defaults K=2, J=2).
Weights:
    sample_weight = sqrt(n_articles_at_anchor), capped at SAMPLE_WEIGHT_MAX.
    Weights are metadata, not in FEATURE_COLS.

Usage:
    python -m pipelines.monthly.features
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from pandas.tseries.offsets import DateOffset

from pipelines.paths import (
    MERGED_FINGERPRINT_PARQUET,
    MERGED_UNIVARIATE_PARQUET,
    PROCESSED_DIR,
    TRAINING_FINGERPRINT_PARQUET,
    TRAINING_RUN_JSON,
    TRAINING_UNIVARIATE_PARQUET,
)

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Contract                                                                     #
# --------------------------------------------------------------------------- #

HORIZONS: list[int] = list(range(1, 7))
LAG_PAST_MONTHS: int = 3

SPLIT_K_HOLDOUT: int = 2
SPLIT_J_VAL: int = 2
SAMPLE_WEIGHT_MAX: float = 100.0

FINGERPRINT_COLS: list[str] = [
    "product_type_id",
    "gender_id",
    "color_master_id",
    "graphical_appearance_id",
    "material_id",
]

UNIVARIATE_FEATURE_COLS: list[str] = ["month_of_year", "share_t"] + [
    f"share_lag{i}" for i in range(1, LAG_PAST_MONTHS + 1)
]
UNIVARIATE_TARGET_COLS: list[str] = [f"y_h{h}" for h in HORIZONS]
UNIVARIATE_META: list[str] = [
    "anchor_month", "dimension", "level_id", "source",
    "split_group", "sample_weight", "n_articles",
]

FINGERPRINT_FEATURE_COLS: list[str] = ["month_of_year", "share_t"] + [
    f"share_lag{i}" for i in range(1, LAG_PAST_MONTHS + 1)
]
FINGERPRINT_TARGET_COLS: list[str] = [f"y_h{h}" for h in HORIZONS]


def _fingerprint_meta() -> list[str]:
    return ["anchor_month", *FINGERPRINT_COLS, "source", "split_group", "sample_weight", "n_articles"]


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #

def month_shift(m: pd.Timestamp, k: int) -> pd.Timestamp:
    return m + DateOffset(months=k)


def assign_split_group(df: pd.DataFrame, anchor_col: str = "anchor_month") -> pd.DataFrame:
    out = df.copy()
    months = sorted(out[anchor_col].unique())
    n = len(months)
    K, J = SPLIT_K_HOLDOUT, SPLIT_J_VAL
    while K + J >= n and (K > 0 or J > 0):
        if J > 0:
            J -= 1
        elif K > 0:
            K -= 1
    holdout = set(months[-K:]) if K else set()
    val = set(months[-(K + J): -K]) if J and (K + J) <= n else set()

    def _sg(m: pd.Timestamp) -> str:
        if m in holdout:
            return "holdout"
        if m in val:
            return "val"
        return "train"

    out["split_group"] = out[anchor_col].map(_sg).astype("category")
    return out


def build_calendar_strict_rows(
    cube: pd.DataFrame,
    key_cols: list[str],
    *,
    share_col: str = "share_articles",
    n_col: str = "n_articles",
    month_col: str = "month",
    extra_at_t: dict[str, str] | None = None,
) -> tuple[pd.DataFrame, dict]:
    """Iterate cube groups and emit one training row per (key_cols, anchor)
    with full t-3..t+6 history. Returns (rows_frame, stats)."""
    stats = {"n_groups": 0, "n_candidates": 0, "n_rows": 0}
    cube = cube.copy()
    cube[month_col] = pd.to_datetime(cube[month_col]).dt.as_unit("ns")
    rows: list[dict] = []
    extra_at_t = extra_at_t or {}

    for keys, grp in cube.groupby(key_cols, observed=True, sort=False):
        stats["n_groups"] += 1
        grp = grp.sort_values(month_col)
        idx = grp.set_index(month_col)
        if idx.index.has_duplicates:
            raise ValueError(f"Duplicate {month_col} in group {keys}")
        months_sorted = list(idx.index.sort_values())
        share = idx[share_col]
        n_art = idx[n_col]
        moy = idx["month_of_year"]
        source = idx["source"].iloc[0]

        for t in months_sorted:
            stats["n_candidates"] += 1
            need = [month_shift(t, k) for k in range(-LAG_PAST_MONTHS, 7)]
            if not all(m in share.index for m in need):
                continue
            rec: dict = {}
            if len(key_cols) == 1:
                rec[key_cols[0]] = keys
            else:
                for c, v in zip(key_cols, keys):
                    rec[c] = v
            rec["anchor_month"] = t
            rec["source"] = source
            rec["month_of_year"] = int(moy.loc[t])
            rec["share_t"] = float(share.loc[t])
            for i in range(1, LAG_PAST_MONTHS + 1):
                rec[f"share_lag{i}"] = float(share.loc[month_shift(t, -i)])
            for h in HORIZONS:
                rec[f"y_h{h}"] = float(share.loc[month_shift(t, h)])
            rec[n_col] = int(n_art.loc[t])
            for out_c, cube_c in extra_at_t.items():
                rec[out_c] = float(idx[cube_c].loc[t])
            rows.append(rec)

    stats["n_rows"] = len(rows)
    if not rows:
        return pd.DataFrame(), stats
    return pd.DataFrame(rows), stats


# --------------------------------------------------------------------------- #
# Stage drivers                                                                #
# --------------------------------------------------------------------------- #

def _build_univariate(uv_cube: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    rows, stats = build_calendar_strict_rows(
        uv_cube, key_cols=["dimension", "level_id"], extra_at_t=None
    )
    if rows.empty:
        return rows, stats

    rows["sample_weight"] = (
        np.sqrt(np.maximum(rows["n_articles"].astype(float), 0.0))
        .clip(upper=SAMPLE_WEIGHT_MAX)
    )
    rows = assign_split_group(rows, "anchor_month")
    rows = rows[UNIVARIATE_META + UNIVARIATE_FEATURE_COLS + UNIVARIATE_TARGET_COLS]
    return rows, stats


def _build_fingerprint(fp_cube: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    rows, stats = build_calendar_strict_rows(
        fp_cube, key_cols=FINGERPRINT_COLS, extra_at_t=None
    )
    if rows.empty:
        return rows, stats

    rows["sample_weight"] = (
        np.sqrt(np.maximum(rows["n_articles"].astype(float), 0.0))
        .clip(upper=SAMPLE_WEIGHT_MAX)
    )
    rows = assign_split_group(rows, "anchor_month")
    rows = rows[_fingerprint_meta() + FINGERPRINT_FEATURE_COLS + FINGERPRINT_TARGET_COLS]
    return rows, stats


def run_features() -> dict[str, dict]:
    """Build training tables for both models. Returns summary stats."""
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    logger.info("features: loading merged univariate cube")
    uv_cube = pd.read_parquet(MERGED_UNIVARIATE_PARQUET)
    logger.info("features: building univariate training rows")
    univariate, uni_stats = _build_univariate(uv_cube)
    univariate.to_parquet(TRAINING_UNIVARIATE_PARQUET, index=False)
    logger.info(
        "features: wrote %s | rows=%d | stats=%s",
        TRAINING_UNIVARIATE_PARQUET, len(univariate), uni_stats,
    )

    logger.info("features: loading merged fingerprint cube")
    fp_cube = pd.read_parquet(MERGED_FINGERPRINT_PARQUET)
    logger.info("features: building fingerprint training rows")
    fingerprint, fp_stats = _build_fingerprint(fp_cube)
    fingerprint.to_parquet(TRAINING_FINGERPRINT_PARQUET, index=False)
    logger.info(
        "features: wrote %s | rows=%d | stats=%s",
        TRAINING_FINGERPRINT_PARQUET, len(fingerprint), fp_stats,
    )

    meta = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "calendar_strict": {
            "past_months": LAG_PAST_MONTHS,
            "future_months": 6,
            "description": "rows require cube rows t-3..t+6 (lags at t-1,t-2,t-3)",
        },
        "split_defaults": {
            "K_holdout_tail": SPLIT_K_HOLDOUT,
            "J_val_before_holdout": SPLIT_J_VAL,
            "note": "per-table tail ranks on anchor_month",
        },
        "sample_weight": {
            "formula": "min(sqrt(n_articles_at_anchor), cap)",
            "cap": SAMPLE_WEIGHT_MAX,
        },
        "inputs": {
            "univariate": str(MERGED_UNIVARIATE_PARQUET),
            "fingerprint": str(MERGED_FINGERPRINT_PARQUET),
        },
        "outputs": {
            "univariate_training": {
                "path": str(TRAINING_UNIVARIATE_PARQUET),
                "rows": int(len(univariate)),
                "cols": list(univariate.columns) if not univariate.empty else [],
            },
            "fingerprint_training": {
                "path": str(TRAINING_FINGERPRINT_PARQUET),
                "rows": int(len(fingerprint)),
                "cols": list(fingerprint.columns) if not fingerprint.empty else [],
            },
        },
        "part_a_stats": uni_stats,
        "part_b_stats": fp_stats,
        "univariate_feature_cols": UNIVARIATE_FEATURE_COLS,
        "univariate_target_cols": UNIVARIATE_TARGET_COLS,
        "fingerprint_feature_cols": FINGERPRINT_FEATURE_COLS,
        "fingerprint_target_cols": FINGERPRINT_TARGET_COLS,
    }
    with open(TRAINING_RUN_JSON, "w") as f:
        json.dump(meta, f, indent=2)
    logger.info("features: wrote %s", TRAINING_RUN_JSON)

    return {"univariate_stats": uni_stats, "fingerprint_stats": fp_stats}


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    summary = run_features()
    logger.info("features summary: %s", summary)


if __name__ == "__main__":
    main()
