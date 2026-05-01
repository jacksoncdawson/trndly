"""
Training + inference contract for the listing timeframe classifier.

Consumes rows shaped like ``trend_signals.csv`` (feature_type × feature_value × time-window scores),
joins optional historical seasonality curves from ``seasonality_table.csv``, and emits the fixed-width
numeric vector expected by ``RandomForestClassifier`` models logged via MLflow.

This module also exposes helpers reused by ``hmn_seasonal_processor.py`` (flat ``build_trend_lookup``)
and ``scheduleServer.py`` (nested ``load_trend_lookup`` + ``build_feature_frame``).
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

TREND_SIGNAL_COLUMNS: list[str] = [
    "feature_type",
    "feature_value",
    "current",
    "next_week",
    "next_month",
    "three_months",
    "six_months",
]

# Keys for ``feature_type`` in ``trend_signals*.csv`` rows (retail scrapers iterate these buckets).
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


def validate_trend_signals_frame(frame: pd.DataFrame) -> pd.DataFrame:
    required = {"feature_type", "feature_value", "current"}
    missing_cols = required - set(frame.columns)
    if frame.empty:
        raise ValueError("trend_signals frame is empty")
    if missing_cols:
        raise ValueError(f"trend_signals frame missing columns: {sorted(missing_cols)}")

    cleaned = frame.copy()
    for col in TIMEFRAMES:
        if col not in cleaned.columns:
            cleaned[col] = cleaned["current"]

    cleaned = cleaned[TREND_SIGNAL_COLUMNS].copy()
    score_cols = [c for c in TIMEFRAMES]
    cleaned[score_cols] = cleaned[score_cols].astype(float).clip(0.0, 1.0)
    cleaned["feature_type"] = cleaned["feature_type"].astype(str).str.strip().str.lower()
    cleaned["feature_value"] = cleaned["feature_value"].astype(str).map(normalize_token)
    return cleaned


def load_trend_signals_frame(path: str | Path) -> pd.DataFrame:
    raw = pd.read_csv(path)
    return validate_trend_signals_frame(raw)


def build_trend_lookup(trend_frame: pd.DataFrame) -> FlatTrendLookup:
    """Flatten ``current`` scores for ``item_to_feature_row`` inside ``hmn_seasonal_processor``."""

    df = validate_trend_signals_frame(trend_frame)
    lookup: FlatTrendLookup = {}
    for _, row in df.iterrows():
        ft = str(row["feature_type"])
        fv = str(row["feature_value"])
        lookup.setdefault(ft, {})[fv] = float(row["current"])
    return lookup


def load_trend_lookup(path: str | Path) -> TrendLookup:
    """Nested lookup used by FastAPI ``/options`` + inference utilities."""

    df = load_trend_signals_frame(path)
    nested: TrendLookup = {}
    for _, row in df.iterrows():
        ft = str(row["feature_type"])
        fv = str(row["feature_value"])
        bucket = nested.setdefault(ft, {}).setdefault(fv, {})
        for tf in TIMEFRAMES:
            bucket[tf] = float(row[tf])
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
