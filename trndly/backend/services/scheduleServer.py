from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import mlflow
import pandas as pd
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, status
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from mlflow.tracking import MlflowClient
from pydantic import BaseModel, Field, field_validator

# Add project root to sys.path
SERVICE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SERVICE_DIR.parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))


# Local imports
from pipelines.serving.text_forecast import (
    ForecastDeps,
    forecast_from_text,
    load_forecast_pair,
)
from pipelines.training.feature_contract import (
    TIMEFRAMES,
    SeasonalityTable,
    build_feature_frame,
    compute_feature_scores,
    load_seasonality_table,
    load_trend_lookup_from_univariate,
    normalize_token,
)
from pipelines.training.paths import (
    FRONTEND_DIR,
    LIVE_UNIVARIATE_PARQUET,
    LOOKUP_CSV,
    MONTHLY_FINGERPRINT_PARQUET,
    MONTHLY_UNIVARIATE_PARQUET,
    SEASONALITY_TABLE_CSV as DEFAULT_SEASONALITY_TABLE_PATH,
)

# ENV VARIABLES
ENV_PATH = SERVICE_DIR / ".env"
load_dotenv(dotenv_path=ENV_PATH)

MLFLOW_TRACKING_URI = os.getenv("MLFLOW_TRACKING_URI")
MLFLOW_MODEL_URI = os.getenv("MLFLOW_MODEL_URI")
MLFLOW_FORECAST_MODEL_URI = os.getenv(
    "MLFLOW_FORECAST_MODEL_URI", "models:/trndly_fingerprint@candidate"
)
MLFLOW_UNIVARIATE_FORECAST_MODEL_URI = os.getenv(
    "MLFLOW_UNIVARIATE_FORECAST_MODEL_URI", "models:/trndly_univariate@candidate"
)


def _resolve_configured_path(env_var: str, default: Path) -> Path:
    configured = Path(os.getenv(env_var, str(default))).expanduser()
    return configured if configured.is_absolute() else (SERVICE_DIR / configured).resolve()


LIVE_UNIVARIATE_PATH = _resolve_configured_path(
    "LIVE_UNIVARIATE_PATH", LIVE_UNIVARIATE_PARQUET
)
SEASONALITY_TABLE_PATH = _resolve_configured_path(
    "SEASONALITY_TABLE_PATH", DEFAULT_SEASONALITY_TABLE_PATH
)


# Initialize logger
logger = logging.getLogger(__name__)


# DATA CLASSES

@dataclass
class ModelState:
    model: Optional[Any] = None
    model_uri: Optional[str] = None
    model_version: Optional[str] = None
    run_id: Optional[str] = None
    error: Optional[str] = None

    @property
    def loaded(self) -> bool:
        return self.model is not None


@dataclass
class TrendState:
    lookup: Optional[TrendLookup] = None
    source_path: Optional[str] = None
    error: Optional[str] = None

    @property
    def loaded(self) -> bool:
        return self.lookup is not None


@dataclass
class SeasonalityState:
    table: Optional[SeasonalityTable] = None
    source_path: Optional[str] = None
    error: Optional[str] = None

    @property
    def loaded(self) -> bool:
        return self.table is not None


MODEL_STATE = ModelState()
TREND_STATE = TrendState(source_path=str(LIVE_UNIVARIATE_PATH))
SEASONALITY_STATE = SeasonalityState(source_path=str(SEASONALITY_TABLE_PATH))

FORECAST_DEPS: ForecastDeps | None = None
FORECAST_LOAD_ERROR: str | None = None


# --- FASTAPI APP ---

@asynccontextmanager
async def lifespan(_: FastAPI):
    reload_trend_data()
    reload_seasonality_table()
    reload_model()
    reload_forecast_bundle()
    yield


# Initialize FastAPI app
app = FastAPI(
    title="MLflow-backed Timeframe Recommendation Service",
    lifespan=lifespan,
)


class RootResponse(BaseModel):
    message: str


class HealthResponse(BaseModel):
    status: str
    model_loaded: bool
    trend_data_loaded: bool
    seasonality_table_loaded: bool
    forecast_catalog_loaded: bool = False
    forecast_catalog_error: Optional[str] = None
    forecast_model_uri: Optional[str] = None
    forecast_univariate_uri: Optional[str] = None
    tracking_uri: Optional[str]
    configured_model_uri: Optional[str]
    configured_trend_data_path: str
    configured_seasonality_table_path: str
    active_model_uri: Optional[str]
    model_version: Optional[str]
    run_id: Optional[str]
    error: Optional[str]
    trend_error: Optional[str]
    seasonality_error: Optional[str]


