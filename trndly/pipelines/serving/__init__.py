"""Shared serving logic — single source of truth for the lag-join + response shapes.

Lifted out of ``backend/services/scheduleServer.py`` so that BOTH the static
publisher (``pipelines.monthly.publish``) and the (slimmed) local dev server
import the same code. The serve-time lag attach (``share_lag3/2/1/t``) is the
#1 parity risk — ``contracts.py`` does NOT validate those columns — so it lives
here once and is locked by the golden-file test (``tests/serving/test_publish.py``).

Pydantic schemas are the response contracts; ``load_bundle`` + the ``build_*``
functions turn the predictions parquets + merged cubes + lookup.csv into those
shapes. Nothing here depends on FastAPI — the server wraps these with HTTP
semantics (404/503); the publisher serializes them to JSON.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pandas as pd
from pydantic import BaseModel

from pipelines.contracts import (
    validate_predictions_fingerprint_frame,
    validate_predictions_univariate_frame,
)
from pipelines.paths import (
    LOOKUP_CSV,
    latest_successful_tick,
    tick_merged_path,
    tick_predictions_path,
)

logger = logging.getLogger(__name__)

# 5-D fingerprint key, in the canonical order used everywhere (server lookup,
# fingerprint.json index keys, and the frontend's client-side 5-D lookup).
FINGERPRINT_KEY_COLS: list[str] = [
    "product_type_id",
    "gender_id",
    "color_master_id",
    "graphical_appearance_id",
    "material_id",
]


# --------------------------------------------------------------------------- #
# Schemas (response contracts)                                                  #
# --------------------------------------------------------------------------- #

class HealthResponse(BaseModel):
    status: str
    predictions_loaded: bool
    predictions_anchor_month: Optional[str] = None
    predictions_univariate_rows: Optional[int] = None
    predictions_fingerprint_rows: Optional[int] = None
    # True when the lag values served alongside predictions came from the
    # synthetic-backfill stopgap (see scripts/backfill_anchor_lags.py). The UI
    # surfaces this as a footnote so users know the "past 3mo" context isn't
    # real observed history.
    lags_synthetic: bool = False
    error: Optional[str] = None


class CategoryOption(BaseModel):
    name: str
    id: int


class OptionsResponse(BaseModel):
    colors: list[CategoryOption]         # color_master
    categories: list[CategoryOption]     # product_type
    materials: list[CategoryOption]
    appearances: list[CategoryOption]    # graphical_appearance
    genders: list[CategoryOption]


class TrendRow(BaseModel):
    dimension: str
    level_id: int
    level_name: str
    # Observed shares at anchor − 3, − 2, − 1, and the anchor month itself.
    share_lag3: Optional[float] = None
    share_lag2: Optional[float] = None
    share_lag1: Optional[float] = None
    share_t:    Optional[float] = None
    # Forward 6-horizon forecast.
    y_h1: float
    y_h2: float
    y_h3: float
    y_h4: float
    y_h5: float
    y_h6: float
    state: str
    stat: str


class FingerprintForecastResponse(BaseModel):
    product_type_id: int
    gender_id: int
    color_master_id: int
    graphical_appearance_id: int
    material_id: int
    product_type_name: str
    gender_name: str
    color_master_name: str
    graphical_appearance_name: str
    material_name: str
    share_lag3: Optional[float] = None
    share_lag2: Optional[float] = None
    share_lag1: Optional[float] = None
    share_t:    Optional[float] = None
    y_h1: float
    y_h2: float
    y_h3: float
    y_h4: float
    y_h5: float
    y_h6: float
    state: str
    stat: str


# --------------------------------------------------------------------------- #
# Bundle + lag attach                                                           #
# --------------------------------------------------------------------------- #

@dataclass
class PredictionsBundle:
    univariate: pd.DataFrame
    fingerprint: pd.DataFrame
    anchor_month: str   # 'YYYY-MM'
    lags_synthetic: bool = False


def _opt_float(v) -> Optional[float]:
    """Coerce a possibly-NaN pandas value to a JSON-safe float-or-None."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if pd.isna(f):
        return None
    return f


