"""
Build live counterparts to the historical fingerprint + univariate cubes.

Each retail scraper writes a raw per-(style_id × cc_id) row file named
`items_<retailer>.csv` in `data/raw/items/` — one row per
"article" (style + color variant), matching H&M's article-level grain.
This module unions all four files, derives a month from `scraped_at`,
and writes per-snapshot-month parquets to `data/processed/`:

  - ``live_fingerprint_<YYYY-MM>.parquet`` — 5-D cube keyed on
    (month, product_type_id, gender_id, color_master_id,
    graphical_appearance_id, material_id) with n_articles, share_articles,
    avg_price (NaN — price is not scraped).
  - ``live_univariate_<YYYY-MM>.parquet`` — long format with one row per
    (month, dimension, level_id) for 5 dimensions: product_type, gender,
    color_master, graphical_appearance, material. Skips color_spectrum
    (mostly noise) and product_group (deterministic from product_type).

One file per snapshot month: re-running build_live_cube within the same
month overwrites that month's file. Items spanning multiple months
(e.g., recovered/historical scrapes) emit multiple files. Notebook 1b
discovers them by globbing ``live_*_*.parquet`` and concats with the
historical cube to produce the always-rebuilt ``merged_*.parquet``.

Schema is byte-compatible with notebook 1's ``historical_*.parquet``
(``source`` is the only differing value: ``'live'`` vs ``'historical'``)
so the 1b concat preserves Categorical dtypes and IDs decode against
the same ``lookup.csv``.

SEMANTICS
---------
The cube is a *snapshot*, not a running tally. Within-month re-runs
overwrite prior `(month, fingerprint, source='live')` rows in the merged
universe. Items dropped from the catalog between runs are dropped from
the cube. Multi-month inputs emit one self-contained parquet per month.

Usage
-----
    # Default: read all items_*.csv, write live_*_<YYYY-MM>.parquet per month
    python build_live_cube.py

    # Override input set:
    python build_live_cube.py \\
        --input data/raw/items/items_gap.csv

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

from pipelines.contracts import (  # noqa: E402
    validate_live_fingerprint_frame,
    validate_live_univariate_frame,
)
from pipelines.paths import (  # noqa: E402
    ITEMS_FILE_GLOB,
    PROCESSED_DIR,
    RAW_ITEMS_DIR,
    discover_items_files,
    live_fingerprint_path_for,
    live_univariate_path_for,
)

DEFAULT_SIGNALS_DIR = RAW_ITEMS_DIR

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

def load_items(paths: list[Path]) -> pd.DataFrame:
    """Read every items_<retailer>.csv and union them. Adds a `month` column
    derived from scraped_at (month-start). Source tagging happens in the
    cube builders.
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
# Pre-aggregation: unisex collapsing                                            #
# --------------------------------------------------------------------------- #

def collapse_unisex(items: pd.DataFrame) -> pd.DataFrame:
    """Collapse same-SKU-in-both-catalogs into single ``gender='unisex'`` rows.

    A SKU is unisex (id=2) when the same ``(retailer, style_id, cc_id)``
    tuple appears with both ``gender='women'`` AND ``gender='men'`` in
    the unioned items frame. The ``women`` row wins as the canonical row;
    the ``men`` row is dropped, and the kept row's ``gender`` /
    ``gender_id`` are rewritten to ``unisex`` / 2.

    Rows already tagged ``gender='unisex'`` (from a hypothetical retailer
    that exposes a unisex catalog directly) pass through unchanged.

    The dedup key intentionally includes ``retailer`` so two retailers
    happening to share an SKU number do not collide.
    """
    sku_key = ["retailer", "style_id", "cc_id"]
    genders_per_sku = (
        items.groupby(sku_key, dropna=False)["gender"]
        .agg(lambda s: frozenset(s.dropna().astype(str)))
    )
    unisex_pairs = genders_per_sku[
        genders_per_sku == frozenset({"women", "men"})
    ].index

    if len(unisex_pairs) == 0:
        return items

    unisex_set = set(map(tuple, unisex_pairs))
    key_tuples = list(zip(*(items[c] for c in sku_key)))
    is_pair = pd.Series(
        [k in unisex_set for k in key_tuples], index=items.index
    )
    is_men = is_pair & (items["gender"] == "men")
    is_women_idx = items.index[is_pair & (items["gender"] == "women")]

    kept = items[~is_men].copy()  # drops the "men" half of every unisex pair
    kept.loc[kept.index.intersection(is_women_idx), "gender"] = "unisex"
    kept.loc[kept.index.intersection(is_women_idx), "gender_id"] = 2

    return kept.reset_index(drop=True)


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
# Per-month writer                                                              #
# --------------------------------------------------------------------------- #

