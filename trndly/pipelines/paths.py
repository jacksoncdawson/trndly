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
        items_<retailer>_<YYYY-MM>.csv         ← items_csv_path_for(retailer[, month])  (immutable per-month raw)
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
      champion_metrics.json                    ← evaluate.py promotion record (per-model champion)
      runs/<run_id>/                           ← MODEL_RUNS_DIR / run_id (archived joblibs per run; champion guard)
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

# Static demo UI (buildless React SPA). Served as static files on Firebase
# Hosting (Phase 2); also mounted at /ui by the local dev server.
FRONTEND_DIR: Path = PROJECT_ROOT / "frontend"

# Where the publisher (pipelines.monthly.publish) writes browser-ready JSON.
# Same-origin under the SPA so it fetches ./data/<name>.json; the canonical
# month-less files are the "latest pointer" the SPA reads (cache-busted via
# Hosting Cache-Control), alongside versioned <name>_<YYYY-MM>.json archives.
FRONTEND_DATA_DIR: Path = FRONTEND_DIR / "data"

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

# Per-run joblib archive for the local champion guard (evaluate.py): each run's
# canonical joblibs are copied to data/models/runs/<run_id>/ so a losing
# candidate can be reverted to the prior champion's weights.
MODEL_RUNS_DIR: Path = MODELS_DIR / "runs"


def model_run_dir_for(run_id: str) -> Path:
    """Directory holding one training run's archived joblibs + manifest."""
    return MODEL_RUNS_DIR / run_id

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

# Immutable raw landing zone: items_<retailer>_<YYYY-MM>.csv. The back-compat
# glob also matches the legacy unsuffixed items_<retailer>.csv during transition.
ITEMS_FILE_GLOB: str = "items_*.csv"
_ITEMS_MONTH_RE = re.compile(r"^items_(?P<retailer>.+)_(?P<month>\d{4}-\d{2})\.csv$")
_ITEMS_LEGACY_RE = re.compile(r"^items_(?P<retailer>.+)\.csv$")


def _format_month(month) -> str:
    """Coerce ``month`` (datetime/Timestamp/'YYYY-MM-DD' string) to ``'YYYY-MM'``."""
    return pd.Timestamp(month).strftime("%Y-%m")


def items_csv_path_for(retailer: str, month=None) -> Path:
    """Path for a retailer's immutable per-month items CSV, e.g. for May 2026:
    ``data/raw/items/items_gap_2026-05.csv``.

    Within-month re-runs overwrite that month's file; prior months are preserved
    (the immutable raw landing zone). ``month`` defaults to the current month —
    a scrape writes "now"'s snapshot.
    """
    stamp = _format_month(month if month is not None else pd.Timestamp.now())
    return RAW_ITEMS_DIR / f"items_{retailer}_{stamp}.csv"


def _parse_items_filename(name: str) -> tuple[str | None, str | None]:
    """Return ``(retailer, month)`` for an items CSV filename, ``month=None`` for
    the legacy unsuffixed form. ``(None, None)`` if it isn't an items file."""
    m = _ITEMS_MONTH_RE.match(name)
    if m:
        return m.group("retailer"), m.group("month")
    m = _ITEMS_LEGACY_RE.match(name)
    if m:
        return m.group("retailer"), None
    return None, None


def discover_items_files(signals_dir: Path | None = None) -> list[Path]:
    """Discover items CSVs, preferring the immutable monthly files.

    Matches both ``items_<retailer>_<YYYY-MM>.csv`` and the legacy
    ``items_<retailer>.csv`` (back-compat). To avoid double-counting a retailer
    that has both forms on disk, the legacy file is used only when *no* monthly
    file exists for that retailer; otherwise every monthly file is returned.
    """
    base = signals_dir if signals_dir is not None else RAW_ITEMS_DIR
    monthly_by_retailer: dict[str, list[Path]] = {}
    legacy_by_retailer: dict[str, Path] = {}
    for p in sorted(base.glob(ITEMS_FILE_GLOB)):
        # Skip the scrapers' in-progress resume files (items_<retailer>_<month>_partial.csv,
        # StreamingItemWriter). A crashed run leaves one behind; it shares the full
        # item schema, so without this it would parse as a phantom retailer and get
        # double-counted into the cube on the next build_cube.
        if p.name.endswith("_partial.csv"):
            continue
        retailer, month = _parse_items_filename(p.name)
        if retailer is None:
            continue
        if month is None:
            legacy_by_retailer[retailer] = p
        else:
            monthly_by_retailer.setdefault(retailer, []).append(p)

    selected: list[Path] = []
    for paths in monthly_by_retailer.values():
        selected.extend(paths)
    for retailer, p in legacy_by_retailer.items():
        if retailer not in monthly_by_retailer:
            selected.append(p)
    return sorted(selected)


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


