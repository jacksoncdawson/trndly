"""Unit tests for ``pipelines.monthly.evaluate`` — the decision rule and the
promote-copy flow (candidate joblib → canonical champion on a per-model win)."""
from __future__ import annotations

import json

import pytest

from pipelines.monthly import evaluate
from pipelines.monthly.evaluate import _decide, run_evaluate


def _manifest(uni_wmae: float, fp_wmae: float) -> dict:
    """Minimal manifest matching what train.py writes."""
    return {
        "univariate": {"holdout_wmae": uni_wmae},
        "fingerprint": {"holdout_wmae": fp_wmae},
    }


def test_decide_no_incumbent_promotes_both():
    candidate = _manifest(0.01, 0.001)
    decisions = _decide(candidate, None)
    assert decisions["univariate"]["action"] == "promote"
    assert decisions["fingerprint"]["action"] == "promote"
    assert decisions["univariate"]["incumbent_wmae"] is None
    assert decisions["fingerprint"]["incumbent_wmae"] is None


def test_decide_candidate_better_promotes():
    candidate = _manifest(0.005, 0.0005)   # better
    incumbent = _manifest(0.010, 0.0010)
    decisions = _decide(candidate, incumbent)
    assert decisions["univariate"]["action"] == "promote"
    assert decisions["fingerprint"]["action"] == "promote"


def test_decide_candidate_worse_keeps():
    candidate = _manifest(0.020, 0.0020)   # worse
    incumbent = _manifest(0.010, 0.0010)
    decisions = _decide(candidate, incumbent)
    assert decisions["univariate"]["action"] == "keep"
    assert decisions["fingerprint"]["action"] == "keep"


def test_decide_tie_promotes():
    """Tie goes to candidate (≤ rule)."""
    candidate = _manifest(0.010, 0.0010)
    incumbent = _manifest(0.010, 0.0010)
    decisions = _decide(candidate, incumbent)
    assert decisions["univariate"]["action"] == "promote"
    assert decisions["fingerprint"]["action"] == "promote"


def test_decide_independent_per_model():
    """One model wins, the other loses — decisions are independent."""
    candidate = _manifest(0.005, 0.0050)   # univariate better, fingerprint worse
    incumbent = _manifest(0.010, 0.0010)
    decisions = _decide(candidate, incumbent)
    assert decisions["univariate"]["action"] == "promote"
    assert decisions["fingerprint"]["action"] == "keep"


def test_decide_reads_nested_metrics_fallback():
    """Manifest using nested metrics shape (no top-level holdout_wmae) still works."""
    candidate = {
        "univariate": {
            "metrics": {"model": {"holdout": {"wmae_mean": 0.005}}},
        },
        "fingerprint": {
            "metrics": {"model": {"holdout": {"wmae_mean": 0.0005}}},
        },
    }
    decisions = _decide(candidate, None)
    assert decisions["univariate"]["candidate_wmae"] == 0.005
    assert decisions["fingerprint"]["candidate_wmae"] == 0.0005


# --------------------------------------------------------------------------- #
# Promote-copy flow (plan §12): candidate joblib → canonical champion on a win   #
# --------------------------------------------------------------------------- #


@pytest.fixture
def promote_env(tmp_path, monkeypatch):
    """Point evaluate's champion + tick paths at a tmp tree. Returns handles.

    The candidate joblib lives in this MONTH's tick model dir; the canonical
    champion joblibs + champion.json live in a tmp models dir. The guard copies
    files, never loads them, so small text-byte stand-ins suffice.
    """
    import pandas as pd

    models = tmp_path / "models"
    models.mkdir()
    canon_uni = models / "univariate_model.joblib"
    canon_fp = models / "fingerprint_model.joblib"
    champ = models / "champion.json"
    ticks = tmp_path / "ticks"

    monkeypatch.setattr(evaluate, "MODELS_DIR", models)
    monkeypatch.setattr(evaluate, "CHAMPION_JSON", champ)
    monkeypatch.setattr(
        evaluate, "champion_joblib_for",
        lambda role: canon_fp if role == "fingerprint" else canon_uni,
    )

    def _tick_model_dir(month):
        return ticks / pd.Timestamp(month).strftime("%Y-%m") / "model"

    def _tick_model_joblib(month, role):
        return _tick_model_dir(month) / f"{role}_model.joblib"

    def _tick_model_training_run_json(month):
        return _tick_model_dir(month) / "model_training_run.json"

    monkeypatch.setattr(evaluate, "tick_model_joblib", _tick_model_joblib)
    monkeypatch.setattr(
        evaluate, "tick_model_training_run_json", _tick_model_training_run_json
    )

    return {
        "canon_uni": canon_uni, "canon_fp": canon_fp, "champ": champ,
        "tick_model_dir": _tick_model_dir,
        "tick_model_joblib": _tick_model_joblib,
        "tick_model_training_run_json": _tick_model_training_run_json,
    }