_LAG_COLUMNS: tuple[tuple[int, str], ...] = (
    (3, "share_lag3"),
    (2, "share_lag2"),
    (1, "share_lag1"),
    (0, "share_t"),
)


def _attach_lag_shares(
    preds: pd.DataFrame, merged: pd.DataFrame, key_cols: list[str]
) -> pd.DataFrame:
    """Attach `share_lag3`, `share_lag2`, `share_lag1`, `share_t` to a predictions
    frame by joining against the merged cube on (anchor − lag, *key_cols).

    The merged cube may carry duplicate (month, *key) rows when historical and
    live snapshots overlap; we mean-pool to a single observation per key + month
    before the join. Missing lag values land as NaN/None.
    """
    if preds.empty:
        for _, col in _LAG_COLUMNS:
            preds[col] = float("nan")
        return preds

    # Dedupe + restrict to the columns we need for the join.
    obs = (
        merged.groupby(["month"] + key_cols, as_index=False, observed=False)
        ["share_articles"]
        .mean()
    )
    obs["month"] = pd.to_datetime(obs["month"])

    out = preds.copy()
    out["_anchor_dt"] = pd.to_datetime(out["anchor_month"])
    for lag, col in _LAG_COLUMNS:
        target = out["_anchor_dt"] - pd.DateOffset(months=lag)
        joined = out.assign(_target=target).merge(
            obs.rename(columns={"month": "_target", "share_articles": col}),
            on=["_target"] + key_cols,
            how="left",
        )
        out[col] = joined[col].values
    out = out.drop(columns=["_anchor_dt"])
    return out


def load_bundle(
    *,
    univariate_path=None,
    fingerprint_path=None,
    merged_univariate_path=None,
    merged_fingerprint_path=None,
) -> tuple[Optional[PredictionsBundle], Optional[str]]:
    """Load the predictions parquets, validate them, and attach the observed
    lag shares from the merged cubes. Returns ``(bundle, None)`` on success or
    ``(None, error_message)`` on failure (no exceptions for the missing-parquet
    case — the server reports it via /health, the publisher raises on it).

    Paths default to the latest *successful* tick checkpoint
    (``ticks/<M>/predictions_*.parquet`` + ``ticks/<M>/merged_*.parquet``); tests
    and the publisher inject explicit paths.
    """
    try:
        # When any path isn't injected we need the latest successful tick to
        # resolve the defaults. If there's no checkpoint and nothing injected,
        # there's nothing to serve.
        need_default = (
            univariate_path is None
            or fingerprint_path is None
            or merged_univariate_path is None
            or merged_fingerprint_path is None
        )
        latest_tick = latest_successful_tick() if need_default else None
        if need_default and latest_tick is None and (
            univariate_path is None and fingerprint_path is None
        ):
            return None, "no successful tick checkpoint; run the monthly tick"
        tick_month = latest_tick.name if latest_tick is not None else None

        uv_path = (
            univariate_path
            if univariate_path is not None
            else (tick_predictions_path(tick_month, "univariate") if tick_month else None)
        )
        fp_path = (
            fingerprint_path
            if fingerprint_path is not None
            else (tick_predictions_path(tick_month, "fingerprint") if tick_month else None)
        )
        merged_uv_path = (
            merged_univariate_path
            if merged_univariate_path is not None
            else (tick_merged_path(tick_month, "univariate") if tick_month else None)
        )
        merged_fp_path = (
            merged_fingerprint_path
            if merged_fingerprint_path is not None
            else (tick_merged_path(tick_month, "fingerprint") if tick_month else None)
        )

        if uv_path is None:
            return None, (
                "no univariate predictions found; "
                "run `python -m pipelines.monthly run`"
            )
        if fp_path is None:
            return None, (
                "no fingerprint predictions found; "
                "run `python -m pipelines.monthly run`"
            )

        uv = pd.read_parquet(uv_path)
        fp = pd.read_parquet(fp_path)
        uv = validate_predictions_univariate_frame(uv)
        fp = validate_predictions_fingerprint_frame(fp)

        # Attach observed shares at anchor and 3 prior months so the API/JSON
        # carries the full 10-point series the chart needs. Detect whether any
        # of those lag months came from the synthetic backfill stopgap.
        anchor_dt = pd.Timestamp(uv["anchor_month"].iloc[0])
        lag_months = {anchor_dt - pd.DateOffset(months=k) for k in (1, 2, 3)}
        lags_synthetic = False

        if merged_uv_path is not None and Path(merged_uv_path).exists():
            merged_uv = pd.read_parquet(merged_uv_path)
            merged_uv["month"] = pd.to_datetime(merged_uv["month"])
            if "source" in merged_uv.columns and (
                (merged_uv["source"] == "backfill")
                & (merged_uv["month"].isin(lag_months))
            ).any():
                lags_synthetic = True
            uv = _attach_lag_shares(uv, merged_uv, key_cols=["dimension", "level_id"])
        if merged_fp_path is not None and Path(merged_fp_path).exists():
            merged_fp = pd.read_parquet(merged_fp_path)
            merged_fp["month"] = pd.to_datetime(merged_fp["month"])
            if "source" in merged_fp.columns and (
                (merged_fp["source"] == "backfill")
                & (merged_fp["month"].isin(lag_months))
            ).any():
                lags_synthetic = True
            fp = _attach_lag_shares(fp, merged_fp, key_cols=list(FINGERPRINT_KEY_COLS))

        anchors = sorted(
            [pd.Timestamp(uv["anchor_month"].iloc[0]),
             pd.Timestamp(fp["anchor_month"].iloc[0])]
        )
        anchor_month = anchors[-1].strftime("%Y-%m")

        bundle = PredictionsBundle(
            univariate=uv, fingerprint=fp,
            anchor_month=anchor_month, lags_synthetic=lags_synthetic,
        )
        logger.info(
            "predictions bundle ready (univariate=%d rows, fingerprint=%d rows, anchor=%s)",
            len(uv), len(fp), anchor_month,
        )
        return bundle, None
    except Exception as exc:  # noqa: BLE001 — report, don't crash the server lifespan
        logger.exception("load_bundle failed")
        return None, str(exc)


