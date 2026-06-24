"""Precompute predictions for the entire universe.

Iterates every ``(dimension, level_id)`` and every distinct 5-D fingerprint
present at the latest anchor month of the merged cubes; scores each via the
champion model; classifies the trajectory via :mod:`pipelines.monthly.state`
(forward-first hybrid rule consuming share_lag1, share_t, and y_h1..h6);
decodes IDs to human names via ``lookup.csv``; writes two parquet files into the
tick's immutable checkpoint dir (plan §12):

    data/ticks/<YYYY-MM>/predictions_univariate.parquet
    data/ticks/<YYYY-MM>/predictions_fingerprint.parquet

The scoring model is the canonical CHAMPION (``data/models/<role>_model.joblib``,
promoted by ``evaluate``) — never the tick's own candidate. The cube it scores is
this tick's ``merged_*`` checkpoint.

Anchor selection: ``_find_eligible_anchor`` picks the latest month with
3 contiguous prior months in the cube. When the latest live month is
isolated (no real priors), run ``scripts/backfill_anchor_lags.py`` first
to synthesize seasonal priors so the live month becomes eligible. If
this step runs against a cube that has no synthetic backfill and an
isolated live month, predict will log a WARNING and fall back to the
nearest eligible (typically historical) anchor — it tells you how to
fix it in the log message.

Rows where the cube lacks t-3..t-1 lag coverage at a candidate anchor
are silently skipped — the parquet only contains rows for which a
forecast was producible.

Schemas are enforced via :mod:`pipelines.contracts` validators
(``validate_predictions_univariate_frame`` / ``validate_predictions_fingerprint_frame``)
before write.

Usage:
    python -m pipelines.monthly.predict
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import joblib
import numpy as np
import pandas as pd

from pipelines.contracts import (
    validate_predictions_fingerprint_frame,
    validate_predictions_univariate_frame,
)
from pipelines.paths import (
    LOOKUP_CSV,
    champion_joblib_for,
    resolve_tick_month,
    tick_dir,
    tick_merged_path,
    tick_model_training_run_json,
    tick_predictions_path,
    tick_training_run_json,
)
from pipelines.monthly.state import classify_state
from pipelines.cube_slicing import (
    FINGERPRINT_COLS,
    build_fingerprint_inference_rows,
    build_univariate_inference_row,
    month_shift,
)

logger = logging.getLogger(__name__)

HORIZONS: list[str] = [f"y_h{h}" for h in range(1, 7)]


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #

def _find_eligible_anchor(cube: pd.DataFrame, *, lag_months: int = 3) -> pd.Timestamp:
    """Latest month M in ``cube`` where M, M-1, ..., M-lag_months all appear.

    This is the globally-eligible anchor — individual (dim, level) or
    fingerprint groups may still fail their per-key lag check if the cube
    is sparse at that level. Such rows are skipped silently downstream.

    Raises:
        RuntimeError: no anchor found (cube too short for requested lags).
    """
    months_set = set(pd.to_datetime(cube["month"]).dt.as_unit("ns").unique())
    months_sorted_desc = sorted(months_set, reverse=True)
    for candidate in months_sorted_desc:
        needed = {month_shift(candidate, -k) for k in range(0, lag_months + 1)}
        if needed.issubset(months_set):
            return pd.Timestamp(candidate)
    raise RuntimeError(
        f"no anchor with {lag_months} contiguous prior months found in cube; "
        f"cube spans {min(months_sorted_desc)} → {max(months_sorted_desc)}"
    )


def _assert_fresh_anchor(anchor: pd.Timestamp, cube: pd.DataFrame) -> None:
    """Fail loud when ``anchor`` is older than the latest REAL (non-backfill)
    month in ``cube`` — i.e. the live scrape lacks 3 contiguous priors and
    predict would silently anchor on years-old history (the 2026-06 anchor=2020-08
    incident). The persistent synthetic priors (ADR 0002, unioned by aggregate)
    are the fix; this is the backstop if that artifact is missing/misloaded.
    Override with ``TRNDLY_ALLOW_STALE_ANCHOR=1``.
    """
    real = cube[cube["source"] != "backfill"] if "source" in cube.columns else cube
    latest_real = pd.to_datetime(real["month"]).max()
    if anchor < latest_real and not os.environ.get("TRNDLY_ALLOW_STALE_ANCHOR"):
        raise RuntimeError(
            f"predict: eligible anchor {anchor:%Y-%m} is older than the latest real "
            f"live month {latest_real:%Y-%m} — the live month lacks 3 contiguous "
            f"prior months, so predict would anchor on stale history. Generate the "
            f"synthetic anchor priors so aggregate can union them "
            f"(`python -m scripts.backfill_anchor_lags`), then re-run aggregate + "
            f"predict. Override with TRNDLY_ALLOW_STALE_ANCHOR=1 if intended."
        )


def _model_version(manifest_path) -> str:
    """Return the model version string from a model_training_run manifest.

    Falls back to ``"unknown"`` if the manifest is missing. The model version
    is informational metadata in the predictions parquet.
    """
    if not manifest_path.exists():
        return "unknown"
    try:
        with open(manifest_path) as f:
            meta = json.load(f)
        return str(meta.get("generated_at_utc") or "unknown")
    except Exception:
        return "unknown"


def _coerce_predict_frame(X: pd.DataFrame) -> pd.DataFrame:
    """Coerce numeric columns to float32 (matches train.py's split_xy)."""
    out = X.copy()
    for col in out.columns:
        if np.issubdtype(out[col].dtype, np.number):
            out[col] = out[col].astype(np.float32)
    return out


def _decode_lookup(lookup: pd.DataFrame) -> dict[tuple[str, int], str]:
    """Build a {(category, id): name} index from lookup.csv."""
    return {
        (str(row["category"]), int(row["id"])): str(row["name"])
        for _, row in lookup.iterrows()
    }


# --------------------------------------------------------------------------- #
# Univariate predictions                                                       #
# --------------------------------------------------------------------------- #

def _univariate_rows(
    *,
    cube: pd.DataFrame,
    anchor: pd.Timestamp,
    model: Any,
    lookup_index: dict[tuple[str, int], str],
    model_version: str,
    feature_contract_path,
) -> list[dict]:
    """Score every (dimension, level_id) at the anchor month."""
    pairs = (
        cube[cube["month"] == anchor][["dimension", "level_id"]]
        .drop_duplicates()
        .itertuples(index=False, name=None)
    )

    rows: list[dict] = []
    for dimension, level_id in pairs:
        feat = build_univariate_inference_row(
            cube, anchor_month=anchor, dimension=dimension, level_id=int(level_id),
            feature_contract_path=feature_contract_path,
        )
        if feat is None:
            continue  # insufficient lag history
        X = _coerce_predict_frame(feat.to_frame().T)
        y_hat = np.asarray(model.predict(X), dtype=float).ravel().tolist()
        if len(y_hat) != 6:
            raise RuntimeError(
                f"univariate model returned {len(y_hat)} horizons (expected 6)"
            )
        y_h0 = float(feat["share_t"])
        past_lags = [
            float(feat["share_lag3"]),
            float(feat["share_lag2"]),
            float(feat["share_lag1"]),
        ]
        state, stat = classify_state(y_h0, y_hat, past_lags=past_lags)
        level_name = lookup_index.get(
            (str(dimension), int(level_id)),
            f"{dimension}:{level_id}",
        )
        rows.append(
            {
                "anchor_month": anchor,
                "model_version": model_version,
                "dimension": str(dimension),
                "level_id": int(level_id),
                "level_name": level_name,
                **{h: y_hat[i] for i, h in enumerate(HORIZONS)},
                "state": state,
                "stat": stat,
            }
        )
    return rows


# --------------------------------------------------------------------------- #
# Fingerprint predictions                                                      #
# --------------------------------------------------------------------------- #

# Map fingerprint id-column → lookup.csv category (for name decoding).
_FP_NAME_CATEGORIES: dict[str, str] = {
    "product_type_id": "product_type",
    "gender_id": "gender",
    "color_master_id": "color_master",
    "graphical_appearance_id": "graphical_appearance",
    "material_id": "material",
}


def _fingerprint_rows(
    *,
    cube: pd.DataFrame,
    anchor: pd.Timestamp,
    model: Any,
    lookup_index: dict[tuple[str, int], str],
    model_version: str,
    feature_contract_path,
) -> list[dict]:
    """Score every distinct 5-D fingerprint at the anchor month."""
    # Empty dimensions = all fingerprints at the anchor month with lag coverage.
    X, keys = build_fingerprint_inference_rows(
        cube, anchor_month=anchor, dimensions={},
        feature_contract_path=feature_contract_path,
    )
    if X.empty:
        return []

    Y = np.asarray(model.predict(_coerce_predict_frame(X)), dtype=float)
    rows: list[dict] = []
    for i, key_tuple in enumerate(keys):
        if Y.shape[1] != 6:
            raise RuntimeError(
                f"fingerprint model returned {Y.shape[1]} horizons (expected 6)"
            )
        y_hat = Y[i, :].tolist()
        y_h0 = float(X.iloc[i]["share_t"])
        past_lags = [
            float(X.iloc[i]["share_lag3"]),
            float(X.iloc[i]["share_lag2"]),
            float(X.iloc[i]["share_lag1"]),
        ]
        state, stat = classify_state(y_h0, y_hat, past_lags=past_lags)

        ids: dict[str, int] = {col: int(v) for col, v in zip(FINGERPRINT_COLS, key_tuple)}
        names: dict[str, str] = {
            col.replace("_id", "_name"): lookup_index.get(
                (_FP_NAME_CATEGORIES[col], ids[col]),
                f"{_FP_NAME_CATEGORIES[col]}:{ids[col]}",
            )
            for col in FINGERPRINT_COLS
        }
        rows.append(
            {
                "anchor_month": anchor,
                "model_version": model_version,
                **ids,
                **names,
                **{h: y_hat[j] for j, h in enumerate(HORIZONS)},
                "state": state,
                "stat": stat,
            }
        )
    return rows


# --------------------------------------------------------------------------- #
# Stage driver                                                                 #
# --------------------------------------------------------------------------- #

def run_predict(month=None) -> dict[str, int]:
    """Score the universe with the champion; write the tick's predictions parquets.

    ``month`` defaults to the current tick month. The champion weights come from
    ``data/models/`` (promoted by ``evaluate``); the cube comes from this tick's
    ``merged_*`` checkpoint.

    Returns row counts.
    """
    month = resolve_tick_month(month)
    tick_dir(month).mkdir(parents=True, exist_ok=True)

    champ_fp = champion_joblib_for("fingerprint")
    champ_uni = champion_joblib_for("univariate")
    if not champ_fp.exists():
        raise FileNotFoundError(
            f"missing fingerprint champion joblib at {champ_fp} — run evaluate first."
        )
    if not champ_uni.exists():
        raise FileNotFoundError(
            f"missing univariate champion joblib at {champ_uni} — run evaluate first."
        )

    merged_fp_path = tick_merged_path(month, "fingerprint")
    merged_uni_path = tick_merged_path(month, "univariate")

    logger.info("predict: loading champion models")
    fp_model = joblib.load(champ_fp)
    uni_model = joblib.load(champ_uni)

    logger.info("predict: loading cubes + lookup")
    cube_fp = pd.read_parquet(merged_fp_path)
    cube_fp["month"] = pd.to_datetime(cube_fp["month"]).dt.as_unit("ns")
    cube_uni = pd.read_parquet(merged_uni_path)
    cube_uni["month"] = pd.to_datetime(cube_uni["month"]).dt.as_unit("ns")
    lookup = pd.read_csv(LOOKUP_CSV)
    lookup_index = _decode_lookup(lookup)

    # Pick the latest globally-eligible anchor per cube (latest month with
    # 3 contiguous prior months in the cube). Individual fingerprints / level
    # groups may still fail their own lag check and are silently skipped.
    anchor_fp = _find_eligible_anchor(cube_fp)
    anchor_uni = _find_eligible_anchor(cube_uni)

    # Backstop: never silently anchor on stale history (the 2026-06 incident).
    _assert_fresh_anchor(anchor_uni, cube_uni)
    if anchor_fp != anchor_uni:
        logger.warning(
            "predict: cube anchor mismatch (fingerprint=%s, univariate=%s); "
            "predictions will use each cube's own eligible anchor",
            anchor_fp, anchor_uni,
        )
    contract_path = tick_model_training_run_json(month)
    model_version = _model_version(contract_path)
    feature_contract_path = tick_training_run_json(month)

    # Univariate
    logger.info("predict: scoring univariate at anchor=%s", anchor_uni.isoformat())
    uv_rows = _univariate_rows(
        cube=cube_uni, anchor=anchor_uni, model=uni_model,
        lookup_index=lookup_index, model_version=model_version,
        feature_contract_path=feature_contract_path,
    )
    if not uv_rows:
        raise RuntimeError(
            "predict: no univariate predictions produced — check cube lag coverage."
        )
    df_uv = pd.DataFrame(uv_rows)
    df_uv = validate_predictions_univariate_frame(df_uv)
    out_uv = tick_predictions_path(month, "univariate")
    df_uv.to_parquet(out_uv, index=False)
    logger.info("predict: wrote %s | rows=%d", out_uv, len(df_uv))

    # Fingerprint
    logger.info("predict: scoring fingerprint at anchor=%s", anchor_fp.isoformat())
    fp_rows = _fingerprint_rows(
        cube=cube_fp, anchor=anchor_fp, model=fp_model,
        lookup_index=lookup_index, model_version=model_version,
        feature_contract_path=feature_contract_path,
    )
    if not fp_rows:
        raise RuntimeError(
            "predict: no fingerprint predictions produced — check cube lag coverage."
        )
    df_fp = pd.DataFrame(fp_rows)
    df_fp = validate_predictions_fingerprint_frame(df_fp)
    out_fp = tick_predictions_path(month, "fingerprint")
    df_fp.to_parquet(out_fp, index=False)
    logger.info("predict: wrote %s | rows=%d", out_fp, len(df_fp))

    return {"univariate": len(df_uv), "fingerprint": len(df_fp)}


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    summary = run_predict()
    logger.info("predict summary: %s", summary)


if __name__ == "__main__":
    main()
