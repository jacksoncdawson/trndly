"""Pytest suite implementing 9 tests from trndly/tests/test_ideas.md.

Coverage:
    Model inference        -> tests 1, 2, 3
    Feature engineering    -> tests 4, 5
    Data collection        -> tests 6, 7
    Data cleaning          -> tests 8, 9

Tests are resilient to partially-built features: if a dependency (e.g., a live
scraper, a trained MLflow model) is not yet implemented, the test is marked
``xfail``/``skip`` with a clear reason so CI keeps green while still documenting
intent.
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

from pipelines.training.feature_contract import (  # noqa: E402
    DEFAULT_MISSING_SCORE,
    FEATURE_VECTOR_COLUMNS,
    TIMEFRAMES,
    build_feature_frame,
    load_seasonality_table,
    load_trend_lookup_from_univariate,
    normalize_token,
    prepare_training_frame,
)
from pipelines.training.paths import (  # noqa: E402
    DATA_DIR,
    LOOKUP_CSV,
    MERGED_UNIVARIATE_PARQUET,
    SEASONALITY_TABLE_CSV,
    TRAIN_CSV,
    discover_live_fingerprint_parquets,
    discover_live_univariate_parquets,
    live_fingerprint_path_for,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def trend_lookup():
    if not MERGED_UNIVARIATE_PARQUET.exists():
        pytest.skip(f"Merged univariate parquet missing at {MERGED_UNIVARIATE_PARQUET}")
    if not LOOKUP_CSV.exists():
        pytest.skip(f"lookup.csv missing at {LOOKUP_CSV}")
    return load_trend_lookup_from_univariate(
        MERGED_UNIVARIATE_PARQUET, source="live", latest_month=True
    )


@pytest.fixture(scope="module")
def seasonality_table():
    if not SEASONALITY_TABLE_CSV.exists():
        pytest.skip(f"Seasonality CSV missing at {SEASONALITY_TABLE_CSV}")
    return load_seasonality_table(SEASONALITY_TABLE_CSV)


@pytest.fixture(scope="module")
def tiny_model(trend_lookup):
    """Train a small RF on the synthetic train split as a stand-in for the
    registered MLflow champion model. Keeps tests hermetic (no network)."""
    if not TRAIN_CSV.exists():
        pytest.skip(f"Training CSV missing at {TRAIN_CSV}")

    from sklearn.ensemble import RandomForestClassifier

    frame = pd.read_csv(TRAIN_CSV)
    prepared = prepare_training_frame(frame)
    x = prepared[FEATURE_VECTOR_COLUMNS]
    y = prepared["best_timeframe"]

    model = RandomForestClassifier(n_estimators=25, random_state=0)
    model.fit(x, y)
    return model


@pytest.fixture()
def sample_item():
    return {
        "item_name": "Linen Summer Dress",
        "color": "white",
        "category": "dress",
        "material": "linen",
    }


# ---------------------------------------------------------------------------
# 1. Model inference
# ---------------------------------------------------------------------------
def test_model_can_make_inference(tiny_model, trend_lookup, seasonality_table, sample_item):
    """Test 1: the listing timeline model produces a prediction without error."""
    frame = build_feature_frame([sample_item], trend_lookup, seasonality_table=seasonality_table)
    predictions = tiny_model.predict(frame)

    assert len(predictions) == 1
    assert predictions[0] is not None


def test_inference_is_in_expected_range(tiny_model, trend_lookup, seasonality_table, sample_item):
    """Test 2: predicted label is one of the documented timeframes."""
    frame = build_feature_frame([sample_item], trend_lookup, seasonality_table=seasonality_table)
    prediction = str(tiny_model.predict(frame)[0])

    assert prediction in TIMEFRAMES, (
        f"Model produced '{prediction}', which is outside the allowed set {TIMEFRAMES}."
    )


@pytest.mark.xfail(
    reason=(
        "Trend-monotonicity regression check is not yet implemented; the "
        "comparison utility `detect_downtrend` will live in pipelines/monitoring."
    ),
    strict=False,
)
def test_inference_trend_is_not_going_down(tiny_model, trend_lookup, seasonality_table):
    """Test 3: when we re-score yesterday's batch against today's model, the
    average alignment score for the predicted timeframe should not drop."""
    from pipelines.monitoring import detect_downtrend  # type: ignore[import-not-found]

    items = [
        {"item_name": "Item A", "color": "black", "category": "tops", "material": "cotton"},
        {"item_name": "Item B", "color": "red",   "category": "dress", "material": "silk"},
    ]
    frame = build_feature_frame(items, trend_lookup, seasonality_table=seasonality_table)
    preds_today = tiny_model.predict(frame)

    # Placeholder "yesterday" scores - in real use these come from the monitoring store.
    yesterday_scores = np.full(len(items), 0.5)
    today_scores = frame["avg_current"].to_numpy()

    assert not detect_downtrend(yesterday_scores, today_scores, preds_today)


# ---------------------------------------------------------------------------
# 2. Feature engineering
# ---------------------------------------------------------------------------
def test_feature_creation_pipeline_produces_contract_columns(trend_lookup, seasonality_table, sample_item):
    """Test 4: `build_feature_frame` emits the full feature vector contract."""
    frame = build_feature_frame([sample_item], trend_lookup, seasonality_table=seasonality_table)

    assert list(frame.columns) == FEATURE_VECTOR_COLUMNS
    assert len(frame) == 1
    assert frame.notna().all().all(), "Feature frame should never contain NaNs."

    bounded = [
        "color_current",
        "category_current",
        "material_current",
        "avg_current",
        "season_plus_0",
        "season_plus_1",
        "season_plus_2",
        "season_plus_3",
        "season_plus_6",
    ]
    assert ((frame[bounded] >= 0.0) & (frame[bounded] <= 1.0)).all().all(), (
        "Trend + seasonal envelope features must stay inside [0, 1]."
    )
    assert frame[["sin_month", "cos_month"]].abs().max().max() <= 1.0 + 1e-9
    assert (frame[["months_until_peak", "months_since_peak"]] >= 0).all().all()
    assert (frame[["months_until_peak", "months_since_peak"]] <= 11).all().all()


def test_nulls_are_handled_safely_during_training_prep():
    """Test 5: `prepare_training_frame` replaces NaN feature values with the
    documented default instead of raising or propagating nulls."""
    base = {col: 0.5 for col in FEATURE_VECTOR_COLUMNS}
    base["best_timeframe"] = "next_week"

    rows = [base.copy(), base.copy()]
    rows[0][FEATURE_VECTOR_COLUMNS[0]] = np.nan
    rows[1][FEATURE_VECTOR_COLUMNS[-1]] = None

    prepared = prepare_training_frame(pd.DataFrame(rows))

    assert prepared[FEATURE_VECTOR_COLUMNS].notna().all().all()
    assert prepared[FEATURE_VECTOR_COLUMNS[0]].iloc[0] == pytest.approx(DEFAULT_MISSING_SCORE)
    assert prepared[FEATURE_VECTOR_COLUMNS[-1]].iloc[1] == pytest.approx(DEFAULT_MISSING_SCORE)


# ---------------------------------------------------------------------------
# 3. Data collection
# ---------------------------------------------------------------------------
@pytest.mark.xfail(
    reason=(
        "Live scraper module (scripts/scrapers/run_scrapers.py) has not been "
        "written yet; this test will start passing once the scraper exposes "
        "`fetch_latest()` and returns a non-empty DataFrame."
    ),
    strict=False,
)
def test_scrapers_are_running_and_returning_data():
    """Test 6: scrapers run end-to-end without being blocked and return rows."""
    from scripts.scrapers.run_scrapers import fetch_latest  # type: ignore[import-not-found]

    result = fetch_latest(limit=5)
    assert isinstance(result, pd.DataFrame)
    assert not result.empty
    assert {"source", "fetched_at"}.issubset(result.columns)


def test_historical_data_is_in_right_form_for_training():
    """Test 7: the checked-in historical/synthetic training CSV still matches
    the feature contract and contains every supported timeframe label."""
    if not TRAIN_CSV.exists():
        pytest.skip(f"Historical training CSV missing at {TRAIN_CSV}")

    frame = pd.read_csv(TRAIN_CSV)

    missing = [col for col in FEATURE_VECTOR_COLUMNS if col not in frame.columns]
    assert not missing, f"Historical data missing feature columns: {missing}"
    assert "best_timeframe" in frame.columns
    assert set(frame["best_timeframe"]).issubset(set(TIMEFRAMES))
    assert len(frame) > 0


# ---------------------------------------------------------------------------
# 4. Data cleaning
# ---------------------------------------------------------------------------
def test_normalize_token_trims_and_lowercases():
    """Test 8: `normalize_token` standardizes user-supplied strings."""
    assert normalize_token("  Navy  ") == "navy"
    assert normalize_token("COTTON") == "cotton"
    assert normalize_token(None) == "none"  # stringified, then lowercased
    assert normalize_token(42) == "42"


# ---------------------------------------------------------------------------
# Lookup-csv consistency
# ---------------------------------------------------------------------------
def test_lookup_consistency_validator_passes():
    """Every (name, id) pair in feature_lookups.py *_TO_ID dicts must exist in
    data/processed/lookup.csv. Catches typos and drift."""
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


def test_items_csv_id_validity():
    """Every ID-column value in any items_*.csv must be a valid id within its
    lookup.csv category. Catches scraper bugs that silently emit bad IDs."""
    items_dir = PROJECT_ROOT / "pipelines" / "training" / "synthetic_data"
    items_files = sorted(items_dir.glob("items_*.csv"))
    if not items_files:
        pytest.skip(f"No items_*.csv files in {items_dir}")

    lookup_path = PROJECT_ROOT / "data" / "processed" / "lookup.csv"
    if not lookup_path.exists():
        pytest.skip(f"lookup.csv missing at {lookup_path}")
    lookup = pd.read_csv(lookup_path)
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
    from pipelines.training.feature_contract import validate_live_fingerprint_frame
    with pytest.raises(ValueError, match="empty"):
        validate_live_fingerprint_frame(pd.DataFrame())

    incomplete = pd.DataFrame([{"month": pd.Timestamp("2026-03-01"), "source": "live"}])
    with pytest.raises(ValueError, match="missing columns"):
        validate_live_fingerprint_frame(incomplete)


def test_validate_live_univariate_frame_rejects_bad_inputs():
    from pipelines.training.feature_contract import validate_live_univariate_frame
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
    from pipelines.training.paths import LIVE_FINGERPRINT_GLOB, LIVE_UNIVARIATE_GLOB
    import fnmatch
    fp_path = live_fingerprint_path_for("2026-05-01")
    uv_path = live_fingerprint_path_for(pd.Timestamp("2026-05-15"))
    assert fp_path.name == "live_fingerprint_2026-05.parquet"
    assert uv_path.name == "live_fingerprint_2026-05.parquet"  # day collapsed to month
    assert fnmatch.fnmatch(fp_path.name, LIVE_FINGERPRINT_GLOB)
    # Univariate glob should match the univariate filename
    from pipelines.training.paths import live_univariate_path_for
    uv_path2 = live_univariate_path_for("2026-05-01")
    assert fnmatch.fnmatch(uv_path2.name, LIVE_UNIVARIATE_GLOB)


def test_discover_live_parquets_returns_sorted():
    """`discover_live_*_parquets()` returns whatever's in PROCESSED_DATA_DIR
    sorted by month — exercises the path glob the way notebook 1b uses it."""
    fp_files = discover_live_fingerprint_parquets()
    uv_files = discover_live_univariate_parquets()
    # Files may or may not exist depending on whether build_live_cube ran;
    # what we care about is the function returns a list and is sorted.
    assert isinstance(fp_files, list)
    assert isinstance(uv_files, list)
    assert fp_files == sorted(fp_files)
    assert uv_files == sorted(uv_files)
