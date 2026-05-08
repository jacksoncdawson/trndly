"""Generator for 5_forecast_from_text.ipynb — run: cd trndly && python Notebooks/_gen_5_forecast_ui.py"""
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
    nb_path = Path(__file__).resolve().parent / "5_forecast_from_text.ipynb"
    cells: list[dict] = []

    cells.append(
        md(
            r"""# Forecast from natural language — notebook twin of `/forecast-text`

Turn free-text such as **`white linen pants`** into MLflow-backed **six-month catalog-share forecasts** (`y_h1` … `y_h6`). This notebook mirrors the FastAPI route implemented in [`scheduleServer.py`](../backend/services/scheduleServer.py) and uses [`pipelines/serving/text_forecast.py`](../pipelines/serving/text_forecast.py).

## Pipeline

1. Tokenize + synonym-expand (`pants → trousers`, `jeans → denim`, …).
2. Resolve tokens against [`lookup.csv`](../data/processed/lookup.csv) (`category`, `id`, `name`).
3. Slice [`merged_fingerprint.parquet`](../data/processed/merged_fingerprint.parquet) at the latest anchor month (or the optional calendar month you pick).
4. Build feature rows (`month_of_year`, `share_t`, `avg_price_t`, three lags) per matching fingerprint.
5. Average forecasts across every fingerprint consistent with the partial specification (covers omitted gender/pattern dims).

Fallback: if no fingerprint rows qualify (calendar-strict history missing), call the **univariate** model on the strongest resolved dimension (`product_type`, `material`, …).

## Prerequisites

- Run notebooks **`1_aggregate_historical.ipynb`** → **`1b_scrape_aggregate_live.ipynb`** *(optional)* → **`2_feature_processing.ipynb`** → **`3_train_models.ipynb`** (writes ``fingerprint_model.joblib`` / ``univariate_model.joblib`` under ``data/processed/``).
- Optionally run **`4_hyperparameter_search.ipynb`** so MLflow registry names ``trndly_fingerprint`` / ``trndly_univariate`` exist with alias **`candidate`**; if they are missing, this notebook loads the notebook-3 joblibs automatically.
- Optional: set ``MLFLOW_TRACKING_URI`` if your MLruns folder is not ``<trndly>/mlruns``. The setup cell defaults to that path via ``PROJECT_ROOT`` so **`os.chdir` does not break** registry resolution.

## Outputs

- Interactive experimentation here.
- Production serving uses **`POST /forecast-text`** + static UI tab **Catalog share (text)** under `/ui/`.

## Contents

1. Setup
2. Build ``ForecastDeps`` (same objects FastAPI loads)
3. Try sample queries + inspect JSON payloads
4. Optional visualization (matplotlib)
5. Curl snippet for remote QA

## Does NOT do yet

- Rich NLP beyond token lookup / small synonym tables (swap in embeddings later).
- Live cubes (`source='live'`) once scrapers pipe into ``aggregate_live.ipynb``.
"""
        )
    )

    cells.append(md("## 1. Setup\n\nThe Jupyter kernel **cwd is usually `trndly/Notebooks/`**, which is not on `PYTHONPATH`. The first snippet inserts **`trndly/`** (parent of `Notebooks`) into `sys.path` so `from pipelines...` works—same idea as `_run_notebook.py` changing into the notebook directory.\n"))
    cells.append(
        code(
            r"""import json
import os
import sys
from pathlib import Path
from pprint import pprint

# --- make `pipelines` importable (kernel cwd is typically trndly/Notebooks/) ---
_root = Path.cwd().resolve()
if _root.name == "Notebooks":
    _root = _root.parent
elif not (_root / "pipelines").is_dir() and (_root / "trndly" / "pipelines").is_dir():
    _root = _root / "trndly"
if (_root / "pipelines").is_dir() and str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

import mlflow
import pandas as pd

from pipelines.serving.text_forecast import (
    ForecastDeps,
    forecast_from_text,
    load_forecast_pair,
)
from pipelines.training.paths import (
    LOOKUP_CSV,
    MERGED_FINGERPRINT_PARQUET,
    MERGED_UNIVARIATE_PARQUET,
    PROJECT_ROOT,
)

# Absolute default so registry lookups still hit trndly/mlruns after os.chdir(...)
_mlruns_default = (PROJECT_ROOT / "mlruns").resolve().as_uri()
TRACKING_URI = os.getenv("MLFLOW_TRACKING_URI", _mlruns_default)
FP_URI = os.getenv("MLFLOW_FORECAST_MODEL_URI", "models:/trndly_fingerprint@candidate")
UNI_URI = os.getenv("MLFLOW_UNIVARIATE_FORECAST_MODEL_URI", "models:/trndly_univariate@candidate")

mlflow.set_tracking_uri(TRACKING_URI)
print("trndly root:", _root)
print("tracking_uri:", mlflow.get_tracking_uri())
print("fp_model_uri:", FP_URI)
print("uni_model_uri:", UNI_URI)
"""
        )
    )

    cells.append(
        md(
            "## 2. Materialize ``ForecastDeps``\n\n"
            "Models load via **`load_forecast_pair`**: registry URIs first, then **`data/processed/fingerprint_model.joblib`** "
            "(and univariate joblib when the cube exists) from notebook **`3_train_models`**.\n"
        )
    )
    cells.append(
        code(
            r"""cube_fp = pd.read_parquet(MERGED_FINGERPRINT_PARQUET)
cube_fp["month"] = pd.to_datetime(cube_fp["month"]).dt.as_unit("ns")

cube_uni = None
if MERGED_UNIVARIATE_PARQUET.exists():
    cube_uni = pd.read_parquet(MERGED_UNIVARIATE_PARQUET)
    cube_uni["month"] = pd.to_datetime(cube_uni["month"]).dt.as_unit("ns")

lookup = pd.read_csv(LOOKUP_CSV)

fp_model, uni_model, model_src = load_forecast_pair(
    tracking_uri=TRACKING_URI,
    fingerprint_uri=FP_URI,
    univariate_uri=UNI_URI,
    load_univariate=cube_uni is not None,
)
print("forecast models:", model_src)

deps = ForecastDeps(
    fingerprint_model=fp_model,
    univariate_model=uni_model,
    cube_fp=cube_fp,
    cube_uni=cube_uni,
    lookup=lookup,
)

print("cube_fp:", cube_fp.shape, "| latest month:", cube_fp["month"].max())
print("cube_uni:", None if cube_uni is None else cube_uni.shape)
print("lookup:", lookup.shape)
"""
        )
    )

    cells.append(md("## 3. Sample queries\n"))
    cells.append(
        code(
            r"""for q in ["white linen pants", "women black denim skirt", "solid cotton tee"]:
    print("\n===", q, "===")
    out = forecast_from_text(q, deps)
    pprint(out)
"""
        )
    )

    cells.append(md("## 4. Visual spot check\n"))
    cells.append(
        code(
            r"""import matplotlib.pyplot as plt

q = "white linen pants"
res = forecast_from_text(q, deps)
if res.get("forecast"):
    xs = list(range(1, 7))
    ys = [res["forecast"][f"y_h{h}"] for h in xs]
    fig, ax = plt.subplots(figsize=(8, 3))
    ax.bar(xs, ys, color="#6ea8ff")
    ax.set_xticks(xs)
    ax.set_xlabel("months ahead")
    ax.set_ylabel("predicted catalog share")
    ax.set_title(q + " · mode=" + str(res["mode"]))
    plt.tight_layout()
    plt.show()
"""
        )
    )

    cells.append(md("## 5. Curl snippet (matches `/ui` fetch)\n"))
    cells.append(
        code(
            r"""print(
    '''
curl -s http://127.0.0.1:8000/forecast-text \\
  -H 'Content-Type: application/json' \\
  -d '{"query":"white linen pants"}' | jq
'''
)
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
