"""End-to-end smoke for ``pipelines.monthly.predict``.

Checks:
  - latest predictions parquet exists for both models
  - schema validates via ``pipelines.contracts``
  - state column is from the documented vocabulary
  - row count is positive
"""
from __future__ import annotations

import pandas as pd
import pytest

from pipelines.contracts import (
    VALID_TREND_STATES,
    validate_predictions_fingerprint_frame,
    validate_predictions_univariate_frame,
)
from pipelines.paths import (
    latest_predictions_fingerprint_parquet,
    latest_predictions_univariate_parquet,
)


def _require_predictions(parquet_finder, label: str) -> pd.DataFrame:
    p = parquet_finder()
    if p is None or not p.exists():
        pytest.skip(
            f"no {label} predictions parquet on disk; "
            f"run `python -m pipelines.monthly.predict` first."
        )
    return pd.read_parquet(p)


def test_predictions_univariate_validates():
    df = _require_predictions(latest_predictions_univariate_parquet, "univariate")
    assert len(df) > 0, "univariate predictions parquet is empty"
    validate_predictions_univariate_frame(df)


def test_predictions_fingerprint_validates():
    df = _require_predictions(latest_predictions_fingerprint_parquet, "fingerprint")
    assert len(df) > 0, "fingerprint predictions parquet is empty"
    validate_predictions_fingerprint_frame(df)


def test_predictions_univariate_state_vocabulary():
    df = _require_predictions(latest_predictions_univariate_parquet, "univariate")
    assert set(df["state"].unique()).issubset(VALID_TREND_STATES)


def test_predictions_fingerprint_state_vocabulary():
    df = _require_predictions(latest_predictions_fingerprint_parquet, "fingerprint")
    assert set(df["state"].unique()).issubset(VALID_TREND_STATES)


def test_predictions_univariate_covers_lookup_dimensions():
    """Every dimension in the predictions parquet should be one we recognize."""
    df = _require_predictions(latest_predictions_univariate_parquet, "univariate")
    dims = set(df["dimension"].unique())
    expected = {
        "product_type", "product_group", "graphical_appearance",
        "color_master", "color_spectrum", "gender", "material",
    }
    assert dims.issubset(expected), f"unexpected dimensions: {dims - expected}"


def test_predictions_fingerprint_id_columns_are_ints():
    df = _require_predictions(latest_predictions_fingerprint_parquet, "fingerprint")
    id_cols = [
        "product_type_id", "gender_id", "color_master_id",
        "graphical_appearance_id", "material_id",
    ]
    for col in id_cols:
        # any int dtype acceptable
        assert pd.api.types.is_integer_dtype(df[col]), f"{col} is not integer dtype"
