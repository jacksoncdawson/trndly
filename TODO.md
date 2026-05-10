# TODO

Forward-looking work list for the trndly forecaster pipeline.
Last updated: 2026-05-09.

For the shipped state, read [README.md](README.md) and
[trndly/docs/architecture.md](trndly/docs/architecture.md). For
recent landmark commits, run `git log --oneline -20`.

---

## Active

*Empty — pinned items below are next-up candidates when you reprioritize.*

---

## Pinned (out of scope until reprioritized)

### Cloud deployment (target architecture)

The shipped MVP is laptop-driven. The
[architecture.md "Future" section](trndly/docs/architecture.md#future-target-architecture-not-yet-shipped)
captures the target end state. Concretely:

- **Storage migration: local parquet → GCS.** `paths.py` is the single
  chokepoint; swap with an `fsspec`-backed resolver. `gcsfs` already in
  `requirements.txt`. Existing infra: `gs://trndly-mlops-us`.
- **Cadence: manual CLI → Cloud Scheduler + Vertex Custom Container.**
  Replace `python -m pipelines.monthly run` with a Vertex job, fire
  monthly via Cloud Scheduler.
- **MLflow registry: local file → managed tracking.** `evaluate.py`
  currently writes `champion_metrics.json` locally; swap for
  `MlflowClient.set_registered_model_alias`.
- **Frontend hosting: same-origin uvicorn → Firebase Hosting + Cloud
  Run API.** Add CORS allowlist.
- **Auth: none → Firebase Auth.** Per-user inventory in Firestore.
- **Container split: 3 images (collectors / monthly tick / API).**
  Currently single `trndly/Dockerfile`.

### Univariate `dimension` feature

Univariate model is currently dimension-blind (features:
`[month_of_year, share_t, share_lag1..3]`). Adding `dimension` as a
pandas Categorical lets the model specialize per dim (color seasonality
≠ material seasonality) without splitting into N models. Touchpoints:
`pipelines/monthly/features.py` (add column to
`UNIVARIATE_FEATURE_COLS` + `training_run.json`),
`pipelines/cube_slicing.py::build_univariate_inference_row` (emit the
column), `pipelines/monthly/predict.py` (passes through).

### State-classifier threshold tuning

`pipelines/monthly/state.py` ships with placeholder thresholds
(`RISING_RATIO=1.15`, `FALLING_RATIO=0.85`). Validate against real
predictions distributions; consider adding seasonality-aware variants
(e.g., "rising for time of year").

### Auto-rebootstrap AE on 401

American Eagle's Akamai JWT has ~30-min TTL.
[american_eagle_scraper.py](trndly/pipelines/collectors/american_eagle_scraper.py)
should detect a 401 mid-run and re-invoke `_bootstrap_session` instead
of failing the whole scrape. Today the user has to re-run from scratch.

### `evaluate.py` candidate-rollback on regression

When a candidate model loses to the incumbent on holdout WMAE,
`evaluate.py` keeps `champion_metrics.json` pointing at the old model
but the canonical joblibs in `data/models/` were already overwritten by
`train.py` with the (worse) candidate. Recovery requires retraining
from a prior month or restoring from backup. Add an archive-on-train +
revert-on-loss path: `train.py` writes to
`data/models/runs/<timestamp>/`, `evaluate.py` swaps the canonical
symlink/copy on promotion.

### MLflow registry hygiene

The retired `listing_timeline` registered model still lives in MLflow.
Clean up via the UI or
`MlflowClient.delete_registered_model(name="listing_timeline")`.

### Test infrastructure: missing pytest plugins

`pytest tests/` reports 16 errors + 1 failure due to
`pytest-asyncio` and `pytest-httpx` not being installed in
`trndly/.venv`. They're listed in `requirements.txt`; reinstall the
venv (or `pip install pytest-asyncio pytest-httpx`) to get those tests
passing.

---

## Brittle areas (carry from previous handoffs — still apply)

In rough order of "most likely to break first":

### AE Akamai fingerprint check (HIGH)

[american_eagle_scraper.py](trndly/pipelines/collectors/american_eagle_scraper.py)
requires a one-time Playwright bootstrap that captures the **full set**
of browser headers (`sec-ch-ua-*`, `sec-fetch-*`, `aesite`, `aelang`,
`channeltype`, `Authorization: Bearer <JWT>`). Captured headers pin to
whatever Chrome version Playwright is running — if Akamai later
validates against a *current* Chrome, you'll get silent 403s on httpx.

**Detection:** `Phase 1` log shows `[http] api ... got 403, retry ...` spam.
**Fix:** re-run Playwright bootstrap with a Chrome devtools network
capture, diff request headers, update `STATIC_API_HEADERS`.

### Hollister TLS/HTTP fingerprint (MEDIUM)

[hollister_scraper.py](trndly/pipelines/collectors/hollister_scraper.py)
only works because plain `httpx` over HTTP/1.1 happens to satisfy
Akamai's edge fingerprint. If `httpx` changes its default TLS handshake
or someone "improves" the client to use HTTP/2, the scraper silently
dies.

**Detection:** `productTotalCount=0 totalPages=0` and a 149-byte response
body. Caught by `pytest -m live`'s Hollister structural sanity check.

### Hollister Apollo-state parsing

Catalog data lives inside
`window['APOLLO_STATE__catalog-mfe-web-service-CategoryPageFrontEnd-config'] = {...}`
in the SSR HTML. If Hollister renames the variable or wraps it
differently, parsing returns `None` and Hollister's items file becomes
empty. Constant: `APOLLO_STATE_PREFIX` at the top of
[hollister_scraper.py](trndly/pipelines/collectors/hollister_scraper.py).

### PDP fabric regex per retailer

Each retailer's PDP fabric extraction depends on a regex matching
specific JSON-string structure:

| Retailer  | Pattern | Lives in |
| --------- | ------- | -------- |
| Gap       | `\\"label\\":\\"Fabric \\u0026 care\\".*?\\"bullets\\":\[(.*?)\]` | `gap_scraper.py:FABRIC_BULLETS_RE` |
| Uniqlo    | `"composition"\s*:\s*"((?:.\|[^"])*)"` | `uniqlo_scraper.py` |
| AE        | JSON path `data["data"]["attributes"]["copySections"]["material"]["bullets"]` | `american_eagle_scraper.py:_fetch_pdp_fabric` |
| Hollister | `"fabricDetails":"((?:[^"]\|.)*)"` | `hollister_scraper.py` |

If any retailer changes their PDP serialization, enrichment silently
returns empty strings → `material_raw` unknown rate jumps from ~2% to
~14%.

### `feature_lookups.py` ID drift

The validator catches drift between the hand-typed `*_TO_ID` dicts and
`data/reference/lookup.csv` at module import. **If you edit either, the
import will raise — fix the diff before the scrapers can run.**
Negative test in
[tests/test_trndly.py::test_lookup_consistency_validator_detects_drift](trndly/tests/test_trndly.py).

### Sparse cube → empty predictions

`pipelines/monthly/predict.py` requires 4 contiguous months in the cube
to produce predictions for an anchor (t, t-1, t-2, t-3). Currently the
merged cube has 23 contiguous historical months (Oct 2018 → Aug 2020)
plus a single live month (May 2026). The eligible-anchor finder picks
the latest historical month for both fingerprint and univariate output.

**Until ≥4 contiguous live months accumulate, predictions reflect
historical (H&M) anchor data, not current retail snapshots.** This is a
known limitation flagged for the user; see
[docs/monthly_tick.md](trndly/docs/monthly_tick.md) for behavior detail.

---

## Useful pointers

### Conventions

- **Cwd matters.** All Python invocations expect `trndly/` as the
  working directory (the inner one). The monthly CLI's `python -m
  pipelines.monthly` resolves imports off cwd; running from the project
  root will fail with `ModuleNotFoundError: pipelines`.
- **Python interpreter.** `trndly/.venv/bin/python` is the supported
  env. Built from `trndly/requirements.txt`.

### Smoke commands

```bash
cd /Users/jackcdawson/Desktop/trndly/trndly

# Full monthly tick (scrape → ... → predict). ~15 min including scrape.
.venv/bin/python -m pipelines.monthly run

# Skip scrape stage (use existing items_*.csv). ~1 min.
.venv/bin/python -m pipelines.monthly run --skip-scrape

# Single retailer
.venv/bin/python pipelines/collectors/gap_scraper.py

# All 4 scrapers + build_live_cube (replaces old run_all.sh)
.venv/bin/python -m pipelines.monthly scrape

# Just the merge stage (rebuild merged_*.parquet from cubes on disk)
.venv/bin/python -m pipelines.monthly aggregate

# Test integrity
.venv/bin/python -m pytest tests/ -q

# Serve the API
.venv/bin/python -m uvicorn backend.services.scheduleServer:app --port 8000
```

### Where to look when X fails

| Symptom | Most likely cause | Where to look |
| ------- | ----------------- | ------------- |
| `ModuleNotFoundError: No module named 'pipelines'` | Wrong cwd | `cd trndly/` first |
| `ValueError: feature_lookups.py drift vs ...` at import | Hand-edited `*_TO_ID` dict diverged from `data/reference/lookup.csv` | The diff in the error message |
| Hollister `productTotalCount=0` | TLS fingerprint changed (or HTML rewritten) | `hollister_scraper.py:_parse_apollo_state` |
| AE 100% 403s | Akamai tightened or Playwright Chromium too old | `american_eagle_scraper.py:_bootstrap_session` |
| Material unknowns spike to ~14% | PDP fabric regex broke (retailer changed PDP HTML) | The `*_RE` constants in each scraper |
| Live cube share-sums fail invariant | `build_live_cube.py` upstream got NaN IDs | `validate_live_*_frame` in `pipelines/contracts.py` raises with details |
| `/options` returns empty arrays | `data/reference/lookup.csv` missing or wrong category | Check `lookup.csv` `category` column values |
| `/trends` returns `[]` | No predictions parquet, or anchor month has no rows | Run `python -m pipelines.monthly predict`; restart API |
| API returns 503 with "predictions bundle not loaded" | No predictions parquet found at startup | `ls data/predictions/`; run the monthly tick |
| `predict` exits "no univariate predictions produced" | Latest cube month has no 3 contiguous prior months | See "Sparse cube → empty predictions" above |
| Tests show 16 errors / 1 failure | Missing `pytest-asyncio` / `pytest-httpx` | Reinstall venv from `requirements.txt` |

### Documentation

- [README.md](README.md) — entry point, repo layout, quick start
- [trndly/docs/architecture.md](trndly/docs/architecture.md) — full
  architecture (shipped + future)
- [trndly/docs/api.md](trndly/docs/api.md) — endpoint reference
- [trndly/docs/monthly_tick.md](trndly/docs/monthly_tick.md) — operator
  runbook
- [trndly/docs/rationale.md](trndly/docs/rationale.md) — design
  decisions
- [trndly/pipelines/collectors/README.md](trndly/pipelines/collectors/README.md)
  — scrapers, items.csv schema, brittle areas
- [trndly/data/reference/SCHEMA.md](trndly/data/reference/SCHEMA.md) —
  per-dimension reachability audit (lookup vs. historical vs. live vs.
  merged) plus the deliberately-unreachable allow-list rationale
- [trndly/data/reference/lookup.csv](trndly/data/reference/lookup.csv)
  — canonical feature universe; `*_TO_ID` dicts validated against it
  at import