# --------------------------------------------------------------------------- #
# Response builders (single serialization point for server + publisher)        #
# --------------------------------------------------------------------------- #

def fingerprint_key(
    product_type_id: int,
    gender_id: int,
    color_master_id: int,
    graphical_appearance_id: int,
    material_id: int,
) -> str:
    """The canonical 5-D index key: ``"pt|g|cm|ga|m"``. Mirrored by the frontend."""
    return "|".join(
        str(int(v))
        for v in (
            product_type_id, gender_id, color_master_id,
            graphical_appearance_id, material_id,
        )
    )


def _trend_row_from_series(row: pd.Series) -> TrendRow:
    return TrendRow(
        dimension=str(row["dimension"]),
        level_id=int(row["level_id"]),
        level_name=str(row["level_name"]),
        share_lag3=_opt_float(row.get("share_lag3")),
        share_lag2=_opt_float(row.get("share_lag2")),
        share_lag1=_opt_float(row.get("share_lag1")),
        share_t=_opt_float(row.get("share_t")),
        y_h1=float(row["y_h1"]),
        y_h2=float(row["y_h2"]),
        y_h3=float(row["y_h3"]),
        y_h4=float(row["y_h4"]),
        y_h5=float(row["y_h5"]),
        y_h6=float(row["y_h6"]),
        state=str(row["state"]),
        stat=str(row["stat"]),
    )


