"""Tests for the monthly tick CLI driver — the per-month idempotency guard.

``run_full`` is a no-op when the tick's ``_SUCCESS`` marker already exists,
unless ``--force``. These tests stub every stage callable (so nothing touches
real data) and point the tick dir at a tmp tree, asserting which stages run.
"""
from __future__ import annotations

import pandas as pd
import pytest

from pipelines.monthly import cli


@pytest.fixture
def stub_stages(tmp_path, monkeypatch):
    """Redirect the tick tree to tmp_path and record which stages get called.

    Returns ``(calls, ticks_dir)`` where ``calls`` is the running list of stage
    names invoked through ``_call_stage``.
    """
    ticks = tmp_path / "ticks"
    ticks.mkdir()

    # Repoint every tick-path helper the CLI touches at the tmp tree.
    def _tick_dir(month):
        return ticks / pd.Timestamp(month).strftime("%Y-%m")

    monkeypatch.setattr(cli, "tick_success_marker", lambda m: _tick_dir(m) / "_SUCCESS")
    monkeypatch.setattr(cli, "tick_is_complete", lambda m: (_tick_dir(m) / "_SUCCESS").exists())
    monkeypatch.setattr(cli, "tick_manifest_json", lambda m: _tick_dir(m) / "manifest.json")

    calls: list[str] = []

    def _fake_call_stage(name, month):
        calls.append(name)
        return {"stage": name}

    monkeypatch.setattr(cli, "_call_stage", _fake_call_stage)
    # Don't shell out to git inside the hermetic test.
    monkeypatch.setattr(cli, "_git_sha", lambda: None)
    return calls, ticks


def test_run_full_runs_all_stages_when_no_success(stub_stages):
    calls, ticks = stub_stages
    summary = cli.run_full(month="2026-06")

    assert calls == list(cli.FULL_ORDER)
    assert "skipped" not in summary
    # The guard markers were written last.
    assert (ticks / "2026-06" / "_SUCCESS").exists()
    assert (ticks / "2026-06" / "manifest.json").exists()


def test_run_full_is_noop_when_success_present(stub_stages):
    calls, ticks = stub_stages
    # Simulate a completed tick.
    (ticks / "2026-06").mkdir()
    (ticks / "2026-06" / "_SUCCESS").touch()

    summary = cli.run_full(month="2026-06")

    assert summary == {"skipped": "2026-06"}
    assert calls == []  # nothing ran


def test_run_full_force_reruns_completed_tick(stub_stages):
    calls, ticks = stub_stages
    (ticks / "2026-06").mkdir()
    (ticks / "2026-06" / "_SUCCESS").touch()

    cli.run_full(month="2026-06", force=True)

    assert calls == list(cli.FULL_ORDER)  # all stages ran despite _SUCCESS


def test_run_full_skips_respect_flags(stub_stages):
    calls, _ = stub_stages
    cli.run_full(month="2026-06", skip_scrape=True, skip_build_cube=True)

    assert "scrape" not in calls
    assert "build_cube" not in calls
    # The remaining stages still run, in order.
    assert calls == [s for s in cli.FULL_ORDER if s not in ("scrape", "build_cube")]


def test_publish_is_after_predict_in_full_order():
    order = cli.FULL_ORDER
    assert order.index("publish") == order.index("predict") + 1
    assert order[-1] == "publish"