def write_per_month_cubes(
    fingerprint: pd.DataFrame,
    univariate: pd.DataFrame,
    out_dir: Path,
) -> list[tuple[Path, Path]]:
    """Split each cube by `month` and write one parquet per month under
    out_dir, named ``live_<role>_<YYYY-MM>.parquet``. Re-running for the
    same month overwrites that month's file (snapshot semantics).

    Returns a list of (fingerprint_path, univariate_path) per month written.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[tuple[Path, Path]] = []
    for month, fp_group in fingerprint.groupby("month", observed=True, sort=True):
        fp_path = out_dir / live_fingerprint_path_for(month).name
        uv_path = out_dir / live_univariate_path_for(month).name
        uv_group = univariate[univariate["month"] == month]
        # Validate per-month before writing — invariant must hold within
        # the slice we're persisting, not just on the union.
        validate_live_fingerprint_frame(fp_group)
        validate_live_univariate_frame(uv_group)
        fp_group.to_parquet(fp_path, index=False)
        uv_group.to_parquet(uv_path, index=False)
        paths.append((fp_path, uv_path))
    return paths


# --------------------------------------------------------------------------- #
# CLI                                                                           #
# --------------------------------------------------------------------------- #

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Aggregate per-retailer items_*.csv files into the live "
            "fingerprint + univariate cubes consumed by pipelines.monthly.aggregate. "
            "Writes one parquet per snapshot month: "
            "live_fingerprint_<YYYY-MM>.parquet + live_univariate_<YYYY-MM>.parquet."
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
        "--output-dir", default=str(PROCESSED_DIR),
        help=(
            "Where to write live_<role>_<YYYY-MM>.parquet files "
            f"(default: {PROCESSED_DIR})."
        ),
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
    months = sorted(items["month"].unique().tolist())
    print(
        f"\nLoaded {len(items):,} articles across {len(months)} month(s); "
        f"months={[pd.Timestamp(m).strftime('%Y-%m') for m in months]}"
    )

    n_before = len(items)
    items = collapse_unisex(items)
    n_collapsed = n_before - len(items)
    if n_collapsed:
        print(
            f"Collapsed {n_collapsed:,} (M+W)-pair rows into "
            f"{n_collapsed} unisex rows ({n_before:,} → {len(items):,})."
        )

    fingerprint = build_fingerprint_cube(items)
    univariate = build_univariate_cube(items)

    written = write_per_month_cubes(fingerprint, univariate, out_dir)
    for fp_path, uv_path in written:
        n_fp = (fingerprint["month"] == pd.Timestamp(fp_path.stem.split("_")[-1] + "-01")).sum()
        n_uv = (univariate["month"] == pd.Timestamp(uv_path.stem.split("_")[-1] + "-01")).sum()
        print(f"\nWrote {n_fp:>6} fingerprint rows  → {fp_path}")
        print(f"Wrote {n_uv:>6} univariate rows   → {uv_path}")

    print("\nUnivariate share-sum invariant per (month, dimension):")
    sums = univariate.groupby(["month", "dimension"], observed=True)["share_articles"].sum()
    print(f"  min={sums.min():.6f}  max={sums.max():.6f}  (expected ≈ 1.0)")


if __name__ == "__main__":
    main()