def _fingerprint_from_series(row: pd.Series) -> FingerprintForecastResponse:
    return FingerprintForecastResponse(
        product_type_id=int(row["product_type_id"]),
        gender_id=int(row["gender_id"]),
        color_master_id=int(row["color_master_id"]),
        graphical_appearance_id=int(row["graphical_appearance_id"]),
        material_id=int(row["material_id"]),
        product_type_name=str(row["product_type_name"]),
        gender_name=str(row["gender_name"]),
        color_master_name=str(row["color_master_name"]),
        graphical_appearance_name=str(row["graphical_appearance_name"]),
        material_name=str(row["material_name"]),
        share_lag3=_opt_float(row.get("share_lag3")),
        share_lag2=_opt_float(row.get("share_lag2")),
        share_lag1=_opt_float(row.get("share_lag1")),
        share_t=_opt_float(row.get("share_t")),
        y_h1=float(row["y_h1"]),
        y_h2=float(row["y_h2"]),
        y_h3=float(row["y_h3"]),
        y_h4=float(row["y_h4"]),
        y_h5=float(row["y_h5"]),
        y_h6=float(row["y_h6"]),
        state=str(row["state"]),
        stat=str(row["stat"]),
    )


def build_trend_rows(
    bundle: PredictionsBundle,
    *,
    dimension: str | None = None,
    state: str | None = None,
) -> list[TrendRow]:
    """Every univariate (dimension, level) row as TrendRow. Optional filters
    mirror the server's ``/trends?dimension=&state=`` query."""
    df = bundle.univariate
    if dimension is not None:
        df = df[df["dimension"] == dimension]
    if state is not None:
        df = df[df["state"] == state]
    return [_trend_row_from_series(row) for _, row in df.iterrows()]


def lookup_fingerprint(
    bundle: PredictionsBundle,
    *,
    product_type_id: int,
    gender_id: int,
    color_master_id: int,
    graphical_appearance_id: int,
    material_id: int,
) -> FingerprintForecastResponse | None:
    """Exact 5-D fingerprint lookup. Returns the response or None on a miss
    (the server raises 404; the frontend routes None into synthesis)."""
    df = bundle.fingerprint
    mask = (
        (df["product_type_id"] == product_type_id)
        & (df["gender_id"] == gender_id)
        & (df["color_master_id"] == color_master_id)
        & (df["graphical_appearance_id"] == graphical_appearance_id)
        & (df["material_id"] == material_id)
    )
    hit = df[mask]
    if hit.empty:
        return None
    return _fingerprint_from_series(hit.iloc[0])


def build_fingerprint_index(
    bundle: PredictionsBundle,
) -> dict[str, FingerprintForecastResponse]:
    """Every fingerprint row indexed by the canonical 5-D key — the
    ``fingerprint.json`` bundle the SPA loads once and looks up client-side."""
    out: dict[str, FingerprintForecastResponse] = {}
    for _, row in bundle.fingerprint.iterrows():
        key = fingerprint_key(
            row["product_type_id"], row["gender_id"], row["color_master_id"],
            row["graphical_appearance_id"], row["material_id"],
        )
        out[key] = _fingerprint_from_series(row)
    return out


def build_options(lookup_path=None) -> OptionsResponse:
    """Dropdown vocabularies sourced from lookup.csv (191 rows), independent of
    the predictions parquets. Each entry is ``{name, id}``."""
    path = lookup_path if lookup_path is not None else LOOKUP_CSV
    lk = pd.read_csv(path)

    def opts_for(category: str) -> list[CategoryOption]:
        rows = lk[lk["category"] == category]
        return [
            CategoryOption(name=str(r["name"]), id=int(r["id"]))
            for _, r in rows.sort_values("name").iterrows()
        ]

    return OptionsResponse(
        colors=opts_for("color_master"),
        categories=opts_for("product_type"),
        materials=opts_for("material"),
        appearances=opts_for("graphical_appearance"),
        genders=opts_for("gender"),
    )


def build_health(
    bundle: PredictionsBundle | None,
    error: str | None = None,
) -> HealthResponse:
    """The ``/health`` payload (also published as a static health.json)."""
    if bundle is None:
        return HealthResponse(
            status="degraded",
            predictions_loaded=False,
            error=error,
        )
    return HealthResponse(
        status="healthy",
        predictions_loaded=True,
        predictions_anchor_month=bundle.anchor_month,
        predictions_univariate_rows=int(len(bundle.univariate)),
        predictions_fingerprint_rows=int(len(bundle.fingerprint)),
        lags_synthetic=bundle.lags_synthetic,
    )
