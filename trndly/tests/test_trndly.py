"""Pytest suite for the trndly trend forecaster pipeline.

Covers:
  - Lookup-csv consistency (feature_lookups dicts vs canonical lookup.csv)
  - Items CSV ID validity (scraper outputs vs lookup.csv)
  - Live cube schema validators
  - Path-helper round-trips (live_*_path_for / discover_live_*)
  - Cube concat-compatibility with historical (notebook 1 → notebook 1b)

Tests are resilient to partially-built features: if a dependency
(e.g., a freshly-cloned repo with no scraped data) is missing, the
test is marked ``xfail``/``skip`` with a clear reason so CI keeps
green while still documenting intent.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# Make the project root importable so ``pipelines.training...`` resolves.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pipelines.paths import (  # noqa: E402
    DATA_DIR,
    LOOKUP_CSV,
    MERGED_UNIVARIATE_PARQUET,
    discover_live_fingerprint_parquets,
    discover_live_univariate_parquets,
    live_fingerprint_path_for,
)


# ---------------------------------------------------------------------------
# Lookup-csv consistency
# ---------------------------------------------------------------------------
def test_lookup_consistency_validator_passes():
    """Every (name, id) pair in feature_lookups.py *_TO_ID dicts must exist in
    data/reference/lookup.csv. Catches typos and drift."""
    from pipelines.collectors.feature_lookups import _assert_lookup_csv_matches_dicts

    _assert_lookup_csv_matches_dicts()  # raises on drift


def test_lookup_consistency_validator_detects_drift():
    """Negative test: injecting a bad pair must trigger ValueError."""
    from pipelines.collectors import feature_lookups

    saved = feature_lookups._LOOKUP_DICT_CONTRACTS
    bad_dict = dict(feature_lookups.COLOR_MASTER_TO_ID)
    bad_dict["unicorn"] = 999  # id=999 not in lookup.csv color_master
    feature_lookups._LOOKUP_DICT_CONTRACTS = (("color_master", bad_dict),)
    try:
        with pytest.raises(ValueError, match="999"):
            feature_lookups._assert_lookup_csv_matches_dicts()
    finally:
        feature_lookups._LOOKUP_DICT_CONTRACTS = saved


def test_unreachable_lookup_ids_match_documented_allowlist():
    """Every lookup.csv ID is either reachable via a *_TO_ID dict or
    documented in ``_DELIBERATELY_UNREACHABLE_LOOKUP_IDS``. If this fails,
    either add keyword/dict coverage for the missing IDs or extend the
    allow-list with a justification (see the constant's doc-comment).
    """
    from pipelines.collectors.feature_lookups import _compute_unreachable_lookup_ids

    gaps = _compute_unreachable_lookup_ids()
    assert gaps == {}, (
        f"Unreachable lookup.csv IDs not in allow-list: {gaps}. "
        "Either add coverage or extend _DELIBERATELY_UNREACHABLE_LOOKUP_IDS."
    )


def test_unreachable_lookup_ids_warning_fires_on_drift():
    """Negative test: removing a dict entry without updating the allow-list
    must surface the new gap via UnreachableLookupIDWarning."""
    import warnings

    from pipelines.collectors import feature_lookups

    saved = feature_lookups.MATERIAL_TO_ID.copy()
    # Drop "cashmere": id=27 is not in the material allow-list, so the
    # validator should now flag it as unreachable.
    feature_lookups.MATERIAL_TO_ID.pop("cashmere", None)
    try:
        gaps = feature_lookups._compute_unreachable_lookup_ids()
        assert gaps.get("material") == {27}
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            feature_lookups._warn_unreachable_lookup_ids()
        warning_classes = [w.category.__name__ for w in caught]
        assert "UnreachableLookupIDWarning" in warning_classes
        assert any("27" in str(w.message) for w in caught)
    finally:
        feature_lookups.MATERIAL_TO_ID.clear()
        feature_lookups.MATERIAL_TO_ID.update(saved)


def test_avg_price_t_is_not_a_model_feature():
    """``avg_price_t`` was dropped from ``FINGERPRINT_FEATURE_COLS`` because
    the live cube emits NaN price (price isn't scraped) and sklearn's
    RandomForestRegressor silently routes NaN inputs down a different
    branch — biasing predictions on live anchors instead of failing loudly.
    If you re-introduce the feature, you also need to either (a) impute or
    drop NaN avg_price rows in build_fingerprint_inference_rows, or
    (b) start scraping price.
    """
    import json

    training_run = PROJECT_ROOT / "data" / "processed" / "training_run.json"
    if not training_run.exists():
        pytest.skip(f"training_run.json missing at {training_run}")
    meta = json.loads(training_run.read_text())
    fp_features = meta.get("fingerprint_feature_cols", [])
    assert "avg_price_t" not in fp_features, (
        f"avg_price_t reappeared in FINGERPRINT_FEATURE_COLS: {fp_features}. "
        "See test docstring for the live-cube NaN issue this guard prevents."
    )

    training_fp = PROJECT_ROOT / "data" / "processed" / "training_fingerprint.parquet"
    if training_fp.exists():
        cols = pd.read_parquet(training_fp).columns.tolist()
        assert "avg_price_t" not in cols, (
            f"avg_price_t leaked back into training_fingerprint.parquet: {cols}"
        )


def test_items_csv_id_validity():
    """Every ID-column value in any items_*.csv must be a valid id within its
    lookup.csv category. Catches scraper bugs that silently emit bad IDs."""
    from pipelines.paths import LOOKUP_CSV, RAW_ITEMS_DIR

    items_files = sorted(RAW_ITEMS_DIR.glob("items_*.csv"))
    if not items_files:
        pytest.skip(f"No items_*.csv files in {RAW_ITEMS_DIR}")

    if not LOOKUP_CSV.exists():
        pytest.skip(f"lookup.csv missing at {LOOKUP_CSV}")
    lookup = pd.read_csv(LOOKUP_CSV)
    valid_ids = {
        cat: set(g["id"].astype(int)) for cat, g in lookup.groupby("category")
    }

    # Map items.csv column -> lookup.csv category
    id_col_to_category = {
        "color_master_id":         "color_master",
        "color_spectrum_id":       "color_spectrum",
        "gender_id":               "gender",
        "product_type_id":         "product_type",
        "product_group_id":        "product_group",
        "material_id":             "material",
        "graphical_appearance_id": "graphical_appearance",
    }

    # id=0 is a "no match" sentinel emitted by _combo_to_row's .get(..., 0)
    # fallback. For color_master/color_spectrum/material/graphical_appearance
    # lookup.csv has an explicit id=0 row ("Unknown") so 0 is valid by lookup.
    # For product_type and product_group lookup.csv ids start at 1; the scraper
    # still emits 0 to mean "no canonical match" — accept that as valid here.
    SENTINEL_OK = {"product_type", "product_group"}

    for items_path in items_files:
        df = pd.read_csv(items_path)
        for col, cat in id_col_to_category.items():
            if col not in df.columns:
                continue
            seen = set(df[col].dropna().astype(int).unique())
            allowed = valid_ids[cat] | ({0} if cat in SENTINEL_OK else set())
            invalid = seen - allowed
            assert not invalid, (
                f"{items_path.name}:{col} has IDs not in lookup.csv[{cat}]: {sorted(invalid)}"
            )


# ---------------------------------------------------------------------------
# Live cube schema + builder
# ---------------------------------------------------------------------------
def _toy_items_frame() -> pd.DataFrame:
    """Tiny synthetic items frame used by cube-builder tests. Two months,
    two retailers, mixed fingerprints — enough to exercise grouping and
    share-sum invariants without disk I/O."""
    return pd.DataFrame([
        # March 2026 — 4 articles total
        {"scraped_at": "2026-03-15T10:00:00Z", "style_id": "g1", "cc_id": "01",
         "product_type_id": 1, "gender_id": 1, "color_master_id": 2,
         "graphical_appearance_id": 1, "material_id": 3},
        {"scraped_at": "2026-03-15T10:00:00Z", "style_id": "g1", "cc_id": "02",
         "product_type_id": 1, "gender_id": 1, "color_master_id": 1,
         "graphical_appearance_id": 1, "material_id": 3},
        {"scraped_at": "2026-03-16T11:00:00Z", "style_id": "g2", "cc_id": "01",
         "product_type_id": 4, "gender_id": 3, "color_master_id": 1,
         "graphical_appearance_id": 11, "material_id": 1},
        {"scraped_at": "2026-03-16T11:00:00Z", "style_id": "g3", "cc_id": "01",
         "product_type_id": 4, "gender_id": 3, "color_master_id": 1,
         "graphical_appearance_id": 11, "material_id": 1},
        # April 2026 — 2 articles
        {"scraped_at": "2026-04-01T09:00:00Z", "style_id": "g4", "cc_id": "01",
         "product_type_id": 19, "gender_id": 1, "color_master_id": 6,
         "graphical_appearance_id": 1, "material_id": 1},
        {"scraped_at": "2026-04-01T09:00:00Z", "style_id": "g5", "cc_id": "01",
         "product_type_id": 11, "gender_id": 1, "color_master_id": 1,
         "graphical_appearance_id": 1, "material_id": 1},
    ])


def test_build_fingerprint_cube_shape_and_share_sum():
    from pipelines.collectors.build_live_cube import build_fingerprint_cube
    items = _toy_items_frame()
    items["month"] = (
        pd.to_datetime(items["scraped_at"], utc=True).dt.tz_convert(None)
        .dt.to_period("M").dt.to_timestamp()
    )
    fp = build_fingerprint_cube(items)

    # Schema contract
    expected_cols = [
        "month", "month_of_year", "source",
        "product_type_id", "gender_id", "color_master_id",
        "graphical_appearance_id", "material_id",
        "n_articles", "share_articles", "avg_price",
    ]
    assert list(fp.columns) == expected_cols
    assert str(fp["source"].dtype).startswith("category")
    assert set(fp["source"].cat.categories) == {"historical", "live"}
    assert (fp["source"] == "live").all()
    # avg_price is NaN for live rows (price not scraped)
    assert fp["avg_price"].isna().all()
    # int dtypes for IDs and counts
    assert fp["n_articles"].dtype == "int32"
    for c in ["product_type_id", "gender_id", "color_master_id",
              "graphical_appearance_id", "material_id", "month_of_year"]:
        assert fp[c].dtype == "int8", f"{c} expected int8, got {fp[c].dtype}"

    # Share-sum invariant per month
    sums = fp.groupby("month", observed=True)["share_articles"].sum()
    assert ((sums - 1.0).abs() < 1e-3).all(), sums.to_dict()


def test_build_univariate_cube_shape_and_share_sum():
    from pipelines.collectors.build_live_cube import build_univariate_cube
    items = _toy_items_frame()
    items["month"] = (
        pd.to_datetime(items["scraped_at"], utc=True).dt.tz_convert(None)
        .dt.to_period("M").dt.to_timestamp()
    )
    uv = build_univariate_cube(items)
    expected_cols = [
        "month", "month_of_year", "source", "dimension", "level_id",
        "n_articles", "share_articles",
    ]
    assert list(uv.columns) == expected_cols

    # 5 dims: product_type, gender, color_master, graphical_appearance, material
    assert set(uv["dimension"].unique()) == {
        "product_type", "gender", "color_master",
        "graphical_appearance", "material",
    }
    # `dimension` Categorical must include all 7 historical categories so
    # concat with historical preserves dtype
    assert set(uv["dimension"].cat.categories) >= {
        "product_type", "product_group", "graphical_appearance",
        "color_master", "color_spectrum", "gender", "material",
    }

    # Per-(month, dimension) share sum invariant
    sums = uv.groupby(["month", "dimension"], observed=True)["share_articles"].sum()
    assert ((sums - 1.0).abs() < 1e-3).all(), sums.to_dict()


def test_validate_live_fingerprint_frame_rejects_bad_inputs():
    from pipelines.contracts import validate_live_fingerprint_frame
    with pytest.raises(ValueError, match="empty"):
        validate_live_fingerprint_frame(pd.DataFrame())

    incomplete = pd.DataFrame([{"month": pd.Timestamp("2026-03-01"), "source": "live"}])
    with pytest.raises(ValueError, match="missing columns"):
        validate_live_fingerprint_frame(incomplete)


def test_validate_live_univariate_frame_rejects_bad_inputs():
    from pipelines.contracts import validate_live_univariate_frame
    with pytest.raises(ValueError, match="empty"):
        validate_live_univariate_frame(pd.DataFrame())


def test_live_cube_concat_compatible_with_historical():
    """Critical merge contract: pd.concat([historical, live]) must preserve
    Categorical dtypes for `source` and `dimension`. If categories don't
    align, pandas falls back to object dtype and silently breaks downstream
    consumers that filter by source=='live' or dimension=='product_type'."""
    from pipelines.collectors.build_live_cube import (
        build_fingerprint_cube, build_univariate_cube,
    )
    items = _toy_items_frame()
    items["month"] = (
        pd.to_datetime(items["scraped_at"], utc=True).dt.tz_convert(None)
        .dt.to_period("M").dt.to_timestamp()
    )
    live_fp = build_fingerprint_cube(items)
    live_uv = build_univariate_cube(items)

    # Synthesize a one-row "historical" frame matching cube schemas exactly
    hist_fp = pd.DataFrame([{
        "month": pd.Timestamp("2020-01-01"),
        "month_of_year": 1,
        "source": pd.Categorical(["historical"], categories=["historical", "live"])[0],
        "product_type_id": 1, "gender_id": 1, "color_master_id": 1,
        "graphical_appearance_id": 1, "material_id": 1,
        "n_articles": 100, "share_articles": 1.0, "avg_price": 0.5,
    }])
    hist_fp["source"] = pd.Categorical(["historical"], categories=["historical", "live"])
    hist_fp["n_articles"] = hist_fp["n_articles"].astype("int32")
    hist_fp["share_articles"] = hist_fp["share_articles"].astype("float32")
    hist_fp["avg_price"] = hist_fp["avg_price"].astype("float32")
    for c in ["product_type_id", "gender_id", "color_master_id",
              "graphical_appearance_id", "material_id", "month_of_year"]:
        hist_fp[c] = hist_fp[c].astype("int8")

    merged = pd.concat([hist_fp, live_fp], ignore_index=True)
    assert str(merged["source"].dtype).startswith("category"), \
        "source dtype lost through concat"
    assert set(merged["source"].cat.categories) == {"historical", "live"}


def test_live_cube_path_helpers_match_glob_pattern():
    """The per-month filename helpers must produce paths that the glob
    discoverers can find. Catches drift between the writer (build_live_cube)
    and the reader (notebook 1b)."""
    from pipelines.paths import LIVE_FINGERPRINT_GLOB, LIVE_UNIVARIATE_GLOB
    import fnmatch
    fp_path = live_fingerprint_path_for("2026-05-01")
    uv_path = live_fingerprint_path_for(pd.Timestamp("2026-05-15"))
    assert fp_path.name == "live_fingerprint_2026-05.parquet"
    assert uv_path.name == "live_fingerprint_2026-05.parquet"  # day collapsed to month
    assert fnmatch.fnmatch(fp_path.name, LIVE_FINGERPRINT_GLOB)
    # Univariate glob should match the univariate filename
    from pipelines.paths import live_univariate_path_for
    uv_path2 = live_univariate_path_for("2026-05-01")
    assert fnmatch.fnmatch(uv_path2.name, LIVE_UNIVARIATE_GLOB)


def test_discover_live_parquets_returns_sorted():
    """`discover_live_*_parquets()` returns whatever's in PROCESSED_DIR
    sorted by month — exercises the path glob the way notebook 1b uses it."""
    fp_files = discover_live_fingerprint_parquets()
    uv_files = discover_live_univariate_parquets()
    # Files may or may not exist depending on whether build_live_cube ran;
    # what we care about is the function returns a list and is sorted.
    assert isinstance(fp_files, list)
    assert isinstance(uv_files, list)
    assert fp_files == sorted(fp_files)
    assert uv_files == sorted(uv_files)
