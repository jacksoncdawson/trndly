# trndly

Fashion trend forecasting for secondhand apparel resellers. A monthly
batch tick scrapes retailer catalogs, retrains two RandomForest
regressors, and **precomputes 6-horizon catalog-share forecasts** for
the entire universe of (dimension, level) pairs and 5-D fingerprints.

Serving is **static**: the tick's `publish` stage emits browser-ready JSON
that ships to **Firebase Hosting** (CDN) — there is no server and no live
`model.predict()` in the request path. The only managed compute is a
**private MLflow** for model tracking. All infrastructure is **Terraform**.

**Live: [https://trndly.web.app](https://trndly.web.app)**

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
├── project_materials/         ← pitch deck, demo videos, checkpoints
└── trndly/                    ← the application
    ├── pipelines/
    │   ├── paths.py           — central path registry
    │   ├── contracts.py       — schema validators
    │   ├── cube_slicing.py    — shared cube → feature-row helpers
    │   ├── serving/           — shared serve-shape module (lag-join + Pydantic schemas)
    │   ├── collectors/        — 4 retail scrapers + build_live_cube.py
    │   └── monthly/           — the monthly tick (scrape → ... → predict → publish)
    ├── backend/services/
    │   └── scheduleServer.py  — FastAPI server: LOCAL-DEV convenience + contract ref (not prod)
    ├── frontend/              — React SPA, no build step; fetches static ./data/*.json
    ├── infra/                 — Terraform: Firebase Hosting, private MLflow, WIF CI identity
    ├── notebooks/             — 0 (Kaggle clean), 1 (historical agg), 4 (HP sweep)
    ├── tests/                 — unit, contract, and integration tests
    ├── data/                  — raw / reference / processed / models / ticks
    └── docs/                  — architecture, runbooks, decision records (ADRs)
```

Two RandomForest regressors:

- **Univariate** — one row per `(dimension, level_id)`. Powers trend exploration.
- **Fingerprint** — one row per 5-D `(product_type, gender, color_master, graphical_appearance, material)`. Powers per-item recommendations.

## Architecture

```
monthly tick (local CLI)  ──►  data/ticks/<YYYY-MM>/published/*.json
   scrape → build_cube → aggregate → features → train → evaluate → predict → publish
                                                                       │
                                          firebase deploy / CI ◄───────┘  (SPA + JSON)
                                                                       ▼
                                          Firebase Hosting (CDN)  →  https://trndly.web.app
                                          static files, no compute behind them

private MLflow (Cloud Run, IAM-gated) + Cloud SQL + GCS artifacts   — model tracking (Phase 3)
all infra in Terraform (trndly/infra/)                              — Firebase, MLflow, WIF CI
```

The **tick itself is local** (it scrapes, trains, and writes JSON to disk — no
cloud calls). The cloud pieces are: the static **serving** (published JSON on
Firebase Hosting, refreshed via `firebase deploy` / CI), a **private MLflow** on
Cloud Run for model development, and **Terraform** for everything. See
[docs/serving-redesign.md](trndly/docs/serving-redesign.md) for the full build
plan and [trndly/infra/README.md](trndly/infra/README.md) for the IaC.

## Quick start

```bash
cd trndly                                                  # the inner package dir
python -m venv .venv
./scripts/setup_venv.sh                                    # pip install + playwright install chromium

# One-time bootstrap from the H&M Kaggle dump (places it in data/raw/kaggle/)
.venv/bin/python notebooks/_run_notebook.py notebooks/0_clean_historical.ipynb
.venv/bin/python notebooks/_run_notebook.py notebooks/1_aggregate_historical.ipynb

# One-time: generate the synthetic anchor priors. The live data has a ~5-year
# gap to the historical block, so without 3 contiguous priors the predictor
# would anchor on 2020-08. This writes a PERSISTENT artifact
# (data/processed/backfill_*.parquet) that `aggregate` unions into every tick
# (ADR 0002) — NOT a per-tick step. Self-retires once ≥4 contiguous live months
# exist. See docs/decisions/0002-persistent-backfill-cube.md.
.venv/bin/python scripts/backfill_anchor_lags.py

# Monthly tick: scrape → build_cube → aggregate → features → train → evaluate → predict → publish
.venv/bin/python -m pipelines.monthly run            # --force to re-run a completed month
.venv/bin/python -m pipelines.monthly run --skip-scrape   # reuse items_*.csv already on disk

# Local dev: serve the SPA + a live mirror of the published API at /ui
.venv/bin/python -m uvicorn backend.services.scheduleServer:app --port 8000
```

Then open `http://localhost:8000/ui/` for the React app locally. **Production is
static** — `frontend/` (SPA + the published `data/*.json`) is deployed to Firebase
Hosting via `firebase deploy` (or the CI workflow on merge to `main`); see
[docs/runbooks/deploy-hosting.md](trndly/docs/runbooks/deploy-hosting.md).

## The monthly tick

`python -m pipelines.monthly run` drives the full chain end-to-end. Every
per-tick artifact lands in an immutable checkpoint `data/ticks/<YYYY-MM>/`
(plan §12); `historical_*`/`live_*`/`backfill_*` stay in `data/processed/` as
shared inputs, and the cross-tick champion lives in `data/models/`. The tick is
**idempotent per month** — `run` is a no-op when `_SUCCESS` exists unless `--force`.

| Stage       | What it does                                            | Output                                                                                       |
| ----------- | ------------------------------------------------------- | -------------------------------------------------------------------------------------------- |
| `scrape`    | Subprocess each retailer scraper; fail-loud completeness guard | `data/raw/items/items_<retailer>_<YYYY-MM>.csv`                                        |
| `build_cube`| Aggregate items CSVs into per-month live cubes          | `data/processed/live_*_<YYYY-MM>.parquet`                                                     |
| `aggregate` | Concat historical + live + synthetic-backfill cubes, dedup | `data/ticks/<YYYY-MM>/merged_*.parquet`                                                    |
| `features`  | Calendar-strict windowing (lags + 6 targets)            | `data/ticks/<YYYY-MM>/training_*.parquet`, `training_run.json`                                |
| `train`     | Fit RandomForest, persist this tick's **candidate**     | `data/ticks/<YYYY-MM>/model/*.joblib`, `model_training_run.json`                              |
| `evaluate`  | Compare candidate vs champion WMAE; promote-copy on win | winner's joblib → `data/models/`, repoint `data/models/champion.json`                         |
| `predict`   | Score the universe (champion); fail-loud anchor guard   | `data/ticks/<YYYY-MM>/predictions_*.parquet`                                                  |
| `publish`   | Emit browser-ready JSON for the SPA/CDN                 | `data/ticks/<YYYY-MM>/published/*.json` + refreshed `frontend/data/*.json`                    |

Two guards keep a bad month from publishing silently: the **scrape-completeness
guard** (a retailer that collapses to ~0 rows aborts the tick) and the
**fail-loud anchor guard** (predict errors rather than anchoring on stale
history). Stages are also runnable individually (`python -m pipelines.monthly
aggregate`, etc.). See [docs/monthly_tick.md](trndly/docs/monthly_tick.md) for
the operator runbook.

### Champion model management (local-MVP)

`evaluate` is the **local-MVP** champion manager: the champion is a local
`data/models/champion.json` pointer, not yet an MLflow registry alias. Promotion
rule (per model): `candidate.holdout_wmae <= incumbent.holdout_wmae` → promote.
Per plan §12, `train` writes candidate joblibs to `data/ticks/<YYYY-MM>/model/`
and **never** touches `data/models/`; on a win `evaluate` copies the candidate
over the canonical joblib and repoints `champion.json`, on a loss it does nothing
(so `predict` always loads the champion — no revert). Registry-backed champion
aliasing against the private MLflow is the next step (Phase 4); the MLflow server
is already deployed and reachable for it.

## Cloud architecture (deployed)

| Concern           | What runs                                                                      | Phase |
| ----------------- | ----------------------------------------------------------------------------- | ----- |
| Static serving    | Firebase Hosting (CDN) serving the SPA + published JSON; `Cache-Control: no-cache` on `/data/**` | 2 |
| Model tracking    | **Private** MLflow on Cloud Run (no `allUsers`; IAM-gated) + Cloud SQL Postgres + GCS artifacts (proxied) | 3 |
| Build identity    | Artifact Registry; Cloud Build pushes the MLflow image                        | 0/3   |
| CI deploy         | GitHub Actions, **keyless** via Workload Identity Federation (no SA key)       | 2     |
| Infra as code     | Terraform (`trndly/infra/`), remote state in a private GCS bucket             | 0     |

Security posture (non-negotiable): no public + unauthenticated compute (MLflow is
private), least-privilege dedicated SAs, secrets in Secret Manager, buckets with
uniform access + public-access-prevention. Details in
[docs/serving-redesign.md](trndly/docs/serving-redesign.md) §5.

**Still planned:** unattended cloud tick (Cloud Scheduler + Cloud Run Job, all
data in GCS — [docs/phase6-cloud-native-tick.md](trndly/docs/phase6-cloud-native-tick.md)),
MLflow champion-alias wiring (Phase 4), and the dynamic tier (Firebase Auth +
Firestore per-user inventory, Phase 5).

## Serving surface

Production is **static JSON** on the CDN; the SPA fetches it directly:

| File (`./data/…`)   | Contents                                                        |
| ------------------- | -------------------------------------------------------------- |
| `trends.json`       | Univariate predictions (every dimension/level)                 |
| `options.json`      | Dropdown vocabularies (sourced from `reference/lookup.csv`)     |
| `fingerprint.json`  | One 5-D-keyed bundle; the client does the lookup (miss → null → client-side synthesis) |
| `health.json`       | Bundle status + anchor month + `lags_synthetic` flag           |

The local `scheduleServer` (FastAPI) mirrors these as `GET /trends`, `/options`,
`/forecast/fingerprint`, `/health` for development and as the live contract
reference — both it and the publisher import the shared `pipelines/serving/`
module, so they can't diverge. Full shapes in [docs/api.md](trndly/docs/api.md).

## Testing

```bash
cd trndly
.venv/bin/python -m pytest tests/         # full suite
.venv/bin/python -m pytest tests/monthly/ # tick-stage unit tests
```

**293 tests** covering schema validators, lookup-csv consistency, cube
concat-compatibility, items-CSV ID validity, trend-state classification,
evaluator promotion logic, scraper retry/dedup logic, the scrape-completeness +
anchor guards, the persistent-backfill union, and the static-publish golden diff
(the authoritative lag-join gate). Live retailer smoke checks are gated behind
`pytest -m live` (skipped by default).

CI:
- [`tests.yml`](.github/workflows/tests.yml) — runs the suite on every push/PR to
  `main` (Python 3.11), uploads a junit report.
- [`deploy-hosting.yml`](.github/workflows/deploy-hosting.yml) — on push to `main`,
  runs the `tests/serving` golden gate then deploys to Firebase Hosting (keyless,
  WIF). Never deploys on red.

## Documentation

- [docs/serving-redesign.md](trndly/docs/serving-redesign.md) — the full build plan (architecture, security, IaC, phases)
- [docs/architecture.md](trndly/docs/architecture.md) — shipped architecture
- [docs/api.md](trndly/docs/api.md) — serving-shape reference
- [docs/monthly_tick.md](trndly/docs/monthly_tick.md) — operator runbook
- [docs/runbooks/](trndly/docs/runbooks/) — deploy-hosting + mlflow-deploy runbooks
- [docs/decisions/](trndly/docs/decisions/) — ADRs (0001 CDN refresh, 0002 backfill cube)
- [docs/phase6-cloud-native-tick.md](trndly/docs/phase6-cloud-native-tick.md) — proposed cloud-tick scope
- [TODO.md](TODO.md) — forward-looking work + brittle-area watchlist

## Status

**Shipped + deployed:** monthly batch tick (manual cadence) with scrape +
anchor guards; precomputed predictions; local-file champion management; **static
serving live on Firebase Hosting** (`trndly.web.app`); **private MLflow on Cloud
Run** + Cloud SQL + GCS; **all infrastructure as Terraform**; keyless CI deploy.

**Planned (not yet built):**

- Cloud-native tick: Cloud Scheduler + Cloud Run Job, all data in GCS (Phase 6)
- Champion management: local `champion.json` → MLflow registry alias (Phase 4)
- Dynamic tier: Firebase Auth + per-user inventory in Firestore (Phase 5)
