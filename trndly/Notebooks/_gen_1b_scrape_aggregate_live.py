"""Generator for 1b_scrape_aggregate_live.ipynb — run: cd trndly && python Notebooks/_gen_1b_scrape_aggregate_live.py"""
from __future__ import annotations

import json
from pathlib import Path


def md(s: str) -> dict:
    lines = s.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    return {"cell_type": "markdown", "metadata": {}, "source": lines}


def code(s: str) -> dict:
    lines = s.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    return {
        "cell_type": "code",
        "metadata": {"execution_count": None},
        "outputs": [],
        "source": lines,
    }


def main() -> None:
    nb_path = Path(__file__).resolve().parent / "1b_scrape_aggregate_live.ipynb"
    cells: list[dict] = []

    cells.append(
        md(
            r"""# `1b_scrape_aggregate_live` — scrapers + live cubes + merge (step **~2.5** in the pipeline)

In the notebook folder this sorts **right after** [`1_aggregate_historical.ipynb`](1_aggregate_historical.ipynb) and **before** [`2_feature_processing.ipynb`](2_feature_processing.ipynb).

### Canonical run order

1. **`1_aggregate_historical.ipynb`** — writes immutable `historical_*.parquet` + `lookup.csv`
2. **`1b_scrape_aggregate_live.ipynb`** *(this notebook)* — refresh retailer **`items_*.csv`** → **`build_live_cube.py`** → emits **`live_<role>_<YYYY-MM>.parquet`** per snapshot month → merges historical + every live month into **`merged_*.parquet`**
3. **`2_feature_processing.ipynb`** — reads merged cubes, builds `training_*.parquet`
4. **`3_train_models.ipynb`** → **`4_hyperparameter_search.ipynb`** → **`5_forecast_from_text.ipynb`**

## Pipeline shape

| Stage | Output | Used by |
|------|--------|---------|
| Retail scrapers (`pipelines/collectors/*_scraper.py`) | `pipelines/training/synthetic_data/items_<retailer>.csv` | `build_live_cube.py` |
| **`build_live_cube.py`** | **`data/processed/live_<role>_<YYYY-MM>.parquet`** (one per snapshot month) | merge cells below |
| Notebook **1** outputs | `data/processed/historical_*.parquet` (immutable) | merge cells below |
| Merge cells (this notebook) | **`data/processed/merged_*.parquet`** (always rebuilt) | notebooks **2–5**, `/forecast-text`, `scheduleServer` |

The live cube schema mirrors notebook 1's exactly (`source='live'` is the only differing value), so `pd.concat([historical, live])` with dedup on `(month, fingerprint, source) keep='last'` is the merge. No `.bak` files: `historical_*` is immutable, `merged_*` is always rebuilt — losing it just means re-running this notebook.

## Typical workflow here

1. Toggle **`RUN_*`** (network / Playwright).
2. **Build live cubes** → `live_<role>_<YYYY-MM>.parquet` (one per scraped month)
3. **Change detection** snapshot under **`data/processed/live_refresh_state.json`**
4. **Merge** cells (always run) → `merged_*.parquet`
5. Open **`2_feature_processing.ipynb`** next.

## Contents

1. Setup
2. Paths & toggles
3. Run scrapers
4. Build live cubes
5. Change detection
6. Merge fingerprint cubes (always rebuild)
7. Merge univariate cubes (always rebuild)

"""
        )
    )

    cells.append(md("## 1. Setup\n"))
    cells.append(
        code(
            r"""import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

# Kernel cwd is often notebooks/ — put trndly on PYTHONPATH
_root = Path.cwd().resolve()
if _root.name in ("notebooks", "Notebooks"):
    _root = _root.parent
elif not (_root / "pipelines").is_dir() and (_root / "trndly" / "pipelines").is_dir():
    _root = _root / "trndly"
if (_root / "pipelines").is_dir() and str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from pipelines.serving.text_forecast import FINGERPRINT_COLS
from pipelines.training.paths import (
    HISTORICAL_FINGERPRINT_PARQUET,
    HISTORICAL_UNIVARIATE_PARQUET,
    LIVE_FINGERPRINT_GLOB,
    LIVE_UNIVARIATE_GLOB,
    MERGED_FINGERPRINT_PARQUET,
    MERGED_UNIVARIATE_PARQUET,
    PROCESSED_DATA_DIR,
    PROJECT_ROOT,
    discover_live_fingerprint_parquets,
    discover_live_univariate_parquets,
)

COLLECTORS_DIR = PROJECT_ROOT / "pipelines" / "collectors"
SYNTH_DATA_DIR = PROJECT_ROOT / "pipelines" / "training" / "synthetic_data"
STATE_PATH = PROCESSED_DATA_DIR / "live_refresh_state.json"

print("PROJECT_ROOT:", PROJECT_ROOT)
print("SYNTH_DATA_DIR:", SYNTH_DATA_DIR)
print("HISTORICAL_FINGERPRINT_PARQUET:", HISTORICAL_FINGERPRINT_PARQUET)
print("HISTORICAL_UNIVARIATE_PARQUET:", HISTORICAL_UNIVARIATE_PARQUET)
print("MERGED_FINGERPRINT_PARQUET:", MERGED_FINGERPRINT_PARQUET)
print("LIVE_FINGERPRINT_GLOB:", LIVE_FINGERPRINT_GLOB)
"""
        )
    )

    cells.append(md("## 2. Paths & toggles\n\nSet **`RUN_*`** to **`True`** only for scrapers you want to invoke (each may take minutes and requires outbound network).\n"))
    cells.append(
        code(
            r"""# --- retailer / collector subprocess scrapers (optional) ---
RUN_GAP = False
RUN_HOLLISTER = False
RUN_AMERICAN_EAGLE = False
RUN_UNIQLO = False

# After scrapers: rebuild live_fingerprint_<YYYY-MM>.parquet + live_univariate_<YYYY-MM>.parquet
# under data/processed/, which scheduleServer + notebook 1b's merge cells consume.
RUN_BUILD_LIVE_CUBE = True

PYTHON = sys.executable


def scraper_script(name: str) -> Path:
    p = COLLECTORS_DIR / name
    if not p.exists():
        raise FileNotFoundError(p)
    return p


SCRAPER_JOBS = [
    ("gap_scraper.py", RUN_GAP),
    ("hollister_scraper.py", RUN_HOLLISTER),
    ("american_eagle_scraper.py", RUN_AMERICAN_EAGLE),
    ("uniqlo_scraper.py", RUN_UNIQLO),
]  # (filename under pipelines/collectors/, enabled)


def run_scraper(path: Path, *, cwd: Path) -> int:
    cmd = [PYTHON, str(path)]
    print("$", " ".join(cmd))
    return subprocess.call(cmd, cwd=str(cwd))

print("Scraper toggles:", {name: on for name, on in SCRAPER_JOBS})
print("RUN_BUILD_LIVE_CUBE:", RUN_BUILD_LIVE_CUBE)
"""
        )
    )

    cells.append(md("## 3. Run scrapers\n"))
    cells.append(
        code(
            r"""for script_name, enabled in SCRAPER_JOBS:
    if not enabled:
        continue
    rc = run_scraper(scraper_script(script_name), cwd=PROJECT_ROOT)
    if rc != 0:
        raise RuntimeError(f"{script_name} exited with code {rc}")

print("Scraper stage done.")
"""
        )
    )

    cells.append(md("## 4. Build live cubes → `live_fingerprint_<YYYY-MM>.parquet` + `live_univariate_<YYYY-MM>.parquet`\n\nWrites the live counterparts of notebook 1's historical cubes under **`data/processed/`**. The merge cells below stitch them into the canonical cubes that **`scheduleServer`** / **`hmn_seasonal_processor`** consume.\n"))
    cells.append(
        code(
            r"""if RUN_BUILD_LIVE_CUBE:
    PROCESSED_DATA_DIR.mkdir(parents=True, exist_ok=True)
    SYNTH_DATA_DIR.mkdir(parents=True, exist_ok=True)
    builder = COLLECTORS_DIR / "build_live_cube.py"
    cmd = [
        PYTHON,
        str(builder),
        "--signals-dir",
        str(SYNTH_DATA_DIR),
        "--output-dir",
        str(PROCESSED_DATA_DIR),
    ]
    print("$", " ".join(cmd))
    rc = subprocess.call(cmd, cwd=str(PROJECT_ROOT))
    if rc != 0:
        raise RuntimeError(f"build_live_cube exited with code {rc}")
    fp_files = discover_live_fingerprint_parquets()
    uv_files = discover_live_univariate_parquets()
    for p in fp_files:
        print("Wrote", p)
    for p in uv_files:
        print("Wrote", p)
else:
    print("Skipped live cube build (RUN_BUILD_LIVE_CUBE=False)")
"""
        )
    )

    cells.append(md("## 5. Change detection\n\nStores hashes/mtimes under **`data/processed/live_refresh_state.json`** so you can tell whether inputs drifted since the last run.\n"))
    cells.append(
        code(
            r"""PROCESSED_DATA_DIR.mkdir(parents=True, exist_ok=True)


def sha256_file(path: Path) -> str | None:
    if not path.exists():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


retailer_csvs = sorted(SYNTH_DATA_DIR.glob("items_*.csv"))
live_fp_files = discover_live_fingerprint_parquets()
live_uv_files = discover_live_univariate_parquets()
snapshot = {
    "updated_at": datetime.now(timezone.utc).isoformat(),
    "merged_fingerprint_sha256": sha256_file(MERGED_FINGERPRINT_PARQUET),
    "merged_univariate_sha256":  sha256_file(MERGED_UNIVARIATE_PARQUET),
    "live_fingerprint_files": [
        {"path": str(p.relative_to(PROJECT_ROOT)), "sha256": sha256_file(p)}
        for p in live_fp_files
    ],
    "live_univariate_files": [
        {"path": str(p.relative_to(PROJECT_ROOT)), "sha256": sha256_file(p)}
        for p in live_uv_files
    ],
    "retailer_files": {
        str(p.relative_to(PROJECT_ROOT)): {
            "mtime_ns": p.stat().st_mtime_ns,
            "size": p.stat().st_size,
        }
        for p in retailer_csvs
        if p.exists()
    },
}

prev = {}
if STATE_PATH.exists():
    prev = json.loads(STATE_PATH.read_text())

changed = (
    snapshot["merged_fingerprint_sha256"] != prev.get("merged_fingerprint_sha256")
    or snapshot["merged_univariate_sha256"] != prev.get("merged_univariate_sha256")
)
STATE_PATH.write_text(json.dumps(snapshot, indent=2))
print("State written:", STATE_PATH)
print("Merged cubes changed vs last notebook run:", changed)
if not changed and prev:
    print("(SHA256 matched previous snapshot.)")

snapshot
"""
        )
    )

    cells.append(md("## 6. Merge fingerprint cubes (always rebuild)\n\nReads **`historical_fingerprint.parquet`** (immutable, from notebook 1) and globs every **`live_fingerprint_<YYYY-MM>.parquet`** in `data/processed/`. Concats with dedup on `(month, *FINGERPRINT_COLS, source) keep='last'` and writes **`merged_fingerprint.parquet`**. Always rebuilds — no `.bak` needed because `historical_*` is never overwritten.\n"))
    cells.append(
        code(
            r"""if not HISTORICAL_FINGERPRINT_PARQUET.exists():
    print("Missing", HISTORICAL_FINGERPRINT_PARQUET, "— run notebook 1 first.")
else:
    hist = pd.read_parquet(HISTORICAL_FINGERPRINT_PARQUET)
    hist["month"] = pd.to_datetime(hist["month"]).dt.as_unit("ns")

    live_files = discover_live_fingerprint_parquets()
    if live_files:
        live_frames = []
        for p in live_files:
            f = pd.read_parquet(p)
            f["month"] = pd.to_datetime(f["month"]).dt.as_unit("ns")
            live_frames.append(f)
            print(f"  loaded {len(f):>5} rows from {p.name}")
        live = pd.concat(live_frames, ignore_index=True)
    else:
        print("No live_fingerprint_*.parquet found — merged cube will be historical-only.")
        live = pd.DataFrame(columns=hist.columns)

    dup_cols = ["month", *FINGERPRINT_COLS, "source"]
    merged = pd.concat([hist, live], ignore_index=True)
    merged = merged.drop_duplicates(subset=dup_cols, keep="last")

    merged.to_parquet(MERGED_FINGERPRINT_PARQUET, index=False)
    print("Wrote", MERGED_FINGERPRINT_PARQUET, "| rows:", len(merged))
"""
        )
    )

    cells.append(md("## 7. Merge univariate cubes (always rebuild)\n\nSame pattern for the long-format cube: read **`historical_univariate.parquet`** + glob every **`live_univariate_<YYYY-MM>.parquet`**, concat with dedup on `(month, dimension, level_id, source) keep='last'`, write **`merged_univariate.parquet`**.\n"))
    cells.append(
        code(
            r"""if not HISTORICAL_UNIVARIATE_PARQUET.exists():
    print("Missing", HISTORICAL_UNIVARIATE_PARQUET, "— run notebook 1 first.")
else:
    hist = pd.read_parquet(HISTORICAL_UNIVARIATE_PARQUET)
    hist["month"] = pd.to_datetime(hist["month"]).dt.as_unit("ns")

    live_files = discover_live_univariate_parquets()
    if live_files:
        live_frames = []
        for p in live_files:
            f = pd.read_parquet(p)
            f["month"] = pd.to_datetime(f["month"]).dt.as_unit("ns")
            live_frames.append(f)
            print(f"  loaded {len(f):>5} rows from {p.name}")
        live = pd.concat(live_frames, ignore_index=True)
    else:
        print("No live_univariate_*.parquet found — merged cube will be historical-only.")
        live = pd.DataFrame(columns=hist.columns)

    dup_cols = ["month", "dimension", "level_id", "source"]
    merged = pd.concat([hist, live], ignore_index=True)
    merged = merged.drop_duplicates(subset=dup_cols, keep="last")

    merged.to_parquet(MERGED_UNIVARIATE_PARQUET, index=False)
    print("Wrote", MERGED_UNIVARIATE_PARQUET, "| rows:", len(merged))
"""
        )
    )

    cells.append(
        md(
            r"""### Next steps

- **Listing timeframe model:** rerun **`python pipelines/collectors/hmn_seasonal_processor.py`** after **`merged_univariate.parquet`** changes.
- **Forecast-from-text:** after cube merges here, run **`2_feature_processing.ipynb`** then **`3_*` / `4_*`**.

"""
        )
    )

    nb = {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python", "pygments_lexer": "ipython3"},
        },
        "cells": cells,
    }

    nb_path.write_text(json.dumps(nb, indent=1))
    print("Wrote", nb_path, "n_cells=", len(cells))


if __name__ == "__main__":
    main()
