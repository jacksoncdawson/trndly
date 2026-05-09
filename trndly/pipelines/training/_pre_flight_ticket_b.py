"""Pre-flight measurement for Ticket B (add fingerprint IDs as features).

Read-only against the trained model artifact; only writes a temp .joblib
for the A/B candidate model under /tmp.

Run from repo root or trndly/:
    /opt/anaconda3/bin/python pipelines/training/_pre_flight_ticket_b.py

Output:
    1. Per-dim residual breakdown (5 tables)
    2. Top-N (product_type_id, month_of_year) seasonality misses
    3. Holdout WMAE for: current RF | HGBR-5feat | HGBR-6feat (+product_type_id)
    4. Recommendation: ship / narrow / kill Ticket B
"""
from __future__ import annotations

import json
import sys
import tempfile
import warnings
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=FutureWarning)

HERE = Path(__file__).resolve()
TRNDLY_ROOT = HERE.parents[2]
TRAINING_FP_PARQUET = TRNDLY_ROOT / "data" / "processed" / "training_fingerprint.parquet"
MODEL_JOBLIB = TRNDLY_ROOT / "data" / "models" / "fingerprint_model.joblib"

DIM_COLS = [
    "product_type_id", "gender_id", "color_master_id",
    "graphical_appearance_id", "material_id",
]
FEATURE_COLS = ["month_of_year", "share_t", "share_lag1", "share_lag2", "share_lag3"]
TARGET_COLS = [f"y_h{h}" for h in range(1, 7)]


def wmae_per_row(y_true: np.ndarray, y_pred: np.ndarray) -> np.ndarray:
    """Mean abs error across the 6 horizons, returned per-row."""
    return np.abs(y_true - y_pred).mean(axis=1)


def weighted_mae(y_true: np.ndarray, y_pred: np.ndarray, weights: np.ndarray) -> float:
    """Sample-weight-weighted mean abs error across all rows × all horizons."""
    abs_err = np.abs(y_true - y_pred)             # (n, 6)
    row_mae = abs_err.mean(axis=1)                # (n,)
    return float(np.average(row_mae, weights=weights))


