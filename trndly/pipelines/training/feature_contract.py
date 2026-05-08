"""
Training + inference contract for the listing timeframe classifier.

Consumes the merged univariate cube (``data/processed/merged_univariate.parquet``,
the always-rebuilt historical+live concat from notebook 1b), joins optional
historical seasonality curves from ``seasonality_table.csv``, and emits the
fixed-width numeric vector expected by ``RandomForestClassifier`` models
logged via MLflow.

Public lookup helper: ``load_trend_lookup_from_univariate``. Returns a nested
``{feature_type: {feature_value: {timeframe: score}}}`` dict where only
``current`` carries real data; future timeframes resolve to ``DEFAULT_MISSING_SCORE``.
The cube's ``dimension='product_type'`` is aliased to ``feature_type='category'``
to preserve the trained model's feature column names.

Schema validators for the live cubes live here too:
``validate_live_fingerprint_frame`` and ``validate_live_univariate_frame``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TIMEFRAMES: tuple[str, ...] = ("current", "next_week", "next_month", "three_months", "six_months")

FEATURE_VECTOR_COLUMNS: list[str] = [
    "color_current",
    "category_current",
    "material_current",
    "avg_current",
    "season_plus_0",
    "season_plus_1",
    "season_plus_2",
    "season_plus_3",
    "season_plus_6",
    "months_until_peak",
    "months_since_peak",
    "sin_month",
    "cos_month",
]

TARGET_COLUMN_DEFAULT: str = "best_timeframe"

DEFAULT_MISSING_SCORE: float = 0.0

# Keys for ``feature_type`` exposed by load_trend_lookup_from_univariate.
# These names are baked into the trained sklearn model's feature columns
# (color_current, category_current, material_current); we alias dimensions
# from the cube ('product_type' → 'category') rather than rename and retrain.
FEATURE_TYPES: tuple[str, ...] = ("color", "category", "material")

TrendLookup = dict[str, dict[str, dict[str, float]]]
FlatTrendLookup = dict[str, dict[str, float]]


@dataclass
class SeasonalityTable:
    """12-month curves keyed by normalized ``(color, category, material)`` tuples."""

    frame: pd.DataFrame

    def curve(self, color: str, category: str, material: str) -> np.ndarray:
        c = normalize_token(color)
        cat = normalize_token(category)
        m = normalize_token(material)
        row = self.frame[
            (self.frame["color"] == c)
            & (self.frame["category"] == cat)
            & (self.frame["material"] == m)
        ]
        if row.empty:
            return np.full(12, DEFAULT_MISSING_SCORE, dtype=float)
        cols = [f"month_{i}" for i in range(1, 13)]
        return row.iloc[0][cols].to_numpy(dtype=float)

    def peak_month(self, color: str, category: str, material: str) -> int:
        series = self.curve(color, category, material)
        return int(np.argmax(series) + 1)


def normalize_token(value: Any) -> str:
    """Trim + lowercase arbitrary user/system tokens."""
    if value is None:
        return "none"
    return str(value).strip().lower()


# ---------------------------------------------------------------------------
# Live cube schema validators
# ---------------------------------------------------------------------------

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


# Map univariate cube `dimension` values to the feature_type names that the
# trained sklearn model expects in its feature columns. The model's column
# names ("color_current", "category_current", "material_current") are baked in;
# we alias 'product_type' → 'category' rather than rename and retrain.
_DIM_TO_FEATURE_TYPE: dict[str, str] = {
    "color_master":  "color",
    "product_type":  "category",
    "material":      "material",
}


def load_trend_lookup_from_univariate(
    parquet_path: str | Path,
    *,
    source: str = "live",
    latest_month: bool = True,
    lookup_csv_path: str | Path | None = None,
) -> TrendLookup:
    """Build a nested ``{feature_type: {feature_value: {timeframe: score}}}``
    lookup from a univariate cube parquet (typically
    ``data/processed/merged_univariate.parquet`` — the always-rebuilt
    historical+live merge produced by notebook 1b).

    Filters by ``source`` (default ``'live'``) and the latest available
    month within that source. Decodes ``level_id`` → name via
    ``lookup.csv`` and lowercases.

    Only ``current`` carries a real score; future timeframes fall back to
    DEFAULT_MISSING_SCORE — until per-fingerprint forecasting lands.
    """
    parquet_path = Path(parquet_path)
    if lookup_csv_path is None:
        # paths.LOOKUP_CSV resolves to data/processed/lookup.csv but we avoid a
        # circular import by reading it lazily here.
        from pipelines.training.paths import LOOKUP_CSV
        lookup_csv_path = LOOKUP_CSV
    lookup_csv_path = Path(lookup_csv_path)

    if not parquet_path.exists():
        raise FileNotFoundError(f"univariate cube not found: {parquet_path}")
    if not lookup_csv_path.exists():
        raise FileNotFoundError(f"lookup.csv not found: {lookup_csv_path}")

    cube = pd.read_parquet(parquet_path)
    if source is not None:
        cube = cube[cube["source"].astype(str) == source]
    if cube.empty:
        return {}
    if latest_month:
        max_month = cube["month"].max()
        cube = cube[cube["month"] == max_month]

    # Decode level_id → canonical name
    decoder = pd.read_csv(lookup_csv_path)
    decoder = decoder.rename(columns={"category": "dimension", "id": "level_id", "name": "feature_value"})

    cube = cube[["dimension", "level_id", "share_articles"]].merge(
        decoder, on=["dimension", "level_id"], how="left"
    )
    # Sentinel level_ids (e.g., product_type=0) that don't exist in lookup.csv
    # become NaN after the merge; drop them.
    cube = cube.dropna(subset=["feature_value"])
    cube["feature_value"] = cube["feature_value"].astype(str).map(normalize_token)
    # lookup.csv has explicit "Unknown" rows for color_master / material / etc.
    # at id=0 — these represent scrape failures, not real catalog values, so
    # don't expose them as a queryable feature_value.
    cube = cube[cube["feature_value"] != "unknown"]
    # Map the cube's `dimension` to model-facing feature_type, dropping any
    # dimension we don't expose (gender, graphical_appearance, etc.).
    cube["feature_type"] = cube["dimension"].astype(str).map(_DIM_TO_FEATURE_TYPE)
    cube = cube.dropna(subset=["feature_type"])

    nested: TrendLookup = {}
    for ft, fv, score in zip(cube["feature_type"], cube["feature_value"], cube["share_articles"]):
        bucket = nested.setdefault(ft, {}).setdefault(fv, {})
        s = float(score)
        for tf in TIMEFRAMES:
            bucket[tf] = s if tf == "current" else DEFAULT_MISSING_SCORE
    return nested


def load_seasonality_table(path: str | Path) -> SeasonalityTable:
    tbl = pd.read_csv(path)
    for col in ("color", "category", "material"):
        tbl[col] = tbl[col].astype(str).map(normalize_token)
    return SeasonalityTable(frame=tbl)


def prepare_training_frame(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    for col in FEATURE_VECTOR_COLUMNS:
        if col not in out.columns:
            out[col] = DEFAULT_MISSING_SCORE
        out[col] = pd.to_numeric(out[col], errors="coerce").fillna(DEFAULT_MISSING_SCORE)
    return out


def _nested_score(lookup: Mapping[str, Any], feature_type: str, token: str, timeframe: str) -> float:
    tok = normalize_token(token)
    node = lookup.get(feature_type, {}).get(tok)
    if node is None:
        return DEFAULT_MISSING_SCORE
    if isinstance(node, Mapping):
        return float(node.get(timeframe, DEFAULT_MISSING_SCORE))
    if timeframe == "current":
        return float(node)
    return DEFAULT_MISSING_SCORE


def months_since_peak(*, peak_month: int, reference_month: int) -> int:
    return (reference_month - peak_month) % 12


def months_until_peak(*, peak_month: int, reference_month: int) -> int:
    return (peak_month - reference_month) % 12


def item_to_feature_row(
    *,
    item: Mapping[str, Any],
    lookup: Mapping[str, Any],
    reference_month: int,
    seasonality_table: SeasonalityTable,
    peak_month: int | None = None,
) -> dict[str, float]:
    """Emit one dictionary aligned with ``FEATURE_VECTOR_COLUMNS``."""

    color = normalize_token(item.get("color"))
    category = normalize_token(item.get("category"))
    material = normalize_token(item.get("material"))

    color_current = _nested_score(lookup, "color", color, "current")
    category_current = _nested_score(lookup, "category", category, "current")
    material_current = _nested_score(lookup, "material", material, "current")
    avg_current = float(np.mean([color_current, category_current, material_current]))

    curve = seasonality_table.curve(color, category, material)
    peak = int(peak_month or seasonality_table.peak_month(color, category, material))

    ref = int(reference_month)
    mu_peak = months_until_peak(peak_month=peak, reference_month=ref)
    ms_peak = months_since_peak(peak_month=peak, reference_month=ref)

    def month_value(offset: int) -> float:
        idx = (ref - 1 + offset) % 12
        return float(curve[idx])

    theta = 2.0 * math.pi * ref / 12.0
    sin_month = float(math.sin(theta))
    cos_month = float(math.cos(theta))

    return {
        "color_current": color_current,
        "category_current": category_current,
        "material_current": material_current,
        "avg_current": avg_current,
        "season_plus_0": month_value(0),
        "season_plus_1": month_value(1),
        "season_plus_2": month_value(2),
        "season_plus_3": month_value(3),
        "season_plus_6": month_value(6),
        "months_until_peak": float(mu_peak),
        "months_since_peak": float(ms_peak),
        "sin_month": sin_month,
        "cos_month": cos_month,
    }


def build_feature_frame(
    items: Iterable[Mapping[str, Any]],
    lookup: Mapping[str, Any],
    *,
    reference_month: int | None = None,
    seasonality_table: SeasonalityTable | None = None,
) -> pd.DataFrame:
    """Vectorize ``item_to_feature_row`` for sklearn/pyfunc inference."""

    ref = reference_month or datetime.now().month
    if seasonality_table is None:
        raise ValueError("seasonality_table is required for build_feature_frame")

    rows: list[dict[str, float]] = []
    for raw in items:
        rows.append(
            item_to_feature_row(
                item=raw,
                lookup=lookup,
                reference_month=ref,
                seasonality_table=seasonality_table,
            )
        )
    frame = pd.DataFrame(rows)
    return frame[FEATURE_VECTOR_COLUMNS]


def compute_feature_scores(*, item: Mapping[str, Any], lookup: TrendLookup) -> dict[str, float]:
    """Human-readable breakdown for the demo UI."""

    scores: dict[str, float] = {}
    for ft in ("color", "category", "material"):
        tok = normalize_token(item.get(ft))
        node = lookup.get(ft, {}).get(tok, {})
        for tf in TIMEFRAMES:
            scores[f"{ft}_{tf}"] = float(node.get(tf, DEFAULT_MISSING_SCORE))
        scores[f"{ft}_current"] = float(node.get("current", DEFAULT_MISSING_SCORE))
    scores["avg_current"] = float(
        np.mean([scores["color_current"], scores["category_current"], scores["material_current"]])
    )
    return scores
