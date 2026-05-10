# Monthly tick runbook

The monthly tick produces a fresh predictions parquet that the FastAPI
service serves. This document is the operator runbook: prerequisites,
how to invoke each stage, what to expect, and how to debug a failure.

---

## Prerequisites

1. **Python environment.** `trndly/.venv/bin/python` with
   `requirements.txt` installed.
   ```bash
   cd trndly
   python3.11 -m venv .venv
   .venv/bin/python -m pip install -r requirements.txt
   ```

2. **Historical cubes.** `data/processed/historical_*.parquet` must
   exist (one-time bootstrap from notebooks 0 → 1 against the H&M Kaggle
   dump in `data/raw/kaggle/`). These are immutable; the tick only reads
   them.

3. **MLflow tracking URI** *(optional — only if you want runs logged
   to MLflow registry).* Set `MLFLOW_TRACKING_URI` in
   `backend/services/.env`. Local-only promotion via
   `champion_metrics.json` works without it.

4. **Outbound network** for the scrape stage. Each retailer has its own
   bot-protection quirks (see `pipelines/collectors/README.md`).

---

## Running the tick

### Full chain

```bash
.venv/bin/python -m pipelines.monthly run
```

Stage order: `scrape → aggregate → features → train → evaluate → predict`.
Wall-clock: ~15 minutes (most of which is scraping).

### Skip the scrape

If `data/raw/items/items_*.csv` and the corresponding
`data/processed/live_*_<YYYY-MM>.parquet` are already on disk:

```bash
.venv/bin/python -m pipelines.monthly run --skip-scrape
```

This typically takes ~1 minute.

### Individual stages

```bash
.venv/bin/python -m pipelines.monthly scrape
.venv/bin/python -m pipelines.monthly aggregate
.venv/bin/python -m pipelines.monthly features
.venv/bin/python -m pipelines.monthly train
.venv/bin/python -m pipelines.monthly evaluate
.venv/bin/python -m pipelines.monthly predict
```

Each stage prints structured `INFO`-level logs and exits non-zero on
failure.

---

## What each stage does

### `scrape` — `pipelines.monthly.scrape`

Subprocesses each retail scraper sequentially:

1. `gap_scraper.py`     → `data/raw/items/items_gap.csv`
2. `uniqlo_scraper.py`  → `data/raw/items/items_uniqlo.csv`
3. `american_eagle_scraper.py` → `data/raw/items/items_american_eagle.csv`
4. `hollister_scraper.py` → `data/raw/items/items_hollister.csv`

Then runs `build_live_cube.py` which unions the four CSVs and writes
`data/processed/live_{fingerprint,univariate}_<YYYY-MM>.parquet` for the
current month.

**Flags:**
- `--retailers gap,uniqlo` — run a subset
- `--no-enrich-pdp` — skip PDP material enrichment for ~3× speedup at
  the cost of ~14% material unknown
- `--skip-build-cube` — stop after the scrapers; useful when iterating

### `aggregate` — `pipelines.monthly.aggregate`

Reads `historical_{fingerprint,univariate}.parquet` + globs every
`live_*_<YYYY-MM>.parquet`; concatenates with dedup on
`(month, fingerprint, source)` (or `(month, dimension, level_id, source)`)
keeping `last`; writes `merged_*.parquet`.

Always rebuilds — `historical_*` is never overwritten.

### `features` — `pipelines.monthly.features`

Builds calendar-strict training rows from the merged cubes. For each
anchor month `t`, requires cube rows on every month in `t-3..t+6` (10
months: 3 lags + anchor + 6 horizons). Rows that don't qualify are
silently dropped. Outputs:
- `data/processed/training_univariate.parquet`
- `data/processed/training_fingerprint.parquet`
- `data/processed/training_run.json` (feature/target column manifest)

Sample weights = `sqrt(n_articles)` capped at 100; split groups
(train/val/holdout) assigned by tail rank on `anchor_month`.

### `train` — `pipelines.monthly.train`

Fits two `RandomForestRegressor` models (200 estimators,
`min_samples_leaf=2`, no max depth) — one per training table. Each
predicts the 6-vector `[y_h1, ..., y_h6]`. Logs persistence-baseline
comparison (`ŷ_h = share_t`) — model that doesn't beat baseline gets a
warning.

