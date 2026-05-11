"""
Central path registry for the trndly training + serving pipeline.

Every script should import its filesystem paths from here instead of
recomputing ``Path(__file__).resolve().parents[...]`` locally. This keeps
the data layout in one place so moves/renames are a one-line change.

DIRECTORY LAYOUT
----------------
<PROJECT_ROOT>/                                ← trndly/
  pipelines/
    paths.py                                   ← this file
    contracts.py                               ← live cube + predictions schema validators
    collectors/                                ← COLLECTORS_DIR
    monthly/                                   ← scrape → aggregate → features → train → ...
    serving/
  data/                                        ← DATA_DIR
    raw/                                       ← RAW_DIR
      kaggle/                                  ← RAW_KAGGLE_DIR
        articles.csv                           ← HM_ARTICLES_CSV
        transactions_train.csv                 ← HM_TRANSACTIONS_CSV
      items/                                   ← RAW_ITEMS_DIR
        items_<retailer>.csv                   ← items_csv_path_for(retailer)
    reference/                                 ← REFERENCE_DIR
      lookup.csv                               ← LOOKUP_CSV
      SCHEMA.md                                ← SCHEMA_MD
    processed/                                 ← PROCESSED_DIR
      historical_fingerprint.parquet           ← HISTORICAL_FINGERPRINT_PARQUET (notebook 1, immutable)
      historical_fingerprint.meta.json         ← HISTORICAL_FINGERPRINT_META_JSON
      historical_univariate.parquet            ← HISTORICAL_UNIVARIATE_PARQUET (notebook 1, immutable)
      live_fingerprint_<YYYY-MM>.parquet       ← matches LIVE_FINGERPRINT_GLOB (per-snapshot-month)
      live_univariate_<YYYY-MM>.parquet        ← matches LIVE_UNIVARIATE_GLOB
      merged_fingerprint.parquet               ← MERGED_FINGERPRINT_PARQUET (pipelines.monthly.aggregate)
      merged_univariate.parquet                ← MERGED_UNIVARIATE_PARQUET (pipelines.monthly.aggregate)
      training_fingerprint.parquet             ← TRAINING_FINGERPRINT_PARQUET (pipelines.monthly.features)
      training_univariate.parquet              ← TRAINING_UNIVARIATE_PARQUET
      training_run.json                        ← TRAINING_RUN_JSON
    models/                                    ← MODELS_DIR
      fingerprint_model.joblib                 ← FINGERPRINT_MODEL_JOBLIB
      univariate_model.joblib                  ← UNIVARIATE_MODEL_JOBLIB
      model_training_run.json                  ← MODEL_TRAINING_RUN_JSON
      champion_metrics.json                    ← evaluate.py promotion record
    predictions/                               ← PREDICTIONS_DIR
      predictions_univariate_<YYYY-MM>.parquet  ← matches PREDICTIONS_UNIVARIATE_GLOB
      predictions_fingerprint_<YYYY-MM>.parquet ← matches PREDICTIONS_FINGERPRINT_GLOB
  backend/
  frontend/                                    ← FRONTEND_DIR
  tests/

Cube pipeline stages (left to right):
  notebook 1                          →  historical_*       (immutable raw cube)
  build_live_cube                     →  live_*_<YYYY-MM>   (one parquet per snapshot month)
  pipelines.monthly.aggregate         →  merged_*           (always rebuilt: historical + glob(live_*))
  pipelines.monthly.features          →  training_*         (lags + targets + splits + weights)
  pipelines.monthly.train             →  data/models/*.joblib
  pipelines.monthly.predict           →  data/predictions/*.parquet
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

# --------------------------------------------------------------------------- #
# Top-level anchors                                                             #
# --------------------------------------------------------------------------- #

# This file sits at <trndly>/pipelines/paths.py, so parents[1] == trndly/.
PROJECT_ROOT: Path = Path(__file__).resolve().parents[1]

PIPELINES_DIR: Path = PROJECT_ROOT / "pipelines"
COLLECTORS_DIR: Path = PIPELINES_DIR / "collectors"

# Static demo UI, served by the FastAPI app at /ui (see scheduleServer.py).
FRONTEND_DIR: Path = PROJECT_ROOT / "frontend"

# --------------------------------------------------------------------------- #
# Canonical data tree (PROJECT_ROOT/data, gitignored except RAW_ITEMS_DIR)     #
# --------------------------------------------------------------------------- #

DATA_DIR: Path = PROJECT_ROOT / "data"

RAW_DIR: Path = DATA_DIR / "raw"
RAW_KAGGLE_DIR: Path = RAW_DIR / "kaggle"
RAW_ITEMS_DIR: Path = RAW_DIR / "items"

# H&M Kaggle raw dump. Download once with the Kaggle CLI.
HM_ARTICLES_CSV: Path = RAW_KAGGLE_DIR / "articles.csv"
HM_TRANSACTIONS_CSV: Path = RAW_KAGGLE_DIR / "transactions_train.csv"

REFERENCE_DIR: Path = DATA_DIR / "reference"
LOOKUP_CSV: Path = REFERENCE_DIR / "lookup.csv"
SCHEMA_MD: Path = REFERENCE_DIR / "SCHEMA.md"

PROCESSED_DIR: Path = DATA_DIR / "processed"

MODELS_DIR: Path = DATA_DIR / "models"
FINGERPRINT_MODEL_JOBLIB: Path = MODELS_DIR / "fingerprint_model.joblib"
UNIVARIATE_MODEL_JOBLIB: Path = MODELS_DIR / "univariate_model.joblib"
MODEL_TRAINING_RUN_JSON: Path = MODELS_DIR / "model_training_run.json"

PREDICTIONS_DIR: Path = DATA_DIR / "predictions"
PREDICTIONS_UNIVARIATE_GLOB: str = "predictions_univariate_*.parquet"
PREDICTIONS_FINGERPRINT_GLOB: str = "predictions_fingerprint_*.parquet"

# --------------------------------------------------------------------------- #
# Cube outputs (data/processed/ — gitignored batch artifacts)                  #
# --------------------------------------------------------------------------- #

# Stage 1: notebook 1 outputs — immutable raw cube + run metadata.
HISTORICAL_FINGERPRINT_PARQUET: Path = PROCESSED_DIR / "historical_fingerprint.parquet"
HISTORICAL_FINGERPRINT_META_JSON: Path = PROCESSED_DIR / "historical_fingerprint.meta.json"
HISTORICAL_UNIVARIATE_PARQUET: Path = PROCESSED_DIR / "historical_univariate.parquet"

# Stage 2: build_live_cube outputs — one parquet per snapshot month.
LIVE_FINGERPRINT_GLOB: str = "live_fingerprint_*.parquet"
LIVE_UNIVARIATE_GLOB: str = "live_univariate_*.parquet"

# Stage 3: pipelines.monthly.aggregate output — rebuilt from historical + glob(live_*).
MERGED_FINGERPRINT_PARQUET: Path = PROCESSED_DIR / "merged_fingerprint.parquet"
MERGED_UNIVARIATE_PARQUET: Path = PROCESSED_DIR / "merged_univariate.parquet"

# Stage 4: pipelines.monthly.features output — lag/target/split/weight prepped for training.
TRAINING_FINGERPRINT_PARQUET: Path = PROCESSED_DIR / "training_fingerprint.parquet"
TRAINING_UNIVARIATE_PARQUET: Path = PROCESSED_DIR / "training_univariate.parquet"
TRAINING_RUN_JSON: Path = PROCESSED_DIR / "training_run.json"

# --------------------------------------------------------------------------- #
# Helpers                                                                       #
# --------------------------------------------------------------------------- #

_LIVE_DATE_RE = re.compile(r"_(\d{4}-\d{2})\.parquet$")


def _format_month(month) -> str:
    """Coerce ``month`` (datetime/Timestamp/'YYYY-MM-DD' string) to ``'YYYY-MM'``."""
    return pd.Timestamp(month).strftime("%Y-%m")


def items_csv_path_for(retailer: str) -> Path:
    """Path for a retailer's items CSV, e.g. ``data/raw/items/items_gap.csv``."""
    return RAW_ITEMS_DIR / f"items_{retailer}.csv"


