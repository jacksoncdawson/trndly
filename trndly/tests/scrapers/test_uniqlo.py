"""Unit tests for uniqlo_scraper.

Coverage matches gap_scraper test_gap.py:
  1. Pagination (single-page total ≤ 100 since fixture has 4 items).
  2. _combo_to_row → 18-column items contract.
  3. PDP composition extraction.
  4. Resume semantics.
"""
from __future__ import annotations

import asyncio
import csv

import httpx
import pytest

from pipelines.collectors import uniqlo_scraper as us
from tests.conftest import ITEMS_CSV_FIELDNAMES


# --------------------------------------------------------------------------- #
# 1. Pagination                                                                 #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_uniqlo_pagination_single_page_collects_all(
    httpx_mock,
    uniqlo_listing_page1,
):
    """4 items × 2 colors = 8 combos in a single page (since total ≤ 100)."""
    httpx_mock.add_response(
        url=us.API_URL,
        match_params={**us.API_BASE_PARAMS, "path": "22210,,,",
                      "genderId": "22210", "offset": "0",
                      "limit": str(us.API_PAGE_SIZE)},
        json=uniqlo_listing_page1,
    )

    target = {"genderId": "22210", "gender": "women", "label": "test"}
    sem = asyncio.Semaphore(2)
    async with httpx.AsyncClient() as client:
        combos, total = await us._fetch_listings_for_target(
            client, target, sem, verbose=False,
        )

    assert total == 4
    # 2 products × 2 colors each = 4 combos
    assert len(combos) == 4
    keys = [(c["product_id"], c["cc_code"]) for c in combos]
    assert len(set(keys)) == len(keys), "duplicate (product_id, cc_code) keys"


# --------------------------------------------------------------------------- #
# 2. _combo_to_row                                                              #
# --------------------------------------------------------------------------- #

def test_uniqlo_combo_to_row_emits_18_column_contract():
    combo = {
        "product_id":   "E482196-000",
        "l1_id":        "482196",
        "name":         "AIRism Seamless Hiphuggers",
        "cc_code":      "COL01",
        "cc_display":   "01",
        "color_name":   "WHITE",
        "color_filter": "WHITE",
        "gender":       "women",
    }
    row = us._combo_to_row(combo, scraped_at="2026-05-08T00:00:00",
                           retailer="uniqlo")

    assert list(row.keys()) == ITEMS_CSV_FIELDNAMES
    assert row["style_id"] == "E482196-000"
    assert row["cc_id"] == "482196-01"   # l1Id-displayCode
    assert row["title"] == "AIRism Seamless Hiphuggers"
    assert row["color_master_id"] == 3   # WHITE → 3
    assert row["product_type_id"] == 12  # Underwear bottom (hiphuggers)
    assert row["gender_id"] == 1         # women


def test_uniqlo_combo_to_row_uses_pdp_composition_when_provided():
    """PDP composition '96% Cotton, 4% Spandex' — percentage path picks Cotton."""
    combo = {
        "product_id":   "E999999-000",
        "l1_id":        "999999",
        "name":         "AIRism Mock Neck T-Shirt",
        "cc_code":      "COL01",
        "cc_display":   "01",
        "color_name":   "BLACK",
        "color_filter": "BLACK",
        "gender":       "women",
    }
    enriched = {"E999999-000": "96% Cotton, 4% Spandex"}
    row = us._combo_to_row(combo, scraped_at="2026-05-08T00:00:00",
                           retailer="uniqlo",
                           enriched_material_by_product=enriched)
    assert row["material_raw"] == "cotton"


# --------------------------------------------------------------------------- #
# 3. PDP composition extraction                                                #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_uniqlo_pdp_fabric_extraction(httpx_mock, uniqlo_pdp_html):
    product_id = "E482196-000"
    httpx_mock.add_response(
        url=us.PDP_URL_TEMPLATE.format(product_id=product_id),
        text=uniqlo_pdp_html,
    )
    sem = asyncio.Semaphore(2)
    async with httpx.AsyncClient() as client:
        text = await us._fetch_pdp_fabric(client, product_id, sem, verbose=False)
    assert "96% Cotton" in text


@pytest.mark.asyncio
async def test_uniqlo_pdp_fabric_returns_empty_when_no_match(httpx_mock):
    httpx_mock.add_response(
        url=us.PDP_URL_TEMPLATE.format(product_id="E000-000"),
        text="<html><body>no composition here</body></html>",
    )
    sem = asyncio.Semaphore(2)
    async with httpx.AsyncClient() as client:
        text = await us._fetch_pdp_fabric(client, "E000-000", sem, verbose=False)
    assert text == ""


# --------------------------------------------------------------------------- #
# 4. Resume semantics                                                           #
# --------------------------------------------------------------------------- #

def test_uniqlo_resume_loads_existing_keys(tmp_path):
    final_path = tmp_path / "items_uniqlo.csv"
    partial_path = final_path.with_name("items_uniqlo_partial.csv")
    with partial_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=ITEMS_CSV_FIELDNAMES)
        writer.writeheader()
        for sid, cc in [("E1-000", "111-01"), ("E2-000", "222-02")]:
            row = {k: "" for k in ITEMS_CSV_FIELDNAMES}
            row.update({"scraped_at": "2026-01-01", "retailer": "uniqlo",
                        "style_id": sid, "cc_id": cc, "gender": "women",
                        "color_master_id": "1", "color_spectrum_id": "0",
                        "gender_id": "1", "product_type_id": "4",
                        "product_group_id": "1", "material_id": "1",
                        "graphical_appearance_id": "1"})
            writer.writerow(row)

    with us.StreamingItemWriter(final_path, resume=True) as siw:
        assert siw.already_have("E1-000", "111-01", "women")
        assert siw.already_have("E2-000", "222-02", "women")
        assert not siw.already_have("E3-000", "333-03", "women")
