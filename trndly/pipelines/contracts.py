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
