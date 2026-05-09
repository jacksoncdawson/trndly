from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import pandas as pd
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, status
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

# Add project root to sys.path
SERVICE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SERVICE_DIR.parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))


# Local imports
from pipelines.serving.forecast import (
    FINGERPRINT_COLS,
    UNIVARIATE_DIMENSIONS,
    ForecastDeps,
    forecast_fingerprint,
    forecast_univariate,
    load_forecast_pair,
)
from pipelines.paths import (
    FRONTEND_DIR,
    LOOKUP_CSV,
    MERGED_FINGERPRINT_PARQUET,
    MERGED_UNIVARIATE_PARQUET,
)

# ENV VARIABLES
ENV_PATH = SERVICE_DIR / ".env"
load_dotenv(dotenv_path=ENV_PATH)

MLFLOW_TRACKING_URI = os.getenv("MLFLOW_TRACKING_URI")
MLFLOW_FORECAST_MODEL_URI = os.getenv(
    "MLFLOW_FORECAST_MODEL_URI", "models:/trndly_fingerprint@candidate"
)
MLFLOW_UNIVARIATE_FORECAST_MODEL_URI = os.getenv(
    "MLFLOW_UNIVARIATE_FORECAST_MODEL_URI", "models:/trndly_univariate@candidate"
)


logger = logging.getLogger(__name__)


# --- STATE ---

FORECAST_DEPS: ForecastDeps | None = None
FORECAST_LOAD_ERROR: str | None = None


# --- FASTAPI APP ---

@asynccontextmanager
async def lifespan(_: FastAPI):
    reload_forecast_bundle()
    yield


app = FastAPI(
    title="trndly forecast service",
    lifespan=lifespan,
)


class RootResponse(BaseModel):
    message: str


class HealthResponse(BaseModel):
    status: str
    forecast_catalog_loaded: bool = False
    forecast_catalog_error: Optional[str] = None
    forecast_model_uri: Optional[str] = None
    forecast_univariate_uri: Optional[str] = None
    tracking_uri: Optional[str]


class OptionsResponse(BaseModel):
    colors: list[str]
    categories: list[str]
    materials: list[str]


class FingerprintForecastRequest(BaseModel):
    """All five fingerprint IDs must be supplied. ``reference_month`` (1-12)
    selects the cube anchor month; default = latest available."""

    product_type_id: int = Field(ge=0)
    gender_id: int = Field(ge=0)
    color_master_id: int = Field(ge=0)
    graphical_appearance_id: int = Field(ge=0)
    material_id: int = Field(ge=0)
    reference_month: Optional[int] = Field(default=None, ge=1, le=12)

    def dimensions(self) -> dict[str, int]:
        return {col: getattr(self, col) for col in FINGERPRINT_COLS}


class FingerprintForecastResponse(BaseModel):
    dimensions: dict[str, int]
    anchor_month: str
    reference_month_of_year_used: int
    fingerprint_matches: int
    fingerprint_keys_sample: list[list[int]]
    forecast: dict[str, float]
    horizons: list[str]


class UnivariateForecastRequest(BaseModel):
    dimension: str = Field(min_length=1)
    level_id: int = Field(ge=0)
    reference_month: Optional[int] = Field(default=None, ge=1, le=12)

    @field_validator("dimension")
    @classmethod
    def validate_dimension(cls, value: str) -> str:
        v = value.strip()
        if v not in UNIVARIATE_DIMENSIONS:
            raise ValueError(
                f"unknown dimension {v!r}; expected one of {sorted(UNIVARIATE_DIMENSIONS)}"
            )
        return v


class UnivariateForecastResponse(BaseModel):
    dimension: str
    level_id: int
    anchor_month: str
    forecast: dict[str, float]
    horizons: list[str]


