"""
Live cube schema validators + shared scraper-side feature-type names.

This module used to host the listing-timeframe classifier's feature
contract; that classifier was retired. What remains is the schema/QA
plumbing the live retail pipeline still depends on:

  - ``validate_live_fingerprint_frame`` / ``validate_live_univariate_frame``
    enforce that ``build_live_cube.py`` produces frames byte-compatible
    with notebook 1's historical cubes.
  - ``FEATURE_TYPES`` is the canonical tuple of telemetry buckets each
    retail scraper reports against (color / category / material).
"""

from __future__ import annotations

from typing import Final

import pandas as pd

# --------------------------------------------------------------------------- #
# Scraper telemetry buckets                                                     #
# --------------------------------------------------------------------------- #

# Each retail scraper reports per-bucket coverage at the end of a run.
# The names are aliased forms of the cube dimensions
# (color_master, product_type, material), kept here for shared use.
FEATURE_TYPES: Final[tuple[str, ...]] = ("color", "category", "material")

# --------------------------------------------------------------------------- #
# Live cube schema validators                                                   #
# --------------------------------------------------------------------------- #

# Schema contracts mirror notebook 1's historical_fingerprint.parquet and
# historical_univariate.parquet exactly so a pd.concat([historical, live])
# preserves dtypes. See build_live_cube.py for the producer.

LIVE_FINGERPRINT_COLUMNS: list[str] = [
    "month", "month_of_year", "source",
    "product_type_id", "gender_id", "color_master_id",
    "graphical_appearance_id", "material_id",
    "n_articles", "share_articles", "avg_price",
]

LIVE_UNIVARIATE_COLUMNS: list[str] = [
    "month", "month_of_year", "source",
    "dimension", "level_id",
    "n_articles", "share_articles",
]

_SHARE_TOLERANCE = 1e-3  # per-month share-sum tolerance for fingerprint cube
_UNIVARIATE_SHARE_TOLERANCE = 1e-3  # per-(month, dimension) tolerance


def validate_live_fingerprint_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Assert the live fingerprint cube matches the historical schema.

    Checks: column presence + order, no nulls in required columns
    (avg_price NaN is allowed), share_articles per-month sums to 1.0
    within tolerance.
    """
    if frame.empty:
        raise ValueError("live fingerprint frame is empty")
    missing = set(LIVE_FINGERPRINT_COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError(f"live fingerprint frame missing columns: {sorted(missing)}")
    out = frame[LIVE_FINGERPRINT_COLUMNS].copy()

    required_non_null = [c for c in LIVE_FINGERPRINT_COLUMNS if c != "avg_price"]
    null_counts = out[required_non_null].isna().sum()
    bad = null_counts[null_counts > 0]
    if not bad.empty:
        raise ValueError(f"live fingerprint frame has nulls in: {bad.to_dict()}")

    sums = out.groupby("month", observed=True)["share_articles"].sum()
    out_of_range = sums[(sums < 1.0 - _SHARE_TOLERANCE) | (sums > 1.0 + _SHARE_TOLERANCE)]
    if not out_of_range.empty:
        raise ValueError(
            f"live fingerprint share_articles per-month sum out of range "
            f"(expected ≈1.0): {out_of_range.to_dict()}"
        )
    return out


def validate_live_univariate_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Assert the live univariate cube matches the historical schema.

    Checks: column presence + order, no nulls in required columns,
    share_articles per-(month, dimension) sums to 1.0 within tolerance.
    """
    if frame.empty:
        raise ValueError("live univariate frame is empty")
    missing = set(LIVE_UNIVARIATE_COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError(f"live univariate frame missing columns: {sorted(missing)}")
    out = frame[LIVE_UNIVARIATE_COLUMNS].copy()

    null_counts = out.isna().sum()
    bad = null_counts[null_counts > 0]
    if not bad.empty:
        raise ValueError(f"live univariate frame has nulls in: {bad.to_dict()}")

    sums = out.groupby(["month", "dimension"], observed=True)["share_articles"].sum()
    out_of_range = sums[
        (sums < 1.0 - _UNIVARIATE_SHARE_TOLERANCE)
        | (sums > 1.0 + _UNIVARIATE_SHARE_TOLERANCE)
    ]
    if not out_of_range.empty:
        raise ValueError(
            f"live univariate share_articles per-(month,dim) sum out of range "
            f"(expected ≈1.0): {out_of_range.to_dict()}"
        )
    return out


# --------------------------------------------------------------------------- #
# Predictions parquet schema validators                                        #
# --------------------------------------------------------------------------- #

# Written by ``pipelines/monthly/predict.py``; read by the FastAPI service.

VALID_TREND_STATES: Final[frozenset[str]] = frozenset(
    {"rising", "peak", "flat", "falling"}
)

PREDICTIONS_UNIVARIATE_COLUMNS: list[str] = [
    "anchor_month", "model_version",
    "dimension", "level_id", "level_name",
    "y_h1", "y_h2", "y_h3", "y_h4", "y_h5", "y_h6",
    "state", "stat",
]

PREDICTIONS_FINGERPRINT_COLUMNS: list[str] = [
    "anchor_month", "model_version",
    "product_type_id", "gender_id", "color_master_id",
    "graphical_appearance_id", "material_id",
    "product_type_name", "gender_name", "color_master_name",
    "graphical_appearance_name", "material_name",
    "y_h1", "y_h2", "y_h3", "y_h4", "y_h5", "y_h6",
    "state", "stat",
]


def _validate_predictions_common(
    frame: pd.DataFrame, expected_columns: list[str], label: str
) -> pd.DataFrame:
    """Shared checks for both predictions tables: column presence + order,
    no nulls in any column, state ∈ VALID_TREND_STATES, stat is non-empty."""
    if frame.empty:
        raise ValueError(f"{label} predictions frame is empty")
    missing = set(expected_columns) - set(frame.columns)
    if missing:
        raise ValueError(f"{label} predictions frame missing columns: {sorted(missing)}")
    out = frame[expected_columns].copy()

    null_counts = out.isna().sum()
    bad = null_counts[null_counts > 0]
    if not bad.empty:
        raise ValueError(f"{label} predictions frame has nulls in: {bad.to_dict()}")

    bad_states = set(out["state"].astype(str).unique()) - VALID_TREND_STATES
    if bad_states:
        raise ValueError(
            f"{label} predictions frame has unknown state values: {sorted(bad_states)}; "
            f"expected subset of {sorted(VALID_TREND_STATES)}"
        )
    if (out["stat"].astype(str).str.len() == 0).any():
        raise ValueError(f"{label} predictions frame has empty stat strings")

    return out


def validate_predictions_univariate_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Assert the univariate predictions parquet matches its schema."""
    return _validate_predictions_common(
        frame, PREDICTIONS_UNIVARIATE_COLUMNS, label="univariate"
    )


def validate_predictions_fingerprint_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Assert the fingerprint predictions parquet matches its schema."""
    return _validate_predictions_common(
        frame, PREDICTIONS_FINGERPRINT_COLUMNS, label="fingerprint"
    )
