# trndly — Architecture

trndly is a fashion trend forecasting platform that helps secondhand
apparel resellers list and source inventory at the right time. Two
RandomForest regressors produce 6-horizon catalog-share forecasts:

- **Univariate model** — one row per `(dimension, level_id)`. Trains on
per-feature time series (every color, every material, every product
type, etc.) at the cube grain. Used for trend exploration ("which
colors will be rising in 6 months?").
- **Fingerprint model** — one row per 5-D fingerprint
`(product_type_id, gender_id, color_master_id, graphical_appearance_id, material_id)`. Used for per-item recommendations ("when should I list
this specific blazer?").

This document describes the **shipped** architecture. A `Future` section
at the bottom captures the GCP target we're working toward.

---

## Data flow (shipped)

```
                ┌──────────────────────────────────────────────┐
                │  pipelines/collectors/                       │
                │    {gap,uniqlo,american_eagle,hollister}_    │
                │  scraper.py                                │
                │      └─► data/raw/items/items_<retailer>.csv │
                │  build_live_cube.py                          │
                │      └─► data/processed/live_*_<YYYY-MM>.parquet
                └──────────────────────────────────────────────┘
                              │
                              ▼
                ┌──────────────────────────────────────────────┐
                │  pipelines/monthly/  (the monthly tick)      │
                │    scrape    — runs collectors + build_live  │
                │    aggregate — historical + live → merged_*  │
                │    features  — calendar-strict windows       │
                │    train     — RF fit, write joblibs         │
                │    evaluate  — auto-promote on WMAE          │
                │    predict   — score universe → predictions/ │
                │    cli       — `python -m pipelines.monthly run`
                └──────────────────────────────────────────────┘
                              │
                              ▼
                ┌──────────────────────────────────────────────┐
                │  data/predictions/                           │
                │    predictions_univariate_<YYYY-MM>.parquet  │
                │    predictions_fingerprint_<YYYY-MM>.parquet │
                └──────────────────────────────────────────────┘
                              │
                              ▼
                ┌──────────────────────────────────────────────┐
                │  backend/services/scheduleServer.py (FastAPI)│
                │    GET /options       — dropdown vocabularies│
                │    GET /trends        — univariate predictions
                │    GET /forecast/fingerprint — single 5-D row│
                │    GET /health        — bundle status        │
                │    /ui/  → static React app (no build step)  │
                └──────────────────────────────────────────────┘
                              │
                              ▼
                ┌──────────────────────────────────────────────┐
                │  frontend/  (React + JSX-via-Babel + SWR)    │
                │    Trends screen      ← GET /trends          │
                │    Add Item screen    ← GET /options         │
                │    Item Detail screen ← GET /forecast/fingerprint
                │    Inventory          (session state)        │
                └──────────────────────────────────────────────┘
```

Inference is **precomputed monthly**. The API is a read-only layer over
the predictions parquet — there are no live `model.predict()` calls in
the request path.

---

## Repo layout (the bits worth knowing)

```
trndly/
├── pipelines/
│   ├── paths.py            — central path registry (the chokepoint)
│   ├── contracts.py        — schema validators (live cubes + predictions)
│   ├── cube_slicing.py     — shared cube → feature-row helpers
│   ├── collectors/         — 4 retail scrapers + build_live_cube.py
│   ├── monthly/            — the monthly tick (scrape→aggregate→...→predict)
│   │   ├── scrape.py
│   │   ├── aggregate.py
│   │   ├── features.py
│   │   ├── train.py
│   │   ├── evaluate.py
│   │   ├── state.py        — trend-state classifier
│   │   ├── predict.py
│   │   └── cli.py          — `python -m pipelines.monthly`
│   └── ...
├── backend/services/
│   └── scheduleServer.py   — FastAPI service (read-only over predictions)
├── frontend/               — React SPA, no build step
├── notebooks/              — 0 (Kaggle clean), 1 (historical agg), 4 (HP sweep)
├── tests/
└── data/
    ├── raw/{items,kaggle}/
    ├── reference/          — lookup.csv, SCHEMA.md
    ├── processed/          — historical/live/merged/training parquets
    ├── models/             — fingerprint_model.joblib, univariate_model.joblib,
    │                         champion_metrics.json
    └── predictions/        — predictions_*_<YYYY-MM>.parquet (per monthly tick)
```

---

## The monthly tick (`python -m pipelines.monthly run`)

Stages in order:


| #   | Stage       | Inputs                                         | Outputs                                                                                 |
| --- | ----------- | ---------------------------------------------- | --------------------------------------------------------------------------------------- |
| 1   | `scrape`    | retailer APIs                                  | `data/raw/items/items_*.csv`, `data/processed/live_*_<YYYY-MM>.parquet`                 |
| 2   | `aggregate` | historical + live cubes                        | `data/processed/merged_*.parquet`                                                       |
| 3   | `features`  | merged cubes                                   | `data/processed/training_*.parquet`, `training_run.json`                                |
| 4   | `train`     | training tables                                | `data/models/*.joblib`, `model_training_run.json`                                       |
| 5   | `evaluate`  | candidate manifest + `champion_metrics.json`   | promotion decision; updates `champion_metrics.json` if candidate wins                   |
| 6   | `predict`   | champion joblibs + merged cubes + `lookup.csv` | `data/predictions/predictions_*_<YYYY-MM>.parquet` (with state classification baked in) |


Stages can be invoked individually:

```bash
python -m pipelines.monthly aggregate
python -m pipelines.monthly run --skip-scrape   # use existing items_*.csv
```

**Cadence:** manual for now. The CLI is the one place that drives the
chain. Cloud Scheduler / Vertex AI wiring is in the `Future` section.

**Promotion rule:** for each model independently, if candidate
`holdout_wmae <= incumbent.holdout_wmae`, promote (write new
`champion_metrics.json`). On a tie, candidate wins.

**Trend state classification** (`pipelines/monthly/state.py`):

- `peak`: argmax(y_h0..h6) ≤ 1 AND series declines by horizon 6
- `rising`: y_h6 > 1.15 × y_h0
- `falling`: y_h6 < 0.85 × y_h0
- `flat`: otherwise

Numbers are placeholders flagged for tuning on real data.

---

## Predictions parquet (binding contract)

Two parquets per monthly tick. Schema validators in `pipelines/contracts.py`.

`**predictions_univariate_<YYYY-MM>.parquet`** — 13 columns:

```
anchor_month, model_version,
dimension, level_id, level_name,
y_h1, y_h2, y_h3, y_h4, y_h5, y_h6,
state, stat
```

`**predictions_fingerprint_<YYYY-MM>.parquet**` — 20 columns:

```
anchor_month, model_version,
product_type_id, gender_id, color_master_id, graphical_appearance_id, material_id,
product_type_name, gender_name, color_master_name, graphical_appearance_name, material_name,
y_h1, ..., y_h6,
state, stat
```

Rows where the cube lacks t-3..t-1 lag history are skipped — predictions
parquet only contains rows for which a forecast was producible.

---

## API surface

`backend/services/scheduleServer.py`. All routes are GET; no POST request
triggers a model call.


| Route                       | Behavior                                                                                                           |
| --------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| `GET /`                     | redirect → `/ui/`                                                                                                  |
| `GET /health`               | bundle status: `predictions_loaded`, `predictions_anchor_month`, row counts                                        |
| `GET /options`              | dropdown vocabularies, `[{name, id}]` per category (`colors`, `categories`, `materials`, `appearances`, `genders`) |
| `GET /trends`               | every univariate prediction row. Optional filters `?dimension=&state=`                                             |
| `GET /forecast/fingerprint` | one fingerprint forecast. Query: 5 `*_id` ints. 404 if no precomputed match                                        |
| `/ui/*`                     | static React app (`trndly/frontend/`)                                                                              |


Swagger UI at `/docs`. OpenAPI JSON at `/openapi.json`.

The `BUNDLE` global is loaded at startup via the lifespan hook. Restart
the container (or re-run `pipelines.monthly predict` then restart) to
refresh.

---

## Frontend

Stack: **React 18** + **JSX-via-Babel** (no build step) + **SWR**.

`frontend/dataProvider.js` provides a single `useData()` hook exposing:

- `inventory`, `signals`, `addItem` — session-scoped local state
- `trends` — `GET /trends` via SWR, with `data.js` mocks as fallback
- `options` / `lookupIds` — `GET /options` via SWR, with mocks as fallback

`api.js` reshapes API responses into the frontend's pre-existing
contracts (`TREND_DATA[]`, `LOOKUP_OPTIONS`) so screen components don't
care that the data now comes from a real API.

---

## Testing


| Layer                 | Where                    | Notes                                                    |
| --------------------- | ------------------------ | -------------------------------------------------------- |
| Schema validators     | `pipelines/contracts.py` | Called inside producers + tests                          |
| Path-existence        | `tests/test_paths.py`    | Parametric over every `Path` constant                    |
| Lookup consistency    | `tests/test_trndly.py`   | feature_lookups dicts vs lookup.csv                      |
| Items CSV ID validity | `tests/test_trndly.py`   | scraper outputs vs lookup.csv                            |
| Live cube validators  | `tests/test_trndly.py`   | concat-compatibility with historical                     |
| Tick unit tests       | `tests/monthly/`         | state classifier, evaluate logic, predict E2E            |
| Scrapers              | `tests/scrapers/`        | Mock-based; some require `pytest-asyncio`/`pytest-httpx` |
| API endpoints         | (manual / curl)          | Smoke covered in `monthly_tick.md`                       |


---

## Future (target architecture, not yet shipped)

Items below are deliberately out of scope for the current MVP. The shipped
architecture is designed to swap each piece in without restructuring.

### Storage migration: local parquet → GCS / BigQuery

`paths.py` is the single chokepoint. Migration adds a backend abstraction
(e.g., `fsspec`-resolved paths or a `gs://`-aware helper) without touching
consumer code. `gcsfs` is already a transitive dependency. Target bucket
layout (per existing infra):

- `gs://trndly-mlops-us/data/predictions/<YYYY-MM>/`
- `gs://trndly-mlops-us/data/processed/`
- `gs://trndly-mlops-us/mlflow/`

### Cloud cadence: manual CLI → Cloud Scheduler + Vertex job

Replace `python -m pipelines.monthly run` with a Vertex Custom Container
training job, wrapped by Cloud Scheduler firing on the 1st of each
month. The CLI's stage order doesn't change.

### MLflow registry: local file → managed tracking server

Currently `champion_metrics.json` is a local file. Real `champion`-alias
management uses `MlflowClient.set_registered_model_alias`. Plumbing
notes live in `pipelines/monthly/evaluate.py`.

### Frontend hosting: same-origin uvicorn → Firebase Hosting + Cloud Run API

The React app is served at `/ui/` from the same FastAPI container today.
Production split: Firebase Hosting for static assets, Cloud Run for the
API, CORS allowlist between them.

### Auth: none → Firebase Auth

Add `Authorization: Bearer <token>` validation middleware to the API.
Inventory becomes per-user (Firestore-backed instead of session state).

### Containers: single Dockerfile → multi-image

`trndly/Dockerfile` is a starting point. Split into:

- `trndly-collectors`: scrapers + `build_live_cube` (Cloud Run Job)
- `trndly-monthly`: full tick, runs on Vertex (Cloud Run Job)
- `trndly-api`: FastAPI service (Cloud Run Service)

Each image inherits a shared base layer (Python 3.11 + deps).