def main() -> None:
    print("=" * 78)
    print("Pre-flight: Ticket B (add fingerprint IDs as features)")
    print("=" * 78)

    df = pd.read_parquet(TRAINING_FP_PARQUET)
    train_df = df[df["split_group"] == "train"].reset_index(drop=True)
    val_df = df[df["split_group"] == "val"].reset_index(drop=True)
    holdout_df = df[df["split_group"] == "holdout"].reset_index(drop=True)
    print(f"\nTraining frame: {len(df):,} rows  ({len(train_df):,} train, "
          f"{len(val_df):,} val, {len(holdout_df):,} holdout)")
    print(f"Unique fingerprints in frame: "
          f"{df.groupby(DIM_COLS).ngroups:,} ({len(df) / df.groupby(DIM_COLS).ngroups:.1f} rows/fp avg)")

    rf = joblib.load(MODEL_JOBLIB)
    X_holdout = holdout_df[FEATURE_COLS].astype(np.float32)
    Y_holdout = holdout_df[TARGET_COLS].to_numpy()
    W_holdout = holdout_df["sample_weight"].to_numpy()

    print("\n" + "=" * 78)
    print("BASELINE: existing RandomForestRegressor (5 features)")
    print("=" * 78)
    Y_pred_rf = rf.predict(X_holdout)
    rf_wmae = weighted_mae(Y_holdout, Y_pred_rf, W_holdout)
    print(f"  Holdout WMAE (sample-weighted, mean over 6 horizons): {rf_wmae:.6f}")

    # Per-row residual: mean abs error across 6 horizons.
    holdout_resid = wmae_per_row(Y_holdout, Y_pred_rf)
    holdout_df = holdout_df.copy()
    holdout_df["residual"] = holdout_resid
    global_mean_resid = float(np.average(holdout_resid, weights=W_holdout))
    print(f"  Global mean per-row residual (sample-weighted): {global_mean_resid:.6f}")

    # ------------------------------------------------------------------ #
    # Step 1: per-dim residual breakdown                                   #
    # ------------------------------------------------------------------ #
    print("\n" + "=" * 78)
    print("STEP 1 — Residual breakdown per fingerprint dim")
    print("=" * 78)
    print("(weighted mean residual per level; ratio = level_mean / global_mean)")

    flagged_summary: dict[str, list[tuple[int, float, int]]] = {}
    for col in DIM_COLS:
        grouped = (
            holdout_df.assign(weighted_resid=lambda d: d["residual"] * d["sample_weight"])
            .groupby(col, observed=True)
            .agg(
                n=("residual", "size"),
                w_sum=("sample_weight", "sum"),
                wresid=("weighted_resid", "sum"),
            )
        )
        grouped["mean_resid"] = grouped["wresid"] / grouped["w_sum"]
        grouped["ratio"] = grouped["mean_resid"] / global_mean_resid
        grouped = grouped.sort_values("ratio", ascending=False)

        # Flag levels with ratio > 2 AND meaningful row count (>10).
        flagged = grouped[(grouped["ratio"] > 2.0) & (grouped["n"] > 10)]
        flagged_summary[col] = list(zip(
            flagged.index.tolist(), flagged["ratio"].tolist(), flagged["n"].tolist()
        ))

        print(f"\n--- {col} (n_levels={len(grouped)}) ---")
        # Show top-5 worst + bottom-3 best for context.
        head = grouped.head(5)[["n", "mean_resid", "ratio"]]
        tail = grouped.tail(3)[["n", "mean_resid", "ratio"]]
        print("worst residuals:")
        print(head.to_string(float_format="%.6f"))
        print("best residuals:")
        print(tail.to_string(float_format="%.6f"))
        print(f"levels with ratio > 2.0 and n > 10: {len(flagged)}")

    # ------------------------------------------------------------------ #
    # Step 2: (product_type_id, month_of_year) seasonality misses          #
    # ------------------------------------------------------------------ #
    print("\n" + "=" * 78)
    print("STEP 2 — Seasonality-miss audit: (product_type_id, month_of_year)")
    print("=" * 78)
    cross = (
        holdout_df.assign(weighted_resid=lambda d: d["residual"] * d["sample_weight"])
        .groupby(["product_type_id", "month_of_year"], observed=True)
        .agg(
            n=("residual", "size"),
            w_sum=("sample_weight", "sum"),
            wresid=("weighted_resid", "sum"),
        )
    )
    cross["mean_resid"] = cross["wresid"] / cross["w_sum"]
    cross["ratio"] = cross["mean_resid"] / global_mean_resid

    cross_meaningful = cross[cross["n"] >= 5].sort_values("ratio", ascending=False)
    print(f"\nCells with n >= 5 (out of {len(cross)} total cells): {len(cross_meaningful)}")
    print(f"Top-15 (product_type_id, month_of_year) by mean residual ratio:")
    print(cross_meaningful.head(15)[["n", "mean_resid", "ratio"]].to_string(float_format="%.6f"))

    high_seasonal_mismatch = cross_meaningful[cross_meaningful["ratio"] > 2.0]
    pt_with_seasonal_misses = high_seasonal_mismatch.index.get_level_values(0).unique()
    print(f"\nProduct types with at least one (pt, month) cell ratio > 2.0: "
          f"{len(pt_with_seasonal_misses)}")
    if len(pt_with_seasonal_misses) > 0:
        print(f"  pt_ids: {sorted(pt_with_seasonal_misses.tolist())}")

    # ------------------------------------------------------------------ #
    # Step 3: A/B — HGBR with and without product_type_id                  #
    # ------------------------------------------------------------------ #
    print("\n" + "=" * 78)
    print("STEP 3 — A/B: HistGradientBoostingRegressor")
    print("=" * 78)
    print("Training each model on TRAIN, evaluating on HOLDOUT (sample-weighted).")
    print("Multi-output via MultiOutputRegressor wrapper.")

    from sklearn.ensemble import HistGradientBoostingRegressor
    from sklearn.multioutput import MultiOutputRegressor

    X_tr = train_df[FEATURE_COLS].astype(np.float32)
    Y_tr = train_df[TARGET_COLS].to_numpy()
    W_tr = train_df["sample_weight"].to_numpy()

    print("\n[A] HGBR with 5 features (no IDs) — isolates model-class effect:")
    hgbr_5 = MultiOutputRegressor(HistGradientBoostingRegressor(
        max_iter=200, learning_rate=0.05, max_depth=6, random_state=0,
    ))
    hgbr_5.fit(X_tr, Y_tr, sample_weight=W_tr)
    Y_pred_hgbr5 = hgbr_5.predict(holdout_df[FEATURE_COLS].astype(np.float32))
    hgbr5_wmae = weighted_mae(Y_holdout, Y_pred_hgbr5, W_holdout)
    print(f"  Holdout WMAE: {hgbr5_wmae:.6f}  (Δ vs RF: "
          f"{(hgbr5_wmae - rf_wmae) / rf_wmae * 100:+.2f}%)")

    print("\n[B] HGBR with 6 features (5 + product_type_id, native categorical):")
    feature_cols_with_pt = FEATURE_COLS + ["product_type_id"]
    X_tr_pt = train_df[feature_cols_with_pt].copy()
    # Use pandas categorical dtype so HGBR auto-detects via categorical_features.
    X_tr_pt["product_type_id"] = X_tr_pt["product_type_id"].astype("category")
    X_ho_pt = holdout_df[feature_cols_with_pt].copy()
    X_ho_pt["product_type_id"] = X_ho_pt["product_type_id"].astype(
        pd.CategoricalDtype(categories=X_tr_pt["product_type_id"].cat.categories)
    )

    hgbr_6 = MultiOutputRegressor(HistGradientBoostingRegressor(
        max_iter=200, learning_rate=0.05, max_depth=6, random_state=0,
        categorical_features=["product_type_id"],
    ))
    hgbr_6.fit(X_tr_pt, Y_tr, sample_weight=W_tr)
    Y_pred_hgbr6 = hgbr_6.predict(X_ho_pt)
    hgbr6_wmae = weighted_mae(Y_holdout, Y_pred_hgbr6, W_holdout)
    print(f"  Holdout WMAE: {hgbr6_wmae:.6f}  (Δ vs RF: "
          f"{(hgbr6_wmae - rf_wmae) / rf_wmae * 100:+.2f}%, "
          f"Δ vs HGBR-5: {(hgbr6_wmae - hgbr5_wmae) / hgbr5_wmae * 100:+.2f}%)")

    print("\nMarginal lift of product_type_id (HGBR-6 vs HGBR-5):")
    pt_lift_pct = (hgbr5_wmae - hgbr6_wmae) / hgbr5_wmae * 100
    print(f"  {pt_lift_pct:+.2f}% holdout WMAE reduction")

    # Save the candidate to /tmp for inspection.
    tmp_path = Path(tempfile.gettempdir()) / "fingerprint_model_pt_candidate.joblib"
    joblib.dump(hgbr_6, tmp_path)
    print(f"\nCandidate model saved to: {tmp_path}")

    # ------------------------------------------------------------------ #
    # Step 3b: Apples-to-apples — RF with product_type_id one-hot          #
    # ------------------------------------------------------------------ #
    print("\n" + "=" * 78)
    print("STEP 3b — Same RF class, +product_type_id one-hot")
    print("=" * 78)
    print("(HGBR baseline is 2× worse than production RF — model class matters.")
    print(" Measuring product_type_id lift in the *actual* production model class.)")
    from sklearn.ensemble import RandomForestRegressor

    rf_5 = RandomForestRegressor(
        n_estimators=200, n_jobs=-1, random_state=0,
    )
    rf_5.fit(X_tr, Y_tr, sample_weight=W_tr)
    Y_pred_rf5_fresh = rf_5.predict(X_holdout)
    rf5_fresh_wmae = weighted_mae(Y_holdout, Y_pred_rf5_fresh, W_holdout)
    print(f"\n[A'] Fresh RF on 5 features (sanity-check matches prod artifact):")
    print(f"  Holdout WMAE: {rf5_fresh_wmae:.6f}  (prod RF: {rf_wmae:.6f})")

    # One-hot encode product_type_id
    pt_dummies_tr = pd.get_dummies(
        train_df["product_type_id"], prefix="pt", dtype=np.float32,
    )
    X_tr_oh = pd.concat([X_tr.reset_index(drop=True), pt_dummies_tr.reset_index(drop=True)], axis=1)
    pt_dummies_ho = pd.get_dummies(
        holdout_df["product_type_id"], prefix="pt", dtype=np.float32,
    )
    # Align columns: holdout may be missing some training pt-ids and vice versa.
    pt_dummies_ho = pt_dummies_ho.reindex(columns=pt_dummies_tr.columns, fill_value=0.0)
    X_ho_oh = pd.concat([X_holdout.reset_index(drop=True), pt_dummies_ho.reset_index(drop=True)], axis=1)

    print(f"\n[B'] RF with 5 features + product_type_id one-hot ({pt_dummies_tr.shape[1]} dummies):")
    rf_oh = RandomForestRegressor(
        n_estimators=200, n_jobs=-1, random_state=0,
    )
    rf_oh.fit(X_tr_oh, Y_tr, sample_weight=W_tr)
    Y_pred_rf_oh = rf_oh.predict(X_ho_oh)
    rf_oh_wmae = weighted_mae(Y_holdout, Y_pred_rf_oh, W_holdout)
    rf_pt_lift_pct = (rf5_fresh_wmae - rf_oh_wmae) / rf5_fresh_wmae * 100
    print(f"  Holdout WMAE: {rf_oh_wmae:.6f}  "
          f"(Δ vs fresh RF-5: {(rf_oh_wmae - rf5_fresh_wmae) / rf5_fresh_wmae * 100:+.2f}%, "
          f"marginal lift: {rf_pt_lift_pct:+.2f}%)")

    # ------------------------------------------------------------------ #
    # Step 4: Recommendation                                               #
    # ------------------------------------------------------------------ #
    print("\n" + "=" * 78)
    print("STEP 4 — Recommendation")
    print("=" * 78)

    rf_to_hgbr6_pct = (rf_wmae - hgbr6_wmae) / rf_wmae * 100
    print(f"\nKey numbers:")
    print(f"  RF (5 feat, prod artifact) : WMAE = {rf_wmae:.6f}")
    print(f"  RF (5 feat, fresh fit)     : WMAE = {rf5_fresh_wmae:.6f}")
    print(f"  RF (5 feat + pt one-hot)   : WMAE = {rf_oh_wmae:.6f}  "
          f"({rf_pt_lift_pct:+.2f}% marginal lift from pt)")
    print(f"  HGBR (5 feat)              : WMAE = {hgbr5_wmae:.6f}  ({(hgbr5_wmae-rf_wmae)/rf_wmae*100:+.2f}% vs prod)")
    print(f"  HGBR (6 feat, +pt cat)     : WMAE = {hgbr6_wmae:.6f}  "
          f"({pt_lift_pct:+.2f}% marginal lift from pt)")

    n_flagged_levels_total = sum(len(v) for v in flagged_summary.values())
    print(f"\nResidual evidence:")
    for col, items in flagged_summary.items():
        print(f"  {col}: {len(items)} levels with ratio > 2.0 (n>10)")
    print(f"  total flagged levels across 5 dims: {n_flagged_levels_total}")
    print(f"  product_types with seasonal cells > 2.0x: {len(pt_with_seasonal_misses)}")

    print("\nDecision rules from the brief:")
    print("  - ≥5% holdout WMAE improvement → ship Ticket B as planned (all 5 IDs)")
    print("  - 2-5% lift → narrower / engineered features")
    print("  - <2% lift → kill Ticket B")
    if pt_lift_pct >= 5.0:
        verdict = "SHIP TICKET B AS PLANNED — but verify other 4 IDs are pulling weight too"
    elif pt_lift_pct >= 2.0:
        verdict = "SHIP A NARROWER VERSION — only IDs that residual analysis flagged"
    else:
        verdict = "KILL TICKET B — lift insufficient to justify complexity"
    print(f"\nVerdict (using marginal lift {pt_lift_pct:+.2f}%): {verdict}")


if __name__ == "__main__":
    main()