def live_fingerprint_path_for(month) -> Path:
    """Path for the per-month live fingerprint parquet, e.g. for the
    May 2026 snapshot: ``data/processed/live_fingerprint_2026-05.parquet``."""
    return PROCESSED_DIR / f"live_fingerprint_{_format_month(month)}.parquet"


def live_univariate_path_for(month) -> Path:
    """Path for the per-month live univariate parquet, e.g. for May 2026:
    ``data/processed/live_univariate_2026-05.parquet``."""
    return PROCESSED_DIR / f"live_univariate_{_format_month(month)}.parquet"


def discover_live_fingerprint_parquets() -> list[Path]:
    """Return every ``live_fingerprint_<YYYY-MM>.parquet`` in PROCESSED_DIR,
    sorted by month."""
    return sorted(PROCESSED_DIR.glob(LIVE_FINGERPRINT_GLOB))


def discover_live_univariate_parquets() -> list[Path]:
    """Return every ``live_univariate_<YYYY-MM>.parquet`` in PROCESSED_DIR,
    sorted by month."""
    return sorted(PROCESSED_DIR.glob(LIVE_UNIVARIATE_GLOB))


def predictions_univariate_path_for(month) -> Path:
    """Path for the per-month univariate predictions parquet, e.g. for May 2026:
    ``data/predictions/predictions_univariate_2026-05.parquet``."""
    return PREDICTIONS_DIR / f"predictions_univariate_{_format_month(month)}.parquet"


