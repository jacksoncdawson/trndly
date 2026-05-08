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
    monthly_fingerprint.parquet                ← MONTHLY_FINGERPRINT_PARQUET (historical+live merged)
    monthly_univariate.parquet                 ← MONTHLY_UNIVARIATE_PARQUET (historical+live merged)
    live_monthly_fingerprint.parquet           ← LIVE_FINGERPRINT_PARQUET (live-only, pre-merge)
    live_monthly_univariate.parquet            ← LIVE_UNIVARIATE_PARQUET (live-only, pre-merge)
  backend/
  frontend/                                    ← FRONTEND_DIR
    index.html                                 ← static UI served at /ui
  tests/
"""

from __future__ import annotations

from pathlib import Path

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
MONTHLY_FINGERPRINT_PARQUET: Path = PROCESSED_DATA_DIR / "monthly_fingerprint.parquet"
MONTHLY_UNIVARIATE_PARQUET: Path = PROCESSED_DATA_DIR / "monthly_univariate.parquet"
FEATURE_TRAINING_CONTRACT_JSON: Path = PROCESSED_DATA_DIR / "feature_training_run.json"

# Live cube outputs from build_live_cube.py — same schema as the historical
# parquets, so notebook 1b can pd.concat them into the merged universe.
LIVE_FINGERPRINT_PARQUET: Path = PROCESSED_DATA_DIR / "live_monthly_fingerprint.parquet"
LIVE_UNIVARIATE_PARQUET: Path = PROCESSED_DATA_DIR / "live_monthly_univariate.parquet"

# --------------------------------------------------------------------------- #
# Helpers                                                                       #
# --------------------------------------------------------------------------- #

def ensure_data_dirs() -> None:
    """
    Create DATA_DIR, PROCESSED_DATA_DIR, and HM_KAGGLE_DIR if they don't exist.
    Idempotent — safe to call at the top of any script that is about to write
    artifacts.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DATA_DIR.mkdir(parents=True, exist_ok=True)
    HM_KAGGLE_DIR.mkdir(parents=True, exist_ok=True)
