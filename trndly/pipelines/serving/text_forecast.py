"""Natural-language → fingerprint/univariate inference helpers.

Resolve free-text queries against ``lookup.csv`` + ``merged_fingerprint.parquet``,
then call MLflow-loaded sklearn/pyfunc forecast models produced by notebooks ``3_*`` / ``4_*``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
from pandas.tseries.offsets import DateOffset

from pipelines.training.paths import (
    LOOKUP_CSV,
    MERGED_FINGERPRINT_PARQUET,
    PROCESSED_DATA_DIR,
    TRAINING_RUN_JSON,
)

FINGERPRINT_COLS = [
    "product_type_id",
    "gender_id",
    "color_master_id",
    "graphical_appearance_id",
    "material_id",
]

LOOKUP_CATEGORIES = {
    "product_type",
    "product_group",
    "graphical_appearance",
    "color_master",
    "color_spectrum",
    "gender",
    "material",
}

SYNONYMS: dict[str, str] = {
    "pants": "trousers",
    "jeans": "denim",
    "tee": "t-shirt",
    "tshirt": "t-shirt",
    "trouser": "trousers",
}

COMPOSITE_PHRASES: list[tuple[str, str, str]] = [
    ("all over pattern", "graphical_appearance", "All over pattern"),
    ("colour blocking", "graphical_appearance", "Colour blocking"),
    ("solid color", "graphical_appearance", "Solid"),
]


def load_lookup_csv(path: str | Path | None = None) -> pd.DataFrame:
    p = Path(path or LOOKUP_CSV)
    return pd.read_csv(p)


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


def build_lookup_name_index(lookup: pd.DataFrame) -> dict[str, list[tuple[str, int]]]:
    idx: dict[str, list[tuple[str, int]]] = {}
    for _, row in lookup.iterrows():
        cat = str(row["category"])
        if cat not in LOOKUP_CATEGORIES:
            continue
        key = str(row["name"]).strip().lower()
        idx.setdefault(key, []).append((cat, int(row["id"])))
    return idx


def tokenize(query: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", query.lower()) if t]


def resolve_query_to_dimensions(query: str, lookup: pd.DataFrame) -> dict[str, int]:
    """Map text tokens to at most one id per lookup ``category``."""

    idx = build_lookup_name_index(lookup)
    resolved: dict[str, int] = {}
    q = query.lower().strip()

    for phrase, cat, canonical_name in COMPOSITE_PHRASES:
        if phrase in q:
            hit = lookup[(lookup["category"] == cat) & (lookup["name"] == canonical_name)]
            if not hit.empty:
                resolved[cat] = int(hit.iloc[0]["id"])

    tokens = tokenize(query)
    tokens = [SYNONYMS.get(t, t) for t in tokens]

    for tok in tokens:
        hits = idx.get(tok)
        if not hits:
            continue
        for cat, lid in hits:
            if cat not in resolved:
                resolved[cat] = lid

    return resolved


def fingerprint_subset_mask(cube: pd.DataFrame, resolved: dict[str, int]) -> pd.Series:
    mask = pd.Series(True, index=cube.index)
    for cat, lid in resolved.items():
        if cat == "product_group":
            continue
        col = f"{cat}_id"
        if col not in cube.columns:
            continue
        mask &= cube[col] == lid
    return mask


def dimension_key_for_fallback(resolved: dict[str, int]) -> tuple[str, int] | None:
    priority = (
        "product_type",
        "product_group",
        "material",
        "color_master",
        "graphical_appearance",
        "color_spectrum",
        "gender",
    )
    for cat in priority:
        if cat in resolved:
            return cat, int(resolved[cat])
    return None


def build_fingerprint_inference_rows(
    cube: pd.DataFrame,
    *,
    anchor_month: pd.Timestamp,
    resolved_partial: dict[str, int],
) -> tuple[pd.DataFrame, list[tuple[Any, ...]]]:
    """Return feature matrix rows for every fingerprint matching ``resolved_partial`` at ``anchor_month``."""

    cube = cube.copy()
    cube["month"] = pd.to_datetime(cube["month"]).dt.as_unit("ns")

    mask_anchor = cube["month"] == anchor_month
    mask_fp = fingerprint_subset_mask(cube, resolved_partial)
    slice_anchor = cube.loc[mask_anchor & mask_fp].drop_duplicates(subset=FINGERPRINT_COLS)

    feature_cols_expected = load_feature_contract()["fingerprint_feature_cols"]

    rows: list[dict[str, float]] = []
    keys: list[tuple[Any, ...]] = []

    for _, fp_row in slice_anchor.iterrows():
        key_tuple = tuple(int(fp_row[c]) for c in FINGERPRINT_COLS)
        sub = cube[np.logical_and.reduce([cube[c] == fp_row[c] for c in FINGERPRINT_COLS])].sort_values(
            "month"
        )
        idx = sub.set_index("month")
        if anchor_month not in idx.index:
            continue
        share = idx["share_articles"]
        price = idx["avg_price"]

        need_prev = [month_shift(anchor_month, -k) for k in range(1, 4)]
        if not all(m in share.index for m in need_prev):
            continue

        rows.append(
            {
                "month_of_year": float(idx.loc[anchor_month, "month_of_year"]),
                "share_t": float(share.loc[anchor_month]),
                "avg_price_t": float(price.loc[anchor_month]),
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
    """Pull one calendar-strict row from ``merged_univariate.parquet``-style long cube."""

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


@dataclass
class ForecastDeps:
    fingerprint_model: Any
    univariate_model: Any | None
    cube_fp: pd.DataFrame
    cube_uni: pd.DataFrame | None
    lookup: pd.DataFrame


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

    fp_path = PROCESSED_DATA_DIR / "fingerprint_model.joblib"
    uni_path = PROCESSED_DATA_DIR / "univariate_model.joblib"
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


def _prediction_frame(X: pd.DataFrame) -> pd.DataFrame:
    """Coerce numeric columns to float32 so MLflow pyfunc input schemas match pandas defaults."""

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


def forecast_from_text(
    query: str,
    deps: ForecastDeps,
    *,
    reference_month_of_year: int | None = None,
) -> dict[str, Any]:
    """Main entrypoint used by FastAPI + notebook 5."""

    resolved = resolve_query_to_dimensions(query, deps.lookup)

    anchor = pick_anchor_month(reference_month_of_year, deps.cube_fp)

    X_fp, fp_keys = build_fingerprint_inference_rows(
        deps.cube_fp, anchor_month=anchor, resolved_partial=resolved
    )

    horizons = [f"y_h{h}" for h in range(1, 7)]
    out: dict[str, Any] = {
        "query": query.strip(),
        "resolved_dimensions": resolved,
        "anchor_month": anchor.isoformat(),
        "reference_month_of_year_used": int(deps.cube_fp.loc[deps.cube_fp["month"] == anchor, "month_of_year"].iloc[0]),
        "mode": None,
        "fingerprint_matches": len(fp_keys),
        "fingerprint_keys_sample": [list(k) for k in fp_keys[:5]],
        "forecast": None,
        "horizons": horizons,
    }

    if not X_fp.empty:
        raw = deps.fingerprint_model.predict(_prediction_frame(X_fp))
        arr = np.asarray(raw, dtype=float)
        mean_forecast = arr.mean(axis=0).tolist()
        out["mode"] = "fingerprint"
        out["forecast"] = dict(zip(horizons, mean_forecast))
        return out

    if deps.univariate_model is not None and deps.cube_uni is not None:
        fb = dimension_key_for_fallback(resolved)
        if fb:
            dim, lid = fb
            row = build_univariate_inference_row(
                deps.cube_uni, anchor_month=anchor, dimension=dim, level_id=lid
            )
            if row is not None:
                raw = deps.univariate_model.predict(_prediction_frame(row.to_frame().T))
                out["mode"] = f"univariate:{dim}"
                out["forecast"] = dict(zip(horizons, np.asarray(raw, dtype=float).ravel().tolist()))
                out["fallback_dimension"] = dim
                out["fallback_level_id"] = lid
                return out

    out["mode"] = "unresolved"
    out["error"] = (
        "Could not resolve tokens to lookup IDs with cube coverage at the anchor month, "
        "or insufficient history for lags. Try adding product type / material / color tokens."
    )
    return out
