"""Unit tests for gap_scraper.

Coverage matrix (per the Phase 2 plan):
  1. Pagination: 3-page mock → all combos collected, no duplicates.
  2. _combo_to_row: synthetic combo → 18-column items row contract.
  3. PDP fabric extraction: synthetic Fabric & care HTML → bullets text.
  4. Resume semantics: partial CSV pre-seeds the seen-keys set.

Mocked HTTP via pytest-httpx; no real network. AE bootstrap is skipped
in this file (Gap doesn't need Playwright).
"""
from __future__ import annotations

import asyncio
import csv
from pathlib import Path

import httpx
import pytest

from pipelines.collectors import gap_scraper as gs
from tests.conftest import ITEMS_CSV_FIELDNAMES


# --------------------------------------------------------------------------- #
# 1. Pagination                                                                 #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_gap_pagination_collects_all_pages_no_dupes(
    httpx_mock,
    gap_listing_page1,
    gap_listing_page2,
    gap_listing_page3,
):
    """Feed a 3-page listing API mock; assert the full set of (style, color)
    combos comes back, deduped per-target."""
    # Match by query param (pageNumber) so each page response is keyed
    # to its request — order-independent, robust to concurrency.
    httpx_mock.add_response(
        url=gs.API_URL,
        match_params={**gs.API_BASE_PARAMS, "cid": "TEST", "department": "999",
                      "pageSize": str(gs.API_PAGE_SIZE), "pageNumber": "0"},
        json=gap_listing_page1,
    )
    httpx_mock.add_response(
        url=gs.API_URL,
        match_params={**gs.API_BASE_PARAMS, "cid": "TEST", "department": "999",
                      "pageSize": str(gs.API_PAGE_SIZE), "pageNumber": "1"},
        json=gap_listing_page2,
    )
    httpx_mock.add_response(
        url=gs.API_URL,
        match_params={**gs.API_BASE_PARAMS, "cid": "TEST", "department": "999",
                      "pageSize": str(gs.API_PAGE_SIZE), "pageNumber": "2"},
        json=gap_listing_page3,
    )

    target = {"cid": "TEST", "department": "999", "gender": "men", "label": "test"}
    sem = asyncio.Semaphore(3)
    async with httpx.AsyncClient() as client:
        combos, total = await gs._fetch_listings_for_target(
            client, target, sem, verbose=False,
        )

    assert total == 6, f"expected totalColors=6, got {total}"
    assert len(combos) == 6, f"expected 6 combos, got {len(combos)}"
    keys = [(c["style_id"], c["cc_id"]) for c in combos]
    assert len(set(keys)) == len(keys), "duplicate (style_id, cc_id) keys"
    assert all(c["gender"] == "men" for c in combos)


# --------------------------------------------------------------------------- #
# 2. _combo_to_row                                                              #
# --------------------------------------------------------------------------- #

def test_gap_combo_to_row_emits_18_column_contract():
    """Feed a synthetic combo dict; assert the row matches the 18-column
    items.csv schema with sane IDs resolved via feature_lookups."""
    combo = {
        "style_id":             "100001",
        "cc_id":                "100001-001",
        "style_name":           "Vintage Classic T-Shirt",
        "web_product_type":     "mens tops",
        "cc_name":              "Black",
        "cc_short_description": "True black",
        "gender":               "men",
    }
    row = gs._combo_to_row(combo, scraped_at="2026-05-08T00:00:00", retailer="gap")

    assert list(row.keys()) == ITEMS_CSV_FIELDNAMES
    assert row["style_id"] == "100001"
    assert row["cc_id"] == "100001-001"
    assert row["title"] == "Vintage Classic T-Shirt"
    assert row["gender"] == "men"
    assert row["color_raw"] == "True black"
    assert row["material_raw"] == "cotton"           # default for tops
    assert row["product_type_raw"] == "T-shirt"      # title keyword
    assert row["color_master_id"] == 1               # black
    assert row["gender_id"] == 3                     # men
    assert row["product_type_id"] == 4               # T-shirt
    # No NaN in any column
    assert not any(v is None for v in row.values())


