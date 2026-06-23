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

3. **MLflow tracking URI** *(optional — not used by the monthly tick).*
   The monthly tick's champion management is fully local: promotion is
   decided by comparing `model_training_run.json` against
   `champion_metrics.json` on disk (see the `evaluate` stage). The
   self-hosted MLflow server (`MLFLOW_TRACKING_URI=http://34.169.170.34:5000`,
   Postgres backend, GCS artifacts under `gs://trndly-mlops-us/mlflow/`)
   is used only during **model development / hyperparameter sweeps**
   (`notebooks/_gen_4_hyperparameter_search.py`), not by any stage below.
   The `MLFLOW_*` vars in `backend/services/.env` are likewise leftovers
   from an older registry-backed serving design and are not read by the
   tick or the serving layer. *(See "MVP vs. target" at the bottom — the
   MLflow registry champion alias is the target state, deferred until
   cloud deployment.)*

4. **Outbound network** for the scrape stage. Each retailer has its own
   bot-protection quirks (see `pipelines/collectors/README.md`).

---

## Running the tick

### Full chain

```bash
.venv/bin/python -m pipelines.monthly run
```

Stage order: `scrape → aggregate → features → train → evaluate → predict`.
Wall-clock: ~15 minutes, dominated by the scrape stage (~9–10 min with
PDP enrichment).

The `run` subcommand takes exactly one option, `--skip-scrape` (see
below). It does **not** forward scraper flags — to control the scrapers
(subset of retailers, PDP enrichment, skipping the cube build) call the
standalone scrape module directly (see the `scrape` stage section).

### Skip the scrape

If `data/raw/items/items_*.csv` and the corresponding
`data/processed/live_*_<YYYY-MM>.parquet` are already on disk:

```bash
.venv/bin/python -m pipelines.monthly run --skip-scrape
```

This typically takes ~1–2 minutes (train usually dominates).

### Individual stages

```bash
.venv/bin/python -m pipelines.monthly scrape
.venv/bin/python -m pipelines.monthly aggregate
.venv/bin/python -m pipelines.monthly features
.venv/bin/python -m pipelines.monthly train
.venv/bin/python -m pipelines.monthly evaluate
.venv/bin/python -m pipelines.monthly predict
```

Each of these monthly-CLI subcommands takes **no options** other than
`-h`. They call the corresponding `run_<stage>()` with default
arguments. (To pass scrape flags, use the standalone scrape module —
see below.) Each stage prints structured `INFO`-level logs and the CLI
exits non-zero on any stage failure.

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

**Flags (standalone module only).** The monthly-CLI `scrape` subcommand
above takes no options. To control the scrapers, invoke the module
directly:

```bash
.venv/bin/python -m pipelines.monthly.scrape [flags]
```

- `--retailers gap,uniqlo` — run a subset (default: all four)
- `--no-enrich-pdp` — skip PDP material enrichment for ~3× speedup at
  the cost of ~14% material unknown (default is `--enrich-pdp`)
- `--skip-build-cube` — stop after the scrapers; useful when iterating

> Note the dotted path: the flags live on `pipelines.monthly.scrape`
> (the module), **not** on `pipelines.monthly scrape` (the monthly-CLI
> subcommand). The latter ignores everything but `-h`.

### `aggregate` — `pipelines.monthly.aggregate`

Reads `historical_{fingerprint,univariate}.parquet` + globs every
`live_*_<YYYY-MM>.parquet`; concatenates with dedup on
`(month, *FINGERPRINT_COLS, source)` (fingerprint) or
`(month, dimension, level_id, source)` (univariate) keeping `last`;
writes `merged_*.parquet`.

Always rebuilds — `historical_*` is never overwritten. If no live
parquets are found, the merged cube is historical-only.

### `features` — `pipelines.monthly.features`

Builds calendar-strict training rows from the merged cubes. For each
anchor month `t`, requires cube rows on every month in `t-3..t+6` (10
months: 3 lags + anchor + 6 horizons). Rows that don't qualify are
silently dropped. Outputs:
- `data/processed/training_univariate.parquet`
- `data/processed/training_fingerprint.parquet`
- `data/processed/training_run.json` (feature/target column manifest +
  split/sample-weight contract)