def reload_forecast_bundle() -> None:
    """Load parquet cubes + forecast models (MLflow registry or local ``*.joblib``)."""

    global FORECAST_DEPS, FORECAST_LOAD_ERROR

    FORECAST_DEPS = None
    FORECAST_LOAD_ERROR = None

    try:
        if not MERGED_FINGERPRINT_PARQUET.exists():
            FORECAST_LOAD_ERROR = f"missing fingerprint cube at {MERGED_FINGERPRINT_PARQUET}"
            return
        if not LOOKUP_CSV.exists():
            FORECAST_LOAD_ERROR = f"missing lookup table at {LOOKUP_CSV}"
            return
        cube_uni = None
        if MERGED_UNIVARIATE_PARQUET.exists():
            try:
                cube_uni = pd.read_parquet(MERGED_UNIVARIATE_PARQUET)
                cube_uni["month"] = pd.to_datetime(cube_uni["month"]).dt.as_unit("ns")
            except Exception:
                logger.exception("Optional univariate cube load skipped.")

        cube_fp = pd.read_parquet(MERGED_FINGERPRINT_PARQUET)
        cube_fp["month"] = pd.to_datetime(cube_fp["month"]).dt.as_unit("ns")

        fp_model, uni_model, model_src = load_forecast_pair(
            tracking_uri=MLFLOW_TRACKING_URI,
            fingerprint_uri=MLFLOW_FORECAST_MODEL_URI,
            univariate_uri=MLFLOW_UNIVARIATE_FORECAST_MODEL_URI,
            load_univariate=cube_uni is not None,
        )

        FORECAST_DEPS = ForecastDeps(
            fingerprint_model=fp_model,
            univariate_model=uni_model,
            cube_fp=cube_fp,
            cube_uni=cube_uni,
        )
        logger.info(
            "Forecast bundle ready (models=%s, fp_uri=%s, uni_uri=%s).",
            model_src,
            MLFLOW_FORECAST_MODEL_URI,
            MLFLOW_UNIVARIATE_FORECAST_MODEL_URI,
        )
    except Exception as exc:
        FORECAST_LOAD_ERROR = str(exc)
        logger.exception("reload_forecast_bundle failed")


# --- ROUTES ---

@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/ui/")


@app.get("/options", response_model=OptionsResponse)
def options() -> OptionsResponse:
    """
    Return the vocabularies the UI should use for dropdowns. Sourced
    directly from ``lookup.csv`` so the UI stays in sync with the
    canonical ID universe automatically.
    """
    if not LOOKUP_CSV.exists():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"lookup.csv missing at {LOOKUP_CSV}",
        )
    lk = pd.read_csv(LOOKUP_CSV)

    def names_for(category: str) -> list[str]:
        rows = lk[lk["category"] == category]
        return sorted(rows["name"].astype(str).str.lower().tolist())

    return OptionsResponse(
        colors=names_for("color_master"),
        categories=names_for("product_type"),
        materials=names_for("material"),
    )


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    current_status = "healthy" if FORECAST_DEPS is not None else "degraded"
    return HealthResponse(
        status=current_status,
        forecast_catalog_loaded=FORECAST_DEPS is not None,
        forecast_catalog_error=FORECAST_LOAD_ERROR,
        forecast_model_uri=MLFLOW_FORECAST_MODEL_URI,
        forecast_univariate_uri=MLFLOW_UNIVARIATE_FORECAST_MODEL_URI,
        tracking_uri=MLFLOW_TRACKING_URI,
    )


def _require_forecast_deps() -> ForecastDeps:
    if FORECAST_DEPS is None:
        detail = FORECAST_LOAD_ERROR or "Forecast bundle is not loaded."
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=detail)
    return FORECAST_DEPS


@app.post("/forecast/fingerprint", response_model=FingerprintForecastResponse)
def forecast_fingerprint_route(
    payload: FingerprintForecastRequest,
) -> FingerprintForecastResponse:
    """Predict the next 6 months of catalog share for a fully-specified
    5-D fingerprint."""

    deps = _require_forecast_deps()
    raw = forecast_fingerprint(
        payload.dimensions(), deps, reference_month=payload.reference_month
    )
    if raw.get("forecast") is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=raw.get("error", "Unable to forecast from fingerprint."),
        )
    raw.pop("error", None)
    return FingerprintForecastResponse(**raw)


@app.post("/forecast/univariate", response_model=UnivariateForecastResponse)
def forecast_univariate_route(
    payload: UnivariateForecastRequest,
) -> UnivariateForecastResponse:
    """Predict the next 6 months of catalog share for one
    ``(dimension, level_id)`` series."""

    deps = _require_forecast_deps()
    if deps.univariate_model is None or deps.cube_uni is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Univariate model or cube is not loaded.",
        )
    raw = forecast_univariate(
        payload.dimension,
        payload.level_id,
        deps,
        reference_month=payload.reference_month,
    )
    if raw.get("forecast") is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=raw.get("error", "Unable to forecast from (dimension, level_id)."),
        )
    raw.pop("error", None)
    return UnivariateForecastResponse(**raw)


# --------------------------------------------------------------------------- #
# Static UI                                                                     #
# --------------------------------------------------------------------------- #
# A minimal demo page (vanilla HTML/CSS/JS, no build step) lives in
# trndly/frontend/ (a sibling of backend/, path sourced from paths.py).
# Mounted at /ui so everything stays same-origin with /options and /forecast/* —
# no CORS setup required.
if FRONTEND_DIR.exists():
    app.mount("/ui", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="ui")
else:
    logger.warning("Frontend directory not found at %s; /ui will 404.", FRONTEND_DIR)