Outputs:
- `data/models/fingerprint_model.joblib`
- `data/models/univariate_model.joblib`
- `data/models/model_training_run.json` (metrics + manifest)

### `evaluate` — `pipelines.monthly.evaluate`

Compares the just-written candidate manifest against
`data/models/champion_metrics.json` (the prior champion's record). Per
model independently:
- No incumbent → promote
- `candidate.holdout_wmae <= incumbent.holdout_wmae` → promote
- Else → keep (no file changes)

"Promote" = copy `model_training_run.json` → `champion_metrics.json`.
The joblibs in `data/models/` are always the just-trained candidate;
**a candidate that loses still leaves the canonical joblibs as the
candidate's**. Recovery requires retraining from a prior month or
restoring from backup. This is the local-MVP trade-off; cloud
deployment uses real MLflow alias swaps with snapshot semantics.

### `predict` — `pipelines.monthly.predict`

Iterates the universe, scores everything that has lag coverage, classifies
state via `pipelines.monthly.state`, decodes IDs to names via
`lookup.csv`, validates via `pipelines/contracts.py`, writes:
- `data/predictions/predictions_univariate_<YYYY-MM>.parquet` (~190 rows)
- `data/predictions/predictions_fingerprint_<YYYY-MM>.parquet` (~6,500 rows)

Anchor month is the latest cube month with 3 contiguous prior months
(picked separately per cube). Rows where individual fingerprints/levels
lack lag coverage are skipped.

---

## After the tick

Restart the FastAPI service so it loads the new predictions parquet:

```bash
# In whichever process / container the API runs:
pkill -f 'uvicorn.*scheduleServer'  # or your usual restart
.venv/bin/python -m uvicorn backend.services.scheduleServer:app --port 8000 &
```

Verify via `/health`:

```bash
curl -s http://localhost:8000/health | python3 -m json.tool
# Expect: predictions_loaded=true, predictions_anchor_month=<expected month>
```

---

## Common failure modes

### `RuntimeError: predict: no univariate predictions produced — check cube lag coverage`

**Cause:** the latest cube month has no prior 3 months in the cube
(e.g., a single live month with no historical neighbors).

**Fix:** wait for additional live data to accumulate, OR make sure
`historical_*.parquet` is present so the predict stage can use a
historical anchor month.

### `evaluate` warns "candidate model does NOT beat persistence baseline"

**Cause:** the model is performing worse than just predicting
`y_h = share_t` for every horizon. With small training data this is
not unusual.

**Fix:** investigate via `data/models/model_training_run.json`. The
candidate is still promoted if it beats the *prior* champion; the
baseline warning is informational.

### `aggregate` complains `missing fingerprint historical at .../historical_fingerprint.parquet`

**Cause:** notebook 1 hasn't been run on the H&M Kaggle data.

**Fix:** run `notebooks/0_clean_historical.ipynb` then
`notebooks/1_aggregate_historical.ipynb` from the trndly/ directory.
These produce the immutable historical cubes; only needs to happen
once.

### Scraper fails with 403 / 401 / Akamai mention

See `pipelines/collectors/README.md` "Brittle areas" section. Most
common fixes:
- Re-run American Eagle's Playwright bootstrap to refresh JWT
- Wait 30 minutes (Akamai cooldown)
- Run with `--retailers` to skip the offending retailer for now

---

## What to do if WMAE regresses

If `evaluate` flips a model from promote→keep this month after promoting
it last month, the candidate model is worse than the now-locked
incumbent. Investigate by comparing
`data/models/model_training_run.json` (candidate) vs
`data/models/champion_metrics.json` (incumbent):

- Did the new live month introduce noisy outliers?
- Did `feature_importances_` shift drastically?
- Is the holdout split still representative of recent months?

The candidate joblib is preserved at
`data/models/{fingerprint,univariate}_model.joblib` — restoring the
champion would currently require retraining from the prior month's
data. Document failures in `TODO.md` and decide whether to extend the
runs/ archive logic in `evaluate.py`.
