"""Forecast inference helpers for the trndly serving layer.

Two explicit modes, each driven by user-supplied IDs (no text resolution):

  - ``forecast_fingerprint(dimensions, deps)`` — caller supplies all five
    fingerprint IDs (``product_type_id``, ``gender_id``, ``color_master_id``,
    ``graphical_appearance_id``, ``material_id``); returns the
    horizon-1..6 share forecast averaged across matching cube rows.
  - ``forecast_univariate(dimension, level_id, deps)`` — caller supplies
    one ``(dimension, level_id)`` pair; returns the horizon-1..6 share
    forecast for that single series.

Models are loaded from the MLflow registry first, falling back to local
``*.joblib`` artifacts produced by notebook ``3_*``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from pandas.tseries.offsets import DateOffset

from pipelines.paths import (
    FINGERPRINT_MODEL_JOBLIB,
    MERGED_FINGERPRINT_PARQUET,
    TRAINING_RUN_JSON,
    UNIVARIATE_MODEL_JOBLIB,
)

FINGERPRINT_COLS = [
    "product_type_id",
    "gender_id",
    "color_master_id",
    "graphical_appearance_id",
    "material_id",
]

UNIVARIATE_DIMENSIONS = {
    "product_type",
    "product_group",
    "graphical_appearance",
    "color_master",
    "color_spectrum",
    "gender",
    "material",
}

HORIZONS = [f"y_h{h}" for h in range(1, 7)]


def load_feature_contract(path: str | Path | None = None) -> dict[str, Any]:
    p = Path(path or TRAINING_RUN_JSON)
    with open(p) as f:
        return json.load(f)


def load_merged_fingerprint(path: str | Path | None = None) -> pd.DataFrame:
    p = Path(path or MERGED_FINGERPRINT_PARQUET)
    df = pd.read_parquet(p)
    df["month"] = pd.to_datetime(df["month"]).dt.as_unit("ns")
    return df.sort_values("month")


def month_shift(ts: pd.Timestamp, k: int) -> pd.Timestamp:
    return ts + DateOffset(months=k)


# --------------------------------------------------------------------------- #
# Cube slicing                                                                  #
# --------------------------------------------------------------------------- #

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
) -> tuple[pd.DataFrame, list[tuple[Any, ...]]]:
    """Return feature matrix rows for every fingerprint matching ``dimensions``
    at ``anchor_month``. Each row needs t-3..t-1 lags present in the cube.
    """

    cube = cube.copy()
    cube["month"] = pd.to_datetime(cube["month"]).dt.as_unit("ns")

    mask_anchor = cube["month"] == anchor_month
    mask_fp = _fingerprint_mask(cube, dimensions)
    slice_anchor = cube.loc[mask_anchor & mask_fp].drop_duplicates(subset=FINGERPRINT_COLS)

    feature_cols_expected = load_feature_contract()["fingerprint_feature_cols"]

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
) -> pd.Series | None:
    """Pull one calendar-strict row from the merged univariate cube."""

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

    contract = load_feature_contract()
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


# --------------------------------------------------------------------------- #
# Model loading                                                                 #
# --------------------------------------------------------------------------- #

@dataclass
class ForecastDeps:
    fingerprint_model: Any
    univariate_model: Any | None
    cube_fp: pd.DataFrame
    cube_uni: pd.DataFrame | None


def load_forecast_pair(
    *,
    tracking_uri: str | None,
    fingerprint_uri: str,
    univariate_uri: str,
    load_univariate: bool,
) -> tuple[Any, Any | None, str]:
    """Try MLflow registry URIs, then sklearn ``*.joblib`` from notebook ``3_*``."""

    import joblib
    import mlflow

    last_exc: BaseException | None = None
    uri = (tracking_uri or "").strip()
    if uri:
        try:
            mlflow.set_tracking_uri(uri)
            fp_model = mlflow.pyfunc.load_model(fingerprint_uri)
            uni_model = (
                mlflow.pyfunc.load_model(univariate_uri) if load_univariate else None
            )
            return fp_model, uni_model, "mlflow-registry"
        except Exception as exc:
            last_exc = exc

    fp_path = FINGERPRINT_MODEL_JOBLIB
    uni_path = UNIVARIATE_MODEL_JOBLIB
    if fp_path.exists():
        fp_model = joblib.load(fp_path)
        uni_model = (
            joblib.load(uni_path) if load_univariate and uni_path.exists() else None
        )
        src = "joblib:fingerprint_model.joblib"
        if load_univariate and uni_model is None:
            src += " (univariate joblib missing)"
        return fp_model, uni_model, src

    detail = repr(last_exc) if last_exc else "MLFLOW_TRACKING_URI unset"
    err = RuntimeError(
        "Could not load forecast models from MLflow "
        f"({detail}) or from missing file {fp_path}."
    )
    if last_exc:
        raise err from last_exc
    raise err


# --------------------------------------------------------------------------- #
# Inference utilities                                                           #
# --------------------------------------------------------------------------- #

def _prediction_frame(X: pd.DataFrame) -> pd.DataFrame:
    """Coerce numeric columns to float32 so MLflow pyfunc input schemas match
    pandas defaults."""
    out = X.copy()
    for col in out.columns:
        if np.issubdtype(out[col].dtype, np.number):
            out[col] = out[col].astype(np.float32)
    return out


def pick_anchor_month(user_month: int | None, cube: pd.DataFrame) -> pd.Timestamp:
    latest = pd.Timestamp(cube["month"].max())
    if user_month is None:
        return latest
    candidates = cube[cube["month_of_year"] == user_month]["month"]
    if candidates.empty:
        return latest
    return pd.Timestamp(candidates.max())


# --------------------------------------------------------------------------- #
# Public entrypoints                                                            #
# --------------------------------------------------------------------------- #

def forecast_fingerprint(
    dimensions: dict[str, int],
    deps: ForecastDeps,
    *,
    reference_month: int | None = None,
) -> dict[str, Any]:
    """Predict the next 6 months of catalog share for a fingerprint.

    ``dimensions`` must include all five fingerprint id columns
    (``product_type_id``, ``gender_id``, ``color_master_id``,
    ``graphical_appearance_id``, ``material_id``). If multiple cube rows
    match (e.g., one per source), predictions are averaged across them.
    """

    missing = [c for c in FINGERPRINT_COLS if c not in dimensions]
    if missing:
        raise ValueError(f"missing fingerprint dimensions: {missing}")
    extras = set(dimensions) - set(FINGERPRINT_COLS)
    if extras:
        raise ValueError(f"unexpected dimensions: {sorted(extras)}")

    anchor = pick_anchor_month(reference_month, deps.cube_fp)
    X, keys = build_fingerprint_inference_rows(
        deps.cube_fp, anchor_month=anchor, dimensions=dimensions
    )

    out: dict[str, Any] = {
        "dimensions": {k: int(v) for k, v in dimensions.items()},
        "anchor_month": anchor.isoformat(),
        "reference_month_of_year_used": int(
            deps.cube_fp.loc[deps.cube_fp["month"] == anchor, "month_of_year"].iloc[0]
        ),
        "fingerprint_matches": len(keys),
        "fingerprint_keys_sample": [list(k) for k in keys[:5]],
        "horizons": HORIZONS,
        "forecast": None,
    }

    if X.empty:
        out["error"] = (
            "No cube coverage at the anchor month for this fingerprint, or "
            "insufficient lag history (need t-3..t)."
        )
        return out

    raw = deps.fingerprint_model.predict(_prediction_frame(X))
    arr = np.asarray(raw, dtype=float)
    mean_forecast = arr.mean(axis=0).tolist()
    out["forecast"] = dict(zip(HORIZONS, mean_forecast))
    return out


def forecast_univariate(
    dimension: str,
    level_id: int,
    deps: ForecastDeps,
    *,
    reference_month: int | None = None,
) -> dict[str, Any]:
    """Predict the next 6 months of catalog share for one
    ``(dimension, level_id)`` series."""

    if dimension not in UNIVARIATE_DIMENSIONS:
        raise ValueError(
            f"unknown dimension {dimension!r}; expected one of {sorted(UNIVARIATE_DIMENSIONS)}"
        )

    if deps.univariate_model is None:
        raise RuntimeError("univariate model is not loaded")
    if deps.cube_uni is None:
        raise RuntimeError("univariate cube is not loaded")

    anchor = pick_anchor_month(reference_month, deps.cube_uni)
    row = build_univariate_inference_row(
        deps.cube_uni, anchor_month=anchor, dimension=dimension, level_id=int(level_id)
    )

    out: dict[str, Any] = {
        "dimension": dimension,
        "level_id": int(level_id),
        "anchor_month": anchor.isoformat(),
        "horizons": HORIZONS,
        "forecast": None,
    }

    if row is None:
        out["error"] = (
            f"No cube coverage for ({dimension}, level_id={level_id}) at the "
            "anchor month, or insufficient lag history (need t-3..t)."
        )
        return out

    raw = deps.univariate_model.predict(_prediction_frame(row.to_frame().T))
    out["forecast"] = dict(zip(HORIZONS, np.asarray(raw, dtype=float).ravel().tolist()))
    return out
