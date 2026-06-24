# trndly — Build Plan

**Serving redesign · Pipeline hardening · MLflow rebuild · Infrastructure-as-Code**

**Status:** Accepted (2026-06-23), revised after an adversarial review pass. Design + sequence locked; implementation not started. Covers the full build: the tick, static serving, a private MLflow, model-lifecycle wiring, the dynamic tier, security/incident remediation — all infra as Terraform.

---

## 1. Context & current reality

trndly serves **precomputed monthly forecasts** from two models:
- **Univariate "general trends"** — one row per `(dimension, level_id)`.
- **Fingerprint "item configurations"** — one row per 5-D fingerprint; the current 6-month forecast + trend state for a specific item config.

The monthly tick (`scrape → build_cube → aggregate → features → train → evaluate → predict`) does **all** inference offline and writes Parquet. No live inference in serving.

### Verified reality (live-checked 2026-06-23)

| Fact | Detail |
|------|--------|
| Served data size | **~0.2 MB** — 119 univariate + 3,830 fingerprint + 191 lookup rows; global; changes monthly |
| Serving today | Read-only FastAPI (`backend/services/scheduleServer.py`) over local parquet; mounts a **buildless** React SPA at `/ui` |
| Data in repo | Only `predictions_*.parquet` are committed. **`lookup.csv` (`data/reference/`) and `merged_*.parquet` (`data/processed/`) are gitignored** (`.gitignore:42`) — they are NOT in a clean checkout (matters for the publisher + golden test) |
| Database / Auth / Inventory | None / demo stub (`frontend/auth.js`) / ephemeral `useState` |
| Dockerfile | **Broken**; being **deleted** (+ its orphaned `.dockerignore`) |
| GCP project | `ml-ops-491417` (#117350290566). **Billing linked** to edu acct `01E4BE-D797BA-7C7744` |
| Old MLflow VM | **DELETED.** Ran unauthenticated on `0.0.0.0/0:5000` and was **compromised** (registry polluted w/ OAST-callback models; MLflow-CVE SSRF/RCE). Backend was **SQLite** (not Postgres); artifacts **local on the VM** (not GCS). → rebuilt clean (Phase 3) |
| Incident remediation | ✅ (2026-06-23) Key revoked, all SAs deleted, VM/firewall destroyed, old bucket `trndly-mlops-us` audited (clean) + **renamed to `trndly-data`**, dead IP/port/bucket scrubbed from README/TODO/docs. Remaining: delete `backend/services/.env` when FastAPI leaves serving (Phase 2). See §2.5 |

**The serving problem:** a hosted app server reading 0.2 MB at boot and echoing it is the wrong shape — it's a static-publish problem. Only inventory + auth are genuinely dynamic.

---

## 2. Decisions (consolidated)

1. **Static-first serving.** The tick becomes a **publisher** emitting browser-ready JSON; served as static files on **Firebase Hosting**. **FastAPI leaves serving; the Dockerfile is deleted.**
2. **One database** for the one dynamic surface: **Firebase Auth + Firestore** (deferred to Phase 5).
3. **MLflow rebuilt clean on Cloud Run** — *private* (no `allUsers` invoker) + **Cloud SQL Postgres** + **GCS artifacts (proxied)** + a **dedicated minimal SA**. The model-tracking tool + champion-registry home.
4. **All infrastructure is Terraform** (`infra/`, greenfield). Content/image deploys (`firebase deploy`, `gsutil`, image push) are CLI/CI.
5. **Security + incident remediation are first-class** (§2.5, §5).
6. **Stay on GCP.** Résumé claims out of scope until the build is done.

### 2.5 Incident remediation (do NOW — Phase 1/2, not "future hardening")
- ✅ **DONE (2026-06-23):** `VERTEX_API_KEY` revoked + **all service accounts deleted**; VM + firewall already destroyed. The live exposure is closed. Still pending: **delete `backend/services/.env`** when FastAPI leaves serving (Phase 2) + make `scheduleServer`'s `load_dotenv` optional/removed.
- ✅ **DONE (2026-06-23):** repo scrubbed of the dead IP/port/old-bucket and the false "Postgres" claim across `README.md`, `TODO.md`, `docs/{api,monthly_tick,architecture}.md` (replaced with accurate retired/rebuilt-private prose).
- ✅ **DONE (2026-06-23):** old bucket `trndly-mlops-us` audited (only old `data/synthetic/`, **no `mlflow/` remnants**, no public IAM), **renamed to `trndly-data`** (fresh bucket w/ uniform access + public-access-prevention; old deleted). NOTE: the MLflow rebuild's artifacts bucket is provisioned **fresh via Terraform in Phase 3** — not this legacy bucket.

---

## 3. Why
- **Data settles serving.** 0.2 MB, global, static-until-tick = CDN/static-publish. The one real server computation — `scheduleServer._attach_lag_shares()` (attaches `share_lag3/2/1/t` for the 10-point chart series) — is a pure monthly function, so it moves into the tick. The fingerprint-miss fallback is already client-side.
- **Cloud Run over a VM for MLflow.** A VM is what got popped. Cloud Run is managed (nothing to patch), scales to ~zero, private by default; access via IAM, no public endpoint.
- **Terraform everything.** Greenfield IaC, reproducible (a suspect resource is `destroy`+re-apply, not forensics), real "I provision GCP with Terraform" story.

---

## 4. Target architecture

### Serving (static)
```
tick → predict → predictions_*_<YYYY-MM>.parquet
   ▼
pipelines/serving/  (NEW shared module: _attach_lag_shares + lag/_opt_float + Pydantic schemas,
   │                 lifted out of scheduleServer so publish.py AND a slimmed scheduleServer import it)
   ▼
pipelines/monthly/publish.py   ← reads predictions_* + merged_* + reference/lookup.csv
   │ emits versioned trends_/fingerprint_/options_/health_<YYYY-MM>.json
   │ AND canonical trends.json/fingerprint.json/options.json/health.json (what the SPA fetches)
   ▼
firebase deploy → Firebase Hosting (CDN, SSL)   ← SPA + JSON, same-origin; Cache-Control busts the canonical files
   ▼
Buildless React SPA  ── fetches canonical JSON (api.js shapes preserved for /trends, /options, /health)
   │  FINGERPRINT IS THE EXCEPTION: today api.js does a parameterized `/forecast/fingerprint?ids` query;
   │  it must be rewritten to load fingerprint.json once, index by the 5-D key, return hit-or-null,
   │  and route null into the existing synthesizeFingerprintSeries fallback (replacing the 404 catch).
   ▼
Firebase Auth + Firestore (Phase 5)  ← per-user inventory keyed by uid; rules enforce isolation
```
**Parity note:** `contracts.py` validators do **NOT** cover `share_lag*`/`share_t` (those are serve-time, only in `scheduleServer`'s Pydantic models) — so "reuse contracts.py" does **not** validate the lag-join. The golden-file diff (Phase 1.4) is the authoritative lag-join gate, with an explicit float tolerance (the merge mean-pools duplicate `(month,key)` rows, so re-derivation can differ by float noise). `options_*.json` is sourced from **`lookup.csv`** (191 rows), independently of the predictions parquets.

**`scheduleServer.py` fate:** retained but **slimmed** — imports the shared `pipelines/serving/` module, no longer loads `.env`. It's a local dev convenience + the schema reference; the shared module (not the server) is the single source of truth.

### MLflow + model lifecycle
```
Artifact Registry ← dedicated MLflow image (Cloud Build): mlflow==<pin> + psycopg2-binary + google-cloud-storage + gunicorn
   ▼
Cloud Run v2 "mlflow"  (PRIVATE: no allUsers run.invoker; service_account = sa-mlflow)
   ├─ args: mlflow server --backend-store-uri postgresql+psycopg2://USER:PASS@/DB?host=/cloudsql/<CONN>
   │        --serve-artifacts --artifacts-destination gs://<artifacts>/mlflow --host 0.0.0.0 --port $PORT
   ├─ Cloud SQL via template.volumes.cloud_sql_instance + volume_mounts (socket at /cloudsql)
   ├─ DB password via env value_source.secret_key_ref (Secret Manager)  [sa-mlflow needs secretAccessor]
   └─ artifacts PROXIED through the server → only sa-mlflow has GCS objectAdmin; clients need only run.invoker
   ▲
   callers set MLFLOW_TRACKING_URI + present an IAM ID token (audience = service URL)
```

---

## 5. Security posture (non-negotiable)
- **No public + unauthenticated compute, ever.** MLflow Cloud Run: no `allUsers` `run.invoker` binding (that's how v2 enforces private — there is no `--no-allow-unauthenticated` in HCL). Static serving is public but **read-only static files, no compute behind them**.
- **Least-privilege dedicated SAs.** `sa-mlflow` = Cloud SQL Client + objectAdmin on **the one artifacts bucket** (bucket-scoped `storage_bucket_iam_member`) + `secretmanager.secretAccessor` on the DB-password secret. Never the default Compute SA.
- **Secrets in Secret Manager**, injected at deploy. (Caveat: `google_sql_user.password` still lands in TF state in plaintext — so the state bucket being private+versioned is what protects it; noted, not ignored.)
- **Private by default.** Cloud SQL: no authorized networks (the Cloud Run connector is IAM-gated); upgrade path is private-IP + VPC connector. Artifacts/state buckets: uniform bucket-level access + public-access-prevention enforced.
- **Reproducible = recoverable.** All infra in Terraform → suspect resource is `destroy`+re-apply.
- **MLflow pinned + patched** in a dedicated image. (Dropped: "basic-auth on top of IAM" — IAM `run.invoker` is the real control; a shared basic-auth secret tends to get hardcoded, the exact anti-pattern this section forbids.)

---

## 6. Infrastructure as Code (Terraform)

**`infra/` root module**, applied **incrementally per phase**. Remote state in a **GCS bucket kept in a SEPARATE bootstrap config** (applied once, `prevent_destroy = true`) — so Terraform never manages the backend it depends on. State holds the DB password + SA details → the state bucket is private, versioned, uniform-access, public-access-prevention enforced, IAM-bound only to the Terraform runner.

**Provider split (corrected):** `google-beta` is needed **only** for `google_firebase_project` / `_web_app` / `_hosting_site`. `google_identity_platform_config`, `google_firestore_database`/`_index`, `google_firebaserules_*`, Cloud Run/SQL/Storage/Secret Manager all live in the **stable `google` provider** — don't beta-pin them.

| Concern | Key resources | Phase |
|---|---|---|
| APIs | `google_project_service` for: run, sqladmin, firestore, firebasehosting, identitytoolkit, secretmanager, artifactregistry, **cloudbuild**, **servicenetworking** (if private-IP SQL), **vpcaccess** (if VPC connector) | 0 |
| State | separate bootstrap `google_storage_bucket` (versioned, private, `prevent_destroy`) | 0 |
| Build identity | `roles/artifactregistry.writer` on the repo for the Cloud Build SA (push stays CLI) | 0 |
| Static serving | `google_firebase_project`/`_web_app`/`_hosting_site` (**beta**) | 2 |
| MLflow runtime | `google_artifact_registry_repository`, `google_cloud_run_v2_service` (Cloud SQL volume, secret env, `sa-mlflow`), `google_cloud_run_v2_service_iam_member` (run.invoker — to named principals/`sa-tick`, never allUsers) | 3 |
| MLflow backend | `google_sql_database_instance`, `_database`, `_user`, `google_secret_manager_secret`/`_version` (DB pw) | 3 |
| MLflow identity/artifacts | `google_storage_bucket` (fresh artifacts bucket, UBLA+PAP), `google_service_account` `sa-mlflow`, scoped IAM bindings | 3 |
| Dynamic tier | `google_firestore_database`/`_index`, `google_firebaserules_ruleset`/`_release`, `google_identity_platform_config` (stable) | 5 |

**Terraform = infra. CLI/CI = content & images:** `firebase deploy` (SPA + JSON), `gsutil` (objects), Cloud Build / image push. Reference IAM bindings by attribute (SA email, bucket name) not literals so the DAG resolves. A few Firebase Auth provider toggles remain console steps. (Confirm exact beta-provider resource names at apply time.)

---

## 7. Build sequence

Ordered by dependency. **Why this order:** local-first de-risks the riskiest swap before any cloud; the user-facing static demo lands before internal tooling; MLflow is independent infra; lifecycle wiring needs MLflow; the dynamic tier (riskiest greenfield) is last.

| Phase | Goal | Depends on |
|------|------|-----------|
| **0 — Foundations** | Billing ✅ + TF skeleton (separate state bootstrap, providers, APIs, build identity) | — |
| **1 — Tick hardening + publisher + remediation** | Immutable raw, build_cube extraction, **local champion guard**, `publish.py`, golden test, frontend repoint (incl. fingerprint rewrite), incident remediation — a working **local** static demo | — (local) |
| **2 — Static serving live** | Firebase Hosting (TF) + deploy; delete FastAPI/Dockerfile/.dockerignore; CI deploy job | 1, 0 |
| **3 — MLflow rebuilt (private)** | Cloud Run + Cloud SQL + GCS + `sa-mlflow` (TF); image; validate private access | 0 |
| **4 — Lifecycle wiring** | `train` logs → `evaluate` flips `champion` alias → `predict` loads it (supersedes the local guard) | 3 |
| **5 — Dynamic tier (deferred)** | Firestore + Auth (TF) → persistent inventory + login | 0 |

**Parallelism:** Phases 0+1 overlap. **Phase 3 depends only on Phase 0** (no shared code with 1/2) and its long pole (Cloud SQL provisioning + image build) should **start as soon as Phase 0 lands, in parallel with the Phase-1 local work.** Phase 2 depends on 1 (the JSON) + 0 (Firebase project). 4 requires 3. 5 last.

---

## 8. Concrete moves (per phase)

### Phase 0 — Foundations
- Billing linked ✅. Scaffold `infra/`: providers; **separate** state-bucket bootstrap (`prevent_destroy`, then `terraform init -migrate-state`); `google_project_service` for the API set above; Cloud Build SA + `artifactregistry.writer`.

### Phase 1 — Tick hardening + publisher + remediation *(all local)*
1. **Immutable raw landing zone.** Scrapers write `items_<retailer>_<YYYY-MM>.csv` (within-month re-run overwrites that month). **Touch-set:** `items_csv_path_for` + `ITEMS_FILE_GLOB`/`discover_items_files` in `build_live_cube.py` + `_universe_smoke.py` (globs `items_*.csv`). Keep a **back-compat glob** (match both `items_<retailer>.csv` and `items_<retailer>_<YYYY-MM>.csv`) during transition.
2. **Extract `build_cube` from `scrape`** (it's an *extraction*, not a promotion — today `build_live_cube` runs *inside* `run_scrape`, and `cli.py` FULL_ORDER has **no** build_cube stage). Remove the `_run_one('build_live_cube.py')` tail from `scrape.py`; add `run_build_cube()` + module; insert `build_cube` into FULL_ORDER (between scrape and aggregate); add a subparser + `--skip-build-cube`/`--skip-scrape` interaction. Fix the §1 header's stage order to match the code once done.
3. **Local champion guard (pull-forward — closes the cached-wrong-weights bug NOW).** In `evaluate.py`: archive each run's joblibs to `data/models/runs/<ts>/` and, on a candidate **loss**, **revert** the canonical joblibs to the prior champion's (today `train` overwrites them and `evaluate` doesn't revert, so `predict` bakes losing weights into the parquet the publisher then CDN-caches for a month). MLflow-independent; Phase 4's alias later supersedes it.
4. **`pipelines/serving/` shared module + `pipelines/monthly/publish.py`.** Lift `_attach_lag_shares` + lag/`_opt_float` + the Pydantic schemas out of `scheduleServer` into a new `pipelines/serving/` package (`__init__.py`); have **both** `publish.py` and a slimmed `scheduleServer` import it. `publish.py` reads `predictions_*` + `merged_*` + `reference/lookup.csv`, emits versioned `*_<YYYY-MM>.json` **and** canonical `*.json` (+ define **"latest pointer" concretely**: the canonical month-less files the SPA fetches at today's fixed paths, cache-busted via Hosting `Cache-Control`).
5. **Golden-file test (the linchpin — make it runnable).** It cannot curl a live server (CI runs only `pytest tests`) and its inputs are gitignored. So: **commit fixtures** under `tests/serving/fixtures/` (capture once locally), and **un-ignore + commit the ~5 KB `lookup.csv` and a tiny `merged_*` snapshot** the publisher needs (or have the publisher fetch them from GCS). Test = `tests/serving/test_publish.py` imports `publish.py` functions directly and asserts emitted JSON == committed fixture, **with a float tolerance** on the lag columns. State plainly that `contracts.py` does not cover `share_lag*`.
6. **Repoint the frontend (AFTER 4→5 pass).** Point `apiFetcher`/`window.API_BASE` at the canonical JSON. **Fingerprint rewrite (the one real client change):** rewrite `fetchFingerprintSignals` + the `/forecast/fingerprint` URL builder (`frontend/api.js`, `frontend/screens/ScreenItem.jsx`) to fetch `fingerprint.json` once, index by the 5-D key, return hit-or-null, route null into `synthesizeFingerprintSeries`. `/trends`/`/options`/`/health` shapes are preserved. Verify all screens render identically.
7. **Remediation (§2.5):** rotate `VERTEX_API_KEY`; scrub repo of the IP/port/bucket; (delete `.env` happens in Phase 2 with FastAPI).

### Phase 2 — Static serving live
- **TF:** Firebase project + Hosting site. **Deploy** SPA + canonical JSON via `firebase deploy` (publisher writes JSON into the deploy dir). **CI:** add a deploy job (or a documented manual `firebase deploy` runbook) naming the Hosting token / Workload-Identity SA; wire the `tests/serving` golden test as a **gate**.
- **Delete** `Dockerfile` + the orphaned `.dockerignore`; drop FastAPI from serving (slimmed `scheduleServer` stays as the local contract ref, no `.env`); **delete `backend/services/.env`**.

### Phase 3 — MLflow rebuilt (private)
- **Image:** dedicated `Dockerfile.mlflow` (NOT the tick's `requirements.txt`): `mlflow==<pin>`, `psycopg2-binary`, `google-cloud-storage` (the proxied GCS path needs it; the repo ships `gcsfs`, which is wrong here), `gunicorn`; `CMD` binds `--host 0.0.0.0 --port $PORT`. Build via Cloud Build → Artifact Registry.
- **TF (Cloud Run v2 HCL, not gcloud flags):** Cloud SQL (`google_sql_database_instance` — public IP, **no authorized networks**, or private-IP+VPC per §6) + DB + user; password in Secret Manager. Cloud Run v2 service: `template.volumes.cloud_sql_instance` + `volume_mounts` (socket `/cloudsql/<CONN>`); `template.service_account = sa-mlflow`; DB pw via `env.value_source.secret_key_ref`; MLflow command via `template.containers.args` = `mlflow server --backend-store-uri postgresql+psycopg2://…?host=/cloudsql/<CONN> --serve-artifacts --artifacts-destination gs://<artifacts>/mlflow --host 0.0.0.0 --port $PORT` (**`--artifacts-destination`, NOT `--default-artifact-root`** — the latter makes clients write GCS directly, breaking the proxy/least-priv design). **Private = simply no `allUsers` `run.invoker` binding.** Fresh artifacts bucket (UBLA + PAP). `sa-mlflow` bindings: Cloud SQL Client, objectAdmin on the artifacts bucket only, secretAccessor on the DB secret.
- **Validate:** `gcloud run services proxy mlflow` → hit UI/API locally; confirm an experiment + a logged run round-trips through Cloud SQL + GCS.

### Phase 4 — Lifecycle wiring *(supersedes the local guard)*
1. Extract the model-registration logic from `notebooks/_gen_4_hyperparameter_search.py` into `pipelines/training/registry.py` (`__init__.py`). **Note:** that notebook flips only the **`candidate`** alias and treats `champion` as a *manual* promotion. So Phase 4 introduces a **NEW champion auto-flip policy** in `evaluate.py` (decided on `holdout_wmae`, keeping the existing `cand ≤ incb` tie-break + per-model independence) — this is a policy change layered on the candidate-registration code, **not** a mechanical lift.
2. `train.py` logs each RF as a new version of `trndly_univariate`/`trndly_fingerprint` — gated behind `MLFLOW_TRACKING_URI` present, so the offline path still works.
3. `evaluate.py` flips the `champion` alias on a win.
4. `predict.py` loads the **champion** version.
5. **Auth to private MLflow (must be named):** dev — operator runs `gcloud run services proxy mlflow` and sets `MLFLOW_TRACKING_URI` to the local proxy; automation — run the tick as a **Cloud Run Job** whose SA holds `run.invoker`, attaching an **ID token** (audience = service URL) via `MLFLOW_TRACKING_TOKEN`/`fetch_id_token`. Until automation lands, the champion-flip only happens on a developer-run tick. **`--allow-unauthenticated`/`allUsers` is explicitly forbidden as a shortcut.**
- **CI:** MLflow-touching tests gated behind `MLFLOW_TRACKING_URI`-absent so CI passes without MLflow. Add `tests/monthly/test_registry.py` (present-vs-absent branches, mocked `MlflowClient`).

### Phase 5 — Dynamic tier *(deferred)*
- **TF:** Firestore (db + indexes + rules) + Identity Platform (Auth). Firestore rules **must** scope every read/write to `request.auth.uid == uid` (never just `!= null`), gated by an **emulator/rules unit test + two-user isolation check** (not a manual "verify"). Pin the Firebase compat UMD SDK to an exact version (+ SRI).
- **Firebase Auth** (UMD/compat bundle) replaces the `auth.js` stub; gate only inventory. **Firestore inventory** keyed by `uid`; rewire `dataProvider.js`.

---

## 9. Consequences & caveats
- **Lag-join parity is the #1 risk** — `contracts.py` does NOT validate it; the golden-file diff (with float tolerance) is the gate. Mandatory.
- **Cache invalidation is new** — canonical files + Hosting `Cache-Control`, or users see last month's trends.
- **Buildless-frontend SDK tax** — Firebase SDKs as pinned UMD/compat bundles in strict `<script>` order; version mismatch breaks silently.
- **MLflow on Cloud Run** — cold starts (fine interactively); proxied artifacts so only `sa-mlflow` touches GCS; private = no `allUsers` invoker; the tick needs an ID token (not a generic access token).
- **TF state holds secrets** — the DB password renders into state; the private+versioned state bucket is the control.
- **Deferring Phase 5** — the live demo can't truly log in / persist inventory yet (pre-existing gaps); frame the demo around the fully-functional Trends/forecast exploration.

---

## 10. Locked sub-decisions
- **Fingerprint serving:** single `fingerprint.json` bundle (not sharded); client does the 5-D lookup.
- **Cloud:** GCP; **MLflow compute:** Cloud Run (private), not a VM.
- **Warehouse:** none in the serving path; optional analytics external table later.
- **IaC:** Terraform for all infra (greenfield); content/images via CLI/CI; state in a separate bootstrap.
- **Sparse fingerprint coverage** (~3,830 observed of a mostly-meaningless 1.2M max) is **intrinsic** — preserve the miss→synthesis path; quality is a model-performance question.

---

## 11. Deferred / out of scope (now)
- **Résumé accuracy** — evaluate post-build (the GCP-MLflow claim needs revisiting; lead on the verifiable pipeline/contracts/256-tests/CI).
- **Model performance** — champion quality / drift / synthesis quality — separate phase after wiring.
- **Tick cadence automation** (Cloud Scheduler + Cloud Run Job + the MLflow ID-token path) — a clean follow-on; note it's also what unblocks unattended Phase-4 champion-flips.

---

## 12. Data layout — per-tick checkpoints (addendum, accepted 2026-06-23)

Decided after Phase 1, **before any cloud work**. Replaces the in-place overwrite
of `merged_*` / `training_*` / model artifacts. Motivation: serving moved to
static GCS objects + CDN (immutable, versioned), not the old relational-upsert
plan — so the pipeline should keep **immutable historical checkpoints**, never
overwrite. The artifacts that already do this (`live_*_<YYYY-MM>`,
`predictions_*_<YYYY-MM>`, the published `*_<YYYY-MM>.json`, `models/runs/`) are
the template; this generalizes the convention to the whole tick.

### 12.1 Principle
Every per-tick artifact is written **immutably into a month-keyed checkpoint dir
and never overwritten in place**. Serving and the next tick read the latest
*successful* checkpoint. The tick is **idempotent per month**: a completed month
is not re-run without `--force`.

### 12.2 Layout
```
data/
  raw/kaggle/...                                source dump (immutable)
  raw/items/items_<retailer>_<YYYY-MM>.csv      per-month raw landing zone (immutable)
  reference/lookup.csv                          committed
  processed/                                    SHARED cube inputs (cumulative across ticks)
    historical_*.parquet                        notebook 1 (immutable)
    live_*_<YYYY-MM>.parquet                     build_cube output (per snapshot month)
  models/                                       CURRENT champion (cross-tick)
    {fingerprint,univariate}_model.joblib       canonical = the champion weights predict loads
    champion.json                               per-model pointer {model: {month, holdout_wmae, run}}
  ticks/<YYYY-MM>/                              one IMMUTABLE checkpoint per tick
    merged_{fingerprint,univariate}.parquet
    training_{fingerprint,univariate}.parquet   training_run.json
    model/{fingerprint,univariate}_model.joblib  model_training_run.json   (THIS tick's candidate)
    predictions_{fingerprint,univariate}.parquet
    published/{trends,options,health,fingerprint}.json
    manifest.json                               git sha, stage timings, source months, decisions, anchor
    _SUCCESS                                    written ONLY after publish completes (idempotency marker)
```
The **"latest" for serving** = the max-month `ticks/<M>/` containing `_SUCCESS`
(no separate pointer to drift). `frontend/data/` (the SPA/CDN copy) is refreshed
from the latest tick's `published/` on each successful run.

### 12.3 Champion handling (a simplification this refactor unlocks)
Per-tick model isolation removes the *"train clobbers the canonical joblib"* bug
at the root — the exact reason Phase 1.3 needed an archive + revert:
- `train` writes the candidate to `ticks/<M>/model/` — it **never** touches
  `data/models/` (canonical).
- `evaluate` compares the candidate vs `champion.json`; on a per-model **win** it
  copies `ticks/<M>/model/<m>.joblib` → `data/models/<m>.joblib` and points
  `champion.json[m]` at `<M>`; on a **loss** it does nothing (canonical stays the
  champion). **No revert needed** — `predict` always loads the champion.
- `predict` loads `data/models/` (champion) → scores M's anchor → `ticks/<M>/predictions`.

This **supersedes `models/runs/<id>/` + the revert logic** from Phase 1.3 (now
month-keyed and overwrite-proof). Still superseded later by Phase 4's MLflow
`champion` alias.

### 12.4 Idempotency
- Tick month = current calendar month (default) or `--month YYYY-MM`.
- `run` is a **no-op when `ticks/<month>/_SUCCESS` exists**, unless `--force`
  (re-run + overwrite that month's checkpoint). This is the "don't overwrite a
  good month by accident" guard.
- Individual stages stay runnable for debugging; they write into `ticks/<month>/`.

### 12.5 GCS mapping
`gs://<data-bucket>/ticks/<YYYY-MM>/…` — immutable, **Object Versioning on** as a
backstop, bucket private (UBLA + PAP). The CDN (Firebase Hosting) serves only the
latest tick's `published/` JSON. No warehouse, no DB, no upsert.

### 12.6 Migration (one-time)
- Delete the stale `predictions_*_2020-08` (pre-backfill fallback-anchor output).
- Rename the committed seed `items_<retailer>.csv` → `items_<retailer>_2026-05.csv`.
- `historical_*` / `live_*` stay in `processed/`; the first run under the new code
  seeds `ticks/<month>/`. `transactions.parquet` (214 MB) is a notebook-only
  intermediate — left in `processed/` for now (notebook-coupled), flagged to move.

---

## AWS / Snowflake mapping (for interviews)
| GCP (built) | AWS/Snowflake |
|---|---|
| predictions Parquet in GCS | S3; Snowflake/BigQuery external table (analytics, not serving) |
| Firebase Hosting + CDN | S3 + CloudFront |
| Cloud Run (MLflow) | ECS/Fargate or App Runner behind IAM |
| Cloud SQL Postgres | RDS Postgres |
| Firestore / Firebase Auth | DynamoDB / Cognito |
| Cloud Scheduler + Cloud Run Job (later) | EventBridge + Step Functions/ECS |
