"""Unit tests for ``pipelines.monthly.evaluate`` — the decision rule and the
local champion guard (archive + revert-on-loss)."""
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
# Local champion guard (Phase 1.3): archive + revert-on-loss                    #
# --------------------------------------------------------------------------- #


@pytest.fixture
def guard_env(tmp_path, monkeypatch):
    """Point evaluate's paths at a tmp models dir. Returns the path handles."""
    models = tmp_path / "models"
    models.mkdir()
    uni = models / "univariate_model.joblib"
    fp = models / "fingerprint_model.joblib"
    runs = models / "runs"
    champ = models / "champion_metrics.json"
    cand = models / "model_training_run.json"
    monkeypatch.setattr(evaluate, "MODEL_RUNS_DIR", runs)
    monkeypatch.setattr(evaluate, "MODEL_TRAINING_RUN_JSON", cand)
    monkeypatch.setattr(evaluate, "CHAMPION_METRICS_JSON", champ)
    monkeypatch.setattr(evaluate, "_CANONICAL_JOBLIB", {"univariate": uni, "fingerprint": fp})
    return {"models": models, "uni": uni, "fp": fp, "runs": runs, "champ": champ, "cand": cand}


def _write_candidate(env, *, ts, uni_wmae, fp_wmae, uni_blob, fp_blob):
    """Simulate a train run: overwrite canonical joblibs + write the candidate manifest."""
    env["uni"].write_text(uni_blob)
    env["fp"].write_text(fp_blob)
    env["cand"].write_text(
        json.dumps(
            {
                "generated_at_utc": ts,
                "univariate": {"holdout_wmae": uni_wmae, "model_path": str(env["uni"])},
                "fingerprint": {"holdout_wmae": fp_wmae, "model_path": str(env["fp"])},
            }
        )
    )


def test_guard_first_run_promotes_and_archives(guard_env):
    env = guard_env
    _write_candidate(
        env, ts="2026-05-01T00:00:00+00:00", uni_wmae=0.01, fp_wmae=0.001,
        uni_blob="UNI-A", fp_blob="FP-A",
    )
    summary = run_evaluate()
    run_id = summary["run_id"]

    assert summary["action"] == "promoted"
    assert set(summary["promoted"]) == {"univariate", "fingerprint"}
    # Canonical weights untouched on a promotion.
    assert env["uni"].read_text() == "UNI-A"
    assert env["fp"].read_text() == "FP-A"
    # Run archive captured both joblibs + the manifest.
    archived = env["runs"] / run_id
    assert (archived / "univariate_model.joblib").read_text() == "UNI-A"
    assert (archived / "fingerprint_model.joblib").read_text() == "FP-A"
    assert (archived / "model_training_run.json").exists()
    # Champion record points each model at this run.
    champ = json.loads(env["champ"].read_text())
    assert champ["univariate"]["champion_run"] == run_id
    assert champ["fingerprint"]["champion_run"] == run_id


def test_guard_reverts_canonical_on_loss(guard_env):
    env = guard_env
    # Round 1: establish a champion at run A.
    _write_candidate(
        env, ts="2026-05-01T00:00:00+00:00", uni_wmae=0.01, fp_wmae=0.001,
        uni_blob="UNI-A", fp_blob="FP-A",
    )
    run_evaluate()

    # Round 2: train overwrote canonical with WORSE weights (B).
    _write_candidate(
        env, ts="2026-06-01T00:00:00+00:00", uni_wmae=0.02, fp_wmae=0.002,
        uni_blob="UNI-B", fp_blob="FP-B",
    )
    summary = run_evaluate()

    assert summary["action"] == "reverted"
    assert set(summary["reverted"]) == {"univariate", "fingerprint"}
    # Canonical reverted to the champion's (A) weights — predict won't load B.
    assert env["uni"].read_text() == "UNI-A"
    assert env["fp"].read_text() == "FP-A"
    # The losing run B's weights are still archived for the record.
    run_b = summary["run_id"]
    assert (env["runs"] / run_b / "univariate_model.joblib").read_text() == "UNI-B"


