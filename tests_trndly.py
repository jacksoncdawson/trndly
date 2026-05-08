import pytest
import pandas as pd
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "trndly"))

from pipelines.training.feature_contract import (
    DEFAULT_MISSING_SCORE,
    FEATURE_VECTOR_COLUMNS,
    TIMEFRAMES,
    compute_feature_scores,
    normalize_token,
    prepare_training_frame,
)


def _minimal_lookup() -> dict[str, dict[str, dict[str, float]]]:
    """A small valid nested trend lookup for reuse across tests. Mirrors the
    shape returned by load_trend_lookup_from_univariate (each leaf is a
    {timeframe: score} dict; only `current` carries real data)."""
    def _entry(score: float) -> dict[str, float]:
        return {tf: (score if tf == "current" else DEFAULT_MISSING_SCORE) for tf in TIMEFRAMES}
    return {
        "color":    {"red": _entry(0.8)},
        "category": {"tops": _entry(0.6)},
        "material": {"cotton": _entry(0.4)},
    }


# 1. normalize_token strips whitespace and lowercases
def test_normalize_token():
    assert normalize_token("  RED  ") == "red"
    assert normalize_token("Cotton") == "cotton"
    assert normalize_token(42) == "42"


# 5. compute_feature_scores always returns all four expected keys with values in [0, 1]
def test_compute_feature_scores_output_shape_and_range():
    lookup = _minimal_lookup()
    item = {"color": "red", "category": "tops", "material": "cotton"}
    scores = compute_feature_scores(item=item, lookup=lookup)

    # compute_feature_scores returns per-(feature_type, timeframe) keys plus
    # *_current shorthand and avg_current; not every FEATURE_VECTOR_COLUMNS key.
    expected_keys = {
        f"{ft}_{tf}" for ft in ("color", "category", "material") for tf in TIMEFRAMES
    } | {
        f"{ft}_current" for ft in ("color", "category", "material")
    } | {"avg_current"}
    assert set(scores.keys()) == expected_keys
    for key, value in scores.items():
        assert 0.0 <= value <= 1.0, f"{key} = {value} is out of range"


# 6. prepare_training_frame backfills missing columns with DEFAULT_MISSING_SCORE
def test_prepare_training_frame_backfills():
    bare = pd.DataFrame([{"color_current": 0.5}])
    out = prepare_training_frame(bare)
    for col in FEATURE_VECTOR_COLUMNS:
        assert col in out.columns
    # Missing columns get the default
    assert (out.loc[0, "category_current"] == DEFAULT_MISSING_SCORE)
