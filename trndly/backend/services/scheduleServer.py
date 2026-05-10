"""trndly forecast service — read-only API over the predictions parquet.

The monthly tick (``python -m pipelines.monthly run``) writes two parquets:

    data/predictions/predictions_univariate_<YYYY-MM>.parquet
    data/predictions/predictions_fingerprint_<YYYY-MM>.parquet

This service loads the most recent of each at startup and exposes them via
three GET routes:

    GET /options                  — dropdown vocabularies (name + id per category)
    GET /trends                   — every (dimension, level) row from univariate
    GET /forecast/fingerprint     — single row matching the 5-D fingerprint
    GET /health                   — liveness + bundle status

There are no live model calls in the request path. To refresh the bundle,
restart the container (or re-run the tick + redeploy).
"""

from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pandas as pd
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, status
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Add project root to sys.path
SERVICE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SERVICE_DIR.parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))


from pipelines.contracts import (
    validate_predictions_fingerprint_frame,
    validate_predictions_univariate_frame,
)
from pipelines.paths import (
    FRONTEND_DIR,
    LOOKUP_CSV,
    latest_predictions_fingerprint_parquet,
    latest_predictions_univariate_parquet,
)

# ENV VARIABLES
ENV_PATH = SERVICE_DIR / ".env"
load_dotenv(dotenv_path=ENV_PATH)


logger = logging.getLogger(__name__)


# --- STATE ---

@dataclass
class PredictionsBundle:
    univariate: pd.DataFrame
    fingerprint: pd.DataFrame
    anchor_month: str   # ISO date string from the parquets


BUNDLE: PredictionsBundle | None = None
BUNDLE_LOAD_ERROR: str | None = None


# --- FASTAPI APP ---

@asynccontextmanager
async def lifespan(_: FastAPI):
    reload_predictions_bundle()
    yield


app = FastAPI(
    title="trndly forecast service",
    lifespan=lifespan,
)


# --- SCHEMAS ---

class HealthResponse(BaseModel):
    status: str
    predictions_loaded: bool
    predictions_anchor_month: Optional[str] = None
    predictions_univariate_rows: Optional[int] = None
    predictions_fingerprint_rows: Optional[int] = None
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
    y_h1: float
    y_h2: float
    y_h3: float
    y_h4: float
    y_h5: float
    y_h6: float
    state: str
    stat: str


# --- LOADER ---

def reload_predictions_bundle() -> None:
    """Load the latest predictions parquets into BUNDLE."""
    global BUNDLE, BUNDLE_LOAD_ERROR
    BUNDLE = None
    BUNDLE_LOAD_ERROR = None
    try:
        uv_path = latest_predictions_univariate_parquet()
        fp_path = latest_predictions_fingerprint_parquet()
        if uv_path is None:
            BUNDLE_LOAD_ERROR = (
                "no predictions_univariate_*.parquet found; "
                "run `python -m pipelines.monthly run`"
            )
            return
        if fp_path is None:
            BUNDLE_LOAD_ERROR = (
                "no predictions_fingerprint_*.parquet found; "
                "run `python -m pipelines.monthly run`"
            )
            return

        uv = pd.read_parquet(uv_path)
        fp = pd.read_parquet(fp_path)
        # Validate via contract — raises on schema drift.
        uv = validate_predictions_univariate_frame(uv)
        fp = validate_predictions_fingerprint_frame(fp)

        # Both parquets carry an anchor_month column; report whichever's most recent.
        anchors = sorted(
            [pd.Timestamp(uv["anchor_month"].iloc[0]),
             pd.Timestamp(fp["anchor_month"].iloc[0])]
        )
        anchor_month = anchors[-1].strftime("%Y-%m")

        BUNDLE = PredictionsBundle(
            univariate=uv, fingerprint=fp, anchor_month=anchor_month
        )
        logger.info(
            "predictions bundle ready (univariate=%d rows, fingerprint=%d rows, anchor=%s)",
            len(uv), len(fp), anchor_month,
        )
    except Exception as exc:
        BUNDLE_LOAD_ERROR = str(exc)
        logger.exception("reload_predictions_bundle failed")


