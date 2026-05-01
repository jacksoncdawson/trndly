"""
data_validation.py
------------------
Very simple data / model validation checks for the trndly training data.

Run from `trndly/`:
    python scripts/data_validation.py

What this script does (and WHY each check exists):

1. Schema & null checks
   - Idea: before any fancy stats, make sure the columns you expect are
     actually there, of the right type, and not full of NaNs. 90% of
     "model is broken" incidents are really just a column rename or a
     pipeline that started writing nulls.

2. Basic distribution summary (mean / std / min / max / quantiles)
   - Idea: a quick "does this look sane?" snapshot. If train mean is 0.5
     but test mean is 5.0, something upstream changed.

3. PSI - Population Stability Index  (numeric features, train vs test)
   - Idea: PSI measures how much a feature's *distribution* has shifted
     between two samples (e.g. training data vs. live/production data).
   - How: bin the reference (train) into N buckets, compute the % of
     rows in each bucket for both samples, then:
         PSI = sum( (p_test - p_train) * ln(p_test / p_train) )
   - Rule of thumb:
         PSI < 0.1  -> no real shift
         0.1 - 0.25 -> moderate shift, investigate
         > 0.25     -> major shift, model is probably stale

4. KS - Kolmogorov-Smirnov 2-sample test  (numeric features)
   - Idea: a statistical test asking "are these two samples drawn from
     the same distribution?" It compares the empirical CDFs and reports
     the max gap (the KS statistic) plus a p-value.
   - Use: complements PSI. PSI tells you *how much* shifted, KS tells
     you *is the shift statistically significant*.

5. Category drift (categorical features)
   - Idea: PSI/KS are for numbers. For categoricals we just compare the
     value frequencies and flag categories that appeared/disappeared or
     whose share moved a lot. New unseen categories at inference time
     are a classic source of silent model failures.

6. Target balance check
   - Idea: confirm the label distribution in train vs test is similar.
     If train is 80% class A and test is 20% class A, accuracy numbers
     will lie to you.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats


# ---------- config ----------
DATA_DIR = Path(__file__).resolve().parents[1] / "pipelines" / "training" / "data"
TRAIN_CSV = DATA_DIR / "train.csv"
TEST_CSV = DATA_DIR / "test.csv"
TARGET_COL = "best_timeframe"

PSI_BINS = 10
PSI_WARN = 0.10
PSI_ALERT = 0.25
KS_PVALUE_ALERT = 0.05


# ---------- helpers ----------
def _section(title: str) -> None:
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def psi(reference: np.ndarray, current: np.ndarray, bins: int = PSI_BINS) -> float:
    """Population Stability Index between two 1-D numeric samples.

    We build bin edges from the reference sample's quantiles, then compare
    the % of rows that fall in each bin. A tiny epsilon avoids log(0).
    """
    reference = pd.Series(reference).dropna().to_numpy()
    current = pd.Series(current).dropna().to_numpy()
    if len(reference) == 0 or len(current) == 0:
        return np.nan

    # quantile-based edges so each ref bin has ~equal mass
    quantiles = np.linspace(0, 1, bins + 1)
    edges = np.unique(np.quantile(reference, quantiles))
    if len(edges) < 3:
        return 0.0

    edges[0], edges[-1] = -np.inf, np.inf
    ref_counts, _ = np.histogram(reference, bins=edges)
    cur_counts, _ = np.histogram(current, bins=edges)

    eps = 1e-6
    ref_pct = ref_counts / max(ref_counts.sum(), 1) + eps
    cur_pct = cur_counts / max(cur_counts.sum(), 1) + eps

    return float(np.sum((cur_pct - ref_pct) * np.log(cur_pct / ref_pct)))


def psi_label(value: float) -> str:
    if np.isnan(value):
        return "n/a"
    if value < PSI_WARN:
        return "ok"
    if value < PSI_ALERT:
        return "WARN"
    return "ALERT"


# ---------- checks ----------
def check_schema_and_nulls(train: pd.DataFrame, test: pd.DataFrame) -> None:
    _section("1. Schema & nulls")
    missing = set(train.columns) ^ set(test.columns)
    if missing:
        print(f"  column mismatch between train and test: {sorted(missing)}")
    else:
        print(f"  columns match  ({len(train.columns)} columns)")

    print(f"  rows: train={len(train):,}  test={len(test):,}")

    null_train = train.isna().mean()
    null_test = test.isna().mean()
    nulls = pd.DataFrame({"train_null_pct": null_train, "test_null_pct": null_test})
    nulls = nulls[(nulls["train_null_pct"] > 0) | (nulls["test_null_pct"] > 0)]
    if nulls.empty:
        print("  no nulls anywhere")
    else:
        print("  columns with nulls:")
        print(nulls.round(4).to_string())


def check_distributions(train: pd.DataFrame, test: pd.DataFrame) -> None:
    _section("2. Numeric distribution summary (train vs test)")
    num_cols = train.select_dtypes(include=np.number).columns.tolist()
    summary = pd.DataFrame(
        {
            "train_mean": train[num_cols].mean(),
            "test_mean": test[num_cols].mean(),
            "train_std": train[num_cols].std(),
            "test_std": test[num_cols].std(),
        }
    ).round(4)
    print(summary.to_string())


def check_psi_and_ks(train: pd.DataFrame, test: pd.DataFrame) -> None:
    _section("3. PSI + KS for numeric features (train = reference)")
    num_cols = train.select_dtypes(include=np.number).columns.tolist()
    rows = []
    for col in num_cols:
        psi_val = psi(train[col].to_numpy(), test[col].to_numpy())
        ks_stat, ks_p = stats.ks_2samp(
            train[col].dropna(), test[col].dropna(), method="asymp"
        )
        rows.append(
            {
                "feature": col,
                "psi": round(psi_val, 4),
                "psi_flag": psi_label(psi_val),
                "ks_stat": round(float(ks_stat), 4),
                "ks_pvalue": round(float(ks_p), 4),
                "ks_flag": "DIFFERENT" if ks_p < KS_PVALUE_ALERT else "ok",
            }
        )
    print(pd.DataFrame(rows).to_string(index=False))


def check_categorical_drift(train: pd.DataFrame, test: pd.DataFrame) -> None:
    _section("4. Categorical drift (share of each category)")
    cat_cols = train.select_dtypes(include=["object", "category"]).columns.tolist()
    cat_cols = [c for c in cat_cols if c != "item_name"]  # IDs are not features
    for col in cat_cols:
        tr = train[col].value_counts(normalize=True)
        te = test[col].value_counts(normalize=True)
        merged = pd.DataFrame({"train_pct": tr, "test_pct": te}).fillna(0.0)
        merged["abs_diff"] = (merged["train_pct"] - merged["test_pct"]).abs()
        unseen = merged[(merged["train_pct"] == 0) & (merged["test_pct"] > 0)]
        dropped = merged[(merged["train_pct"] > 0) & (merged["test_pct"] == 0)]
        print(f"\n  [{col}]  unique: train={tr.size}  test={te.size}")
        print(merged.sort_values("abs_diff", ascending=False).head(5).round(4).to_string())
        if not unseen.empty:
            print(f"  WARN unseen-in-train categories appearing in test: {list(unseen.index)}")
        if not dropped.empty:
            print(f"  WARN train categories missing from test: {list(dropped.index)}")


def check_target_balance(train: pd.DataFrame, test: pd.DataFrame) -> None:
    _section("5. Target balance")
    if TARGET_COL not in train.columns:
        print(f"  target column '{TARGET_COL}' not found; skipping")
        return
    tr = train[TARGET_COL].value_counts(normalize=True)
    te = test[TARGET_COL].value_counts(normalize=True)
    merged = pd.DataFrame({"train_pct": tr, "test_pct": te}).fillna(0.0)
    merged["abs_diff"] = (merged["train_pct"] - merged["test_pct"]).abs()
    print(merged.round(4).to_string())
    if merged["abs_diff"].max() > 0.10:
        print("  WARN target distribution differs by >10pp between train and test")


# ---------- main ----------
def main() -> int:
    if not TRAIN_CSV.exists() or not TEST_CSV.exists():
        print(f"missing data files under {DATA_DIR}")
        return 1

    train = pd.read_csv(TRAIN_CSV)
    test = pd.read_csv(TEST_CSV)

    check_schema_and_nulls(train, test)
    check_distributions(train, test)
    check_psi_and_ks(train, test)
    check_categorical_drift(train, test)
    check_target_balance(train, test)

    print("\ndone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
