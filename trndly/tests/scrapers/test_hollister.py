"""Unit tests for hollister_scraper.

Hollister is unique: no JSON API. Catalog is in SSR HTML's Apollo state.
Test coverage:
  1. _parse_apollo_state: feed an HTML blob, assert state extracted.
  2. _extract_combos_from_apollo: state → combos, total, pages.
  3. _combo_to_row → 18-column items contract.
  4. PDP fabric extraction.
  5. Apollo-state parsing failure modes (no marker, malformed JSON).
  6. Resume semantics.
"""
from __future__ import annotations

import csv

import pytest

from pipelines.collectors import hollister_scraper as hs
from tests.conftest import ITEMS_CSV_FIELDNAMES


# --------------------------------------------------------------------------- #
# 1. + 2. Apollo-state parsing                                                  #
# --------------------------------------------------------------------------- #

def test_hollister_parse_apollo_state_extracts_object(hollister_apollo_html):
    state = hs._parse_apollo_state(hollister_apollo_html)
    assert state is not None
    assert "CACHE" in state
    assert "ROOT_QUERY" in state["CACHE"]


def test_hollister_parse_apollo_state_returns_none_when_marker_missing():
    """Catches the failure mode where Hollister renames the Apollo prefix."""
    html = "<html><body><script>window['SOMETHING_ELSE'] = {};</script></body></html>"
    assert hs._parse_apollo_state(html) is None


def test_hollister_parse_apollo_state_returns_none_when_json_malformed():
    html = (
        "<html><body><script>window['"
        + hs.APOLLO_STATE_PREFIX
        + "'] = {malformed: not valid json};</script></body></html>"
    )
    assert hs._parse_apollo_state(html) is None


def test_hollister_extract_combos_from_apollo(hollister_apollo_html):
    state = hs._parse_apollo_state(hollister_apollo_html)
    combos, total, pages = hs._extract_combos_from_apollo(state, "women")
    # 2 products in the fixture; H001 has 2 swatches, H002 has 1 → 3 combos
    assert total == 2
    assert pages == 1
    assert len(combos) == 3
    # Per-target dedup on (product_id, cc_id) — assert no duplicates
    keys = [(c["product_id"], c["cc_id"]) for c in combos]
    assert len(set(keys)) == len(keys)
    assert all(c["gender"] == "women" for c in combos)


# --------------------------------------------------------------------------- #
# 3. _combo_to_row                                                              #
# --------------------------------------------------------------------------- #

def test_hollister_combo_to_row_emits_18_column_contract():
    combo = {
        "product_id": "H001",
        "name":       "Vintage Stretch Skinny Jeans",
        "url_path":   "/shop/us/p/vintage-stretch-skinny-jeans-H001",
        "cc_id":      "H001-MED",
        "color_name": "Medium Wash",
        "gender":     "women",
    }
    row = hs._combo_to_row(combo, scraped_at="2026-05-08T00:00:00",
                           retailer="hollister")

    assert list(row.keys()) == ITEMS_CSV_FIELDNAMES
    assert row["style_id"] == "H001"
    assert row["cc_id"] == "H001-MED"
    assert row["title"] == "Vintage Stretch Skinny Jeans"
    assert row["color_raw"] == "Medium Wash"
    assert row["product_type_id"] == 1   # Trousers (jeans → Trousers)
    assert row["material_raw"] == "denim"
    assert row["color_master_id"] == 2   # blue (denim wash)
    assert row["gender_id"] == 1


# --------------------------------------------------------------------------- #
# 4. PDP fabric extraction                                                     #
# --------------------------------------------------------------------------- #

def test_hollister_pdp_fabric_extraction(hollister_pdp_html):
    """Fabric extraction is pure parsing over PDP HTML. The PDP fetch now goes
    through the browser (Akamai-protected, not httpx), so we unit-test the parser
    `_parse_fabric_from_html` directly against the fixture HTML."""
    fabric = hs._parse_fabric_from_html(hollister_pdp_html)
    assert "60% Cotton" in fabric
    assert "40% Polyester" in fabric


def test_hollister_pdp_fabric_extraction_miss():
    """No fabricDetails (or no percentage) → empty string, not a crash."""
    assert hs._parse_fabric_from_html("<html><body>no fabric here</body></html>") == ""


# --------------------------------------------------------------------------- #
# 5. Resume semantics                                                           #
# --------------------------------------------------------------------------- #

def test_hollister_resume_loads_existing_keys(tmp_path):
    final_path = tmp_path / "items_hollister.csv"
    partial_path = final_path.with_name("items_hollister_partial.csv")
    with partial_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=ITEMS_CSV_FIELDNAMES)
        writer.writeheader()
        for sid, cc in [("H1", "H1-A"), ("H2", "H2-B")]:
            row = {k: "" for k in ITEMS_CSV_FIELDNAMES}
            row.update({"scraped_at": "2026-01-01", "retailer": "hollister",
                        "style_id": sid, "cc_id": cc, "gender": "women",
                        "color_master_id": "1", "color_spectrum_id": "0",
                        "gender_id": "1", "product_type_id": "4",
                        "product_group_id": "1", "material_id": "1",
                        "graphical_appearance_id": "1"})
            writer.writerow(row)

    with hs.StreamingItemWriter(final_path, resume=True) as siw:
        assert siw.already_have("H1", "H1-A", "women")
        assert not siw.already_have("H3", "H3-C", "women")