def predictions_fingerprint_path_for(month) -> Path:
    """Path for the per-month fingerprint predictions parquet, e.g. for May 2026:
    ``data/predictions/predictions_fingerprint_2026-05.parquet``."""
    return PREDICTIONS_DIR / f"predictions_fingerprint_{_format_month(month)}.parquet"


def discover_predictions_univariate_parquets() -> list[Path]:
    """Return every ``predictions_univariate_<YYYY-MM>.parquet`` in PREDICTIONS_DIR,
    sorted by month."""
    return sorted(PREDICTIONS_DIR.glob(PREDICTIONS_UNIVARIATE_GLOB))


def discover_predictions_fingerprint_parquets() -> list[Path]:
    """Return every ``predictions_fingerprint_<YYYY-MM>.parquet`` in PREDICTIONS_DIR,
    sorted by month."""
    return sorted(PREDICTIONS_DIR.glob(PREDICTIONS_FINGERPRINT_GLOB))


def latest_predictions_univariate_parquet() -> Path | None:
    """Return the most-recent ``predictions_univariate_*.parquet`` (or None)."""
    paths = discover_predictions_univariate_parquets()
    return paths[-1] if paths else None


def latest_predictions_fingerprint_parquet() -> Path | None:
    """Return the most-recent ``predictions_fingerprint_*.parquet`` (or None)."""
    paths = discover_predictions_fingerprint_parquets()
    return paths[-1] if paths else None


def ensure_data_dirs() -> None:
    """
    Create every writable subdirectory of DATA_DIR if it doesn't exist.
    Idempotent — safe to call at the top of any script that writes artifacts.
    """
    for d in (
        RAW_DIR,
        RAW_KAGGLE_DIR,
        RAW_ITEMS_DIR,
        REFERENCE_DIR,
        PROCESSED_DIR,
        MODELS_DIR,
        PREDICTIONS_DIR,
    ):
        d.mkdir(parents=True, exist_ok=True)
