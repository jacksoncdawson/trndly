"""
Central path registry for the trndly training + serving pipeline.

Every script should import its filesystem paths from here instead of
recomputing ``Path(__file__).resolve().parents[...]`` locally. This keeps
the data layout in one place so moves/renames are a one-line change.

DIRECTORY LAYOUT
----------------
<PROJECT_ROOT>/                                ← trndly/
  pipelines/
    collectors/                                ← COLLECTORS_DIR
    training/                                  ← TRAINING_DIR
      paths.py                                 ← this file
      feature_contract.py
      data/                                    ← DATA_DIR
        hm_kaggle/                             ← HM_KAGGLE_DIR  (raw Kaggle dump)
          articles.csv                         ← HM_ARTICLES_CSV
          transactions_train.csv               ← HM_TRANSACTIONS_CSV
        seasonality_table.csv                  ← SEASONALITY_TABLE_CSV
        train.csv / val.csv / test.csv         ← TRAIN_CSV / VAL_CSV / TEST_CSV
        user_upload_items.json                 ← USER_UPLOAD_ITEMS_JSON
        user_upload_items_with_reference.json  ← USER_UPLOAD_ITEMS_WITH_REFERENCE_JSON
      synthetic_data/
        items_<retailer>.csv                   ← per-retailer scrape output
  data/processed/                              ← PROCESSED_DATA_DIR
    lookup.csv                                 ← LOOKUP_CSV
    historical_fingerprint.parquet             ← HISTORICAL_FINGERPRINT_PARQUET (notebook 1, immutable)
    historical_fingerprint.meta.json           ← HISTORICAL_FINGERPRINT_META_JSON
    historical_univariate.parquet              ← HISTORICAL_UNIVARIATE_PARQUET (notebook 1, immutable)
    live_fingerprint_<YYYY-MM>.parquet         ← matches LIVE_FINGERPRINT_GLOB (per-snapshot-month)
    live_univariate_<YYYY-MM>.parquet          ← matches LIVE_UNIVARIATE_GLOB
    merged_fingerprint.parquet                 ← MERGED_FINGERPRINT_PARQUET (notebook 1b, always rebuilt)
    merged_univariate.parquet                  ← MERGED_UNIVARIATE_PARQUET (notebook 1b)
    training_fingerprint.parquet               ← TRAINING_FINGERPRINT_PARQUET (notebook 2)
    training_univariate.parquet                ← TRAINING_UNIVARIATE_PARQUET
    training_run.json                          ← TRAINING_RUN_JSON (notebook 2 metadata)
  backend/
  frontend/                                    ← FRONTEND_DIR
    index.html                                 ← static UI served at /ui
  tests/

Cube pipeline stages (left to right):
  notebook 1   →  historical_*           (immutable raw cube)
  build_live_cube
              →  live_*_<YYYY-MM>        (one parquet per snapshot month)
  notebook 1b  →  merged_*               (always rebuilt: historical + glob(live_*))
  notebook 2   →  training_*             (lags + targets + splits + weights)
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

# --------------------------------------------------------------------------- #
# Top-level anchors                                                             #
# --------------------------------------------------------------------------- #

# This file sits at <trndly>/pipelines/training/paths.py, so parents[2] == trndly/.
PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]

PIPELINES_DIR: Path = PROJECT_ROOT / "pipelines"
COLLECTORS_DIR: Path = PIPELINES_DIR / "collectors"
TRAINING_DIR: Path = PIPELINES_DIR / "training"

# Static demo UI, served by the FastAPI app at /ui (see scheduleServer.py).
# Lives as a sibling of backend/ since it's a project-level concern, not a
# backend-internal one.
FRONTEND_DIR: Path = PROJECT_ROOT / "frontend"

# --------------------------------------------------------------------------- #
# Training data artifacts                                                       #
# --------------------------------------------------------------------------- #

# All generated training artifacts live in DATA_DIR. Scripts should create it
# via ensure_data_dirs() rather than mkdir-ing ad hoc.
DATA_DIR: Path = TRAINING_DIR / "data"

# H&M-derived historical seasonality curves. Written by
# hmn_seasonal_processor.py, consumed everywhere compute_seasonal_features is
# used (training row assembly and inference).
SEASONALITY_TABLE_CSV: Path = DATA_DIR / "seasonality_table.csv"

# Model training splits produced by hmn_seasonal_processor.py (or the legacy
# synthetic generator).
TRAIN_CSV: Path = DATA_DIR / "train.csv"
VAL_CSV: Path = DATA_DIR / "val.csv"
TEST_CSV: Path = DATA_DIR / "test.csv"

# Synthetic upload payloads used for smoke-testing the API surface.
USER_UPLOAD_ITEMS_JSON: Path = DATA_DIR / "user_upload_items.json"
USER_UPLOAD_ITEMS_WITH_REFERENCE_JSON: Path = DATA_DIR / "user_upload_items_with_reference.json"

# --------------------------------------------------------------------------- #
# H&M Kaggle raw dump                                                           #
# --------------------------------------------------------------------------- #

# Raw H&M Kaggle files live under DATA_DIR so they (a) stay alongside the other
# pipeline artifacts and (b) are captured by the `**/data/` .gitignore rule,
# preventing the 3+ GB transactions file from being committed. Download once
# with the Kaggle CLI; hmn_seasonal_processor.py reads from here by default.
HM_KAGGLE_DIR: Path = DATA_DIR / "hm_kaggle"
HM_ARTICLES_CSV: Path = HM_KAGGLE_DIR / "articles.csv"
HM_TRANSACTIONS_CSV: Path = HM_KAGGLE_DIR / "transactions_train.csv"

# --------------------------------------------------------------------------- #
# Cube outputs (data/processed/ — gitignored batch artifacts)                  #
# --------------------------------------------------------------------------- #

PROCESSED_DATA_DIR: Path = PROJECT_ROOT / "data" / "processed"
LOOKUP_CSV: Path = PROCESSED_DATA_DIR / "lookup.csv"

# Stage 1: notebook 1 outputs — immutable raw cube + run metadata.
HISTORICAL_FINGERPRINT_PARQUET: Path = PROCESSED_DATA_DIR / "historical_fingerprint.parquet"
HISTORICAL_FINGERPRINT_META_JSON: Path = PROCESSED_DATA_DIR / "historical_fingerprint.meta.json"
HISTORICAL_UNIVARIATE_PARQUET: Path = PROCESSED_DATA_DIR / "historical_univariate.parquet"

# Stage 2: build_live_cube outputs — one parquet per snapshot month.
# build_live_cube emits live_fingerprint_<YYYY-MM>.parquet using the helpers
# below; notebook 1b discovers them by globbing.
LIVE_FINGERPRINT_GLOB: str = "live_fingerprint_*.parquet"
LIVE_UNIVARIATE_GLOB: str = "live_univariate_*.parquet"

# Stage 3: notebook 1b output — always rebuilt from historical + glob(live_*).
MERGED_FINGERPRINT_PARQUET: Path = PROCESSED_DATA_DIR / "merged_fingerprint.parquet"
MERGED_UNIVARIATE_PARQUET: Path = PROCESSED_DATA_DIR / "merged_univariate.parquet"

# Stage 4: notebook 2 output — lag/target/split/weight prepped for training.
TRAINING_FINGERPRINT_PARQUET: Path = PROCESSED_DATA_DIR / "training_fingerprint.parquet"
TRAINING_UNIVARIATE_PARQUET: Path = PROCESSED_DATA_DIR / "training_univariate.parquet"
TRAINING_RUN_JSON: Path = PROCESSED_DATA_DIR / "training_run.json"

# --------------------------------------------------------------------------- #
# Helpers                                                                       #
# --------------------------------------------------------------------------- #

_LIVE_DATE_RE = re.compile(r"_(\d{4}-\d{2})\.parquet$")


def _format_month(month) -> str:
    """Coerce ``month`` (datetime/Timestamp/'YYYY-MM-DD' string) to ``'YYYY-MM'``."""
    return pd.Timestamp(month).strftime("%Y-%m")


def live_fingerprint_path_for(month) -> Path:
    """Path for the per-month live fingerprint parquet, e.g. for the
    May 2026 snapshot: ``data/processed/live_fingerprint_2026-05.parquet``."""
    return PROCESSED_DATA_DIR / f"live_fingerprint_{_format_month(month)}.parquet"


def live_univariate_path_for(month) -> Path:
    """Path for the per-month live univariate parquet, e.g. for May 2026:
    ``data/processed/live_univariate_2026-05.parquet``."""
    return PROCESSED_DATA_DIR / f"live_univariate_{_format_month(month)}.parquet"


def discover_live_fingerprint_parquets() -> list[Path]:
    """Return every ``live_fingerprint_<YYYY-MM>.parquet`` in PROCESSED_DATA_DIR,
    sorted by month."""
    return sorted(PROCESSED_DATA_DIR.glob(LIVE_FINGERPRINT_GLOB))


def discover_live_univariate_parquets() -> list[Path]:
    """Return every ``live_univariate_<YYYY-MM>.parquet`` in PROCESSED_DATA_DIR,
    sorted by month."""
    return sorted(PROCESSED_DATA_DIR.glob(LIVE_UNIVARIATE_GLOB))


def ensure_data_dirs() -> None:
    """
    Create DATA_DIR, PROCESSED_DATA_DIR, and HM_KAGGLE_DIR if they don't exist.
    Idempotent — safe to call at the top of any script that is about to write
    artifacts.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DATA_DIR.mkdir(parents=True, exist_ok=True)
    HM_KAGGLE_DIR.mkdir(parents=True, exist_ok=True)
