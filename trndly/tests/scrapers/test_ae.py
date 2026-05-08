"""Unit tests for american_eagle_scraper.

AE is special: it requires a Playwright bootstrap to capture an Akamai-
validated header bundle (sec-ch-ua-*, JWT, etc.) before httpx fetches.
We don't run real Playwright in tests — the `ae_bootstrap_headers`
fixture is a pre-captured shape stand-in. Coverage:
  1. Mock _bootstrap_session to return the fixture; assert downstream
     httpx fetches use those headers.
  2. Pagination via mocked listing JSON (small total, single page).
  3. _combo_to_row → 18-column items contract.
  4. PDP fabric extraction (JSON path traversal + bullet picking).
  5. Resume semantics.
"""
from __future__ import annotations

import asyncio
import csv
from unittest.mock import patch

import httpx
import pytest

from pipelines.collectors import american_eagle_scraper as ae
from tests.conftest import ITEMS_CSV_FIELDNAMES


# --------------------------------------------------------------------------- #
# 1. Bootstrap mock                                                             #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_ae_bootstrap_session_can_be_mocked(ae_bootstrap_headers):
    """Mock _bootstrap_session to return our captured header bundle. This is
    the only 'unit-testable' surface for the bootstrap path — running real
    Playwright in tests is too slow/fragile."""
    fake_cookies = {"_abck": "FAKE_COOKIE"}
    async def fake_bootstrap(verbose=True):
        return ae_bootstrap_headers, fake_cookies

    with patch.object(ae, "_bootstrap_session", side_effect=fake_bootstrap):
        headers, cookies = await ae._bootstrap_session(verbose=False)
    assert headers["authorization"].startswith("Bearer ")
    assert headers["aesite"] == "AEO_US"
    assert cookies == fake_cookies


# --------------------------------------------------------------------------- #
# 2. Pagination                                                                 #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_ae_pagination_single_page_collects_all(
    httpx_mock,
    ae_listing_page1,
):
    """totalProducts=2, so single page request. 2 products × (2 + 1) swatches
    = 3 combos."""
    cat_id = "cat0001"
    httpx_mock.add_response(
        url=ae.LISTING_URL_TEMPLATE.format(cat_id=cat_id),
        match_params={"offset": "0"},
        json=ae_listing_page1,
    )

    target = {"cat_id": cat_id, "gender": "women", "label": "test"}
    sem = asyncio.Semaphore(2)
    async with httpx.AsyncClient() as client:
        combos, total = await ae._fetch_listings_for_target(
            client, target, sem, verbose=False,
        )

    assert total == 2
    assert len(combos) == 3
    keys = [(c["product_id"], c["cc_id"]) for c in combos]
    assert len(set(keys)) == len(keys)
    assert all(c["gender"] == "women" for c in combos)


# --------------------------------------------------------------------------- #
# 3. _combo_to_row                                                              #
# --------------------------------------------------------------------------- #

def test_ae_combo_to_row_emits_18_column_contract():
    combo = {
        "product_id": "0123456789",
        "name":       "Vintage Skinny Jeans",
        "url_path":   "/us/p/women/jeans/0123456789",
        "cc_id":      "01",
        "color_name": "Medium Vintage",
        "gender":     "women",
    }
    row = ae._combo_to_row(combo, scraped_at="2026-05-08T00:00:00",
                           retailer="american_eagle")

    assert list(row.keys()) == ITEMS_CSV_FIELDNAMES
    assert row["style_id"] == "0123456789"
    assert row["cc_id"] == "01"
    assert row["title"] == "Vintage Skinny Jeans"
    assert row["color_master_id"] == 2  # Medium Vintage → blue (denim)
    assert row["material_raw"] == "denim"
    assert row["product_type_id"] == 1  # Trousers (Jeans)
    assert row["gender_id"] == 1


def test_ae_combo_to_row_pdp_composition_overrides_title():
    combo = {
        "product_id": "X1",
        "name":       "Stretch Tee",
        "url_path":   "/us/p/X1",
        "cc_id":      "01",
        "color_name": "Black",
        "gender":     "women",
    }
    enriched = {"X1": "100% Polyester"}
    row = ae._combo_to_row(combo, scraped_at="2026-05-08T00:00:00",
                           retailer="american_eagle",
                           enriched_material_by_product=enriched)
    assert row["material_raw"] == "polyester"


# --------------------------------------------------------------------------- #
# 4. PDP fabric extraction                                                     #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_ae_pdp_fabric_extraction_picks_percentage_bullet(
    httpx_mock, ae_pdp_response,
):
    """AE PDP returns JSON with bullets list — pick the FIRST bullet that
    contains '%'. Skip 'Machine wash' / 'Imported'."""
    product_id = "0123456789"
    httpx_mock.add_response(
        url=ae.PRODUCT_URL_TEMPLATE.format(product_id=product_id),
        json=ae_pdp_response,
    )
    sem = asyncio.Semaphore(2)
    async with httpx.AsyncClient() as client:
        text = await ae._fetch_pdp_fabric(client, product_id, sem, verbose=False)
    assert "Cotton 78%" in text
    assert "Machine wash" not in text  # skipped (no %)


@pytest.mark.asyncio
async def test_ae_pdp_fabric_returns_empty_on_missing_keys(httpx_mock):
    """If the PDP JSON doesn't have the copySections.material.bullets path,
    return empty string (not raise)."""
    product_id = "999"
    httpx_mock.add_response(
        url=ae.PRODUCT_URL_TEMPLATE.format(product_id=product_id),
        json={"data": {"attributes": {}}},
    )
    sem = asyncio.Semaphore(2)
    async with httpx.AsyncClient() as client:
        text = await ae._fetch_pdp_fabric(client, product_id, sem, verbose=False)
    assert text == ""


# --------------------------------------------------------------------------- #
# 5. Resume semantics                                                           #
# --------------------------------------------------------------------------- #

def test_ae_resume_loads_existing_keys(tmp_path):
    final_path = tmp_path / "items_american_eagle.csv"
    partial_path = final_path.with_name("items_american_eagle_partial.csv")
    with partial_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=ITEMS_CSV_FIELDNAMES)
        writer.writeheader()
        for sid, cc in [("A1", "01"), ("A2", "02")]:
            row = {k: "" for k in ITEMS_CSV_FIELDNAMES}
            row.update({"scraped_at": "2026-01-01", "retailer": "american_eagle",
                        "style_id": sid, "cc_id": cc, "gender": "women",
                        "color_master_id": "1", "color_spectrum_id": "0",
                        "gender_id": "1", "product_type_id": "4",
                        "product_group_id": "1", "material_id": "1",
                        "graphical_appearance_id": "1"})
            writer.writerow(row)

    with ae.StreamingItemWriter(final_path, resume=True) as siw:
        assert siw.already_have("A1", "01", "women")
        assert siw.already_have("A2", "02", "women")
        assert not siw.already_have("A3", "03", "women")
