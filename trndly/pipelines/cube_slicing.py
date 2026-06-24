"""Cube-slicing helpers shared between aggregation, prediction, and serving.

Was previously colocated in ``pipelines/serving/forecast.py`` (when serving
ran live model inference). Now that serving is a read-only layer over the
precomputed predictions parquet, these helpers are only used by the monthly
tick (``pipelines.monthly.predict``) and the merge stage (``pipelines.monthly.aggregate``).

Public API:
    FINGERPRINT_COLS              — canonical 5-D fingerprint id column order
    HORIZONS                      — ['y_h1', ..., 'y_h6']
    UNIVARIATE_DIMENSIONS         — set of dimension names served by the long
                                    univariate cube
    month_shift(ts, k)            — add k calendar months to a Timestamp
    pick_anchor_month(user, cube) — choose anchor month for inference
    build_fingerprint_inference_rows(cube, *, anchor_month, dimensions, feature_contract_path)
    build_univariate_inference_row(cube_long, *, anchor_month, dimension, level_id, feature_contract_path)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from pandas.tseries.offsets import DateOffset

FINGERPRINT_COLS: list[str] = [
    "product_type_id",
    "gender_id",
    "color_master_id",
    "graphical_appearance_id",
    "material_id",
]

UNIVARIATE_DIMENSIONS: set[str] = {
    "product_type",
    "product_group",
    "graphical_appearance",
    "color_master",
    "color_spectrum",
    "gender",
    "material",
}

HORIZONS: list[str] = [f"y_h{h}" for h in range(1, 7)]


def load_feature_contract(path: str | Path) -> dict[str, Any]:
    """Load a feature contract (``training_run.json``) from an explicit path."""
    with open(Path(path)) as f:
        return json.load(f)


def load_merged_fingerprint(path: str | Path) -> pd.DataFrame:
    """Load a merged fingerprint cube from an explicit path, month-normalized."""
    df = pd.read_parquet(Path(path))
    df["month"] = pd.to_datetime(df["month"]).dt.as_unit("ns")
    return df.sort_values("month")


def month_shift(ts: pd.Timestamp, k: int) -> pd.Timestamp:
    return ts + DateOffset(months=k)


def pick_anchor_month(user_month: int | None, cube: pd.DataFrame) -> pd.Timestamp:
    """Latest month-of-year matching ``user_month``, else latest month overall."""
    latest = pd.Timestamp(cube["month"].max())
    if user_month is None:
        return latest
    candidates = cube[cube["month_of_year"] == user_month]["month"]
    if candidates.empty:
        return latest
    return pd.Timestamp(candidates.max())


def _fingerprint_mask(cube: pd.DataFrame, dimensions: dict[str, int]) -> pd.Series:
    mask = pd.Series(True, index=cube.index)
    for col, lid in dimensions.items():
        if col not in cube.columns:
            continue
        mask &= cube[col] == lid
    return mask


def build_fingerprint_inference_rows(
    cube: pd.DataFrame,
    *,
    anchor_month: pd.Timestamp,
    dimensions: dict[str, int],
    feature_contract_path: str | Path,
) -> tuple[pd.DataFrame, list[tuple[Any, ...]]]:
    """Return feature rows for every fingerprint matching ``dimensions`` at
    ``anchor_month``. Each row needs t-3..t-1 lags present in the cube.

    Pass ``dimensions={}`` to match all fingerprints at the anchor month.
    ``feature_contract_path`` points at the tick's ``training_run.json``.
    """
    cube = cube.copy()
    cube["month"] = pd.to_datetime(cube["month"]).dt.as_unit("ns")

    mask_anchor = cube["month"] == anchor_month
    mask_fp = _fingerprint_mask(cube, dimensions)
    slice_anchor = cube.loc[mask_anchor & mask_fp].drop_duplicates(subset=FINGERPRINT_COLS)

    feature_cols_expected = load_feature_contract(feature_contract_path)["fingerprint_feature_cols"]

    rows: list[dict[str, float]] = []
    keys: list[tuple[Any, ...]] = []

    for _, fp_row in slice_anchor.iterrows():
        key_tuple = tuple(int(fp_row[c]) for c in FINGERPRINT_COLS)
        sub = cube[
            np.logical_and.reduce([cube[c] == fp_row[c] for c in FINGERPRINT_COLS])
        ].sort_values("month")
        idx = sub.set_index("month")
        if anchor_month not in idx.index:
            continue
        share = idx["share_articles"]

        need_prev = [month_shift(anchor_month, -k) for k in range(1, 4)]
        if not all(m in share.index for m in need_prev):
            continue

        rows.append(
            {
                "month_of_year": float(idx.loc[anchor_month, "month_of_year"]),
                "share_t": float(share.loc[anchor_month]),
                "share_lag1": float(share.loc[need_prev[0]]),
                "share_lag2": float(share.loc[need_prev[1]]),
                "share_lag3": float(share.loc[need_prev[2]]),
            }
        )
        keys.append(key_tuple)

    if not rows:
        return pd.DataFrame(columns=feature_cols_expected), []

    X = pd.DataFrame(rows)[feature_cols_expected]
    return X, keys


def build_univariate_inference_row(
    cube_long: pd.DataFrame,
    *,
    anchor_month: pd.Timestamp,
    dimension: str,
    level_id: int,
    feature_contract_path: str | Path,
) -> pd.Series | None:
    """Pull one calendar-strict feature row from the long univariate cube.

    ``feature_contract_path`` points at the tick's ``training_run.json``.
    """
    sub = cube_long[
        (cube_long["dimension"] == dimension)
        & (cube_long["level_id"] == level_id)
        & (cube_long["month"] <= anchor_month)
    ].sort_values("month")

    if sub.empty:
        return None

    idx = sub.set_index("month")
    share = idx["share_articles"]
    need_prev = [month_shift(anchor_month, -k) for k in range(1, 4)]
    if anchor_month not in share.index or not all(m in share.index for m in need_prev):
        return None

    contract = load_feature_contract(feature_contract_path)
    fc = contract["univariate_feature_cols"]

    return pd.Series(
        {
            "month_of_year": float(idx.loc[anchor_month, "month_of_year"]),
            "share_t": float(share.loc[anchor_month]),
            "share_lag1": float(share.loc[need_prev[0]]),
            "share_lag2": float(share.loc[need_prev[1]]),
            "share_lag3": float(share.loc[need_prev[2]]),
        }
    )[fc]
