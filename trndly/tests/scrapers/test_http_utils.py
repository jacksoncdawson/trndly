"""Unit tests for the shared scraper HTTP/output helpers.

Covers:
  1. request_with_retry: 200 returns immediately, 429 retries then succeeds,
     non-retryable 404 returns None without sleeping, retries exhaust to None,
     custom retryable_statuses set unlocks 403 retry.
  2. StreamingItemWriter: writes header + rows, fresh-run clobbers stale
     partial, atomic rename on clean exit, partial preserved on exception,
     resume loads prior keys.
"""
from __future__ import annotations

import csv
from pathlib import Path

import httpx
import pytest

from pipelines.collectors._http_utils import (
    CSV_FIELDNAMES,
    DEFAULT_RETRYABLE_STATUSES,
    StreamingItemWriter,
    request_with_retry,
)


# --------------------------------------------------------------------------- #
# request_with_retry                                                            #
# --------------------------------------------------------------------------- #

@pytest.fixture
def no_sleep(monkeypatch):
    """Skip the asyncio.sleep so retries are instant in tests."""
    async def _instant(_):
        return None
    monkeypatch.setattr("pipelines.collectors._http_utils.asyncio.sleep", _instant)


@pytest.mark.asyncio
async def test_request_with_retry_returns_response_on_200(httpx_mock, no_sleep):
    httpx_mock.add_response(url="https://example.com/x", status_code=200, text="ok")
    async with httpx.AsyncClient() as client:
        resp = await request_with_retry(client, "https://example.com/x", verbose=False)
    assert resp is not None and resp.status_code == 200 and resp.text == "ok"


@pytest.mark.asyncio
async def test_request_with_retry_retries_then_succeeds(httpx_mock, no_sleep):
    httpx_mock.add_response(url="https://example.com/x", status_code=429)
    httpx_mock.add_response(url="https://example.com/x", status_code=429)
    httpx_mock.add_response(url="https://example.com/x", status_code=200, text="ok")
    async with httpx.AsyncClient() as client:
        resp = await request_with_retry(client, "https://example.com/x", verbose=False)
    assert resp is not None and resp.status_code == 200


@pytest.mark.asyncio
async def test_request_with_retry_non_retryable_returns_none(httpx_mock, no_sleep):
    # 404 is not in DEFAULT_RETRYABLE_STATUSES; should bail immediately, no retry.
    httpx_mock.add_response(url="https://example.com/x", status_code=404, text="nope")
    async with httpx.AsyncClient() as client:
        resp = await request_with_retry(client, "https://example.com/x", verbose=False)
    assert resp is None


@pytest.mark.asyncio
async def test_request_with_retry_exhausts_returns_none(httpx_mock, no_sleep):
    for _ in range(3):  # max_attempts=3, all 429
        httpx_mock.add_response(url="https://example.com/x", status_code=429)
    async with httpx.AsyncClient() as client:
        resp = await request_with_retry(
            client, "https://example.com/x", max_attempts=3, verbose=False
        )
    assert resp is None


@pytest.mark.asyncio
async def test_request_with_retry_403_unlocked_for_hollister_set(httpx_mock, no_sleep):
    """Hollister/AE pass DEFAULT_RETRYABLE_STATUSES | {403}. Default set
    treats 403 as non-retryable; the augmented set must retry it."""
    # Default set: 403 is non-retryable → bail immediately.
    httpx_mock.add_response(url="https://example.com/x", status_code=403, text="blocked")
    async with httpx.AsyncClient() as client:
        resp = await request_with_retry(client, "https://example.com/x", verbose=False)
    assert resp is None  # default set bails on 403

    # Augmented set: 403 retried, then succeeds.
    httpx_mock.add_response(url="https://example.com/y", status_code=403)
    httpx_mock.add_response(url="https://example.com/y", status_code=200, text="ok")
    async with httpx.AsyncClient() as client:
        resp = await request_with_retry(
            client,
            "https://example.com/y",
            verbose=False,
            retryable_statuses=DEFAULT_RETRYABLE_STATUSES | {403},
        )
    assert resp is not None and resp.status_code == 200


@pytest.mark.asyncio
async def test_request_with_retry_network_error_retries(httpx_mock, no_sleep):
    httpx_mock.add_exception(httpx.ConnectTimeout("boom"))
    httpx_mock.add_response(url="https://example.com/x", status_code=200, text="ok")
    async with httpx.AsyncClient() as client:
        resp = await request_with_retry(client, "https://example.com/x", verbose=False)
    assert resp is not None and resp.status_code == 200


# --------------------------------------------------------------------------- #
# StreamingItemWriter                                                           #
# --------------------------------------------------------------------------- #

def _row(style: str = "S1", cc: str = "C1", gender: str = "men") -> dict:
    """Build a fully-populated 18-column row."""
    return {k: "" for k in CSV_FIELDNAMES} | {
        "style_id": style, "cc_id": cc, "gender": gender,
    }


def test_writer_writes_header_and_rows_then_atomic_renames(tmp_path: Path):
    final = tmp_path / "items_test.csv"
    with StreamingItemWriter(final) as w:
        # Partial exists during the run.
        assert w.partial_path.exists()
        assert not final.exists()
        w.write(_row("S1", "C1", "men"))
        w.write(_row("S2", "C2", "women"))
    # Final exists after clean exit; partial gone.
    assert final.exists()
    assert not w.partial_path.exists()
    rows = list(csv.DictReader(final.open()))
    assert [r["style_id"] for r in rows] == ["S1", "S2"]
    assert list(rows[0].keys()) == CSV_FIELDNAMES


def test_writer_fresh_run_clobbers_stale_partial(tmp_path: Path):
    final = tmp_path / "items_test.csv"
    partial = final.with_name(final.stem + "_partial.csv")
    # Simulate a stale partial from a prior crashed run.
    partial.write_text("garbage,from,old,run\n")
    with StreamingItemWriter(final, resume=False) as w:
        w.write(_row("Snew", "Cnew", "men"))
    rows = list(csv.DictReader(final.open()))
    assert len(rows) == 1 and rows[0]["style_id"] == "Snew"


def test_writer_partial_preserved_on_exception(tmp_path: Path):
    final = tmp_path / "items_test.csv"
    with pytest.raises(RuntimeError):
        with StreamingItemWriter(final) as w:
            w.write(_row("S1", "C1", "men"))
            raise RuntimeError("boom")
    # Final not written; partial preserved for inspection / resume.
    assert not final.exists()
    assert w.partial_path.exists()
    rows = list(csv.DictReader(w.partial_path.open()))
    assert len(rows) == 1 and rows[0]["style_id"] == "S1"


def test_writer_resume_loads_prior_keys(tmp_path: Path):
    final = tmp_path / "items_test.csv"
    partial = final.with_name(final.stem + "_partial.csv")
    # Pre-seed a partial with two rows from a "prior run".
    with partial.open("w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES)
        wr.writeheader()
        wr.writerow(_row("S1", "C1", "men"))
        wr.writerow(_row("S2", "C2", "women"))

    with StreamingItemWriter(final, resume=True) as w:
        assert w.already_have("S1", "C1", "men")
        assert w.already_have("S2", "C2", "women")
        # Different gender for same SKU is a distinct key (unisex case).
        assert not w.already_have("S1", "C1", "women")
        # Append a new row; resume preserves both old rows.
        w.write(_row("S3", "C3", "men"))
    rows = list(csv.DictReader(final.open()))
    assert [r["style_id"] for r in rows] == ["S1", "S2", "S3"]
