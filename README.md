# trndly

Fashion trend forecasting for secondhand apparel resellers. A monthly
batch tick scrapes retailer catalogs, retrains two RandomForest
regressors, and **precomputes 6-horizon catalog-share forecasts** for
the entire universe of (dimension, level) pairs and 5-D fingerprints.
The FastAPI service is a read-only layer over the predictions parquet —
no live `model.predict()` calls in the request path.

## Demo

Product preview:

![Product preview recording](./trndly_demo.gif)

## Repo layout

The project root has minimal scaffolding; the code lives one level down
in `trndly/`.

```
.
├── README.md                  ← this file
├── trndly_demo.gif            ← product preview recording
├── TODO.md                    ← forward-looking work list
├── mlflow.db                  ← local MLflow tracking store (gitignored)
├── project_materials/         ← pitch deck, demo videos, checkpoints
└── trndly/                    ← the application
    ├── pipelines/
    │   ├── paths.py           — central path registry
    │   ├── contracts.py       — schema validators
    │   ├── cube_slicing.py    — shared cube → feature-row helpers
    │   ├── collectors/        — 4 retail scrapers + build_live_cube.py
    │   └── monthly/           — the monthly tick (scrape → ... → predict)
    ├── backend/services/
    │   └── scheduleServer.py  — FastAPI service (read-only over predictions)
    ├── frontend/              — React SPA, no build step (JSX-via-Babel + SWR)
    ├── notebooks/             — 0 (Kaggle clean), 1 (historical agg), 4 (HP sweep)
    ├── tests/                 — unit, contract, and integration tests
    ├── data/                  — raw / reference / processed / models / predictions
    ├── docs/                  — architecture, API reference, monthly-tick runbook
    └── experiments/           — one-off measurement / A-B scripts
```

Two RandomForest regressors:

- **Univariate** — one row per `(dimension, level_id)`. Powers trend exploration.
- **Fingerprint** — one row per 5-D `(product_type, gender, color_master, graphical_appearance, material)`. Powers per-item recommendations.

## Quick start

```bash
cd trndly                                                  # the inner package dir
python -m venv .venv
./scripts/setup_venv.sh                                    # pip install + playwright install chromium

# One-time bootstrap from the H&M Kaggle dump (places it in data/raw/kaggle/)
.venv/bin/python notebooks/_run_notebook.py notebooks/0_clean_historical.ipynb
.venv/bin/python notebooks/_run_notebook.py notebooks/1_aggregate_historical.ipynb

# Monthly tick: scrape → aggregate → features → train → evaluate → predict
.venv/bin/python -m pipelines.monthly run

# Or skip scrape if items_*.csv already on disk
.venv/bin/python -m pipelines.monthly run --skip-scrape

# (Stopgap, today only) Backfill synthetic Feb/Mar/Apr 2026 lag rows so
# the predictor can anchor on the most recent live scrape (2026-05)
# instead of falling back to the 2020-08 historical block. Re-run
# pipelines.monthly predict afterward. Remove once ≥4 contiguous live
# months have been scraped — see TODO.md "Sparse cube" section.
.venv/bin/python scripts/backfill_anchor_lags.py
.venv/bin/python -m pipelines.monthly predict

# Serve the API (FastAPI + static React UI at /ui)
.venv/bin/python -m uvicorn backend.services.scheduleServer:app --port 8000
```

Then open `http://localhost:8000/ui/` for the React app or
`http://localhost:8000/docs` for Swagger UI.

## The monthly tick

`python -m pipelines.monthly run` drives six stages end-to-end:


| Stage       | What it does                                         | Output                                                       |
| ----------- | ---------------------------------------------------- | ------------------------------------------------------------ |
| `scrape`    | Subprocess each retailer scraper + `build_live_cube` | `data/raw/items/`, `data/processed/live_*_<YYYY-MM>.parquet` |
| `aggregate` | Concat historical + live cubes with dedup            | `data/processed/merged_*.parquet`                            |
| `features`  | Calendar-strict windowing (lags + 6 targets)         | `data/processed/training_*.parquet`                          |
| `train`     | Fit RandomForest, log to MLflow, persist joblibs     | `data/models/*.joblib`, `model_training_run.json`            |
| `evaluate`  | Compare candidate vs incumbent WMAE; auto-promote    | `data/models/champion_metrics.json` (on promotion)           |
| `predict`   | Score the universe, classify state, write parquet    | `data/predictions/predictions_*_<YYYY-MM>.parquet`           |


Stages are also runnable individually:

```bash
python -m pipelines.monthly aggregate
python -m pipelines.monthly evaluate
```

After a tick, restart the FastAPI service to pick up the new predictions.

See [trndly/docs/monthly_tick.md](trndly/docs/monthly_tick.md) for the
full operator runbook (prereqs, debugging, common failures).

## API surface

All routes are `GET`; no POST triggers a model call.


| Route                                                                                                          | Behavior                                               |
| -------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------ |
| `GET /options`                                                                                                 | Dropdown vocabularies, `[{name, id}]` per category     |
| `GET /trends`                                                                                                  | Univariate predictions. Filters: `?dimension=&state=`  |
| `GET /forecast/fingerprint?product_type_id=&gender_id=&color_master_id=&graphical_appearance_id=&material_id=` | One fingerprint forecast (404 if no precomputed match) |
| `GET /health`                                                                                                  | Bundle status + anchor month                           |
| `/ui/*`                                                                                                        | Static React app                                       |


Full request/response shapes in [trndly/docs/api.md](trndly/docs/api.md).

## Testing

```bash
cd trndly
.venv/bin/python -m pytest tests/         # full suite
.venv/bin/python -m pytest tests/monthly/ # tick-stage unit tests
```

220+ tests covering schema validators, lookup-csv consistency, cube
concat-compatibility, items-CSV ID validity, trend-state classification,
evaluator promotion logic, and predictions-parquet integration.

Live retailer smoke checks are gated behind `pytest -m live`.

## Documentation

- [trndly/docs/architecture.md](trndly/docs/architecture.md) — shipped architecture + future GCP target
- [trndly/docs/api.md](trndly/docs/api.md) — endpoint reference with example bodies
- [trndly/docs/monthly_tick.md](trndly/docs/monthly_tick.md) — operator runbook
- [trndly/docs/rationale.md](trndly/docs/rationale.md) — design decisions (SWR, SPA, IaC)
- [trndly/pipelines/collectors/README.md](trndly/pipelines/collectors/README.md) — scrapers, items.csv schema, brittle areas
- [trndly/data/reference/SCHEMA.md](trndly/data/reference/SCHEMA.md) — per-dimension reachability audit
- [TODO.md](TODO.md) — forward-looking work + brittle-area watchlist

## Status

**Shipped:** monthly batch architecture (manual cadence), precomputed predictions, read-only FastAPI service, React frontend wired to API via SWR.

**Future** (out of scope for current MVP):

- Storage migration: local parquet → GCS / BigQuery
- Cloud cadence: manual CLI → Cloud Scheduler + Vertex Custom Container job
- Frontend hosting split: Firebase Hosting (static) + Cloud Run (API)
- Auth: Firebase Auth + per-user inventory in Firestore
- Container split: separate images for collectors / monthly tick / API