Sample weights = `min(sqrt(n_articles_at_anchor), 100.0)`; split groups
(train/val/holdout) assigned by tail rank on each table's distinct
`anchor_month` values (defaults `K=2` holdout, `J=2` val; the split
adapts downward if there are too few distinct months).

### `train` — `pipelines.monthly.train`

Fits two multi-output `RandomForestRegressor` models (`n_estimators=200`,
`min_samples_leaf=2`, `max_depth=None`, `random_state=42`) — one per
training table. Each predicts the 6-vector `[y_h1, ..., y_h6]`. A
persistence baseline (`ŷ_h = share_t`) is computed as a sanity floor;
a model that doesn't beat baseline on weighted holdout MAE gets a
`WARNING` (it does not block promotion).

Outputs:
- `data/models/fingerprint_model.joblib`
- `data/models/univariate_model.joblib`
- `data/models/model_training_run.json` (metrics + manifest)

### `evaluate` — `pipelines.monthly.evaluate`

Compares the just-written candidate manifest
(`model_training_run.json`) against
`data/models/champion_metrics.json` (the prior champion's record). The
comparison is made **per model** (univariate, fingerprint):
- No incumbent recorded → promote
- `candidate.holdout_wmae <= incumbent.holdout_wmae` → promote
- Else → keep that model's incumbent

These per-model decisions drive the logged action. The **file write,
however, is all-or-nothing**: if *any* model is promoted, evaluate
copies the entire candidate `model_training_run.json` over
`champion_metrics.json` (`shutil.copyfile`). So if one model improves
and its co-trained sibling regresses this month, the regressed model's
metrics are *also* written into the champion record. The champion file
is left untouched only when **neither** model improves.

The joblibs in `data/models/` are always the just-trained candidate's
weights — `train.py` overwrites them before `evaluate.py` runs.
**A candidate that loses still leaves the canonical joblibs as the
candidate's**; evaluate only refuses to advance the champion-metrics
pointer, it does not revert the joblibs. Recovery requires retraining
from a prior month or restoring from backup. This is the local-MVP
trade-off; the target state uses MLflow registry alias swaps with
snapshot semantics (see "MVP vs. target").

### `predict` — `pipelines.monthly.predict`

Loads the joblib models, iterates the universe, scores everything that
has lag coverage, classifies state via `pipelines.monthly.state`,
decodes IDs to names via `lookup.csv`, validates via
`pipelines/contracts.py`, writes:
- `data/predictions/predictions_univariate_<YYYY-MM>.parquet`
  (low hundreds of rows)
- `data/predictions/predictions_fingerprint_<YYYY-MM>.parquet`
  (a few thousand rows)

Exact counts depend on the eligible anchor (e.g., the dense historical
2020-08 anchor yields ~182 univariate / ~6,500 fingerprint rows; the
sparse live 2026-05 anchor yields ~120 / ~3,800).

Anchor month is the latest cube month with 3 contiguous prior months,
picked separately per cube (`_find_eligible_anchor`). If the latest
live month is isolated, predict logs a `WARNING`, tells you to run
`scripts/backfill_anchor_lags.py` to synthesize priors, and falls back
to the nearest eligible (typically historical) anchor. Individual
fingerprints / levels that lack lag coverage at the chosen anchor are
silently skipped.

#### State classification (used by `predict`)

`pipelines.monthly.state.classify_state` is a **forward-first hybrid**
(first match wins), with all thresholds defined as module-level
constants in `state.py`:

1. **peak** — the in-band high (over `{lag1, share_t, h1, h2}`) is a
   real high (strictly above anchor, or tied with anchor while `lag1`
   is below it), drops to `y_h6` by at least `PEAK_MIN_DROP` (0.08)
   relative to the high, and the forward forecast declines
   (`y_h6 < share_t`). → `"at peak"`
2. **rising** — `y_h6 > RISING_RATIO (1.08) × share_t` → `"+N% next 6mo"`
3. **falling** — `y_h6 < FALLING_RATIO (0.92) × share_t` → `"−N% next 6mo"`
4. **flat** — otherwise → `"stable"`

Direction (rising/falling) is decided off the **forward window only**
(`share_t → y_h6`); past lags feed only the peak band. Any non-finite
value, or a zero anchor denominator, classifies as flat/stable.

---

## After the tick

Restart the FastAPI service so it loads the new predictions parquet:

```bash
# In whichever process / container the API runs:
pkill -f 'uvicorn.*scheduleServer'  # or your usual restart
.venv/bin/python -m uvicorn backend.services.scheduleServer:app --port 8000 &
```

The service is read-only over the precomputed predictions parquets — it
loads them once at startup and makes no live model calls in the request
path. Verify via `/health`:

```bash
curl -s http://localhost:8000/health | python3 -m json.tool
# Expect: predictions_loaded=true, predictions_anchor_month=<expected month>
# (also reports lags_synthetic=true if the anchor used backfilled priors)
```

---

## Common failure modes

### `RuntimeError: predict: no univariate predictions produced — check cube lag coverage`

**Cause:** the latest cube month has no prior 3 months in the cube
(e.g., a single live month with no historical neighbors).

**Fix:** wait for additional live data to accumulate, OR make sure
`historical_*.parquet` is present so the predict stage can use a
historical anchor month. (If you want the newest live month as the
anchor, run `python scripts/backfill_anchor_lags.py` first.)

### `train` warns "model does NOT beat persistence baseline on holdout"

**Cause:** the model is performing worse than just predicting
`y_h = share_t` for every horizon. With small training data this is
not unusual. (This warning is emitted by the `train` stage, not
`evaluate`.)

**Fix:** investigate via `data/models/model_training_run.json`. The
candidate is still promoted by `evaluate` if it beats the *prior*
champion; the baseline warning is informational only.

### `aggregate` complains `missing fingerprint historical at .../historical_fingerprint.parquet — run notebook 1 first.`

**Cause:** notebook 1 hasn't been run on the H&M Kaggle data.

**Fix:** run `notebooks/0_clean_historical.ipynb` then
`notebooks/1_aggregate_historical.ipynb` from the trndly/ directory.
These produce the immutable historical cubes; only needs to happen
once.

### Scraper fails with 403 / 401 / Akamai mention

See `pipelines/collectors/README.md` — the "Known limitations",
"Operational notes", and per-retailer (American Eagle / Hollister)
sections. Most common fixes:
- Re-run American Eagle's Playwright bootstrap to refresh the JWT
- Wait ~30 minutes (Akamai cooldown)
- Run with `--retailers` (on the standalone
  `pipelines.monthly.scrape` module) to skip the offending retailer for
  now

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

Remember the all-or-nothing champion-file write (see the `evaluate`
section): if the *other* model was promoted this month, the regressed
model's record in `champion_metrics.json` was overwritten anyway — so
compare against last month's archived manifest if you kept one.

The candidate joblib is preserved at
`data/models/{fingerprint,univariate}_model.joblib`, but restoring the
prior champion would currently require retraining from the prior
month's data or a backup. Document failures in the repo-root `TODO.md`
(`../TODO.md` relative to `trndly/`) and decide whether to extend the
runs/ archive + auto-revert logic in `evaluate.py`.

---

## MVP vs. target (current limitations)

The monthly tick is the **local-MVP**. The following are deliberately
deferred to cloud deployment and are NOT how the tick works today:

- **Champion management.** *Now:* local file comparison
  (`champion_metrics.json` vs `model_training_run.json`); the whole
  candidate manifest is copied when any model is promoted; no auto-revert
  of joblibs. *Target:* MLflow model-registry alias
  (`MlflowClient.set_registered_model_alias(name=..., alias='champion',
  version=...)`) against the cloud registry on the GCP VM
  (`http://34.169.170.34:5000`, model `listing_timeline_experiments@champion`),
  with a runs/ archive + auto-revert on demotion.
- **Anchor lag backfill.** *Now:* `scripts/backfill_anchor_lags.py` is a
  manual stopgap run outside the tick when the latest live month is
  isolated. *Target:* automated synthetic-lag synthesis within the tick.
- **Scraper parallelism.** *Now:* sequential subprocess calls
  (gap → uniqlo → american_eagle → hollister), ~9–10 min with PDP
  enrichment. *Target:* parallel dispatch (blocked today by each
  scraper owning its own `asyncio.run`).
- **Serving / storage / cadence.** *Now:* read-only FastAPI over local
  parquets; manual CLI invocation; no IaC; runs on a dev machine.
  *Target:* GCS/BigQuery-backed data, Cloud Scheduler + container job for
  the tick, Cloud Run for the API.