def _require_bundle() -> PredictionsBundle:
    if BUNDLE is None:
        detail = BUNDLE_LOAD_ERROR or "predictions bundle not loaded"
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=detail
        )
    return BUNDLE


# --- ROUTES ---

@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/ui/")


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    if BUNDLE is None:
        return HealthResponse(
            status="degraded",
            predictions_loaded=False,
            error=BUNDLE_LOAD_ERROR,
        )
    return HealthResponse(
        status="healthy",
        predictions_loaded=True,
        predictions_anchor_month=BUNDLE.anchor_month,
        predictions_univariate_rows=int(len(BUNDLE.univariate)),
        predictions_fingerprint_rows=int(len(BUNDLE.fingerprint)),
    )


@app.get("/options", response_model=OptionsResponse)
def options() -> OptionsResponse:
    """Vocabularies for the UI dropdowns. Sourced directly from
    ``lookup.csv`` so the UI stays in sync with the canonical ID universe.
    Each entry is ``{name, id}`` so the frontend can POST IDs back.
    """
    if not LOOKUP_CSV.exists():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"lookup.csv missing at {LOOKUP_CSV}",
        )
    lk = pd.read_csv(LOOKUP_CSV)

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


@app.get("/trends", response_model=list[TrendRow])
def trends(
    dimension: str | None = None,
    state: str | None = None,
) -> list[TrendRow]:
    """Univariate predictions. Optional filters: ``?dimension=color_master&state=rising``."""
    bundle = _require_bundle()
    df = bundle.univariate
    if dimension is not None:
        df = df[df["dimension"] == dimension]
    if state is not None:
        df = df[df["state"] == state]
    return [
        TrendRow(
            dimension=str(row["dimension"]),
            level_id=int(row["level_id"]),
            level_name=str(row["level_name"]),
            y_h1=float(row["y_h1"]),
            y_h2=float(row["y_h2"]),
            y_h3=float(row["y_h3"]),
            y_h4=float(row["y_h4"]),
            y_h5=float(row["y_h5"]),
            y_h6=float(row["y_h6"]),
            state=str(row["state"]),
            stat=str(row["stat"]),
        )
        for _, row in df.iterrows()
    ]


@app.get("/forecast/fingerprint", response_model=FingerprintForecastResponse)
def forecast_fingerprint(
    product_type_id: int,
    gender_id: int,
    color_master_id: int,
    graphical_appearance_id: int,
    material_id: int,
) -> FingerprintForecastResponse:
    """Single-fingerprint forecast lookup. 404 if no precomputed match."""
    bundle = _require_bundle()
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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "no precomputed forecast for fingerprint "
                f"product_type_id={product_type_id} gender_id={gender_id} "
                f"color_master_id={color_master_id} "
                f"graphical_appearance_id={graphical_appearance_id} "
                f"material_id={material_id}"
            ),
        )
    row = hit.iloc[0]
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
        y_h1=float(row["y_h1"]),
        y_h2=float(row["y_h2"]),
        y_h3=float(row["y_h3"]),
        y_h4=float(row["y_h4"]),
        y_h5=float(row["y_h5"]),
        y_h6=float(row["y_h6"]),
        state=str(row["state"]),
        stat=str(row["stat"]),
    )


# --------------------------------------------------------------------------- #
# Static UI                                                                     #
# --------------------------------------------------------------------------- #
# A React demo page (no build step — JSX-via-Babel script tag) lives in
# trndly/frontend/. Mounted at /ui so everything stays same-origin with /options
# /trends, and /forecast/* — no CORS setup required.
if FRONTEND_DIR.exists():
    app.mount("/ui", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="ui")
else:
    logger.warning("Frontend directory not found at %s; /ui will 404.", FRONTEND_DIR)
