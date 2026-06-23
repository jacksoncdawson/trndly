# Serving Redesign — Decision Record & Implementation Plan

**Status:** Accepted (2026-06-23) · **Scope of "now":** static-first serving (Steps 1–4 + Terraform the bucket). Dynamic tier (auth + inventory) is designed here but deferred to a follow-on slice.

---

## 1. Context

trndly serves **precomputed monthly forecasts** from two models:

- **Univariate "general trends"** — one row per `(dimension, level_id)`; global trend exploration.
- **Fingerprint "item configurations"** — one row per 5-D fingerprint `(product_type_id, gender_id, color_master_id, graphical_appearance_id, material_id)`; the current 6-month forecast + trend state for a specific item config.

The monthly tick (`scrape → aggregate → features → train → evaluate → predict`) does **all** inference offline and writes the results to Parquet. There is **no live model inference in the serving path**.

### Verified current reality (2026-06-23)

| Fact | Detail |
|------|--------|
| Served data size | **~0.2 MB total** — 119 univariate rows + 3,830 fingerprint rows + 191 lookup entries; byte-identical for every user; changes once a month |
| Current serving | Read-only FastAPI (`backend/services/scheduleServer.py`) loads parquet from local disk at startup, exposes `/health`, `/options`, `/trends`, `/forecast/fingerprint`, mounts a **buildless** React SPA (Babel-in-browser, no `package.json`) at `/ui` |
| Database | **None.** `requirements.txt` has no Postgres/Supabase/ORM/boto3 |
| Auth | **Demo stub** (`frontend/auth.js`: any login succeeds, in-memory) |
| Inventory | **Ephemeral** React `useState` — lost on reload |
| Dockerfile | **Broken** (copies a nonexistent `pipelines/training`; never copies `data/` or `frontend/`; the service can't boot) |
| Existing cloud | Self-hosted MLflow tracking+registry on a GCP VM (Postgres + GCS artifacts) — **dev-only, not in the serving path** |

### The problem

A hosted app server whose entire job is to read 0.2 MB into memory at boot and echo it back is the wrong shape for this workload — and it's exactly what the broken Dockerfile keeps failing to containerize. The data is a **static-asset publishing problem**, not a request-time-compute problem. The **only** genuinely dynamic, per-user, write-heavy surface is **inventory + auth**, which is greenfield.

---

## 2. Decision

**Static-first serving on GCP + a thin Firebase tier for the one dynamic surface. Delete the FastAPI serving app and the Dockerfile.**

1. The **monthly tick becomes the publisher**: after writing Parquet, a new publish step emits browser-ready JSON to **GCS behind a CDN (Firebase Hosting)**.
2. The **only database** is **Firebase Auth + Firestore**, for per-user inventory + real auth.
3. **FastAPI is removed from the serving path** — kept only as a local dev convenience + living contract reference. **The Dockerfile is deleted, not fixed.**
4. All new cloud infra is **authored as Terraform (greenfield)** in an `infra/` module.
5. **Stay on GCP** — matches the existing MLflow/GCS footprint, the credits, and what `architecture.md`/`TODO.md` already describe under "Future" (Firebase Hosting + Firebase Auth + Firestore).

> This resolves the original "fix the Dockerfile" task: we don't fix it — the serving container's reason to exist goes away.

---

## 3. Why (rationale)

- **The data settles it.** ~0.2 MB, global, static-until-next-tick. That is a CDN/static-publish problem. A hosted server here is the part of today's architecture that doesn't earn its keep.
- **The one piece of real server logic is monthly-static.** `scheduleServer._attach_lag_shares()` (the join that attaches `share_lag3/2/1/t` so charts get the full 10-point series) is a pure function of the monthly Parquet. It belongs in the tick — computed once per month, not once per container boot.
- **The fingerprint-miss fallback is already client-side.** `synthesizeFingerprintSeries()` in `frontend/api.js` synthesizes a forecast from the in-memory trends on a 404 — no backend call.
- **Only inventory + auth justify a database.** They are the sole dynamic, per-user, mutable state. Everything else is read-only global data.
- **GCP over AWS** is the operationally honest, deadline-safe, already-scoped choice. The static-publish DE story is cloud-agnostic and carries the résumé on its own; the AWS/Snowflake mapping is articulated verbally (see §8), not built off-stack.

---

## 4. Target architecture

```
   Monthly tick (unchanged through `predict`)
        │  writes data/predictions/predictions_*_<YYYY-MM>.parquet
        ▼
   NEW: pipelines/monthly/publish.py  (the publisher)
        │  reuse _attach_lag_shares() + contracts.py validators
        │  emit browser-ready JSON
        ▼
   GCS bucket  ──fronted by──▶  Firebase Hosting / Cloud CDN
        trends_<YYYY-MM>.json        (119 rows, lag-shares + y_h1..h6 baked in)
        fingerprint_<YYYY-MM>.json   (3,830 rows, single bundle — 0.1 MB)
        options_<YYYY-MM>.json       (exact nested shape /options returns today)
        health_<YYYY-MM>.json        (anchor_month, counts, lags_synthetic, latest pointer)
        + the static SPA
        ▼
   Buildless React SPA  ── reads static JSON (same shapes api.js already consumes)
        │  client-side ?dimension/?state filtering (unchanged)
        │  fingerprint 404 → synthesizeFingerprintSeries (unchanged)
        ▼
   Firebase Auth (UMD/compat CDN bundle)  +  Firestore
        per-user inventory keyed by uid; security rules enforce isolation
        (the ONLY database; the only dynamic surface)
```

**Serving (both models, static).** The publisher does today's startup work once per month — runs `_attach_lag_shares()` against `merged_univariate/merged_fingerprint`, computes the `lags_synthetic` flag, drops unknown columns, validates via `contracts.py` — and writes the four JSON artifacts above. `options_*.json` is reshaped into the **exact** nested `{colors, categories, materials, appearances, genders}` shape `scheduleServer` returns today, so `api.js` `mapOptionsToLookupOptions` / `indexOptionsById` are untouched. Frontend filtering stays client-side over the 119-row array.

**Inventory + auth (the only dynamic surface).** Firebase Auth replaces the `auth.js` `DEMO_USER` stub (loaded as the Firebase UMD/compat CDN bundle to honor the no-npm constraint). Per-user inventory lives in a Firestore collection keyed by the authenticated `uid`, with security rules enforcing `request.auth.uid` ownership. `dataProvider.js` swaps `useState([])` for a scoped Firestore read and routes `addItem()` to a Firestore write; inventory survives reload. **Forecasts are never duplicated into Firestore** — each inventory item's 5-D tags resolve to its forecast by fetching the static fingerprint JSON (or synthesizing on 404), exactly as today.

**FastAPI.** Removed from serving. Kept as a local dev convenience + **contract reference**: its Pydantic models stay the schema the publisher must emit, and `_attach_lag_shares()` is imported by the publisher so the logic can't drift. No hosted app server, no `/ui` mount, no CORS coupling.

**Dockerfile.** Deleted.

---

## 5. Scope: now vs. deferred

| Phase | What | In this slice? |
|------|------|----------------|
| **A — Static serving** | Publisher + static JSON + repoint frontend + GCS/CDN + delete FastAPI/Dockerfile (Steps 1–4) | ✅ **Now** |
| **A — IaC** | Terraform the GCS bucket + Firebase Hosting site | ✅ **Now** |
| **B — Dynamic tier** | Firebase Auth + Firestore inventory (Steps 5–6) | ⏭ **Next slice** |
| **B — IaC** | Terraform Firestore (db + rules) + Identity Platform config | ⏭ **Next slice** |
| **C — Optional/later** | Cloud Scheduler + Vertex/Cloud Run tick cadence; BigQuery/Snowflake external table over the GCS parquet (analytics signal, **not** serving) | 🔮 Later |

Phase A is deadline-safe and fully reversible. It kills the server, tells the whole *"precomputed batch = static-publish, not hosted-inference"* DE story, and commits to **no** risky auth/inventory rebuild. Phase B (the hard greenfield part: sessions, security rules, isolation) is a clean follow-on, presentable in interviews as designed-and-scoped.

---

## 6. The concrete moves (solid terms)

### Phase A — static serving

1. **Add `pipelines/monthly/publish.py`.** Import and reuse `scheduleServer._attach_lag_shares()` (or lift it into a shared module) + `contracts.py` validators so the lag-join + `lags_synthetic` logic is **byte-identical** to today's startup join. Emit `trends_<YYYY-MM>.json`, `fingerprint_<YYYY-MM>.json`, `options_<YYYY-MM>.json`, `health_<YYYY-MM>.json` to **local disk first**. Wire it into the tick after `predict` (and as a standalone `python -m pipelines.monthly publish`).
2. **Golden-file test the publisher** *(the linchpin)*. Curl the running FastAPI (`/trends`, `/options`, `/forecast/fingerprint`, `/health`) into fixtures, then assert each emitted JSON equals the API's response for the same anchor. This pins correctness before any cloud is involved — if the lag-join diverges by even a rounding, charts shift.
3. **Repoint the frontend at static artifacts.** Point `apiFetcher` / `window.API_BASE` at the JSON files (same-origin from disk for local verification). Confirm Trends, Add Item, Item Detail, and the 404→synthesis fallback render **identically**. The 15s `/health` poll becomes an on-mount read (data is static-until-tick).
4. **Publish to GCS + CDN.** Upload artifacts + the SPA to the Terraformed bucket behind Firebase Hosting; cache-bust per tick (versioned `<YYYY-MM>` key + a `latest` pointer, or explicit invalidation). **Delete the Dockerfile.** → live, server-less demo.

**IaC (Phase A):** `infra/` Terraform module — `google_storage_bucket` (+ `google_storage_bucket_iam_member` for public/CDN read), `google_firebase_project`, `google_firebase_hosting_site` (google-beta). Content deploys stay in `firebase deploy`/`gsutil` (run by the tick/CI), **not** Terraform.

### Phase B — dynamic tier (next slice)

5. **Firebase Auth.** Load the UMD/compat bundle via `<script>`; replace `auth.js` `login()/logout()`/session read with Firebase calls; store the user from token claims. Gate **nothing** on the static endpoints (identical for everyone) — only inventory.
6. **Firestore inventory.** Collection keyed by `uid`; security rules for per-user isolation; rewire `dataProvider.js` initial state to a scoped read and `addItem()` to a write. Verify inventory survives reload and is isolated across two test users.

**IaC (Phase B):** `google_firestore_database` + `google_firestore_index` + `google_firebaserules_ruleset`/`_release` for the rules; `google_identity_platform_config` (google-beta) for Auth. Document honestly that a couple of Firebase Auth provider toggles remain console steps.

---

## 7. Consequences & caveats

- **Lag-join parity is a correctness risk.** "Reuse `_attach_lag_shares` so the JSON is identical" is the whole game — the Step-2 golden-file test is **not optional**.
- **Cache invalidation is new.** A tick must reliably bust the CDN (versioned key + `latest` pointer, or explicit invalidation) or users see last month's trends. Low effort, but genuinely new.
- **Buildless-frontend SDK tax.** Firebase Auth/Firestore must come from UMD/compat CDN bundles wired as `window` globals in strict `<script>` order — no modules, no `process.env`. A CDN version mismatch can break the app silently. (The README already warns about this.)
- **Hybrid surface.** Two serving mechanisms (static CDN for reads, Firestore for the dynamic surface). Justify in one line: *"static-until-tick global data is a CDN problem; per-user mutable state is a DB problem."*
- **Deferring Phase B** means the live demo still can't truly log in or persist inventory (pre-existing gaps, not regressions). Frame the demo around Trends/forecast exploration (fully functional statically) and present persistence as the scoped next slice.
- **Scale assumption.** This is portfolio-scale. If fingerprint coverage exploded well past today's 0.32% of the 1.2M universe, or live per-request inference were ever needed, revisit toward a queryable store. Nothing in current reality is close.

---

## 8. AWS / Snowflake mapping (for interviews)

The architecture is cloud-agnostic in shape; built on GCP, but articulate the transfer:

| GCP (built) | AWS/Snowflake (equivalent) |
|---|---|
| predictions Parquet in GCS | S3; a Snowflake/BigQuery **external table** over the same Parquet (analytics layer, not serving) |
| Firebase Hosting + CDN | S3 + CloudFront |
| Firestore | DynamoDB |
| Firebase Auth | Cognito |
| Cloud Scheduler + Vertex/Cloud Run (later) | EventBridge + Step Functions/ECS |

---

## 9. Locked sub-decisions

- **Fingerprint serving:** single `fingerprint.json` bundle (0.1 MB, one fetch) — not sharded.
- **Warehouse:** **no** Snowflake/BigQuery in the serving path; optional analytics external table later, framed honestly.
- **Cloud:** GCP (AWS-native considered and rejected — would fork the pipeline across two clouds for résumé optics only).
- **IaC:** Terraform, authored greenfield in `infra/`; content deploys via CLI/CI.

---

## 10. Pipeline hardening (decided additions, from the data-flow review)

These came out of reviewing the end-to-end data lineage. They're small, they all live in the tick, and together they make the lineage **reproducible end-to-end** (immutable raw → rederivable everything).

| Change | Why | Lands with | Effort |
|---|---|---|---|
| **Immutable per-month raw landing zone** — scrapers write `items_<retailer>_<YYYY-MM>.csv` (or `raw/items/<YYYY-MM>/`); within-month re-runs overwrite *that month's* file (acceptable); `build_live_cube` reads the current month's set | Today the raw `items_<retailer>.csv` is a fixed name overwritten every scrape — source data is destroyed monthly and past cubes can't be rederived if cube logic changes. Raw should be the immutable layer. | Phase A build pass | S |
| **Split `build_cube` out of `scrape`** — promote `build_live_cube` to its own tick stage: `scrape → build_cube → aggregate → …` | Separates the flaky ~10-min network step from the deterministic ~3s transform; creates a retry/cache boundary (rerun the transform without re-hitting four retailer APIs); matches the orchestration + 3-image plan | Phase A build pass | S |
| **Champion gate must govern the *served* weights** — today `train` overwrites `*_model.joblib` **before** `evaluate` runs, so a rejected candidate can still be the model `predict` loads. Make the champion pointer determine the served model | Correctness: a model that *lost* the holdout-wMAE comparison must not serve forecasts | Best landed **with the MLflow registry wiring** (roadmap #1): "champion" = a registry alias → an immutable version; real promotion + rollback; `predict` loads the champion version. Interim local fallback: archive joblibs per run (`data/models/runs/<ts>/`) + revert on a loss | M |

Notes:
- The first two are pure pipeline hygiene and pair naturally with building the publisher (Phase A).
- The third is the one item that touches **model lifecycle** rather than serving — sequence it alongside the registry work, not the static-publish slice.
- **Not changing:** sparse fingerprint coverage (~3,830 observed configs of a mostly-meaningless 1.2M cartesian max). This is intrinsic — the model has no inputs for unobserved combos — so the frontend marginal-product synthesis fallback is the principled cold-start. The static-publish design must preserve the miss→synthesis path (it does). Synthesis *quality* is a model-performance question, deferred.
