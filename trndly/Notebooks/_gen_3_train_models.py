"""One-off generator for 3_train_models.ipynb — run from trndly/: python Notebooks/_gen_3_train_models.py"""
from __future__ import annotations

import json
from pathlib import Path


def md(s: str) -> dict:
    lines = s.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    return {"cell_type": "markdown", "metadata": {}, "source": lines}


def code(s: str) -> dict:
    lines = s.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    return {
        "cell_type": "code",
        "metadata": {"execution_count": None},
        "outputs": [],
        "source": lines,
    }


def main() -> None:
    nb_path = Path(__file__).resolve().parent / "3_train_models.ipynb"
    cells: list[dict] = []

    cells.append(
        md(
            r"""# Model training — univariate + fingerprint multi-horizon forecasters

Training tables come from [`2_feature_processing.ipynb`](2_feature_processing.ipynb), after cubes from **`1_aggregate_historical`** (optionally refreshed via [`1b_scrape_aggregate_live.ipynb`](1b_scrape_aggregate_live.ipynb)).

Train **two scikit-learn models** from those calendar-strict tables. Both are multi-output regressors that predict catalog `share_articles` at horizons **`h = 1..6`** months ahead, given an anchor month and three monthly lags.

| Part | Input | Output model | Output |
|------|-------|--------------|--------|
| **A — Univariate** | `training_univariate.parquet` | `RandomForestRegressor` | `univariate_model.joblib` |
| **B — Fingerprint** | `training_fingerprint.parquet` | `RandomForestRegressor` | `fingerprint_model.joblib` |

Both models are tuned to be small enough to ship in the API container (a few MB each on this dataset) and use `sample_weight = sqrt(n_articles_at_anchor)` so high-volume series dominate the loss while long-tail series still contribute signal.

A persistence (carry-forward) baseline `ŷ_h = share_t` is reported alongside every model so the random forest's lift is auditable per horizon.

## Schema (model output manifest)

`model_training_run.json` records, for each part:

- `model_path` (joblib), `model_class`, sklearn `params`
- `feature_cols` (order matters at inference), `target_cols`
- per-split metrics for the trained model **and** the persistence baseline:
  - `wmae_h{h}`, `wrmse_h{h}` (sample-weighted)
  - aggregate `wmae_mean`, `wrmse_mean`, `r2_weighted_mean`

## Inputs

- [`trndly/data/processed/training_univariate.parquet`](../data/processed/training_univariate.parquet) — Part A training table.
- [`trndly/data/processed/training_fingerprint.parquet`](../data/processed/training_fingerprint.parquet) — Part B training table.
- [`trndly/data/processed/training_run.json`](../data/processed/training_run.json) — feature/target column lists from `2_*` (used as the canonical contract).

## Outputs

- `trndly/data/models/univariate_model.joblib` (NEW)
- `trndly/data/models/fingerprint_model.joblib` (NEW)
- `trndly/data/processed/model_training_run.json` (NEW) — run metadata + metrics

## Splits

Use the `split_group` column already assigned in `2_*`:

- **`train`** — fit the model.
- **`val`** — used here only to report metrics (no early stopping; RF doesn't need it). Available for downstream hyperparameter sweeps.
- **`holdout`** — final, untouched evaluation. Treat numbers from this split as the headline.

## Convention

- Two rolling working frames: `uni` (univariate training table) and `fp` (fingerprint training table).
- The model **never** sees `anchor_month`, `source`, `split_group`, `sample_weight`, or `n_articles` — those are row-level metadata. The feature matrix `X` is exactly `FEATURE_COLS` from each table's contract.
- Targets are the 2D matrix `Y = df[TARGET_COLS]` (`y_h1`..`y_h6`).
- Sample weights are passed to `.fit(..., sample_weight=w)` and to every weighted metric.

## Contents

1. Setup
2. Load training tables + contract
3. Persistence baseline helpers
4. Part A — train univariate forecaster
5. Part B — train fingerprint forecaster
6. Persist models + manifest
7. Validation (asserts + summary tables)
8. Visual spot checks

## What this notebook does NOT do (deferred)

- No hyperparameter sweep. `RandomForestRegressor(n_estimators=200, max_depth=None, min_samples_leaf=2)` is a sane default that fits in seconds; sweep in a follow-up notebook (`3a_*` or an MLflow experiment) once the baseline is reviewed.
- No MLflow logging. The training script under `pipelines/training/` owns MLflow; this notebook is the offline / reproducible reference run that produces a portable joblib artifact.
- No live-data fine-tuning. When `aggregate_live.ipynb` lands, retrain by re-running `1_*` → `2_*` → this notebook.
- No calibration / quantile heads. v1 ships point predictions only; intervals can be added by switching to `GradientBoostingRegressor(loss='quantile')` per horizon.
"""
        )
    )

    cells.append(md("## 1. Setup\n"))
    cells.append(
        code(
            r"""import json
import os
import time
from datetime import datetime, timezone

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

pd.set_option("display.max_columns", None)
pd.set_option("display.width", None)

DATA_DIR = "../data/processed"
IN_UNIVARIATE = f"{DATA_DIR}/training_univariate.parquet"
IN_FINGERPRINT = f"{DATA_DIR}/training_fingerprint.parquet"
IN_CONTRACT = f"{DATA_DIR}/training_run.json"

OUT_UNIVARIATE_MODEL = f"{DATA_DIR}/univariate_model.joblib"
OUT_FINGERPRINT_MODEL = f"{DATA_DIR}/fingerprint_model.joblib"
OUT_META = f"{DATA_DIR}/model_training_run.json"

RANDOM_STATE = 42
RF_PARAMS = {
    "n_estimators": 200,
    "max_depth": None,
    "min_samples_leaf": 2,
    "n_jobs": -1,
    "random_state": RANDOM_STATE,
}

HORIZONS = list(range(1, 7))
TARGET_COLS = [f"y_h{h}" for h in HORIZONS]


def split_xy(df: pd.DataFrame, feature_cols: list[str]) -> tuple[pd.DataFrame, pd.DataFrame, np.ndarray]:
    X = df[feature_cols].astype(np.float32)
    Y = df[TARGET_COLS].astype(np.float32)
    w = df["sample_weight"].to_numpy(dtype=np.float64)
    return X, Y, w


def weighted_metrics(y_true: pd.DataFrame, y_pred: np.ndarray, w: np.ndarray) -> dict:
    # Per-horizon weighted MAE / RMSE, plus aggregate means and a weighted R^2 (mean over horizons).
    out: dict = {}
    rmses, maes, r2s = [], [], []
    for j, h in enumerate(HORIZONS):
        yt = y_true.iloc[:, j].to_numpy()
        yp = y_pred[:, j]
        mae = float(mean_absolute_error(yt, yp, sample_weight=w))
        rmse = float(np.sqrt(mean_squared_error(yt, yp, sample_weight=w)))
        r2 = float(r2_score(yt, yp, sample_weight=w))
        out[f"wmae_h{h}"] = mae
        out[f"wrmse_h{h}"] = rmse
        out[f"r2_h{h}"] = r2
        maes.append(mae)
        rmses.append(rmse)
        r2s.append(r2)
    out["wmae_mean"] = float(np.mean(maes))
    out["wrmse_mean"] = float(np.mean(rmses))
    out["r2_weighted_mean"] = float(np.mean(r2s))
    return out


def metrics_table(metrics_by_split: dict[str, dict]) -> pd.DataFrame:
    # Flatten {split: {metric: value}} into a tidy DataFrame for printing.
    rows = []
    for split, m in metrics_by_split.items():
        for h in HORIZONS:
            rows.append(
                {
                    "split": split,
                    "horizon": h,
                    "wmae": m[f"wmae_h{h}"],
                    "wrmse": m[f"wrmse_h{h}"],
                    "r2": m[f"r2_h{h}"],
                }
            )
    return pd.DataFrame(rows)
"""
        )
    )

    cells.append(md("## 2. Load training tables + contract\n"))
    cells.append(
        code(
            r"""with open(IN_CONTRACT) as f:
    contract = json.load(f)

UNIVARIATE_FEATURE_COLS = contract["univariate_feature_cols"]
FINGERPRINT_FEATURE_COLS = contract["fingerprint_feature_cols"]
assert contract["univariate_target_cols"] == TARGET_COLS, (
    f"contract target cols mismatch: {contract['univariate_target_cols']} vs {TARGET_COLS}"
)
assert contract["fingerprint_target_cols"] == TARGET_COLS, (
    f"contract target cols mismatch: {contract['fingerprint_target_cols']} vs {TARGET_COLS}"
)

uni = pd.read_parquet(IN_UNIVARIATE)
fp = pd.read_parquet(IN_FINGERPRINT)

print("univariate :", uni.shape, "| feature cols:", UNIVARIATE_FEATURE_COLS)
print("fingerprint:", fp.shape, "| feature cols:", FINGERPRINT_FEATURE_COLS)
print()
print("rows per split (univariate):")
print(uni["split_group"].value_counts().sort_index())
print()
print("rows per split (fingerprint):")
print(fp["split_group"].value_counts().sort_index())
"""
        )
    )

    cells.append(md("## 3. Persistence baseline helpers\n\nBaseline forecast: `ŷ_h = share_t` for every horizon (carry the anchor share forward). Any useful model must beat this on weighted MAE, otherwise `share_t` itself is the recommended estimator and the model is dead weight.\n"))
    cells.append(
        code(
            r"""def persistence_predictions(df: pd.DataFrame) -> np.ndarray:
    return np.tile(df["share_t"].to_numpy(dtype=np.float64).reshape(-1, 1), (1, len(HORIZONS)))


def evaluate_split(
    df: pd.DataFrame,
    feature_cols: list[str],
    model,
) -> tuple[dict, dict]:
    # Return (model_metrics, baseline_metrics) for one split frame.
    X, Y, w = split_xy(df, feature_cols)
    model_pred = model.predict(X)
    base_pred = persistence_predictions(df)
    return weighted_metrics(Y, model_pred, w), weighted_metrics(Y, base_pred, w)
"""
        )
    )

    cells.append(md("## 4. Part A — train univariate forecaster\n"))
    cells.append(
        code(
            r"""uni_train = uni[uni["split_group"] == "train"].reset_index(drop=True)
uni_val = uni[uni["split_group"] == "val"].reset_index(drop=True)
uni_holdout = uni[uni["split_group"] == "holdout"].reset_index(drop=True)
print(f"univariate splits: train={len(uni_train)}  val={len(uni_val)}  holdout={len(uni_holdout)}")

X_tr, Y_tr, w_tr = split_xy(uni_train, UNIVARIATE_FEATURE_COLS)

t0 = time.time()
uni_model = RandomForestRegressor(**RF_PARAMS)
uni_model.fit(X_tr, Y_tr, sample_weight=w_tr)
uni_fit_seconds = time.time() - t0
print(f"univariate model fit in {uni_fit_seconds:.2f}s | n_train={len(X_tr)} | features={UNIVARIATE_FEATURE_COLS}")

uni_metrics: dict[str, dict] = {}
uni_baseline: dict[str, dict] = {}
for split_name, split_df in [("train", uni_train), ("val", uni_val), ("holdout", uni_holdout)]:
    m_model, m_base = evaluate_split(split_df, UNIVARIATE_FEATURE_COLS, uni_model)
    uni_metrics[split_name] = m_model
    uni_baseline[split_name] = m_base

print("\nunivariate model metrics:")
print(metrics_table(uni_metrics).round(6).to_string(index=False))
print("\nunivariate persistence baseline metrics:")
print(metrics_table(uni_baseline).round(6).to_string(index=False))

print("\nunivariate aggregate (mean over horizons):")
agg_rows = []
for split in ["train", "val", "holdout"]:
    agg_rows.append(
        {
            "split": split,
            "model_wmae": uni_metrics[split]["wmae_mean"],
            "base_wmae": uni_baseline[split]["wmae_mean"],
            "wmae_lift_%": 100.0 * (uni_baseline[split]["wmae_mean"] - uni_metrics[split]["wmae_mean"]) / uni_baseline[split]["wmae_mean"],
            "model_wrmse": uni_metrics[split]["wrmse_mean"],
            "base_wrmse": uni_baseline[split]["wrmse_mean"],
            "model_r2": uni_metrics[split]["r2_weighted_mean"],
        }
    )
print(pd.DataFrame(agg_rows).round(6).to_string(index=False))

uni_importance = pd.Series(uni_model.feature_importances_, index=UNIVARIATE_FEATURE_COLS).sort_values(ascending=False)
print("\nunivariate feature importances:")
print(uni_importance.round(4).to_string())
"""
        )
    )

    cells.append(md("## 5. Part B — train fingerprint forecaster\n"))
    cells.append(
        code(
            r"""fp_train = fp[fp["split_group"] == "train"].reset_index(drop=True)
fp_val = fp[fp["split_group"] == "val"].reset_index(drop=True)
fp_holdout = fp[fp["split_group"] == "holdout"].reset_index(drop=True)
print(f"fingerprint splits: train={len(fp_train)}  val={len(fp_val)}  holdout={len(fp_holdout)}")

X_tr, Y_tr, w_tr = split_xy(fp_train, FINGERPRINT_FEATURE_COLS)

t0 = time.time()
fp_model = RandomForestRegressor(**RF_PARAMS)
fp_model.fit(X_tr, Y_tr, sample_weight=w_tr)
fp_fit_seconds = time.time() - t0
print(f"fingerprint model fit in {fp_fit_seconds:.2f}s | n_train={len(X_tr)} | features={FINGERPRINT_FEATURE_COLS}")

fp_metrics: dict[str, dict] = {}
fp_baseline: dict[str, dict] = {}
for split_name, split_df in [("train", fp_train), ("val", fp_val), ("holdout", fp_holdout)]:
    m_model, m_base = evaluate_split(split_df, FINGERPRINT_FEATURE_COLS, fp_model)
    fp_metrics[split_name] = m_model
    fp_baseline[split_name] = m_base

print("\nfingerprint model metrics:")
print(metrics_table(fp_metrics).round(6).to_string(index=False))
print("\nfingerprint persistence baseline metrics:")
print(metrics_table(fp_baseline).round(6).to_string(index=False))

print("\nfingerprint aggregate (mean over horizons):")
agg_rows = []
for split in ["train", "val", "holdout"]:
    agg_rows.append(
        {
            "split": split,
            "model_wmae": fp_metrics[split]["wmae_mean"],
            "base_wmae": fp_baseline[split]["wmae_mean"],
            "wmae_lift_%": 100.0 * (fp_baseline[split]["wmae_mean"] - fp_metrics[split]["wmae_mean"]) / fp_baseline[split]["wmae_mean"],
            "model_wrmse": fp_metrics[split]["wrmse_mean"],
            "base_wrmse": fp_baseline[split]["wrmse_mean"],
            "model_r2": fp_metrics[split]["r2_weighted_mean"],
        }
    )
print(pd.DataFrame(agg_rows).round(6).to_string(index=False))

fp_importance = pd.Series(fp_model.feature_importances_, index=FINGERPRINT_FEATURE_COLS).sort_values(ascending=False)
print("\nfingerprint feature importances:")
print(fp_importance.round(4).to_string())
"""
        )
    )

    cells.append(md("## 6. Persist models + manifest\n"))
    cells.append(
        code(
            r"""os.makedirs(DATA_DIR, exist_ok=True)

joblib.dump(uni_model, OUT_UNIVARIATE_MODEL, compress=3)
joblib.dump(fp_model, OUT_FINGERPRINT_MODEL, compress=3)

meta = {
    "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "random_state": RANDOM_STATE,
    "horizons": HORIZONS,
    "target_cols": TARGET_COLS,
    "rf_params": RF_PARAMS,
    "inputs": {
        "univariate_training": IN_UNIVARIATE,
        "fingerprint_training": IN_FINGERPRINT,
        "feature_contract": IN_CONTRACT,
    },
    "univariate": {
        "model_path": OUT_UNIVARIATE_MODEL,
        "model_class": type(uni_model).__name__,
        "feature_cols": UNIVARIATE_FEATURE_COLS,
        "n_train": int(len(uni_train)),
        "n_val": int(len(uni_val)),
        "n_holdout": int(len(uni_holdout)),
        "fit_seconds": float(uni_fit_seconds),
        "feature_importances": {k: float(v) for k, v in uni_importance.items()},
        "metrics": {"model": uni_metrics, "persistence_baseline": uni_baseline},
    },
    "fingerprint": {
        "model_path": OUT_FINGERPRINT_MODEL,
        "model_class": type(fp_model).__name__,
        "feature_cols": FINGERPRINT_FEATURE_COLS,
        "n_train": int(len(fp_train)),
        "n_val": int(len(fp_val)),
        "n_holdout": int(len(fp_holdout)),
        "fit_seconds": float(fp_fit_seconds),
        "feature_importances": {k: float(v) for k, v in fp_importance.items()},
        "metrics": {"model": fp_metrics, "persistence_baseline": fp_baseline},
    },
}
with open(OUT_META, "w") as f:
    json.dump(meta, f, indent=2)

for p in (OUT_UNIVARIATE_MODEL, OUT_FINGERPRINT_MODEL, OUT_META):
    print("wrote", p, os.path.getsize(p))
"""
        )
    )

    cells.append(md("## 7. Validation (asserts + summary tables)\n\nRound-trip both joblib artifacts, then re-score on holdout to confirm the loaded models match in-memory predictions byte-for-byte. Final headline metrics are printed below.\n"))
    cells.append(
        code(
            r"""# --- Round-trip both models and confirm parity on holdout ---
uni_loaded = joblib.load(OUT_UNIVARIATE_MODEL)
fp_loaded = joblib.load(OUT_FINGERPRINT_MODEL)

X_uh, _, _ = split_xy(uni_holdout, UNIVARIATE_FEATURE_COLS)
X_fh, _, _ = split_xy(fp_holdout, FINGERPRINT_FEATURE_COLS)

uni_in_mem = uni_model.predict(X_uh)
uni_disk = uni_loaded.predict(X_uh)
fp_in_mem = fp_model.predict(X_fh)
fp_disk = fp_loaded.predict(X_fh)
# joblib compress=3 can permute internal float ops; allow machine-epsilon slack.
np.testing.assert_allclose(uni_in_mem, uni_disk, rtol=1e-10, atol=1e-12)
np.testing.assert_allclose(fp_in_mem, fp_disk, rtol=1e-10, atol=1e-12)

# --- Headline metrics ---
def headline(name, metrics, baseline):
    return {
        "model": name,
        "split": "holdout",
        "model_wmae": metrics["holdout"]["wmae_mean"],
        "base_wmae": baseline["holdout"]["wmae_mean"],
        "wmae_lift_%": 100.0 * (baseline["holdout"]["wmae_mean"] - metrics["holdout"]["wmae_mean"]) / baseline["holdout"]["wmae_mean"],
        "model_wrmse": metrics["holdout"]["wrmse_mean"],
        "base_wrmse": baseline["holdout"]["wrmse_mean"],
        "model_r2": metrics["holdout"]["r2_weighted_mean"],
    }

print("=" * 72)
print("HEADLINE — holdout metrics (mean over horizons 1..6)")
print("=" * 72)
print(
    pd.DataFrame(
        [
            headline("univariate", uni_metrics, uni_baseline),
            headline("fingerprint", fp_metrics, fp_baseline),
        ]
    )
    .round(6)
    .to_string(index=False)
)

# --- Per-horizon holdout ---
print("\nper-horizon holdout (univariate):")
print(metrics_table({"holdout": uni_metrics["holdout"]}).round(6).to_string(index=False))
print("\nper-horizon holdout (fingerprint):")
print(metrics_table({"holdout": fp_metrics["holdout"]}).round(6).to_string(index=False))

# --- Sanity: no NaNs in predictions ---
assert np.isfinite(uni_in_mem).all(), "univariate predictions contain non-finite values"
assert np.isfinite(fp_in_mem).all(), "fingerprint predictions contain non-finite values"

# --- Sanity: model beats persistence on holdout (or warn loudly) ---
def lift_check(label, metrics, baseline):
    diff = baseline["holdout"]["wmae_mean"] - metrics["holdout"]["wmae_mean"]
    if diff <= 0:
        print(f"WARNING: {label} model does NOT beat persistence baseline on holdout (Δwmae={diff:.6f})")
    else:
        print(f"OK: {label} beats persistence on holdout (Δwmae={diff:.6f})")

lift_check("univariate", uni_metrics, uni_baseline)
lift_check("fingerprint", fp_metrics, fp_baseline)

print("\nall validation asserts PASSED (joblib round-trip + finite predictions)")
"""
        )
    )

    cells.append(md("## 8. Visual spot checks\n\nFor each model, pick the series with the most training rows and overlay the model's 6-month forecast against the actual share trajectory anchored at the median row. Skipped by `_run_notebook.py` because the cell uses matplotlib.\n"))
    cells.append(
        code(
            r"""import matplotlib.pyplot as plt
from pandas.tseries.offsets import DateOffset

# Part A — univariate spot
counts = uni.groupby(["dimension", "level_id"], observed=True).size().sort_values(ascending=False)
d0, lid0 = counts.index[0]
sub = uni[(uni["dimension"] == d0) & (uni["level_id"] == lid0)].sort_values("anchor_month")
if len(sub):
    row = sub.iloc[len(sub) // 2]
    t0 = row["anchor_month"]
    X_row = row[UNIVARIATE_FEATURE_COLS].to_frame().T.astype(np.float32)
    yhat = uni_model.predict(X_row)[0]
    actual = row[TARGET_COLS].to_numpy(dtype=float)
    base = np.full(len(HORIZONS), float(row["share_t"]))

    xs = list(range(1, 7))
    fig, ax = plt.subplots(figsize=(10, 3))
    ax.plot(xs, actual, "o-", label="actual")
    ax.plot(xs, yhat, "s--", label="random forest")
    ax.plot(xs, base, "x:", label="persistence (share_t)")
    ax.set_xticks(xs)
    ax.set_xlabel("horizon (months ahead)")
    ax.set_ylabel("share_articles")
    ax.set_title(f"Part A spot: {d0} level_id={lid0}  anchor={t0:%Y-%m}")
    ax.legend()
    plt.tight_layout()
    plt.show()

# Part B — fingerprint spot
fp_counts = fp.groupby(
    ["product_type_id", "gender_id", "color_master_id", "graphical_appearance_id", "material_id"],
    observed=True,
).size().sort_values(ascending=False)
key = fp_counts.index[0]
fp_cols = ["product_type_id", "gender_id", "color_master_id", "graphical_appearance_id", "material_id"]
mask = np.logical_and.reduce([fp[c] == key[i] for i, c in enumerate(fp_cols)])
sub = fp.loc[mask].sort_values("anchor_month")
if len(sub):
    row = sub.iloc[len(sub) // 2]
    t0 = row["anchor_month"]
    X_row = row[FINGERPRINT_FEATURE_COLS].to_frame().T.astype(np.float32)
    yhat = fp_model.predict(X_row)[0]
    actual = row[TARGET_COLS].to_numpy(dtype=float)
    base = np.full(len(HORIZONS), float(row["share_t"]))

    xs = list(range(1, 7))
    fig, ax = plt.subplots(figsize=(10, 3))
    ax.plot(xs, actual, "o-", label="actual")
    ax.plot(xs, yhat, "s--", label="random forest")
    ax.plot(xs, base, "x:", label="persistence (share_t)")
    ax.set_xticks(xs)
    ax.set_xlabel("horizon (months ahead)")
    ax.set_ylabel("share_articles")
    ax.set_title(f"Part B spot: fingerprint={key} anchor={t0:%Y-%m}")
    ax.legend()
    plt.tight_layout()
    plt.show()
"""
        )
    )

    cells.append(
        md(
            r"""## Downstream `4_*` handoff

Both models are loadable with `joblib.load(...)` and predict 2D arrays of shape `(n_rows, 6)`. Column order in `X` must match the saved `feature_cols` exactly.

```python
import joblib
import pandas as pd

uni_model = joblib.load("trndly/data/models/univariate_model.joblib")
fp_model  = joblib.load("trndly/data/models/fingerprint_model.joblib")

# X_uni: DataFrame with columns = feature_cols from manifest (univariate)
# X_fp:  DataFrame with columns = feature_cols from manifest (fingerprint)
y_h = uni_model.predict(X_uni)  # shape (n, 6) → y_h1..y_h6
```

Next notebook (`4_*`) ideas: error-decomposition by `dimension`/`level_id`, per-horizon calibration plots, hyperparameter sweep with MLflow, and a serving wrapper that decodes ids via `lookup.csv` for the API.
"""
        )
    )

    nb = {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python", "pygments_lexer": "ipython3"},
        },
        "cells": cells,
    }

    nb_path.write_text(json.dumps(nb, indent=1))
    print("Wrote", nb_path, "n_cells=", len(cells))


if __name__ == "__main__":
    main()