# --------------------------------------------------------------------------- #
# Per-tick checkpoints (plan §12)                                               #
# --------------------------------------------------------------------------- #
#
# Each monthly tick writes an IMMUTABLE checkpoint under data/ticks/<YYYY-MM>/
# (merged → training → model candidate → predictions → published), never
# overwriting a prior month. `historical_*` / `live_*` stay in processed/ as
# shared cumulative inputs; the cross-tick champion lives in models/.

TICKS_DIR: Path = DATA_DIR / "ticks"
_TICK_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")

MODEL_ROLES: tuple[str, ...] = ("fingerprint", "univariate")

# Cross-tick champion pointer: {model: {month, holdout_wmae, ...}}, written by
# evaluate on a promotion. The canonical champion weights predict loads are
# FINGERPRINT_MODEL_JOBLIB / UNIVARIATE_MODEL_JOBLIB above.
CHAMPION_JSON: Path = MODELS_DIR / "champion.json"


def champion_joblib_for(role: str) -> Path:
    """Canonical champion joblib (the weights predict loads) for a model role."""
    return FINGERPRINT_MODEL_JOBLIB if role == "fingerprint" else UNIVARIATE_MODEL_JOBLIB


def current_tick_month() -> pd.Timestamp:
    """Default tick month: the current calendar month, normalized to the 1st."""
    return pd.Timestamp.now().normalize().replace(day=1)


def tick_dir(month) -> Path:
    """Checkpoint directory for one tick, e.g. ``data/ticks/2026-06``."""
    return TICKS_DIR / _format_month(month)


def tick_merged_path(month, role: str) -> Path:
    return tick_dir(month) / f"merged_{role}.parquet"


def tick_training_path(month, role: str) -> Path:
    return tick_dir(month) / f"training_{role}.parquet"


def tick_training_run_json(month) -> Path:
    return tick_dir(month) / "training_run.json"


def tick_model_dir(month) -> Path:
    """This tick's CANDIDATE model dir (not the champion)."""
    return tick_dir(month) / "model"


def tick_model_joblib(month, role: str) -> Path:
    return tick_model_dir(month) / f"{role}_model.joblib"


def tick_model_training_run_json(month) -> Path:
    return tick_model_dir(month) / "model_training_run.json"


def tick_predictions_path(month, role: str) -> Path:
    return tick_dir(month) / f"predictions_{role}.parquet"


def tick_published_dir(month) -> Path:
    return tick_dir(month) / "published"


def tick_manifest_json(month) -> Path:
    return tick_dir(month) / "manifest.json"


def tick_success_marker(month) -> Path:
    return tick_dir(month) / "_SUCCESS"


def tick_is_complete(month) -> bool:
    """True when this tick's ``_SUCCESS`` marker exists (publish finished)."""
    return tick_success_marker(month).exists()


def discover_ticks() -> list[Path]:
    """Every ``ticks/<YYYY-MM>/`` directory, sorted by month."""
    if not TICKS_DIR.exists():
        return []
    return sorted(
        p for p in TICKS_DIR.iterdir() if p.is_dir() and _TICK_MONTH_RE.match(p.name)
    )


def latest_successful_tick() -> Path | None:
    """Most recent ``ticks/<YYYY-MM>/`` with a ``_SUCCESS`` marker (or None)."""
    done = [p for p in discover_ticks() if (p / "_SUCCESS").exists()]
    return done[-1] if done else None


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
        TICKS_DIR,
    ):
        d.mkdir(parents=True, exist_ok=True)
