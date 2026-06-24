"""Compare this tick's candidate vs the incumbent champion; promote on a win.

Reads:
    data/ticks/<YYYY-MM>/model/model_training_run.json   (candidate manifest, written by train.py)
    data/models/champion.json                            (incumbent pointer, written by a prior
                                                          evaluate run; absent on first run)

Writes:
    data/models/{univariate,fingerprint}_model.joblib    (canonical champion weights, on a promotion)
    data/models/champion.json                            (per-model champion pointer)

Promotion rule (per model independently — univariate, fingerprint):
    candidate.holdout_wmae <= incumbent.holdout_wmae  → promote
    no incumbent recorded                              → promote
    else                                               → keep incumbent

Promote-copy flow (simplification this refactor unlocks)
--------------------------------------------------------
Per-tick model isolation (train writes the candidate to ``ticks/<M>/model/``,
never to ``data/models/``) removes the old "train clobbers the canonical joblib"
bug at the root — so there is NO revert anymore. On a per-model **win** this
stage copies ``ticks/<M>/model/<role>_model.joblib`` → ``data/models/<role>_model.joblib``
and points ``champion.json[role]`` at ``<M>``; on a **loss** it does nothing
(the canonical joblib stays the reigning champion). ``predict`` always loads the
canonical champion, so a losing candidate can never reach serving.

MLflow-independent and SUPERSEDED by Phase 4's ``champion`` registry alias once
the private MLflow lands.

Usage:
    python -m pipelines.monthly.evaluate
"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timezone

from pipelines.paths import (
    CHAMPION_JSON,
    MODELS_DIR,
    champion_joblib_for,
    resolve_tick_month,
    tick_model_joblib,
    tick_model_training_run_json,
)

logger = logging.getLogger(__name__)

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


def run_evaluate(month=None) -> dict:
    """Compare the tick's candidate vs the incumbent champion per model.

    On a per-model win the candidate joblib is copied into ``data/models/`` (the
    canonical champion weights ``predict`` loads) and ``champion.json[role]`` is
    repointed at this tick's month. On a loss the canonical joblib is left
    untouched — the reigning champion stays. ``champion.json`` is rewritten every
    run with the current per-model champion blocks.

    ``month`` defaults to the current tick month.
    """
    month = resolve_tick_month(month)
    month_str = month.strftime("%Y-%m")

    cand_run_json = tick_model_training_run_json(month)
    if not cand_run_json.exists():
        raise FileNotFoundError(
            f"missing candidate manifest at {cand_run_json}; "
            f"run `python -m pipelines.monthly.train` first."
        )
    with open(cand_run_json) as f:
        candidate = json.load(f)

    incumbent: dict | None = None
    if CHAMPION_JSON.exists():
        with open(CHAMPION_JSON) as f:
            incumbent = json.load(f)

    decisions = _decide(candidate, incumbent)

    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    champion: dict = {}
    promoted: list[str] = []
    for k in MODEL_KEYS:
        d = decisions[k]
        cand_wmae = d["candidate_wmae"]
        if d["action"] == "promote":
            # Promote the candidate to canonical: copy the tick's joblib over the
            # champion weights predict loads, and repoint the pointer at this month.
            MODELS_DIR.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(tick_model_joblib(month, k), champion_joblib_for(k))
            champion[k] = {
                "month": month_str,
                "holdout_wmae": cand_wmae,
                "promoted_at": now_iso,
            }
            promoted.append(k)
        else:
            # Keep — canonical joblib untouched; carry the incumbent block forward
            # (falling back to the candidate's WMAE if the prior record lacked one).
            champion[k] = (incumbent or {}).get(
                k, {"month": month_str, "holdout_wmae": cand_wmae}
            )
        emoji = "↑" if d["action"] == "promote" else "·"
        logger.info("  %s %-12s %s — %s", emoji, k, d["action"], d["reason"])

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    with open(CHAMPION_JSON, "w") as f:
        json.dump(champion, f, indent=2)
    logger.info("wrote %s", CHAMPION_JSON)

    return {
        "action": "promoted" if promoted else "kept",
        "decisions": decisions,
        "month": month_str,
        "promoted": promoted,
    }


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
