"""Compare candidate vs incumbent; promote champion if candidate is better.

Reads:
    data/models/model_training_run.json   (candidate manifest, written by train.py)
    data/models/champion_metrics.json     (incumbent manifest, written by a prior
                                          successful evaluate run; absent on first run)

Writes (only when promoting):
    data/models/champion_metrics.json     (copy of model_training_run.json)

Promotion rule:
    For each model independently (univariate, fingerprint):
      candidate.holdout_wmae <= incumbent.holdout_wmae  → promote
      no incumbent recorded                              → promote
      else                                               → keep incumbent (no file changes)

Note: this is the local-MVP version. The plan's "MLflow registry champion alias"
is deferred until cloud deployment. When that lands, replace the file shuffling
here with ``MlflowClient.set_registered_model_alias(name=..., alias='champion',
version=candidate.version)`` for each registered model.

Caveat: when a candidate LOSES, train.py has already overwritten the canonical
joblibs in ``data/models/{fingerprint,univariate}_model.joblib`` with the
candidate's weights. evaluate.py does not currently revert those weights — it
only refuses to advance the champion-metrics pointer. To recover the prior
champion's joblibs you'd need to retrain from a prior month or restore from
backup. This trade-off is acceptable for MVP; a future revision can add a
runs/ archive + auto-revert path.

Usage:
    python -m pipelines.monthly.evaluate
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

from pipelines.paths import MODEL_TRAINING_RUN_JSON, MODELS_DIR

logger = logging.getLogger(__name__)

CHAMPION_METRICS_JSON: Path = MODELS_DIR / "champion_metrics.json"

MODEL_KEYS: tuple[str, ...] = ("univariate", "fingerprint")


def _read_holdout_wmae(manifest: dict, model_key: str) -> float:
    """Return ``holdout_wmae`` for one model from a model_training_run-shaped manifest."""
    block = manifest[model_key]
    if "holdout_wmae" in block:
        return float(block["holdout_wmae"])
    # Fallback: dig through metrics → model → holdout → wmae_mean
    return float(block["metrics"]["model"]["holdout"]["wmae_mean"])


def _decide(candidate_manifest: dict, incumbent_manifest: dict | None) -> dict[str, dict]:
    """Per-model decision dict: {model_key: {action, candidate_wmae, incumbent_wmae}}."""
    decisions: dict[str, dict] = {}
    for k in MODEL_KEYS:
        cand = _read_holdout_wmae(candidate_manifest, k)
        if incumbent_manifest is None:
            decisions[k] = {
                "action": "promote",
                "reason": "no incumbent recorded",
                "candidate_wmae": cand,
                "incumbent_wmae": None,
            }
            continue
        incb = _read_holdout_wmae(incumbent_manifest, k)
        if cand <= incb:
            decisions[k] = {
                "action": "promote",
                "reason": f"candidate WMAE {cand:.6g} <= incumbent {incb:.6g}",
                "candidate_wmae": cand,
                "incumbent_wmae": incb,
            }
        else:
            decisions[k] = {
                "action": "keep",
                "reason": f"candidate WMAE {cand:.6g} > incumbent {incb:.6g}",
                "candidate_wmae": cand,
                "incumbent_wmae": incb,
            }
    return decisions


def run_evaluate() -> dict:
    """Compare candidate vs incumbent. Promote if any model improved.

    Returns a {action, decisions} summary. Champion metrics file is updated
    iff at least one model is promoted (so a loss for one model doesn't
    silently overwrite a stale-but-still-better incumbent for the other).
    """
    if not MODEL_TRAINING_RUN_JSON.exists():
        raise FileNotFoundError(
            f"missing candidate manifest at {MODEL_TRAINING_RUN_JSON}; "
            f"run `python -m pipelines.monthly.train` first."
        )
    with open(MODEL_TRAINING_RUN_JSON) as f:
        candidate = json.load(f)

    incumbent: dict | None = None
    if CHAMPION_METRICS_JSON.exists():
        with open(CHAMPION_METRICS_JSON) as f:
            incumbent = json.load(f)

    decisions = _decide(candidate, incumbent)

    any_promoted = any(d["action"] == "promote" for d in decisions.values())
    for k, d in decisions.items():
        emoji = "↑" if d["action"] == "promote" else "·"
        logger.info("  %s %-12s %s — %s", emoji, k, d["action"], d["reason"])

    if any_promoted:
        shutil.copyfile(MODEL_TRAINING_RUN_JSON, CHAMPION_METRICS_JSON)
        logger.info("wrote %s", CHAMPION_METRICS_JSON)
        action = "promoted"
    else:
        action = "kept"

    return {"action": action, "decisions": decisions}


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    summary = run_evaluate()
    logger.info("evaluate summary: %s", summary["action"])
    for k, d in summary["decisions"].items():
        logger.info("  %s: %s", k, d["reason"])


if __name__ == "__main__":
    main()
