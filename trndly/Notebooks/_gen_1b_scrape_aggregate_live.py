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
            r"""# `1b_scrape_aggregate_live` — scrapers + live cubes (step **~2.5** in the pipeline)

In the notebook folder this sorts **right after** [`1_aggregate_historical.ipynb`](1_aggregate_historical.ipynb) and **before** [`2_feature_processing.ipynb`](2_feature_processing.ipynb).

### Canonical run order

1. **`1_aggregate_historical.ipynb`** — `monthly_*.parquet`, `lookup.csv`
2. **`1b_scrape_aggregate_live.ipynb`** *(this notebook; optional)* — refresh retailer **`items_*.csv`** → **`build_live_cube.py`** → merge **`live_monthly_*.parquet`** into processed cubes
3. **`2_feature_processing.ipynb`** — training parquets from cubes
4. **`3_train_models.ipynb`** → **`4_hyperparameter_search.ipynb`** → **`5_forecast_from_text.ipynb`**

If you skip scrapers and live parquet merges, go **`1 → 2 → 3 …`** as usual.

## What exists today

| Step | Output | Used by |
|------|--------|---------|
| Retail scrapers (`pipelines/collectors/*_scraper.py`) | `pipelines/training/synthetic_data/items_<retailer>.csv` | `build_live_cube.py` |
| **`build_live_cube.py`** | **`data/processed/live_monthly_fingerprint.parquet` + `live_monthly_univariate.parquet`** | merge cells below → `monthly_*.parquet` |
| Cubes from notebook **1** | `data/processed/monthly_*.parquet` | notebooks **2–5**, **`/forecast-text`**, **`scheduleServer`** |

The live cube schema mirrors notebook 1's exactly (`source='live'` is the only differing value), so `pd.concat([historical, live])` with dedup-on-(month, fingerprint, source) is the merge.

## Typical workflow here

1. Toggle **`RUN_*`** (network / Playwright).
2. **Build live cubes** → **`data/processed/live_monthly_*.parquet`**
3. **Change detection** snapshot under **`data/processed/live_refresh_state.json`**
4. Optional **merge** cells → patch **`monthly_*.parquet`**
5. Open **`2_feature_processing.ipynb`** next.

## Contents

1. Setup
2. Paths & toggles
3. Run scrapers
4. Build live cubes
5. Change detection
6. Merge optional live fingerprint parquet
7. Merge optional live univariate parquet

"""
        )
    )

    cells.append(md("## 1. Setup\n"))
    cells.append(
        code(
            r"""import hashlib
import json
import shutil
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
    LIVE_FINGERPRINT_PARQUET,
    LIVE_UNIVARIATE_PARQUET,
    MONTHLY_FINGERPRINT_PARQUET,
    MONTHLY_UNIVARIATE_PARQUET,
    PROCESSED_DATA_DIR,
    PROJECT_ROOT,
)

COLLECTORS_DIR = PROJECT_ROOT / "pipelines" / "collectors"
SYNTH_DATA_DIR = PROJECT_ROOT / "pipelines" / "training" / "synthetic_data"
STATE_PATH = PROCESSED_DATA_DIR / "live_refresh_state.json"

print("PROJECT_ROOT:", PROJECT_ROOT)
print("SYNTH_DATA_DIR:", SYNTH_DATA_DIR)
print("LIVE_FINGERPRINT_PARQUET:", LIVE_FINGERPRINT_PARQUET)
print("LIVE_UNIVARIATE_PARQUET:", LIVE_UNIVARIATE_PARQUET)
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

# After scrapers: rebuild live_monthly_fingerprint.parquet + live_monthly_univariate.parquet
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

    cells.append(md("## 4. Build live cubes → `live_monthly_fingerprint.parquet` + `live_monthly_univariate.parquet`\n\nWrites the live counterparts of notebook 1's historical cubes under **`data/processed/`**. The merge cells below stitch them into the canonical cubes that **`scheduleServer`** / **`hmn_seasonal_processor`** consume.\n"))
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
    print("Wrote", LIVE_FINGERPRINT_PARQUET)
    print("Wrote", LIVE_UNIVARIATE_PARQUET)
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
snapshot = {
    "updated_at": datetime.now(timezone.utc).isoformat(),
    "live_fingerprint_sha256": sha256_file(LIVE_FINGERPRINT_PARQUET),
    "live_univariate_sha256":  sha256_file(LIVE_UNIVARIATE_PARQUET),
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
    snapshot["live_fingerprint_sha256"] != prev.get("live_fingerprint_sha256")
    or snapshot["live_univariate_sha256"] != prev.get("live_univariate_sha256")
)
STATE_PATH.write_text(json.dumps(snapshot, indent=2))
print("State written:", STATE_PATH)
print("Live cubes changed vs last notebook run:", changed)
if not changed and prev:
    print("(SHA256 matched previous snapshot.)")

snapshot
"""
        )
    )

    cells.append(md("## 6. Merge optional live fingerprint parquet\n\nExpect **`live_monthly_fingerprint.parquet`** next to other processed artifacts, **`source='live'`**, same columns as **`monthly_fingerprint.parquet`**. Backup is **`monthly_fingerprint.parquet.bak.<timestamp>`**.\n"))
    cells.append(
        code(
            r"""MERGE_LIVE_FINGERPRINT = LIVE_FINGERPRINT_PARQUET.exists()

if not MERGE_LIVE_FINGERPRINT:
    print("No file at", LIVE_FINGERPRINT_PARQUET, "— skipping fingerprint merge.")
elif not MONTHLY_FINGERPRINT_PARQUET.exists():
    print("Missing base cube", MONTHLY_FINGERPRINT_PARQUET, "— run notebook 1 first.")
else:
    hist = pd.read_parquet(MONTHLY_FINGERPRINT_PARQUET)
    live = pd.read_parquet(LIVE_FINGERPRINT_PARQUET)
    hist["month"] = pd.to_datetime(hist["month"]).dt.as_unit("ns")
    live["month"] = pd.to_datetime(live["month"]).dt.as_unit("ns")

    dup_cols = ["month", *FINGERPRINT_COLS, "source"]
    merged = pd.concat([hist, live], ignore_index=True)
    merged = merged.drop_duplicates(subset=dup_cols, keep="last")

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    bak = MONTHLY_FINGERPRINT_PARQUET.with_suffix(f".parquet.bak.{ts}")
    shutil.copy2(MONTHLY_FINGERPRINT_PARQUET, bak)
    print("Backup:", bak)

    merged.to_parquet(MONTHLY_FINGERPRINT_PARQUET, index=False)
    print("Wrote", MONTHLY_FINGERPRINT_PARQUET, "| rows:", len(merged))
"""
        )
    )

    cells.append(md("## 7. Merge optional live univariate parquet\n\nSame pattern for **`live_monthly_univariate.parquet`** (long-format cube).\n"))
    cells.append(
        code(
            r"""MERGE_LIVE_UNIVARIATE = LIVE_UNIVARIATE_PARQUET.exists()

if not MERGE_LIVE_UNIVARIATE:
    print("No file at", LIVE_UNIVARIATE_PARQUET, "— skipping univariate merge.")
elif not MONTHLY_UNIVARIATE_PARQUET.exists():
    print("Missing base cube", MONTHLY_UNIVARIATE_PARQUET, "— run notebook 1 first.")
else:
    hist = pd.read_parquet(MONTHLY_UNIVARIATE_PARQUET)
    live = pd.read_parquet(LIVE_UNIVARIATE_PARQUET)
    hist["month"] = pd.to_datetime(hist["month"]).dt.as_unit("ns")
    live["month"] = pd.to_datetime(live["month"]).dt.as_unit("ns")

    dup_cols = ["month", "dimension", "level_id", "source"]
    merged = pd.concat([hist, live], ignore_index=True)
    merged = merged.drop_duplicates(subset=dup_cols, keep="last")

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    bak = MONTHLY_UNIVARIATE_PARQUET.with_suffix(f".parquet.bak.{ts}")
    shutil.copy2(MONTHLY_UNIVARIATE_PARQUET, bak)
    print("Backup:", bak)

    merged.to_parquet(MONTHLY_UNIVARIATE_PARQUET, index=False)
    print("Wrote", MONTHLY_UNIVARIATE_PARQUET, "| rows:", len(merged))
"""
        )
    )

    cells.append(
        md(
            r"""### Next steps

- **Listing timeframe model:** rerun **`python pipelines/collectors/hmn_seasonal_processor.py`** after **`live_monthly_univariate.parquet`** changes.
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