def test_gap_combo_to_row_uses_pdp_material_when_provided():
    """When PDP enrichment supplies a fabric string, _combo_to_row should
    prefer it over the title-keyword fallback."""
    combo = {
        "style_id":             "100002",
        "cc_id":                "100002-002",
        "style_name":           "Slim-Fit Chino Pants",
        "web_product_type":     "mens pants",
        "cc_name":              "Khaki",
        "cc_short_description": "Light khaki",
        "gender":               "men",
    }
    enriched = {"100002": "98% Polyester, 2% Elastane"}
    row = gs._combo_to_row(combo, scraped_at="2026-05-08T00:00:00",
                           retailer="gap", enriched_material_by_style=enriched)
    assert row["material_raw"] == "polyester"


# --------------------------------------------------------------------------- #
# 3. PDP fabric extraction                                                     #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_gap_pdp_fabric_extraction_pulls_bullets(
    httpx_mock,
    gap_pdp_html,
):
    """Mock a PDP HTML response; assert _fetch_pdp_fabric extracts the
    Fabric & care bullets text (joined with spaces)."""
    style_id = "100099"
    httpx_mock.add_response(
        url=gs.PDP_URL_TEMPLATE.format(style_id=style_id),
        text=gap_pdp_html,
    )
    sem = asyncio.Semaphore(2)
    async with httpx.AsyncClient() as client:
        text = await gs._fetch_pdp_fabric(client, style_id, sem, verbose=False)
    assert "98% Cotton" in text
    assert "Machine wash cold" in text


@pytest.mark.asyncio
async def test_gap_pdp_fabric_returns_empty_when_no_match(httpx_mock):
    """If the PDP HTML lacks a Fabric & care block, return empty string
    (never None — caller treats both the same)."""
    httpx_mock.add_response(
        url=gs.PDP_URL_TEMPLATE.format(style_id="999"),
        text="<html><body>no fabric here</body></html>",
    )
    sem = asyncio.Semaphore(2)
    async with httpx.AsyncClient() as client:
        text = await gs._fetch_pdp_fabric(client, "999", sem, verbose=False)
    assert text == ""


# --------------------------------------------------------------------------- #
# 4. Resume semantics                                                           #
# --------------------------------------------------------------------------- #

def test_gap_resume_loads_existing_keys_from_partial_csv(tmp_path):
    """A pre-existing items_gap_partial.csv should pre-seed the writer's
    `_existing` set so already_have(style, cc, gender) returns True for
    those keys."""
    final_path = tmp_path / "items_gap.csv"
    partial_path = final_path.with_name("items_gap_partial.csv")

    # Simulate a half-finished previous run.
    with partial_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=ITEMS_CSV_FIELDNAMES)
        writer.writeheader()
        for sid, cc in [("S1", "C1"), ("S2", "C2"), ("S3", "C3")]:
            row = {k: "" for k in ITEMS_CSV_FIELDNAMES}
            row.update({
                "scraped_at": "2026-01-01", "retailer": "gap",
                "style_id": sid, "cc_id": cc, "gender": "men",
                "color_master_id": "1", "color_spectrum_id": "0",
                "gender_id": "3", "product_type_id": "4",
                "product_group_id": "1", "material_id": "1",
                "graphical_appearance_id": "1",
            })
            writer.writerow(row)

    with gs.StreamingItemWriter(final_path, resume=True) as siw:
        assert siw.already_have("S1", "C1", "men")
        assert siw.already_have("S2", "C2", "men")
        assert siw.already_have("S3", "C3", "men")
        assert not siw.already_have("S4", "C4", "men")
        assert not siw.already_have("S1", "C1", "women")  # different gender


def test_gap_resume_off_clobbers_partial_csv(tmp_path):
    """Without --resume, the writer treats any pre-existing partial CSV
    as garbage to overwrite — `_existing` starts empty."""
    final_path = tmp_path / "items_gap.csv"
    partial_path = final_path.with_name("items_gap_partial.csv")
    partial_path.write_text("garbage\n")

    with gs.StreamingItemWriter(final_path, resume=False) as siw:
        assert not siw.already_have("S1", "C1", "men")
        assert siw._existing == set()
    # Partial was clobbered — only contains the new header now.
    assert "garbage" not in partial_path.read_text() if partial_path.exists() else True
