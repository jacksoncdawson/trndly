"""
Combine per-retailer trend signal files into a single trend_signals.csv.

Each retail scraper (hollister_scraper.py, gap_scraper.py, ...) writes its
own output file named `trend_signals_<retailer>.csv` in the synthetic_data
directory. This script reads every such file, merges the per-(feature_type,
feature_value) scores across retailers, and writes the canonical
`trend_signals.csv` that the training / serving pipeline actually consumes.

MERGE STRATEGY
---------------
Each per-retailer CSV stores scores as proportions: score = count / total_items
for that site. To recover the partner's intended "proportion of that fingerprint
relative to ALL items scraped this month", the combine step uses a
size-weighted average where each retailer's weight = total_items it scraped.

For each (feature_type, feature_value) pair:
  1. Collect every retailer's `current` score and its weight (total_items).
  2. Drop any score equal to DEFAULT_MISSING_SCORE — those represent
     "retailer didn't see this value" and shouldn't dilute real signal.
  3. If nothing real is left, fall back to DEFAULT_MISSING_SCORE.
  4. Otherwise take a size-weighted mean:
       combined = sum(score_i * total_items_i) / sum(total_items_i)
     which is mathematically equivalent to:
       combined = sum(raw_count_i) / sum(total_items_i across all retailers)

WEIGHT AUTO-DETECTION
---------------------
Each scraper writes a sidecar `trend_signals_<retailer>_meta.json` containing
`{"total_items": N}`. This script reads those automatically and uses them as
weights. You can override with explicit --weight flags if needed.

Usage:
  # Combine every trend_signals_*.csv (weights auto-detected from _meta.json):
  python combine_trend_signals.py

  # Explicit input files, custom output:
  python combine_trend_signals.py \\
      --input trend_signals_hollister.csv \\
      --input trend_signals_gap.csv \\
      --output-path trend_signals.csv

  # Override weights manually (same order as --input):
  python combine_trend_signals.py \\
      --input trend_signals_hollister.csv --weight 800 \\
      --input trend_signals_gap.csv       --weight 500
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from pipelines.training.feature_contract import (  # noqa: E402
    DEFAULT_MISSING_SCORE,
    TREND_SIGNAL_COLUMNS,
    validate_trend_signals_frame,
)

DEFAULT_SIGNALS_DIR = (
    Path(__file__).resolve().parents[1] / "training" / "synthetic_data"
)
DEFAULT_OUTPUT_PATH = DEFAULT_SIGNALS_DIR / "trend_signals.csv"

# Glob used when the user didn't pass --input explicitly.
RETAILER_FILE_GLOB = "trend_signals_*.csv"


# --------------------------------------------------------------------------- #
# Loading                                                                       #
# --------------------------------------------------------------------------- #

def discover_retailer_files(
    signals_dir: Path,
    exclude: Path | None = None,
) -> list[Path]:
    """
    Find all per-retailer trend signal files in signals_dir.

    We exclude `exclude` (typically the canonical combined output) so we
    never feed the previous combined file back into itself.
    """
    found = sorted(signals_dir.glob(RETAILER_FILE_GLOB))
    if exclude is not None:
        exclude_resolved = exclude.resolve()
        found = [p for p in found if p.resolve() != exclude_resolved]
    return found


def load_retailer_signals(path: Path) -> pd.DataFrame:
    """
    Read a per-retailer trend signals CSV and validate it matches the
    feature contract. Adds a `source` column for debugging.
    """
    frame = pd.read_csv(path)
    validated = validate_trend_signals_frame(frame)
    validated = validated[TREND_SIGNAL_COLUMNS].copy()
    validated["source"] = path.stem.replace("trend_signals_", "") or path.stem
    return validated


def load_retailer_total_items(csv_path: Path) -> float:
    """
    Read total_items from the sidecar _meta.json file written by each scraper.

    Each scraper saves trend_signals_<retailer>_meta.json alongside its CSV
    with {"total_items": N}. This is used as the size weight so that the
    combine step is equivalent to pooling raw counts across all retailers and
    dividing by the grand total.

    Falls back to 1.0 (equal weight) if the sidecar is missing or unreadable.
    """
    meta_path = csv_path.with_name(csv_path.stem + "_meta.json")
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text())
            total = float(meta.get("total_items", 0))
            if total > 0:
                return total
        except Exception:
            pass
    return 1.0


# --------------------------------------------------------------------------- #
# Merging                                                                       #
# --------------------------------------------------------------------------- #

def combine_signals(
    per_retailer: list[pd.DataFrame],
    weights: list[float] | None = None,
) -> pd.DataFrame:
    """
    Combine per-retailer frames into one canonical trend_signals frame.

    `weights[i]` is the total number of items retailer i scraped. The
    combined score for each (feature_type, feature_value) is:

        combined = sum(raw_count_i) / grand_total

    where raw_count_i = score_i * total_items_i  (recovering the count
    from the per-site proportion) and grand_total = sum(total_items_i).

    This is equivalent to: pool all raw counts across every retailer, then
    divide by the total number of items scraped across all sites this run.

    A retailer that didn't see a value contributes 0 to the numerator but
    its item count still goes into the denominator — correctly reducing the
    combined proportion if most of the market isn't carrying that value.

    When weights are omitted every retailer gets weight 1.0 (equal size).
    """
    if not per_retailer:
        raise ValueError("combine_signals: no input frames provided.")

    if weights is None:
        weights = [1.0] * len(per_retailer)
    if len(weights) != len(per_retailer):
        raise ValueError(
            f"combine_signals: got {len(per_retailer)} frames but "
            f"{len(weights)} weights (they must match 1:1)."
        )

    grand_total = sum(weights)

    def _is_real(score: float) -> bool:
        """True when the score is a real observation, not a missing-value fill."""
        return not math.isclose(
            float(score), DEFAULT_MISSING_SCORE,
            rel_tol=0.0, abs_tol=1e-9,
        )

    # Recover raw counts from each retailer's proportions.
    # DEFAULT_MISSING_SCORE rows contribute 0 to the numerator
    # (that retailer simply didn't carry the value).
    tagged = []
    for frame, total_items in zip(per_retailer, weights):
        tmp = frame.copy()
        tmp["_raw_count"] = tmp["current"].apply(
            lambda s: float(s) * float(total_items) if _is_real(s) else 0.0
        )
        tagged.append(tmp)

    stacked = pd.concat(tagged, ignore_index=True)

    # Sum raw counts across retailers, then divide by grand total.
    grouped = stacked.groupby(
        ["feature_type", "feature_value"], as_index=False
    ).agg(_total_raw=("_raw_count", "sum"))
    grouped["current"] = (grouped["_total_raw"] / grand_total).round(6)
    combined = grouped[["feature_type", "feature_value", "current"]]

    # Any value whose combined score rounds to DEFAULT_MISSING_SCORE or
    # below gets clamped to DEFAULT_MISSING_SCORE so downstream consumers
    # always see a consistent floor.
    combined["current"] = combined["current"].apply(
        lambda s: DEFAULT_MISSING_SCORE if s <= DEFAULT_MISSING_SCORE else s
    ).round(6)

    return combined[TREND_SIGNAL_COLUMNS].sort_values(
        ["feature_type", "feature_value"]
    ).reset_index(drop=True)


# --------------------------------------------------------------------------- #
# CLI                                                                           #
# --------------------------------------------------------------------------- #

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Combine per-retailer trend_signals_*.csv files into a single "
            "trend_signals.csv usable by the rest of the pipeline."
        )
    )
    parser.add_argument(
        "--input", action="append", default=None,
        help=(
            "Path to a per-retailer trend signals CSV. Repeat for multiple "
            "retailers. If omitted, every trend_signals_*.csv in the default "
            "synthetic_data directory is auto-discovered."
        ),
    )
    parser.add_argument(
        "--weight", action="append", type=float, default=None,
        help=(
            "Weight for the corresponding --input (same order). Defaults to "
            "1.0 for every retailer."
        ),
    )
    parser.add_argument(
        "--signals-dir", default=str(DEFAULT_SIGNALS_DIR),
        help=(
            "Directory to scan for trend_signals_*.csv when --input is not "
            f"given (default: {DEFAULT_SIGNALS_DIR})."
        ),
    )
    parser.add_argument(
        "--output-path", default=str(DEFAULT_OUTPUT_PATH),
        help=f"Where to write the combined CSV (default: {DEFAULT_OUTPUT_PATH}).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_path = Path(args.output_path).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if args.input:
        input_paths = [Path(p).expanduser().resolve() for p in args.input]
    else:
        signals_dir = Path(args.signals_dir).expanduser().resolve()
        input_paths = discover_retailer_files(signals_dir, exclude=output_path)
        if not input_paths:
            print(
                f"ERROR: no {RETAILER_FILE_GLOB} files found in {signals_dir}.\n"
                f"Run a retailer scraper first (e.g. hollister_scraper.py)."
            )
            sys.exit(1)

    if args.weight and len(args.weight) != len(input_paths):
        print(
            f"ERROR: got {len(input_paths)} --input files but "
            f"{len(args.weight)} --weight values. They must match 1:1."
        )
        sys.exit(1)

    # Determine weights: explicit --weight flags override auto-detection.
    use_manual_weights = bool(args.weight)

    print("Combining trend signal files:")
    per_retailer: list[pd.DataFrame] = []
    auto_weights: list[float] = []
    loaded_paths: list[Path] = []

    for path in input_paths:
        if not path.exists():
            print(f"  WARNING: missing file, skipping: {path}")
            continue
        frame = load_retailer_signals(path)
        real_values = (frame["current"] != DEFAULT_MISSING_SCORE).sum()
        total_items = load_retailer_total_items(path)
        weight_src = "manual" if use_manual_weights else f"meta ({int(total_items)} items)"
        print(
            f"  loaded {len(frame):>3} rows "
            f"({real_values} non-default)  weight={weight_src}  ← {path.name}"
        )
        per_retailer.append(frame.drop(columns=["source"]))
        auto_weights.append(total_items)
        loaded_paths.append(path)

    print(f"\nOutput → {output_path}\n")

    if not per_retailer:
        print("ERROR: no valid retailer files loaded.")
        sys.exit(1)

    weights_to_use = args.weight if use_manual_weights else auto_weights
    combined = combine_signals(per_retailer, weights=weights_to_use)
    validated = validate_trend_signals_frame(combined)
    validated.to_csv(output_path, index=False)

    real_values = (validated["current"] != DEFAULT_MISSING_SCORE).sum()
    print(
        f"\nWrote {len(validated)} rows "
        f"({real_values} with real signal, "
        f"{len(validated) - real_values} at default={DEFAULT_MISSING_SCORE}) "
        f"→ {output_path}"
    )


if __name__ == "__main__":
    main()
