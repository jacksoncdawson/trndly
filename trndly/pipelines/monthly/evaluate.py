"""Compare candidate vs incumbent; promote champion if candidate is better.

Reads:
    data/models/model_training_run.json   (candidate manifest, written by train.py)
    data/models/champion_metrics.json     (incumbent record, written by a prior
                                          evaluate run; absent on first run)

Writes:
    data/models/runs/<run_id>/            (this run's archived joblibs + manifest)
    data/models/champion_metrics.json     (per-model champion record)
    data/models/{univariate,fingerprint}_model.joblib  (reverted on a candidate loss)

Promotion rule (per model independently — univariate, fingerprint):
    candidate.holdout_wmae <= incumbent.holdout_wmae  → promote (keep candidate weights)
    no incumbent recorded                              → promote
    else                                               → keep incumbent + REVERT joblib

Local champion guard
--------------------
train.py always overwrites the canonical joblibs with the *candidate's* weights.
Without intervention, a candidate that LOSES would still have its worse weights
loaded by predict.py and baked into the published, CDN-cached forecasts for a
month. So this stage:

  1. Archives each run's canonical joblibs to ``data/models/runs/<run_id>/``.
  2. On a per-model loss, reverts that canonical joblib to the prior champion's
     archived weights (tracked as ``champion_run`` in champion_metrics.json).

MLflow-independent and SUPERSEDED by Phase 4's ``champion`` registry alias once
the private MLflow lands. When a revert can't complete (a champion_metrics.json
predating this guard with no ``champion_run``, or an archived run that was
deleted), the canonical joblib still holds the candidate's weights, so we record
the candidate as champion (matching the deployed joblib) rather than advertising
a champion whose weights no longer exist — quality self-heals on the next
promotion. Such models are reported under ``unrevertable`` in the summary.

Usage:
    python -m pipelines.monthly.evaluate
"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

from pipelines.paths import (
    FINGERPRINT_MODEL_JOBLIB,
    MODEL_RUNS_DIR,
    MODEL_TRAINING_RUN_JSON,
    MODELS_DIR,
    UNIVARIATE_MODEL_JOBLIB,
)

logger = logging.getLogger(__name__)

CHAMPION_METRICS_JSON: Path = MODELS_DIR / "champion_metrics.json"

MODEL_KEYS: tuple[str, ...] = ("univariate", "fingerprint")

# Canonical joblib per model key. Referenced via this module-level mapping so a
# test can monkeypatch ``evaluate._CANONICAL_JOBLIB`` to a tmp dir.
_CANONICAL_JOBLIB: dict[str, Path] = {
    "univariate": UNIVARIATE_MODEL_JOBLIB,
    "fingerprint": FINGERPRINT_MODEL_JOBLIB,
}


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


def _run_id(manifest: dict) -> str:
    """Filesystem-safe, sortable run id from the manifest's ``generated_at_utc``.

    ``2026-05-10T22:12:19+00:00`` → ``2026-05-10T221219Z``.
    """
    ts = str(manifest.get("generated_at_utc") or datetime.now(timezone.utc).isoformat())
    return ts.replace(":", "").replace("+0000", "Z").replace("+00:00", "Z")


def _archive_run(run_id: str) -> Path:
    """Copy the current canonical joblibs + candidate manifest into the run archive.

    The canonical joblibs at this point are the candidate's (train.py just wrote
    them), so this captures the candidate's weights under ``runs/<run_id>/``.
    """
    run_dir = MODEL_RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    for src in _CANONICAL_JOBLIB.values():
        if src.exists():
            shutil.copyfile(src, run_dir / src.name)
    if MODEL_TRAINING_RUN_JSON.exists():
        shutil.copyfile(MODEL_TRAINING_RUN_JSON, run_dir / MODEL_TRAINING_RUN_JSON.name)
    return run_dir


def _revert_canonical(model_key: str, champion_run: str | None) -> bool:
    """Restore the canonical joblib for ``model_key`` from the champion's archived
    weights. Returns True on success; False (with a warning) when the archive is
    unavailable (e.g. a champion_metrics.json predating this guard)."""
    canonical = _CANONICAL_JOBLIB[model_key]
    if not champion_run:
        logger.warning(
            "cannot revert %s: prior champion has no archived run "
            "(champion_metrics.json predates the guard); canonical joblib keeps "
            "the losing candidate's weights until the next promotion.",
            model_key,
        )
        return False
    archived = MODEL_RUNS_DIR / champion_run / canonical.name
    if not archived.exists():
        logger.warning(
            "cannot revert %s: archived champion joblib missing at %s; "
            "canonical keeps the losing candidate's weights.",
            model_key, archived,
        )
        return False
    shutil.copyfile(archived, canonical)
    logger.info("reverted %s canonical joblib ← %s", model_key, archived)
    return True


def run_evaluate() -> dict:
    """Compare candidate vs incumbent per model; promote winners, revert losers.

    The canonical joblib for a model that LOSES is reverted to the prior
    champion's archived weights so predict.py never loads worse weights than the
    champion. The champion record is rewritten every run with the per-model
    champion blocks (and each block's ``champion_run`` archive pointer).
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

    # 1. Archive this run's canonical joblibs (the candidate's, written by train).
    run_id = _run_id(candidate)
    _archive_run(run_id)

    # 2. Per-model: keep winners' canonical weights, revert losers; assemble the
    #    per-model champion record.
    champion: dict = {
        "champion_updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "last_candidate_run": run_id,
    }
    promoted: list[str] = []
    reverted: list[str] = []
    unrevertable: list[str] = []
    for k in MODEL_KEYS:
        d = decisions[k]
        if d["action"] == "promote":
            block = dict(candidate[k])
            block["champion_run"] = run_id
            champion[k] = block
            promoted.append(k)
        else:
            prior_run = (incumbent or {}).get(k, {}).get("champion_run")
            if _revert_canonical(k, prior_run):
                reverted.append(k)
                # Champion unchanged — carry the prior champion's block forward.
                champion[k] = (incumbent or {}).get(k, candidate[k])
            else:
                # The prior champion's weights are unrecoverable (missing archive,
                # or a pre-guard incumbent with no champion_run). train.py already
                # overwrote the canonical joblib, so the candidate's (losing)
                # weights are what predict.py will load. Record THAT as champion so
                # champion_metrics.json matches the deployed joblib and the next
                # better candidate promotes (quality self-heals) — rather than
                # advertising a prior champion whose weights no longer exist.
                logger.warning(
                    "%s: prior champion weights unrecoverable; recording the "
                    "(unreverted) candidate as champion to match the deployed joblib.",
                    k,
                )
                block = dict(candidate[k])
                block["champion_run"] = run_id
                champion[k] = block
                unrevertable.append(k)
        emoji = "↑" if d["action"] == "promote" else "·"
        logger.info("  %s %-12s %s — %s", emoji, k, d["action"], d["reason"])

    # 3. Write the per-model champion record (always — it reflects the current
    #    per-model champions, not just "any promotion happened").
    CHAMPION_METRICS_JSON.parent.mkdir(parents=True, exist_ok=True)
    with open(CHAMPION_METRICS_JSON, "w") as f:
        json.dump(champion, f, indent=2)
    logger.info("wrote %s", CHAMPION_METRICS_JSON)

    if promoted:
        action = "promoted"
    elif reverted:
        action = "reverted"
    elif unrevertable:
        action = "unrevertable"
    else:
        action = "kept"
    return {
        "action": action,
        "decisions": decisions,
        "run_id": run_id,
        "promoted": promoted,
        "reverted": reverted,
        "unrevertable": unrevertable,
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
