"""Tests for the scrape-completeness guard (``pipelines.monthly.scrape``).

Regression cover for the 2026-06 Hollister incident: a retailer scrape that
silently collapses to a header-only CSV (or a steep drop vs the prior month)
must abort the tick rather than letting the bad month reach publish.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from pipelines.monthly import scrape


def _write_csv(path: Path, n_rows: int) -> None:
    """Write an items-style CSV with a header + ``n_rows`` data rows."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["col_a,col_b"] + ["x,y"] * n_rows
    path.write_text("\n".join(lines) + "\n")


def _patch_dirs(monkeypatch, tmp_path: Path, month: str = "2026-06") -> None:
    """Point the guard at ``tmp_path`` and make the 'current' scrape file resolve
    to ``items_<retailer>_<month>.csv`` there."""
    monkeypatch.setattr(scrape, "RAW_ITEMS_DIR", tmp_path)
    monkeypatch.setattr(
        scrape, "items_csv_path_for", lambda r: tmp_path / f"items_{r}_{month}.csv"
    )


def test_count_data_rows(tmp_path):
    p = tmp_path / "items_gap_2026-06.csv"
    _write_csv(p, 0)  # header only
    assert scrape._count_data_rows(p) == 0
    _write_csv(p, 5)
    assert scrape._count_data_rows(p) == 5
    assert scrape._count_data_rows(tmp_path / "missing.csv") == 0


def test_passes_on_healthy_scrape(tmp_path, monkeypatch):
    _patch_dirs(monkeypatch, tmp_path)
    _write_csv(tmp_path / "items_gap_2026-05.csv", 100)
    _write_csv(tmp_path / "items_gap_2026-06.csv", 95)
    scrape._check_scrape_completeness("gap")  # no raise


def test_raises_on_zero_rows(tmp_path, monkeypatch):
    """The exact incident: header-only current file aborts the tick."""
    _patch_dirs(monkeypatch, tmp_path)
    _write_csv(tmp_path / "items_hollister_2026-05.csv", 20788)
    _write_csv(tmp_path / "items_hollister_2026-06.csv", 0)
    with pytest.raises(RuntimeError, match="header-only"):
        scrape._check_scrape_completeness("hollister")


def test_raises_on_collapse_vs_prior(tmp_path, monkeypatch):
    _patch_dirs(monkeypatch, tmp_path)
    _write_csv(tmp_path / "items_gap_2026-05.csv", 100)
    _write_csv(tmp_path / "items_gap_2026-06.csv", 50)  # 50% < 60% threshold
    with pytest.raises(RuntimeError, match="below"):
        scrape._check_scrape_completeness("gap")


def test_passes_with_no_prior_month(tmp_path, monkeypatch):
    _patch_dirs(monkeypatch, tmp_path)
    _write_csv(tmp_path / "items_newco_2026-06.csv", 30)  # first ever month
    scrape._check_scrape_completeness("newco")  # no raise


def test_collapse_allowed_when_threshold_lowered(tmp_path, monkeypatch):
    _patch_dirs(monkeypatch, tmp_path)
    _write_csv(tmp_path / "items_gap_2026-05.csv", 100)
    _write_csv(tmp_path / "items_gap_2026-06.csv", 50)
    monkeypatch.setattr(scrape, "MIN_SCRAPE_RETAIN_FRAC", 0.4)  # 50% >= 40%
    scrape._check_scrape_completeness("gap")  # no raise


def test_zero_rows_fatal_even_with_lowered_threshold(tmp_path, monkeypatch):
    """0 rows is always fatal — the threshold override cannot permit empty."""
    _patch_dirs(monkeypatch, tmp_path)
    _write_csv(tmp_path / "items_gap_2026-06.csv", 0)
    monkeypatch.setattr(scrape, "MIN_SCRAPE_RETAIN_FRAC", 0.0)
    with pytest.raises(RuntimeError, match="header-only"):
        scrape._check_scrape_completeness("gap")