class PredictRequest(BaseModel):
    item_name: str = Field(min_length=1, max_length=120)
    color: str = Field(min_length=1, max_length=40)
    category: str = Field(min_length=1, max_length=40)
    material: str = Field(min_length=1, max_length=40)
    # Optional "as-of" month (1-12). If omitted, the server uses the current
    # calendar month — i.e. "what's the best listing timeframe if I list today?".
    reference_month: Optional[int] = Field(default=None, ge=1, le=12)

    @field_validator("item_name", "color", "category", "material")
    @classmethod
    def strip_non_empty(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("Field must not be empty.")
        return trimmed


class PredictResponse(BaseModel):
    item_name: str
    best_timeframe: str
    reference_month: int
    feature_scores: dict[str, float]
    model_loaded: bool
    model_uri: Optional[str]
    run_id: Optional[str]


class ForecastTextRequest(BaseModel):
    query: str = Field(min_length=1, max_length=280)
    reference_month: Optional[int] = Field(default=None, ge=1, le=12)

    @field_validator("query")
    @classmethod
    def strip_query(cls, value: str) -> str:
        return value.strip()


class ForecastTextResponse(BaseModel):
    query: str
    resolved_dimensions: dict[str, int]
    anchor_month: str
    reference_month_of_year_used: int
    mode: str
    fingerprint_matches: int
    fingerprint_keys_sample: list[list[int]]
    forecast: dict[str, float]
    horizons: list[str]
    fallback_dimension: Optional[str] = None
    fallback_level_id: Optional[int] = None


class ReloadModelResponse(BaseModel):
    loaded: bool
    trend_data_loaded: bool
    seasonality_table_loaded: bool
    forecast_catalog_loaded: bool = False
    forecast_catalog_error: Optional[str] = None
    configured_model_uri: Optional[str]
    configured_trend_data_path: str
    configured_seasonality_table_path: str
    active_model_uri: Optional[str]
    model_version: Optional[str]
    run_id: Optional[str]
    error: Optional[str]
    trend_error: Optional[str]
    seasonality_error: Optional[str]


def _parse_registry_alias_uri(model_uri: str) -> tuple[Optional[str], Optional[str]]:
    if not model_uri.startswith("models:/"):
        return None, None

    locator = model_uri.removeprefix("models:/")
    if "@" not in locator:
        return None, None

    model_name, alias = locator.split("@", maxsplit=1)
    return model_name, alias


def _resolve_registry_metadata(model_uri: str) -> tuple[Optional[str], Optional[str]]:
    model_name, alias = _parse_registry_alias_uri(model_uri)
    if not model_name or not alias:
        return None, None

    try:
        client = MlflowClient(tracking_uri=MLFLOW_TRACKING_URI)
        model_version = client.get_model_version_by_alias(name=model_name, alias=alias)
        return model_version.version, model_version.run_id
    except Exception: 
        # Metadata lookup should not block serving if model loading succeeds.
        logger.exception(
            "Loaded model, but failed resolving registry alias metadata for '%s'.",
            model_uri,
        )
        return None, None


def _load_model_from_mlflow(model_uri: str) -> ModelState:
    try:
        mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
        model = mlflow.pyfunc.load_model(model_uri=model_uri)
        resolved_version, resolved_run_id = _resolve_registry_metadata(model_uri)
        metadata_run_id = getattr(getattr(model, "metadata", None), "run_id", None)
        run_id = resolved_run_id or metadata_run_id

        logger.info(
            "Loaded model from MLflow using model_uri=%s, run_id=%s, version=%s",
            model_uri,
            run_id,
            resolved_version,
        )
        return ModelState(
            model=model,
            model_uri=model_uri,
            model_version=resolved_version,
            run_id=run_id,
        )
    except Exception as exc:
        logger.exception("Failed to load model from MLflow using model_uri=%s", model_uri)
        return ModelState(error=str(exc))


def reload_model() -> ModelState:
    global MODEL_STATE

    primary_state = _load_model_from_mlflow(MLFLOW_MODEL_URI)
    MODEL_STATE = primary_state
    return MODEL_STATE


def reload_trend_data() -> TrendState:
    global TREND_STATE

    try:
        lookup = load_trend_lookup_from_univariate(
            LIVE_UNIVARIATE_PATH, source="live", latest_month=True
        )
        TREND_STATE = TrendState(
            lookup=lookup,
            source_path=str(LIVE_UNIVARIATE_PATH),
        )
        logger.info("Loaded trend signals from %s", LIVE_UNIVARIATE_PATH)
        return TREND_STATE
    except Exception as exc:
        logger.exception("Failed to load trend signals from %s", LIVE_UNIVARIATE_PATH)
        TREND_STATE = TrendState(
            source_path=str(LIVE_UNIVARIATE_PATH),
            error=str(exc),
        )
        return TREND_STATE


def reload_seasonality_table() -> SeasonalityState:
    global SEASONALITY_STATE

    try:
        table = load_seasonality_table(SEASONALITY_TABLE_PATH)
        SEASONALITY_STATE = SeasonalityState(
            table=table,
            source_path=str(SEASONALITY_TABLE_PATH),
        )
        logger.info("Loaded seasonality table from %s", SEASONALITY_TABLE_PATH)
        return SEASONALITY_STATE
    except Exception as exc:
        logger.exception(
            "Failed to load seasonality table from %s", SEASONALITY_TABLE_PATH
        )
        SEASONALITY_STATE = SeasonalityState(
            source_path=str(SEASONALITY_TABLE_PATH),
            error=str(exc),
        )
        return SEASONALITY_STATE


def reload_forecast_bundle() -> None:
    """Load parquet cubes + forecast models (MLflow registry or processed ``*.joblib``)."""

    global FORECAST_DEPS, FORECAST_LOAD_ERROR

    FORECAST_DEPS = None
    FORECAST_LOAD_ERROR = None

    try:
        if not MONTHLY_FINGERPRINT_PARQUET.exists():
            FORECAST_LOAD_ERROR = f"missing fingerprint cube at {MONTHLY_FINGERPRINT_PARQUET}"
            return
        if not LOOKUP_CSV.exists():
            FORECAST_LOAD_ERROR = f"missing lookup table at {LOOKUP_CSV}"
            return
        cube_uni = None
        if MONTHLY_UNIVARIATE_PARQUET.exists():
            try:
                cube_uni = pd.read_parquet(MONTHLY_UNIVARIATE_PARQUET)
                cube_uni["month"] = pd.to_datetime(cube_uni["month"]).dt.as_unit("ns")
            except Exception:
                logger.exception("Optional univariate cube load skipped.")

        cube_fp = pd.read_parquet(MONTHLY_FINGERPRINT_PARQUET)
        cube_fp["month"] = pd.to_datetime(cube_fp["month"]).dt.as_unit("ns")
        lookup = pd.read_csv(LOOKUP_CSV)

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
            lookup=lookup,
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


def _predict_timeframe(payload: PredictRequest) -> tuple[str, int, dict[str, float]]:
    if TREND_STATE.lookup is None:
        raise RuntimeError("Trend signals are not loaded.")
    if SEASONALITY_STATE.table is None:
        raise RuntimeError("Seasonality table is not loaded.")

    item = {
        "item_name": payload.item_name.strip(),
        "color": normalize_token(payload.color),
        "category": normalize_token(payload.category),
        "material": normalize_token(payload.material),
    }

    # If the caller doesn't supply reference_month, default to "today" so the
    # answer means "best timeframe if the user lists right now".
    reference_month = payload.reference_month or datetime.now().month

    inference_frame = build_feature_frame(
        [item],
        TREND_STATE.lookup,
        reference_month=reference_month,
        seasonality_table=SEASONALITY_STATE.table,
    )
    predictions = MODEL_STATE.model.predict(inference_frame)
    model_prediction = str(predictions[0])

    if model_prediction not in TIMEFRAMES:
        logger.warning(
            "Model returned unexpected timeframe '%s'; falling back to 'current'.",
            model_prediction,
        )
        best_timeframe = TIMEFRAMES[0]
    else:
        best_timeframe = model_prediction

    feature_scores = compute_feature_scores(item=item, lookup=TREND_STATE.lookup)
    rounded_scores = {key: round(float(val), 6) for key, val in feature_scores.items()}
    return best_timeframe, reference_month, rounded_scores

@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/ui/")


class OptionsResponse(BaseModel):
    colors: list[str]
    categories: list[str]
    materials: list[str]
    timeframes: list[str]


@app.get("/options", response_model=OptionsResponse)
def options() -> OptionsResponse:
    """
    Return the vocabularies the UI should use for dropdowns. Colors, categories,
    and materials come from whatever is in trend_signals.csv (so the UI stays in
    sync with the model's real feature space automatically).
    """
    if TREND_STATE.lookup is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Trend signals are not loaded.",
        )
    lookup = TREND_STATE.lookup
    return OptionsResponse(
        colors=sorted(lookup.get("color", {}).keys()),
        categories=sorted(lookup.get("category", {}).keys()),
        materials=sorted(lookup.get("material", {}).keys()),
        timeframes=list(TIMEFRAMES),
    )


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    all_loaded = MODEL_STATE.loaded and TREND_STATE.loaded and SEASONALITY_STATE.loaded
    current_status = "healthy" if all_loaded else "degraded"
    return HealthResponse(
        status=current_status,
        model_loaded=MODEL_STATE.loaded,
        trend_data_loaded=TREND_STATE.loaded,
        seasonality_table_loaded=SEASONALITY_STATE.loaded,
        forecast_catalog_loaded=FORECAST_DEPS is not None,
        forecast_catalog_error=FORECAST_LOAD_ERROR,
        forecast_model_uri=MLFLOW_FORECAST_MODEL_URI,
        forecast_univariate_uri=MLFLOW_UNIVARIATE_FORECAST_MODEL_URI,
        tracking_uri=MLFLOW_TRACKING_URI,
        configured_model_uri=MLFLOW_MODEL_URI,
        configured_trend_data_path=str(LIVE_UNIVARIATE_PATH),
        configured_seasonality_table_path=str(SEASONALITY_TABLE_PATH),
        active_model_uri=MODEL_STATE.model_uri,
        model_version=MODEL_STATE.model_version,
        run_id=MODEL_STATE.run_id,
        error=MODEL_STATE.error,
        trend_error=TREND_STATE.error,
        seasonality_error=SEASONALITY_STATE.error,
    )


