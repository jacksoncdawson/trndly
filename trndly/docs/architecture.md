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
                │  frontend/  (React + JSX-via-Babel)          │
                │    Trends screen      ← GET /trends          │
                │    Add Item screen    ← GET /options         │
                │    Item Detail screen ← GET /forecast/fingerprint
                │    Inventory          (session state)        │
                └──────────────────────────────────────────────┘
```

Inference is **precomputed monthly**. The API is a read-only layer over
the predictions parquet — there are no live `model.predict()` calls in
the request path. The `BUNDLE` global is loaded once at startup from the
latest predictions parquets via the FastAPI lifespan hook.

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

> Note: `pipelines/paths.py` is the canonical path registry; its module
> docstring mentions a `pipelines/serving/` directory that does **not**
> exist in the repo — the shipped service lives at
> `backend/services/scheduleServer.py`. Treat that docstring line as
> stale.

---

## The monthly tick (`python -m pipelines.monthly run`)

Stages in order (`pipelines/monthly/cli.py`, `FULL_ORDER`):


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

**Model:** each model is a multi-output `RandomForestRegressor`
(`n_estimators=200`, `min_samples_leaf=2`, `max_depth=None`,
`random_state=42`) predicting `y_h1..y_h6`. A persistence baseline
(ŷ_h = `share_t`) is computed as a sanity floor. `train.py` overwrites
the canonical joblibs in `data/models/` every run; `predict.py` then
loads those same joblibs (logged as "champion models").

**Promotion rule** (`evaluate.py`, the *local-MVP* champion): for each
model independently, if candidate `holdout_wmae <= incumbent.holdout_wmae`,
promote (copy `model_training_run.json` → `champion_metrics.json`). With
no incumbent recorded, the candidate is promoted. On a tie, candidate
wins. The champion is a **local `champion_metrics.json` file**, not an
MLflow registry alias.

> Caveat (acknowledged in `evaluate.py`): because `train.py` has already
> overwritten the joblibs before `evaluate.py` runs, a candidate that
> *loses* the comparison does **not** get its weights reverted — evaluate
> only refuses to advance the champion-metrics pointer. Auto-revert is a
> `Future` item.

**Trend state classification** (`pipelines/monthly/state.py`) — a
forward-first hybrid mapping a `(past3 lags + anchor + 6 forward)`
trajectory to `rising | peak | flat | falling`. The rising/falling
verdict is decided off the **forward window only** (`share_t → y_h6`);
past lags feed only the peak detector. Rules are checked in this order
(first match wins):

- `peak` (checked first): the in-band high — over the band
  `{share_lag1, share_t, y_h1, y_h2}` (`share_lag1` only when past lags
  are supplied) — is a *real* high (strictly above `share_t`, or tied
  with `share_lag1` below `share_t`), **and** the drop from that high to
  `y_h6` is at least `PEAK_MIN_DROP` of the high, **and** the forecast
  declines from anchor (`y_h6 < share_t`).
- `rising`: `y_h6 > RISING_RATIO × share_t`
- `falling`: `y_h6 < FALLING_RATIO × share_t`
- `flat`: otherwise. Any non-finite value or zero denominator → `flat`.

Module-level constants:

```python
RISING_RATIO  = 1.08
FALLING_RATIO = 0.92
PEAK_MIN_DROP = 0.08
```

These thresholds are a deliberate design choice (the previous iteration
used an end-to-end lag3→h6 ratio, which conflated past growth with
forecast direction), not unreviewed placeholders. They can still be
re-tuned as more live months accrue.

---

## Predictions parquet (binding contract)

Two parquets per monthly tick. Schema validators in `pipelines/contracts.py`
(`validate_predictions_univariate_frame`, `validate_predictions_fingerprint_frame`).
The validators enforce column presence + order, no nulls in any column,
`state ∈ {rising, peak, flat, falling}`, and non-empty `stat` strings.

**`predictions_univariate_<YYYY-MM>.parquet`** — 13 columns:

```
anchor_month, model_version,
dimension, level_id, level_name,
y_h1, y_h2, y_h3, y_h4, y_h5, y_h6,
state, stat
```

**`predictions_fingerprint_<YYYY-MM>.parquet`** — 20 columns:

```
anchor_month, model_version,
product_type_id, gender_id, color_master_id, graphical_appearance_id, material_id,
product_type_name, gender_name, color_master_name, graphical_appearance_name, material_name,
y_h1, ..., y_h6,
state, stat
```

`predict.py` scores the entire universe at the latest *eligible* anchor
(`_find_eligible_anchor`: the latest month with 3 contiguous prior
months in the merged cube). Rows where the cube lacks t-3..t-1 lag
coverage at that anchor are silently skipped — the predictions parquet
only contains rows for which a forecast was producible. When the latest
live month is isolated (no real priors), `scripts/backfill_anchor_lags.py`
is a manual stopgap that synthesizes seasonal priors so the live month
becomes eligible.

---

## API surface

`backend/services/scheduleServer.py`. All routes are GET; no POST request
triggers a model call.


| Route                       | Behavior                                                                                                           |
| --------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| `GET /`                     | redirect → `/ui/`                                                                                                  |
| `GET /health`               | bundle status: `predictions_loaded`, `predictions_anchor_month`, row counts, `lags_synthetic`                      |
| `GET /options`              | dropdown vocabularies (read live from `lookup.csv`), `[{name, id}]` per category (`colors`, `categories`, `materials`, `appearances`, `genders`) |
| `GET /trends`               | every univariate prediction row. Optional filters `?dimension=&state=`                                             |
| `GET /forecast/fingerprint` | one fingerprint forecast. Query: 5 `*_id` ints. 404 if no precomputed match                                        |
| `/ui/*`                     | static React app (`trndly/frontend/`)                                                                              |


Swagger UI at `/docs`. OpenAPI JSON at `/openapi.json`.

`/options` is read directly from `lookup.csv` on each request (so the UI
stays in sync with the canonical ID universe). The `BUNDLE` global — the
two predictions frames plus the joined lag shares — is loaded at startup
via the lifespan hook. At load time the service also joins observed
shares at the anchor and its three prior months (`share_lag3`,
`share_lag2`, `share_lag1`, `share_t`) from `merged_*.parquet`, and sets
`BUNDLE.lags_synthetic` if any of those lag months came from the backfill
stopgap (surfaced via `/health`). Restart the container (or re-run
`pipelines.monthly predict` then restart) to refresh.

---

## Frontend

Stack: **React 18** + **JSX-via-Babel** (no build step). React, ReactDOM,
and `@babel/standalone` load from unpkg via `<script>` tags in
`index.html`; application files load as `<script type="text/babel">`.
Data fetching is a tiny in-house `useFetch` hook inside `dataProvider.js`
— keyed module-level cache, optional poll, optional refocus revalidation.
SWR was the original choice (see [rationale.md](rationale.md)), but SWR
2.x ships ESM only with no UMD bundle, which doesn't fit the no-build
setup; the in-house hook covers what we actually use in ~50 lines.

`frontend/dataProvider.js` provides a single `useData()` hook exposing:

- `inventory`, `signals`, `addItem` — session-scoped local state (start empty;
  no seeded mocks)
- `trends` — `GET /trends`. `undefined` while loading/errored; screens
  render explicit loading/error states instead of silently substituting
  fixtures. Each row carries 10 numeric points (`share_lag3..t` joined
  from the merged cube at service startup, then `y_h1..y_h6`) so charts
  draw 3 months of history + 6 months of forecast. The `api.js` reshape
  drops `Unknown` rows (every dimension reserves `id=0 = Unknown` for
  unclassified items — surfacing those in the UI isn't actionable).
- `options` / `lookupIds` — `GET /options`, falling back to the
  `LOOKUP_OPTIONS` seed in `data.js` only for `colorSpectrum` /
  `productGroup` (the two vocabularies the endpoint doesn't expose yet).
- `health` — `GET /health` (15s poll, revalidate-on-focus); drives the
  sidebar API status pill + the chart-legend "synthetic past" footnote
  when `lags_synthetic` is set.

`api.js` reshapes API responses (`mapTrendsToTrendData`,
`mapOptionsToLookupOptions`, `indexOptionsById`) into the shapes the
screens already consume, so the swap from local parquet to cloud SQL
will not touch frontend code as long as the API response shapes stay
stable.

### Item recommendation pipeline

Per-item recommendations (the pill on Item Detail) are derived directly
from a single 10-point series — the same series the Overall Popularity
chart shows. Source priority:

1. `GET /forecast/fingerprint` — gold standard for 5-D combinations that
   are in the precomputed cube.
2. `synthesizeFingerprintSeries(tags, trends)` in `api.js` — multiplicative
   joint of per-dimension univariate motions. Fires when the fingerprint
   endpoint 404s (combination not precomputed). UI labels the chart
   "We've never seen this item before! Predicting based on this item's
   distinct characteristics."
3. `null` — chart hidden; pill reads "More data needed".

`deriveRecommendationFromSeries(series)` in `data.js` then returns one of
five outcomes (`list now / hold 1mo / hold 2mo / hold 3+ / more data needed`)
based on the argmax in the forward window vs. anchor, with a 2.5% minimum
upside threshold (`UPSIDE_THRESHOLD = 0.025`). Per-feature signal cards on
Item Detail are labels only — they do not aggregate into the
recommendation, but they ARE the data source the synthesis step consumes.

### Trend label vocabulary vs. recommendation vocabulary

Two separate vocabularies, two purposes:

- **Trend label** (`rising / peak / flat / falling`): per-feature direction
  classified by [pipelines/monthly/state.py](../pipelines/monthly/state.py)
  on the univariate forward window. Shown on Trends cards + Item Detail
  signal cards.
- **Recommendation outcome** (`list now / hold 1mo / hold 2mo / hold 3+ /
  more data needed`): item-level action derived from
  `deriveRecommendationFromSeries` against the fingerprint/synthesized
  series. Shown on Item Detail pill + Inventory grouping.

The two never coerce into each other.

---

## Testing

The suite is **256 collected tests** (253 run by default; 3 `live`
network tests are deselected) across **107 `def test_` functions** — 55
in `tests/scrapers/`, 36 in `tests/monthly/`, and 16 at the root
(`test_paths.py` + `test_trndly.py`). The gap between functions and
collected count is parametrization (`test_feature_lookups.py` and
`test_state.py` dominate). `pytest.ini` sets `addopts = -m "not live"`
(live retailer tests opt-in via `pytest -m live`) and
`asyncio_mode = strict`.

**CI:** `.github/workflows/tests.yml` (at the **repo root**, one level
above the `trndly/` package) runs `pytest tests -v --junitxml=pytest-report.xml`
on every push and PR to `main`, plus `workflow_dispatch`, on Python 3.11
with `working-directory: trndly`, and uploads the junit report as an
artifact. Tests are gated — a non-zero exit blocks the run.


| Layer                 | Where                    | Notes                                                    |
| --------------------- | ------------------------ | -------------------------------------------------------- |
| Schema validators     | `pipelines/contracts.py` | Called inside producers + tests                          |
| Path-existence        | `tests/test_paths.py`    | Parametric over every `Path` constant                    |
| Lookup consistency    | `tests/test_trndly.py`   | feature_lookups dicts vs lookup.csv                      |
| Items CSV ID validity | `tests/test_trndly.py`   | scraper outputs vs lookup.csv                            |
| Live cube validators  | `tests/test_trndly.py`   | concat-compatibility with historical                     |
| Tick unit tests       | `tests/monthly/`         | state classifier, evaluate logic, predict E2E            |
| Scrapers              | `tests/scrapers/`        | Mock-based; some require `pytest-asyncio`/`pytest-httpx` |
| API endpoints         | (manual / curl)          | No automated FastAPI integration tests yet; smoke covered in `monthly_tick.md` |


---

## Future (target architecture, not yet shipped)

Items below are deliberately out of scope for the current MVP. The shipped
architecture is designed to swap each piece in without restructuring.

### Storage migration: local parquet → GCS / BigQuery

`paths.py` is the single chokepoint. Migration adds a backend abstraction
(e.g., `fsspec`-resolved paths or a `gs://`-aware helper) without touching
consumer code. `gcsfs` is already a transitive dependency. Illustrative target bucket
layout (buckets are provisioned via Terraform per the build plan,
[serving-redesign.md](serving-redesign.md)):

- `gs://<data-bucket>/data/predictions/<YYYY-MM>/`
- `gs://<data-bucket>/data/processed/`

### Cloud cadence: manual CLI → Cloud Scheduler + Vertex job

Replace `python -m pipelines.monthly run` with a Vertex Custom Container
training job, wrapped by Cloud Scheduler firing on the 1st of each
month. The CLI's stage order doesn't change.

### MLflow registry: local file → managed champion alias

A self-hosted MLflow tracking + model registry server was used during
development (`notebooks/_gen_4_hyperparameter_search.py` logs runs to
`MLFLOW_TRACKING_URI` when set, otherwise a local `file:../mlruns` store).
That dev server has since been **retired**; the planned replacement is a
**private, managed MLflow** (Cloud Run + Cloud SQL + GCS) — see
[serving-redesign.md](serving-redesign.md). What is
**not** yet wired up:

- The monthly tick's champion management. `evaluate.py` is explicitly the
  local-MVP version — the champion is the local `champion_metrics.json`
  file, not a registry alias. The target is
  `MlflowClient.set_registered_model_alias(name=..., alias='champion',
  version=...)` against the registry model (`listing_timeline_experiments@champion`),
  with a `runs/` archive + auto-revert when a candidate loses. Plumbing
  notes live in `pipelines/monthly/evaluate.py`.
- The serving path. The `MLFLOW_*` variables in
  `backend/services/.env` are **leftovers** from an older
  registry-backed serving design and are **not referenced** by
  `scheduleServer.py`; the service reads precomputed parquets only.

### Frontend hosting: same-origin uvicorn → Firebase Hosting + Cloud Run API

The React app is served at `/ui/` from the same FastAPI container today.
Production split: Firebase Hosting for static assets, Cloud Run for the
API, CORS allowlist between them.

### Auth: none → Firebase Auth

Add `Authorization: Bearer <token>` validation middleware to the API.
Inventory becomes per-user (Firestore-backed instead of session state).
(`frontend/auth.js` is a demo stub — `login()` always succeeds and
returns a hardcoded demo user.)

### Containers: single Dockerfile → multi-image

`trndly/Dockerfile` is a starting point, but it currently only ships the
**API**: it copies `backend/services` and runs `uvicorn scheduleServer:app`.
Its `COPY pipelines/training /app/pipelines/training` line targets a
directory that **does not exist** in the repo (the real packages are
`pipelines/monthly` and `pipelines/collectors`), so it silently copies
nothing — fix or remove that line when splitting the image. Planned
split:

- `trndly-collectors`: scrapers + `build_live_cube` (Cloud Run Job)
- `trndly-monthly`: full tick, runs on Vertex (Cloud Run Job)
- `trndly-api`: FastAPI service (Cloud Run Service)

Each image inherits a shared base layer (Python 3.11 + deps).