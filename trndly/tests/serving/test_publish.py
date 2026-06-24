"""Golden-file parity test for the static publisher (the #1 lag-join risk).

``share_lag3/2/1/t`` are attached at serve/publish time by
``pipelines.serving._attach_lag_shares`` and are NOT covered by
``pipelines.contracts`` (see ``test_contracts_omit_lag_columns`` below). So this
golden-file diff is the authoritative gate that the lift out of scheduleServer
(and any future change to the lag-join) preserves the exact published JSON.

The golden files in ``fixtures/golden/`` were captured from the original
scheduleServer running on the FULL real merged cubes. The fixtures in
``fixtures/inputs/`` carry a losslessly TRIMMED ``merged_fingerprint`` (only the
5-D keys present in the predictions — the only rows a left-join can match), so a
green test simultaneously proves the lift is faithful and the trim is lossless.

Float tolerance on numeric leaves (the merge mean-pools duplicate (month, key)
rows, so re-derivation can differ by float noise); exact on strings/ids/bools.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipelines.contracts import (
    PREDICTIONS_FINGERPRINT_COLUMNS,
    PREDICTIONS_UNIVARIATE_COLUMNS,
)
from pipelines.monthly import publish

FIXTURES = Path(__file__).resolve().parent / "fixtures"
INPUTS = FIXTURES / "inputs"
GOLDEN = FIXTURES / "golden"

_NUM_TOL = 1e-6


def _fixture_payloads() -> dict:
    payloads, _anchor = publish.build_payloads(
        univariate_path=INPUTS / "predictions_univariate_2026-05.parquet",
        fingerprint_path=INPUTS / "predictions_fingerprint_2026-05.parquet",
        merged_univariate_path=INPUTS / "merged_univariate.parquet",
        merged_fingerprint_path=INPUTS / "merged_fingerprint.parquet",
        lookup_path=INPUTS / "lookup.csv",
    )
    return payloads


def _golden(name: str):
    return json.loads((GOLDEN / f"{name}.json").read_text())


def _assert_equal(actual, golden, path: str = "") -> None:
    """Deep compare with float tolerance on numeric leaves; exact otherwise."""
    if isinstance(golden, dict):
        assert isinstance(actual, dict), f"{path}: {type(actual).__name__} != dict"
        assert set(actual) == set(golden), (
            f"{path}: key mismatch — only_actual={set(actual) - set(golden)} "
            f"only_golden={set(golden) - set(actual)}"
        )
        for k in golden:
            _assert_equal(actual[k], golden[k], f"{path}.{k}")
    elif isinstance(golden, list):
        assert isinstance(actual, list), f"{path}: {type(actual).__name__} != list"
        assert len(actual) == len(golden), f"{path}: len {len(actual)} != {len(golden)}"
        for i, (a, g) in enumerate(zip(actual, golden)):
            _assert_equal(a, g, f"{path}[{i}]")
    elif isinstance(golden, bool) or isinstance(actual, bool):
        assert actual == golden, f"{path}: {actual!r} != {golden!r}"
    elif isinstance(golden, float) or isinstance(actual, float):
        if actual is None or golden is None:
            assert actual == golden, f"{path}: {actual!r} != {golden!r}"
        else:
            delta = abs(actual - golden)
            assert delta <= _NUM_TOL + _NUM_TOL * abs(golden), (
                f"{path}: {actual} != {golden} (Δ={delta})"
            )
    else:
        assert actual == golden, f"{path}: {actual!r} != {golden!r}"


@pytest.fixture(scope="module")
def payloads() -> dict:
    return _fixture_payloads()


def test_trends_matches_golden(payloads):
    _assert_equal(payloads["trends"], _golden("trends"), "trends")


def test_fingerprint_matches_golden(payloads):
    _assert_equal(payloads["fingerprint"], _golden("fingerprint"), "fingerprint")


def test_options_matches_golden(payloads):
    _assert_equal(payloads["options"], _golden("options"), "options")


def test_health_matches_golden(payloads):
    _assert_equal(payloads["health"], _golden("health"), "health")


def test_lag_columns_populated(payloads):
    """The lag-join actually attached shares (not all-null) for the canonical
    trends — guards against a silently empty join."""
    rows = payloads["trends"]
    assert rows, "no trend rows"
    assert any(r["share_t"] is not None for r in rows), "share_t entirely null"
    assert any(r["share_lag3"] is not None for r in rows), "share_lag3 entirely null"


def test_attach_lag_shares_mean_pools_duplicate_month_key():
    """Lock the #1-risk mean-pool: when the merged cube carries duplicate
    (month, key) rows (historical+live overlap), the attached lag is their
    arithmetic MEAN and the row count is preserved (no left-join fan-out).

    The golden fixtures are source-disjoint (no dups), so this is what actually
    discriminates .mean() from .sum() and from a missing dedup (which fans out /
    raises on real dup data). See pipelines.serving._attach_lag_shares.
    """
    import pandas as pd

    from pipelines.serving import _attach_lag_shares

    preds = pd.DataFrame([{
        "anchor_month": pd.Timestamp("2026-05-01"),
        "dimension": "color_master", "level_id": 1, "y_h1": 0.1,
    }])
    # Two rows for the SAME (2026-04, color_master, 1) with differing shares.
    merged = pd.DataFrame([
        {"month": pd.Timestamp("2026-04-01"), "dimension": "color_master",
         "level_id": 1, "share_articles": 0.10, "source": "historical"},
        {"month": pd.Timestamp("2026-04-01"), "dimension": "color_master",
         "level_id": 1, "share_articles": 0.30, "source": "live"},
    ])
    out = _attach_lag_shares(preds, merged, key_cols=["dimension", "level_id"])

    assert len(out) == len(preds), "left-join fanned out on duplicate (month,key)"
    # anchor − 1 == 2026-04 → mean(0.10, 0.30) == 0.20 (not 0.40 sum, not a dup row)
    assert out["share_lag1"].iloc[0] == pytest.approx(0.20)
    # No merged rows at anchor / anchor−2 / anchor−3 → NaN.
    assert pd.isna(out["share_t"].iloc[0])
    assert pd.isna(out["share_lag2"].iloc[0])


def test_attach_lag_shares_mean_pools_fingerprint_key():
    """Same mean-pool guarantee on the 5-D fingerprint key path."""
    import pandas as pd

    from pipelines.serving import FINGERPRINT_KEY_COLS, _attach_lag_shares

    key = {c: 1 for c in FINGERPRINT_KEY_COLS}
    preds = pd.DataFrame([{"anchor_month": pd.Timestamp("2026-05-01"), **key, "y_h1": 0.0}])
    merged = pd.DataFrame([
        {"month": pd.Timestamp("2026-02-01"), **key, "share_articles": 0.02, "source": "historical"},
        {"month": pd.Timestamp("2026-02-01"), **key, "share_articles": 0.04, "source": "live"},
    ])
    out = _attach_lag_shares(preds, merged, key_cols=list(FINGERPRINT_KEY_COLS))

    assert len(out) == len(preds)
    # anchor − 3 == 2026-02 → mean(0.02, 0.04) == 0.03.
    assert out["share_lag3"].iloc[0] == pytest.approx(0.03)


def test_contracts_omit_lag_columns():
    """Document/lock the reason this golden gate exists: contracts.py does NOT
    validate the serve-time lag columns, so only this diff catches lag-join drift."""
    for col in ("share_lag3", "share_lag2", "share_lag1", "share_t"):
        assert col not in PREDICTIONS_UNIVARIATE_COLUMNS
        assert col not in PREDICTIONS_FINGERPRINT_COLUMNS


def test_run_publish_writes_versioned_and_canonical(tmp_path):
    summary = publish.run_publish(
        out_dir=tmp_path,
        univariate_path=INPUTS / "predictions_univariate_2026-05.parquet",
        fingerprint_path=INPUTS / "predictions_fingerprint_2026-05.parquet",
        merged_univariate_path=INPUTS / "merged_univariate.parquet",
        merged_fingerprint_path=INPUTS / "merged_fingerprint.parquet",
        lookup_path=INPUTS / "lookup.csv",
    )
    assert summary["anchor_month"] == "2026-05"
    for name in ("trends", "fingerprint", "options", "health"):
        canonical = tmp_path / f"{name}.json"
        versioned = tmp_path / f"{name}_2026-05.json"
        assert canonical.exists() and versioned.exists()
        # Canonical is the latest pointer: byte-identical to the versioned file.
        assert canonical.read_text() == versioned.read_text()
    # The canonical trends.json the SPA fetches matches the golden.
    _assert_equal(
        json.loads((tmp_path / "trends.json").read_text()), _golden("trends"), "trends.json"
    )


# --------------------------------------------------------------------------- #
# Server parity — the slimmed scheduleServer must use the same shared logic and  #
# produce the same output as the publisher (and the golden).                     #
# --------------------------------------------------------------------------- #

def test_server_uses_shared_module_no_duplication():
    import backend.services.scheduleServer as srv
    from pipelines import serving

    # The server delegates to the shared builders — not a second copy of the logic.
    assert srv.build_trend_rows is serving.build_trend_rows
    assert srv.lookup_fingerprint is serving.lookup_fingerprint
    assert srv.build_health is serving.build_health
    assert srv.build_options is serving.build_options
    # The lag-join now lives only in pipelines.serving.
    assert not hasattr(srv, "_attach_lag_shares")


def test_server_handlers_match_golden(monkeypatch):
    import backend.services.scheduleServer as srv
    from pipelines import serving

    bundle, err = serving.load_bundle(
        univariate_path=INPUTS / "predictions_univariate_2026-05.parquet",
        fingerprint_path=INPUTS / "predictions_fingerprint_2026-05.parquet",
        merged_univariate_path=INPUTS / "merged_univariate.parquet",
        merged_fingerprint_path=INPUTS / "merged_fingerprint.parquet",
    )
    assert bundle is not None, err
    monkeypatch.setattr(srv, "BUNDLE", bundle)
    monkeypatch.setattr(srv, "BUNDLE_LOAD_ERROR", None)

    _assert_equal([r.model_dump() for r in srv.trends()], _golden("trends"), "server.trends")
    _assert_equal(srv.health().model_dump(), _golden("health"), "server.health")

    gold_fp = _golden("fingerprint")
    sample_key = next(iter(gold_fp))
    ids = dict(
        zip(
            ["product_type_id", "gender_id", "color_master_id",
             "graphical_appearance_id", "material_id"],
            [int(x) for x in sample_key.split("|")],
        )
    )
    _assert_equal(
        srv.forecast_fingerprint(**ids).model_dump(),
        gold_fp[sample_key],
        f"server.fingerprint[{sample_key}]",
    )
