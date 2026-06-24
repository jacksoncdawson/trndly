"""Tests for the persistent backfill cube + the fail-loud anchor guard (ADR 0002).

Covers the 2026-06 incident: an isolated live month (no 3 contiguous priors)
must NOT silently anchor on the historical tail (2020-08). The synthetic priors
are a persistent artifact unioned by ``aggregate``; ``predict`` fails loud if they
are absent.
"""
from __future__ import annotations

import pandas as pd
import pytest

from pipelines.monthly import aggregate
from pipelines.monthly.predict import _assert_fresh_anchor, _find_eligible_anchor


def _uv(month: str, source: str, n: int = 2) -> pd.DataFrame:
    """Tiny univariate-shaped frame for one month."""
    return pd.DataFrame({
        "month": pd.to_datetime([month + "-01"] * n),
        "dimension": ["color_master"] * n,
        "level_id": list(range(n)),
        "share_articles": [0.5] * n,
        "n_articles": [10] * n,
        "source": [source] * n,
    })


# --- the anchor mechanism: backfill bridges the gap ---

def test_isolated_live_month_anchors_on_history_without_backfill():
    # historical block 2020-06..2020-08 + an isolated 2026-05/06 live pair.
    cube = pd.concat([
        _uv("2020-05", "historical"), _uv("2020-06", "historical"),
        _uv("2020-07", "historical"), _uv("2020-08", "historical"),
        _uv("2026-05", "live"), _uv("2026-06", "live"),
    ], ignore_index=True)
    # No 3 contiguous priors for 2026-* → falls back to the historical tail.
    assert _find_eligible_anchor(cube).strftime("%Y-%m") == "2020-08"


def test_backfill_priors_make_latest_live_the_anchor():
    cube = pd.concat([
        _uv("2020-07", "historical"), _uv("2020-08", "historical"),
        _uv("2026-05", "live"), _uv("2026-06", "live"),
        # synthetic priors bridging the gap before the first live month
        _uv("2026-02", "backfill"), _uv("2026-03", "backfill"), _uv("2026-04", "backfill"),
    ], ignore_index=True)
    assert _find_eligible_anchor(cube).strftime("%Y-%m") == "2026-06"


# --- aggregate unions the persistent backfill artifact ---

def test_aggregate_unions_backfill(tmp_path):
    hist = tmp_path / "hist.parquet"
    live = tmp_path / "live_2026-05.parquet"
    backfill = tmp_path / "backfill.parquet"
    out = tmp_path / "merged.parquet"
    pd.concat([_uv("2020-07", "historical"), _uv("2020-08", "historical")],
              ignore_index=True).to_parquet(hist)
    _uv("2026-05", "live").to_parquet(live)
    pd.concat([_uv("2026-02", "backfill"), _uv("2026-03", "backfill"),
               _uv("2026-04", "backfill")], ignore_index=True).to_parquet(backfill)

    rows = aggregate._merge_one(
        historical_path=hist, live_paths=[live],
        dup_cols=["month", "dimension", "level_id", "source"],
        out_path=out, label="univariate", backfill_path=backfill,
    )
    merged = pd.read_parquet(out)
    months = {pd.Timestamp(m).strftime("%Y-%m") for m in merged["month"].unique()}
    assert {"2026-02", "2026-03", "2026-04", "2026-05"} <= months
    assert (merged["source"] == "backfill").sum() == 6  # 3 months × 2 rows
    assert rows == len(merged)


def test_aggregate_without_backfill_artifact_is_unaffected(tmp_path):
    hist = tmp_path / "hist.parquet"
    out = tmp_path / "merged.parquet"
    pd.concat([_uv("2020-07", "historical"), _uv("2020-08", "historical")],
              ignore_index=True).to_parquet(hist)
    # backfill_path points at a non-existent file → silently skipped.
    aggregate._merge_one(
        historical_path=hist, live_paths=[],
        dup_cols=["month", "dimension", "level_id", "source"],
        out_path=out, label="univariate", backfill_path=tmp_path / "nope.parquet",
    )
    merged = pd.read_parquet(out)
    assert (merged["source"] == "backfill").sum() == 0


# --- the fail-loud anchor guard ---

def test_guard_raises_on_stale_anchor(monkeypatch):
    monkeypatch.delenv("TRNDLY_ALLOW_STALE_ANCHOR", raising=False)
    cube = pd.concat([_uv("2020-08", "historical"), _uv("2026-06", "live")], ignore_index=True)
    with pytest.raises(RuntimeError, match="stale history"):
        _assert_fresh_anchor(pd.Timestamp("2020-08-01"), cube)


def test_guard_passes_when_anchor_is_latest_real(monkeypatch):
    monkeypatch.delenv("TRNDLY_ALLOW_STALE_ANCHOR", raising=False)
    cube = pd.concat([
        _uv("2026-04", "backfill"), _uv("2026-05", "live"), _uv("2026-06", "live"),
    ], ignore_index=True)
    # backfill rows are excluded from "latest real"; anchor == latest real → ok
    _assert_fresh_anchor(pd.Timestamp("2026-06-01"), cube)


def test_guard_ignores_backfill_rows_for_latest_real(monkeypatch):
    monkeypatch.delenv("TRNDLY_ALLOW_STALE_ANCHOR", raising=False)
    # A backfill row at 2026-07 must NOT count as the latest real month.
    cube = pd.concat([_uv("2026-06", "live"), _uv("2026-07", "backfill")], ignore_index=True)
    _assert_fresh_anchor(pd.Timestamp("2026-06-01"), cube)  # no raise


def test_guard_env_override(monkeypatch):
    monkeypatch.setenv("TRNDLY_ALLOW_STALE_ANCHOR", "1")
    cube = pd.concat([_uv("2020-08", "historical"), _uv("2026-06", "live")], ignore_index=True)
    _assert_fresh_anchor(pd.Timestamp("2020-08-01"), cube)  # override → no raise
