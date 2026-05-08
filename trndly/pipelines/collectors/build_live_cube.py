"""
Build live counterparts to the historical fingerprint + univariate cubes.

Each retail scraper writes a raw per-(style_id × cc_id) row file named
`items_<retailer>.csv` in the synthetic_data directory — one row per
"article" (style + color variant), matching H&M's article-level grain.
This module unions all four files, derives a month from `scraped_at`,
and writes two parquets to `data/processed/`:

  - ``live_monthly_fingerprint.parquet``: 5-D cube keyed on
    (month, product_type_id, gender_id, color_master_id,
    graphical_appearance_id, material_id) with n_articles, share_articles,
    avg_price (NaN — price is not scraped).
  - ``live_monthly_univariate.parquet``: long format with one row per
    (month, dimension, level_id) for 5 dimensions: product_type, gender,
    color_master, graphical_appearance, material. Skips color_spectrum
    (mostly noise) and product_group (deterministic from product_type).

Schema is byte-compatible with notebook 1's ``monthly_fingerprint.parquet``
and ``monthly_univariate.parquet`` (`source` is the only differing value:
`'live'` vs `'historical'`). Notebook 1b's pd.concat + dedup-on-(month,
fingerprint, source) merge is the consumer.

SEMANTICS
---------
The cube is a *snapshot*, not a running tally. Within-month re-runs
overwrite prior `(month, fingerprint, source='live')` rows in the merged
universe (1b's keep='last' enforces this). Items dropped from the catalog
between runs are dropped from the cube.

Usage
-----
    # Default: read all items_*.csv, write both parquets
    python build_live_cube.py

    # Override input set:
    python build_live_cube.py \\
        --input pipelines/training/synthetic_data/items_gap.csv

    # Custom output directory:
    python build_live_cube.py --output-dir /tmp/cube_test/
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from pipelines.training.feature_contract import (  # noqa: E402
    validate_live_fingerprint_frame,
    validate_live_univariate_frame,
)
from pipelines.training.paths import (  # noqa: E402
    LIVE_FINGERPRINT_PARQUET,
    LIVE_UNIVARIATE_PARQUET,
    PROCESSED_DATA_DIR,
)

DEFAULT_SIGNALS_DIR = (
    Path(__file__).resolve().parents[1] / "training" / "synthetic_data"
)
ITEMS_FILE_GLOB = "items_*.csv"

# Fingerprint dim columns, in the canonical order the historical cube uses.
FINGERPRINT_ID_COLS: list[str] = [
    "product_type_id",
    "gender_id",
    "color_master_id",
    "graphical_appearance_id",
    "material_id",
]

# Long-format dimensions emitted to the univariate cube. Each maps to a
# column in items_*.csv. We skip color_spectrum (mostly Unknown) and
# product_group (deterministic from product_type).
UNIVARIATE_DIM_TO_ID_COL: dict[str, str] = {
    "product_type":         "product_type_id",
    "gender":               "gender_id",
    "color_master":         "color_master_id",
    "graphical_appearance": "graphical_appearance_id",
    "material":             "material_id",
}

# Categorical category lists chosen to match notebook 1 outputs so a
# pd.concat([historical, live]) preserves dtype exactly.
SOURCE_CATEGORIES: list[str] = ["historical", "live"]
DIMENSION_CATEGORIES: list[str] = [
    "product_type", "product_group", "graphical_appearance",
    "color_master", "color_spectrum", "gender", "material",
]


# --------------------------------------------------------------------------- #
# Loading                                                                       #
# --------------------------------------------------------------------------- #

def discover_items_files(signals_dir: Path) -> list[Path]:
    return sorted(signals_dir.glob(ITEMS_FILE_GLOB))


def load_items(paths: list[Path]) -> pd.DataFrame:
    """Read every items_<retailer>.csv and union them. Adds a `month` column
    derived from scraped_at (month-start) and a Categorical `source` set
    constant 'live'.
    """
    frames = []
    for path in paths:
        if not path.exists():
            print(f"  WARNING: missing items file, skipping: {path}")
            continue
        frame = pd.read_csv(path)
        required = {"scraped_at", *FINGERPRINT_ID_COLS}
        missing = required - set(frame.columns)
        if missing:
            raise ValueError(
                f"{path.name}: missing required columns {sorted(missing)}. "
                f"Re-run the scraper that produces this file."
            )
        frames.append(frame)
    if not frames:
        raise ValueError("No items_*.csv files loaded; nothing to aggregate.")
    items = pd.concat(frames, ignore_index=True)

    # Derive month-start (datetime64[ns]) from scraped_at. Truncating to month
    # collapses every same-month run into one cube row per fingerprint.
    items["month"] = (
        pd.to_datetime(items["scraped_at"], utc=True)
        .dt.tz_convert(None)
        .dt.to_period("M")
        .dt.to_timestamp()
    )
    return items


# --------------------------------------------------------------------------- #
# Cube builders                                                                 #
# --------------------------------------------------------------------------- #

def build_fingerprint_cube(items: pd.DataFrame) -> pd.DataFrame:
    """5-D fingerprint cube. n_articles = count of rows in
    (month, product_type_id, gender_id, color_master_id,
    graphical_appearance_id, material_id). share_articles is per-month.
    """
    keys = ["month", *FINGERPRINT_ID_COLS]
    agg = (
        items.groupby(keys, dropna=False)
        .size()
        .rename("n_articles")
        .reset_index()
    )
    monthly_total = items.groupby("month").size().rename("total_articles")
    agg = agg.merge(monthly_total, on="month", how="left")
    agg["share_articles"] = agg["n_articles"] / agg["total_articles"]
    agg = agg.drop(columns=["total_articles"])

    # Type & shape contract. Order matches notebook 1's fingerprint output.
    agg["month_of_year"] = agg["month"].dt.month.astype("int8")
    agg["source"] = pd.Categorical(
        ["live"] * len(agg), categories=SOURCE_CATEGORIES,
    )
    agg["avg_price"] = pd.Series([float("nan")] * len(agg), dtype="float32")

    for col in FINGERPRINT_ID_COLS:
        agg[col] = agg[col].astype("int8")
    agg["n_articles"] = agg["n_articles"].astype("int32")
    agg["share_articles"] = agg["share_articles"].astype("float32")

    return agg[
        [
            "month", "month_of_year", "source",
            *FINGERPRINT_ID_COLS,
            "n_articles", "share_articles", "avg_price",
        ]
    ]


def build_univariate_cube(items: pd.DataFrame) -> pd.DataFrame:
    """Long-format cube. One row per (month, dimension, level_id) for each
    of 5 dimensions. share_articles is per-(month, dimension) so it sums
    to 1.0 within each slice.
    """
    monthly_total = items.groupby("month").size().rename("total_articles")
    pieces: list[pd.DataFrame] = []
    for dim_name, id_col in UNIVARIATE_DIM_TO_ID_COL.items():
        agg = (
            items.groupby(["month", id_col], dropna=False)
            .size()
            .rename("n_articles")
            .reset_index()
            .rename(columns={id_col: "level_id"})
        )
        agg["dimension"] = dim_name
        agg = agg.merge(monthly_total, on="month", how="left")
        agg["share_articles"] = agg["n_articles"] / agg["total_articles"]
        agg = agg.drop(columns=["total_articles"])
        pieces.append(agg)
    out = pd.concat(pieces, ignore_index=True)
    out["month_of_year"] = out["month"].dt.month.astype("int8")
    out["source"] = pd.Categorical(
        ["live"] * len(out), categories=SOURCE_CATEGORIES,
    )
    out["dimension"] = pd.Categorical(
        out["dimension"], categories=DIMENSION_CATEGORIES,
    )
    out["level_id"] = out["level_id"].astype("int8")
    out["n_articles"] = out["n_articles"].astype("int32")
    out["share_articles"] = out["share_articles"].astype("float32")
    return out[
        ["month", "month_of_year", "source", "dimension", "level_id",
         "n_articles", "share_articles"]
    ]


# --------------------------------------------------------------------------- #
# CLI                                                                           #
# --------------------------------------------------------------------------- #

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Aggregate per-retailer items_*.csv files into the live "
            "fingerprint + univariate cubes consumed by notebook 1b."
        )
    )
    parser.add_argument(
        "--input", action="append", default=None,
        help=(
            "Path to a per-retailer items CSV. Repeat for multiple "
            "retailers. If omitted, every items_*.csv in --signals-dir is "
            "auto-discovered."
        ),
    )
    parser.add_argument(
        "--signals-dir", default=str(DEFAULT_SIGNALS_DIR),
        help=f"Directory to scan when --input is omitted (default: {DEFAULT_SIGNALS_DIR}).",
    )
    parser.add_argument(
        "--output-dir", default=str(PROCESSED_DATA_DIR),
        help=f"Where to write the live_monthly_*.parquet files (default: {PROCESSED_DATA_DIR}).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    out_dir = Path(args.output_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.input:
        input_paths = [Path(p).expanduser().resolve() for p in args.input]
    else:
        input_paths = discover_items_files(Path(args.signals_dir).expanduser().resolve())
        if not input_paths:
            print(
                f"ERROR: no {ITEMS_FILE_GLOB} files in {args.signals_dir}.\n"
                f"Run a retailer scraper first."
            )
            sys.exit(1)

    print("Building live cubes from:")
    for p in input_paths:
        print(f"  {p.name}")
    items = load_items(input_paths)
    print(
        f"\nLoaded {len(items):,} articles across {items['month'].nunique()} "
        f"month(s); months={sorted(items['month'].unique().tolist())}"
    )

    fingerprint = build_fingerprint_cube(items)
    fingerprint = validate_live_fingerprint_frame(fingerprint)
    univariate = build_univariate_cube(items)
    univariate = validate_live_univariate_frame(univariate)

    fp_path = out_dir / LIVE_FINGERPRINT_PARQUET.name
    uv_path = out_dir / LIVE_UNIVARIATE_PARQUET.name
    fingerprint.to_parquet(fp_path, index=False)
    univariate.to_parquet(uv_path, index=False)

    print(f"\nWrote {len(fingerprint):>6} fingerprint rows  → {fp_path}")
    print(f"Wrote {len(univariate):>6} univariate rows   → {uv_path}")
    print("\nUnivariate share-sum invariant per (month, dimension):")
    sums = univariate.groupby(["month", "dimension"], observed=True)["share_articles"].sum()
    print(f"  min={sums.min():.6f}  max={sums.max():.6f}  (expected ≈ 1.0)")


if __name__ == "__main__":
    main()
