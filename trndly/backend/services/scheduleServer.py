"""trndly forecast service — read-only API over the predictions parquet.

LOCAL DEV CONVENIENCE + schema reference. The production serving path is now
STATIC: the monthly tick's ``publish`` stage emits the same shapes as JSON files
served by Firebase Hosting (no compute behind them). This server is retained for
local development and as a live contract reference.

It imports ``pipelines.serving`` — the single source of truth for the lag-join
(``share_lag*``/``share_t``) and the response shapes — so the server and the
static publisher can never diverge. It loads no ``.env`` (it reads only
filesystem paths from ``pipelines.paths``).

Routes (identical shapes to the published static JSON):
    GET /options  /trends  /forecast/fingerprint  /health
"""

from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, status
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

# Add project root to sys.path so ``pipelines`` is importable when this module is
# run directly (uvicorn backend.services.scheduleServer:app).
SERVICE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SERVICE_DIR.parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from pipelines.paths import FRONTEND_DIR, LOOKUP_CSV  # noqa: E402
from pipelines.serving import (  # noqa: E402
    FingerprintForecastResponse,
    HealthResponse,
    OptionsResponse,
    PredictionsBundle,
    TrendRow,
    build_health,
    build_options,
    build_trend_rows,
    load_bundle,
    lookup_fingerprint,
)

logger = logging.getLogger(__name__)


# --- STATE ---

BUNDLE: PredictionsBundle | None = None
BUNDLE_LOAD_ERROR: str | None = None


def reload_predictions_bundle() -> None:
    """Reload BUNDLE from the latest predictions parquets + merged cubes."""
    global BUNDLE, BUNDLE_LOAD_ERROR
    BUNDLE, BUNDLE_LOAD_ERROR = load_bundle()


@asynccontextmanager
async def lifespan(_: FastAPI):
    reload_predictions_bundle()
    yield


app = FastAPI(title="trndly forecast service", lifespan=lifespan)


def _require_bundle() -> PredictionsBundle:
    if BUNDLE is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=BUNDLE_LOAD_ERROR or "predictions bundle not loaded",
        )
    return BUNDLE


# --- ROUTES ---

@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/ui/")


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return build_health(BUNDLE, error=BUNDLE_LOAD_ERROR)


@app.get("/options", response_model=OptionsResponse)
def options() -> OptionsResponse:
    """Vocabularies for the UI dropdowns, sourced from lookup.csv."""
    if not LOOKUP_CSV.exists():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"lookup.csv missing at {LOOKUP_CSV}",
        )
    return build_options()


@app.get("/trends", response_model=list[TrendRow])
def trends(
    dimension: str | None = None,
    state: str | None = None,
) -> list[TrendRow]:
    """Univariate predictions. Optional filters: ``?dimension=color_master&state=rising``."""
    return build_trend_rows(_require_bundle(), dimension=dimension, state=state)


@app.get("/forecast/fingerprint", response_model=FingerprintForecastResponse)
def forecast_fingerprint(
    product_type_id: int,
    gender_id: int,
    color_master_id: int,
    graphical_appearance_id: int,
    material_id: int,
) -> FingerprintForecastResponse:
    """Single-fingerprint forecast lookup. 404 if no precomputed match (the SPA
    routes a miss into its client-side synthesis fallback)."""
    hit = lookup_fingerprint(
        _require_bundle(),
        product_type_id=product_type_id,
        gender_id=gender_id,
        color_master_id=color_master_id,
        graphical_appearance_id=graphical_appearance_id,
        material_id=material_id,
    )
    if hit is None:
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
    return hit


# --------------------------------------------------------------------------- #
# Static UI                                                                     #
# --------------------------------------------------------------------------- #
# The buildless React demo (JSX-via-Babel) lives in trndly/frontend/. Mounted at
# /ui so it stays same-origin with the API routes — no CORS setup required.
if FRONTEND_DIR.exists():
    app.mount("/ui", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="ui")
else:
    logger.warning("Frontend directory not found at %s; /ui will 404.", FRONTEND_DIR)
