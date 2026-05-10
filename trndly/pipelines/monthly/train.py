"""Train univariate + fingerprint forecasters from the training tables.

Was notebook ``3_train_models.ipynb``.

Reads:
    data/processed/training_univariate.parquet
    data/processed/training_fingerprint.parquet
    data/processed/training_run.json   (feature contract written by features.py)

Writes:
    data/models/univariate_model.joblib
    data/models/fingerprint_model.joblib
    data/models/model_training_run.json   (metrics + manifest)

Each model is a multi-output ``RandomForestRegressor`` (200 estimators,
``min_samples_leaf=2``, no max depth) predicting ``y_h1..y_h6`` (six future
months of ``share_articles``). Persistence baseline (``ŷ_h = share_t``) is
computed as a sanity floor — any useful model must beat it on weighted MAE
on holdout.

Usage:
    python -m pipelines.monthly.train
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

from pipelines.paths import (
    FINGERPRINT_MODEL_JOBLIB,
    MODEL_TRAINING_RUN_JSON,
    MODELS_DIR,
    TRAINING_FINGERPRINT_PARQUET,
    TRAINING_RUN_JSON,
    TRAINING_UNIVARIATE_PARQUET,
    UNIVARIATE_MODEL_JOBLIB,
)

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Hyperparameters                                                              #
# --------------------------------------------------------------------------- #

RANDOM_STATE: int = 42
RF_PARAMS: dict = {
    "n_estimators": 200,
    "max_depth": None,
    "min_samples_leaf": 2,
    "n_jobs": -1,
    "random_state": RANDOM_STATE,
}

HORIZONS: list[int] = list(range(1, 7))
TARGET_COLS: list[str] = [f"y_h{h}" for h in HORIZONS]


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #

def split_xy(
    df: pd.DataFrame, feature_cols: list[str]
) -> tuple[pd.DataFrame, pd.DataFrame, np.ndarray]:
    X = df[feature_cols].astype(np.float32)
    Y = df[TARGET_COLS].astype(np.float32)
    w = df["sample_weight"].to_numpy(dtype=np.float64)
    return X, Y, w


def weighted_metrics(y_true: pd.DataFrame, y_pred: np.ndarray, w: np.ndarray) -> dict:
    """Per-horizon weighted MAE / RMSE + aggregate means and weighted R^2."""
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


def persistence_predictions(df: pd.DataFrame) -> np.ndarray:
    """Baseline: ŷ_h = share_t for every horizon (carry anchor share forward)."""
    return np.tile(
        df["share_t"].to_numpy(dtype=np.float64).reshape(-1, 1),
        (1, len(HORIZONS)),
    )


def evaluate_split(
    df: pd.DataFrame, feature_cols: list[str], model
) -> tuple[dict, dict]:
    """Return (model_metrics, persistence_baseline_metrics) for one split frame."""
    X, Y, w = split_xy(df, feature_cols)
    model_pred = model.predict(X)
    base_pred = persistence_predictions(df)
    return weighted_metrics(Y, model_pred, w), weighted_metrics(Y, base_pred, w)


# --------------------------------------------------------------------------- #
# Stage drivers                                                                #
# --------------------------------------------------------------------------- #

def _train_one(
    *,
    label: str,
    df: pd.DataFrame,
    feature_cols: list[str],
) -> tuple[RandomForestRegressor, dict, dict, dict]:
    """Train one model on the train split, evaluate on all three splits.

    Returns (model, metrics_by_split, baseline_by_split, run_summary).
    """
    train = df[df["split_group"] == "train"].reset_index(drop=True)
    val = df[df["split_group"] == "val"].reset_index(drop=True)
    holdout = df[df["split_group"] == "holdout"].reset_index(drop=True)
    logger.info(
        "%s splits: train=%d val=%d holdout=%d", label, len(train), len(val), len(holdout)
    )

    X_tr, Y_tr, w_tr = split_xy(train, feature_cols)

    t0 = time.time()
    model = RandomForestRegressor(**RF_PARAMS)
    model.fit(X_tr, Y_tr, sample_weight=w_tr)
    fit_seconds = time.time() - t0
    logger.info(
        "%s model fit in %.2fs | n_train=%d | features=%s",
        label, fit_seconds, len(X_tr), feature_cols,
    )

    metrics: dict[str, dict] = {}
    baseline: dict[str, dict] = {}
    for split_name, split_df in [("train", train), ("val", val), ("holdout", holdout)]:
        m_model, m_base = evaluate_split(split_df, feature_cols, model)
        metrics[split_name] = m_model
        baseline[split_name] = m_base

    importance = pd.Series(
        model.feature_importances_, index=feature_cols
    ).sort_values(ascending=False)

    summary = {
        "n_train": int(len(train)),
        "n_val": int(len(val)),
        "n_holdout": int(len(holdout)),
        "fit_seconds": float(fit_seconds),
        "feature_importances": {k: float(v) for k, v in importance.items()},
        "holdout_wmae": metrics["holdout"]["wmae_mean"],
        "holdout_baseline_wmae": baseline["holdout"]["wmae_mean"],
    }
    return model, metrics, baseline, summary


def run_train() -> dict:
    """Fit both models, persist artifacts, write run manifest. Return summary."""
    MODELS_DIR.mkdir(parents=True, exist_ok=True)

    with open(TRAINING_RUN_JSON) as f:
        contract = json.load(f)

    univariate_feature_cols = contract["univariate_feature_cols"]
    fingerprint_feature_cols = contract["fingerprint_feature_cols"]
    if contract["univariate_target_cols"] != TARGET_COLS:
        raise ValueError(
            f"contract univariate target cols mismatch: "
            f"{contract['univariate_target_cols']} vs {TARGET_COLS}"
        )
    if contract["fingerprint_target_cols"] != TARGET_COLS:
        raise ValueError(
            f"contract fingerprint target cols mismatch: "
            f"{contract['fingerprint_target_cols']} vs {TARGET_COLS}"
        )

    uni = pd.read_parquet(TRAINING_UNIVARIATE_PARQUET)
    fp = pd.read_parquet(TRAINING_FINGERPRINT_PARQUET)
    logger.info("loaded univariate=%s fingerprint=%s", uni.shape, fp.shape)

    uni_model, uni_metrics, uni_baseline, uni_summary = _train_one(
        label="univariate", df=uni, feature_cols=univariate_feature_cols,
    )
    fp_model, fp_metrics, fp_baseline, fp_summary = _train_one(
        label="fingerprint", df=fp, feature_cols=fingerprint_feature_cols,
    )

    joblib.dump(uni_model, UNIVARIATE_MODEL_JOBLIB, compress=3)
    joblib.dump(fp_model, FINGERPRINT_MODEL_JOBLIB, compress=3)
    logger.info("wrote %s (%d B)", UNIVARIATE_MODEL_JOBLIB, os.path.getsize(UNIVARIATE_MODEL_JOBLIB))
    logger.info("wrote %s (%d B)", FINGERPRINT_MODEL_JOBLIB, os.path.getsize(FINGERPRINT_MODEL_JOBLIB))

    meta = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "random_state": RANDOM_STATE,
        "horizons": HORIZONS,
        "target_cols": TARGET_COLS,
        "rf_params": RF_PARAMS,
        "inputs": {
            "univariate_training": str(TRAINING_UNIVARIATE_PARQUET),
            "fingerprint_training": str(TRAINING_FINGERPRINT_PARQUET),
            "feature_contract": str(TRAINING_RUN_JSON),
        },
        "univariate": {
            "model_path": str(UNIVARIATE_MODEL_JOBLIB),
            "model_class": type(uni_model).__name__,
            "feature_cols": univariate_feature_cols,
            **uni_summary,
            "metrics": {"model": uni_metrics, "persistence_baseline": uni_baseline},
        },
        "fingerprint": {
            "model_path": str(FINGERPRINT_MODEL_JOBLIB),
            "model_class": type(fp_model).__name__,
            "feature_cols": fingerprint_feature_cols,
            **fp_summary,
            "metrics": {"model": fp_metrics, "persistence_baseline": fp_baseline},
        },
    }
    with open(MODEL_TRAINING_RUN_JSON, "w") as f:
        json.dump(meta, f, indent=2)
    logger.info("wrote %s", MODEL_TRAINING_RUN_JSON)

    # Sanity: each model should beat its persistence baseline on holdout
    for label, m, b in [
        ("univariate", uni_metrics, uni_baseline),
        ("fingerprint", fp_metrics, fp_baseline),
    ]:
        diff = b["holdout"]["wmae_mean"] - m["holdout"]["wmae_mean"]
        if diff <= 0:
            logger.warning(
                "%s model does NOT beat persistence baseline on holdout (Δwmae=%.6f)",
                label, diff,
            )
        else:
            logger.info(
                "%s model beats persistence baseline on holdout (Δwmae=%.6f, lift=%.2f%%)",
                label, diff, 100.0 * diff / b["holdout"]["wmae_mean"],
            )

    return {
        "univariate": uni_summary,
        "fingerprint": fp_summary,
    }


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    summary = run_train()
    logger.info("train summary: %s", summary)


if __name__ == "__main__":
    main()