@app.post("/predict", response_model=PredictResponse)
def predict(payload: PredictRequest) -> PredictResponse:
    if not MODEL_STATE.loaded:
        detail = MODEL_STATE.error or "Model is not loaded."
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=detail,
        )

    if not TREND_STATE.loaded:
        detail = TREND_STATE.error or "Trend signal data is not loaded."
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=detail,
        )

    if not SEASONALITY_STATE.loaded:
        detail = SEASONALITY_STATE.error or "Seasonality table is not loaded."
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=detail,
        )

    best_timeframe, reference_month, feature_scores = _predict_timeframe(payload)
    return PredictResponse(
        item_name=payload.item_name,
        best_timeframe=best_timeframe,
        reference_month=reference_month,
        feature_scores=feature_scores,
        model_loaded=MODEL_STATE.loaded,
        model_uri=MODEL_STATE.model_uri,
        run_id=MODEL_STATE.run_id,
    )


@app.post("/forecast-text", response_model=ForecastTextResponse)
def forecast_text(payload: ForecastTextRequest) -> ForecastTextResponse:
    """Natural-language catalog-share forecast (pairs with notebooks ``5_*``)."""

    if FORECAST_DEPS is None:
        detail = FORECAST_LOAD_ERROR or "Forecast bundle is not loaded."
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=detail)

    raw = forecast_from_text(
        payload.query,
        FORECAST_DEPS,
        reference_month_of_year=payload.reference_month,
    )
    if raw.get("mode") == "unresolved":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=raw.get("error", "Unable to forecast from query."),
        )

    raw.pop("error", None)
    return ForecastTextResponse(**raw)


# --------------------------------------------------------------------------- #
# Static UI                                                                     #
# --------------------------------------------------------------------------- #
# A minimal demo page (vanilla HTML/CSS/JS, no build step) lives in
# trndly/frontend/ (a sibling of backend/, path sourced from paths.py).
# Mounted at /ui so everything stays same-origin with /predict and /options —
# no CORS setup required.
if FRONTEND_DIR.exists():
    app.mount("/ui", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="ui")
else:
    logger.warning("Frontend directory not found at %s; /ui will 404.", FRONTEND_DIR)
