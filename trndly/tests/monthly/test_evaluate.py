"""Unit tests for ``pipelines.monthly.evaluate._decide``."""
from __future__ import annotations

from pipelines.monthly.evaluate import _decide


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