def _write_candidate(env, *, month, uni_wmae, fp_wmae, uni_blob, fp_blob):
    """Simulate a train run for ``month``: write the candidate joblibs + manifest
    into that tick's model dir (canonical/champion is left for evaluate to set)."""
    env["tick_model_dir"](month).mkdir(parents=True, exist_ok=True)
    env["tick_model_joblib"](month, "univariate").write_text(uni_blob)
    env["tick_model_joblib"](month, "fingerprint").write_text(fp_blob)
    env["tick_model_training_run_json"](month).write_text(
        json.dumps(
            {
                "univariate": {"holdout_wmae": uni_wmae},
                "fingerprint": {"holdout_wmae": fp_wmae},
            }
        )
    )


def test_promote_first_run_copies_candidate_to_canonical(promote_env):
    """First run (no incumbent): both promote, candidate joblib → canonical, and
    champion.json records this month."""
    env = promote_env
    _write_candidate(
        env, month="2026-05", uni_wmae=0.01, fp_wmae=0.001,
        uni_blob="UNI-A", fp_blob="FP-A",
    )
    summary = run_evaluate("2026-05")

    assert summary["action"] == "promoted"
    assert set(summary["promoted"]) == {"univariate", "fingerprint"}
    assert summary["month"] == "2026-05"
    # Candidate weights copied into the canonical champion joblibs.
    assert env["canon_uni"].read_text() == "UNI-A"
    assert env["canon_fp"].read_text() == "FP-A"
    champ = json.loads(env["champ"].read_text())
    assert champ["univariate"]["month"] == "2026-05"
    assert champ["fingerprint"]["month"] == "2026-05"
    assert champ["univariate"]["holdout_wmae"] == 0.01


def test_promote_loss_keeps_canonical_unchanged(promote_env):
    """A per-model loss leaves the canonical joblib untouched (champion stays the
    prior month) and champion.json[role].month is unchanged."""
    env = promote_env
    # Pre-seed a strong incumbent champion (May) better than the June candidate.
    env["canon_uni"].write_text("UNI-MAY")
    env["canon_fp"].write_text("FP-MAY")
    env["champ"].write_text(
        json.dumps(
            {
                "univariate": {"month": "2026-05", "holdout_wmae": 0.005},
                "fingerprint": {"month": "2026-05", "holdout_wmae": 0.0005},
            }
        )
    )
    # June candidate is worse on both models → keep.
    _write_candidate(
        env, month="2026-06", uni_wmae=0.02, fp_wmae=0.002,
        uni_blob="UNI-JUN", fp_blob="FP-JUN",
    )
    summary = run_evaluate("2026-06")

    assert summary["action"] == "kept"
    assert summary["promoted"] == []
    # Canonical bytes unchanged — the May champion still serves.
    assert env["canon_uni"].read_text() == "UNI-MAY"
    assert env["canon_fp"].read_text() == "FP-MAY"
    champ = json.loads(env["champ"].read_text())
    assert champ["univariate"]["month"] == "2026-05"  # unchanged
    assert champ["fingerprint"]["month"] == "2026-05"


def test_promote_mixed_one_promote_one_keep(promote_env):
    """Mixed: univariate improves (promote → canonical updated, month=this month),
    fingerprint regresses (keep → canonical + month unchanged)."""
    env = promote_env
    env["canon_uni"].write_text("UNI-MAY")
    env["canon_fp"].write_text("FP-MAY")
    env["champ"].write_text(
        json.dumps(
            {
                "univariate": {"month": "2026-05", "holdout_wmae": 0.01},
                "fingerprint": {"month": "2026-05", "holdout_wmae": 0.0005},
            }
        )
    )
    # June: univariate better (0.005 < 0.01), fingerprint worse (0.002 > 0.0005).
    _write_candidate(
        env, month="2026-06", uni_wmae=0.005, fp_wmae=0.002,
        uni_blob="UNI-JUN", fp_blob="FP-JUN",
    )
    summary = run_evaluate("2026-06")

    assert summary["promoted"] == ["univariate"]
    # univariate canonical updated to June candidate; fingerprint untouched.
    assert env["canon_uni"].read_text() == "UNI-JUN"
    assert env["canon_fp"].read_text() == "FP-MAY"
    champ = json.loads(env["champ"].read_text())
    assert champ["univariate"]["month"] == "2026-06"   # promoted this month
    assert champ["fingerprint"]["month"] == "2026-05"  # kept prior month