def test_guard_mixed_promote_one_revert_other(guard_env):
    env = guard_env
    _write_candidate(
        env, ts="2026-05-01T00:00:00+00:00", uni_wmae=0.01, fp_wmae=0.001,
        uni_blob="UNI-A", fp_blob="FP-A",
    )
    run_a = run_evaluate()["run_id"]

    # univariate improves (0.005 < 0.01), fingerprint regresses (0.002 > 0.001).
    _write_candidate(
        env, ts="2026-06-01T00:00:00+00:00", uni_wmae=0.005, fp_wmae=0.002,
        uni_blob="UNI-B", fp_blob="FP-B",
    )
    summary = run_evaluate()
    run_b = summary["run_id"]

    assert summary["promoted"] == ["univariate"]
    assert summary["reverted"] == ["fingerprint"]
    # univariate keeps the new (better) B weights; fingerprint reverts to A.
    assert env["uni"].read_text() == "UNI-B"
    assert env["fp"].read_text() == "FP-A"
    champ = json.loads(env["champ"].read_text())
    assert champ["univariate"]["champion_run"] == run_b
    assert champ["fingerprint"]["champion_run"] == run_a


def test_guard_unrevertable_keep_warns_and_holds(guard_env, caplog):
    """A champion_metrics.json predating the guard has no champion_run, so a loss
    can't be reverted — the canonical keeps the candidate's weights, with a warning."""
    env = guard_env
    # Pre-guard champion record: per-model blocks, NO champion_run, no runs archive.
    env["champ"].write_text(
        json.dumps(
            {
                "univariate": {"holdout_wmae": 0.01},
                "fingerprint": {"holdout_wmae": 0.001},
            }
        )
    )
    _write_candidate(
        env, ts="2026-06-01T00:00:00+00:00", uni_wmae=0.02, fp_wmae=0.002,
        uni_blob="UNI-B", fp_blob="FP-B",
    )
    import logging

    with caplog.at_level(logging.WARNING):
        summary = run_evaluate()

    assert summary["action"] == "unrevertable"
    assert summary["reverted"] == []
    assert set(summary["unrevertable"]) == {"univariate", "fingerprint"}
    # Cannot revert — canonical holds the candidate's weights; the champion record
    # now matches the deployed joblib (records reality so quality can self-heal).
    assert env["uni"].read_text() == "UNI-B"
    champ = json.loads(env["champ"].read_text())
    assert champ["univariate"]["holdout_wmae"] == 0.02
    assert champ["univariate"]["champion_run"] == summary["run_id"]
    assert "cannot revert" in caplog.text


def test_guard_records_reality_when_archive_missing(guard_env):
    """A real loss whose champion archive was deleted: can't revert, so record the
    deployed candidate as champion (not the prior champion's vanished WMAE), and
    self-heal when a better candidate arrives."""
    import shutil

    env = guard_env
    # Round 1: champion A.
    _write_candidate(
        env, ts="2026-05-01T00:00:00+00:00", uni_wmae=0.01, fp_wmae=0.001,
        uni_blob="UNI-A", fp_blob="FP-A",
    )
    run_a = run_evaluate()["run_id"]
    shutil.rmtree(env["runs"] / run_a)  # champion archive gone

    # Round 2: worse candidate; revert target is missing.
    _write_candidate(
        env, ts="2026-06-01T00:00:00+00:00", uni_wmae=0.02, fp_wmae=0.002,
        uni_blob="UNI-B", fp_blob="FP-B",
    )
    summary = run_evaluate()
    assert set(summary["unrevertable"]) == {"univariate", "fingerprint"}
    assert summary["reverted"] == []
    assert env["uni"].read_text() == "UNI-B"  # couldn't revert
    champ = json.loads(env["champ"].read_text())
    assert champ["univariate"]["holdout_wmae"] == 0.02  # record matches deployed reality

    # Round 3: a better candidate now promotes against the recorded 0.02 → self-heal.
    _write_candidate(
        env, ts="2026-07-01T00:00:00+00:00", uni_wmae=0.015, fp_wmae=0.0015,
        uni_blob="UNI-C", fp_blob="FP-C",
    )
    s3 = run_evaluate()
    assert "univariate" in s3["promoted"]
    assert env["uni"].read_text() == "UNI-C"
