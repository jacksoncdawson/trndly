"""Shared test fixtures for the trndly test suite.

Adds the project root to sys.path (so ``pipelines.collectors...`` resolves)
and exposes per-retailer fixture loaders for the scraper unit tests.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

# Make the project root importable so `pipelines.collectors...` resolves.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


# Canonical 18-column items CSV schema. Every scraper's _combo_to_row
# must produce a dict with exactly these keys, in this order.
ITEMS_CSV_FIELDNAMES = [
    "scraped_at", "retailer",
    "style_id", "cc_id", "web_product_type",
    "title", "gender",
    "color_raw", "product_type_raw", "material_raw", "graphical_appearance_raw",
    "color_master_id", "color_spectrum_id", "gender_id",
    "product_type_id", "product_group_id", "material_id", "graphical_appearance_id",
]


def _load_json(retailer: str, filename: str) -> Any:
    path = FIXTURES_DIR / retailer / filename
    if not path.exists():
        pytest.skip(f"fixture missing: {path}")
    with path.open() as f:
        return json.load(f)


def _load_text(retailer: str, filename: str) -> str:
    path = FIXTURES_DIR / retailer / filename
    if not path.exists():
        pytest.skip(f"fixture missing: {path}")
    return path.read_text()


# --------------------------------------------------------------------------- #
# Per-retailer fixtures                                                         #
# --------------------------------------------------------------------------- #

@pytest.fixture
def gap_listing_page1() -> dict:
    """Synthetic Gap page 1 of 3, with `pagination.pageNumberTotal=3`."""
    return _load_json("gap", "listing_page1.json")


@pytest.fixture
def gap_listing_page2() -> dict:
    return _load_json("gap", "listing_page2.json")


@pytest.fixture
def gap_listing_page3() -> dict:
    return _load_json("gap", "listing_page3.json")


@pytest.fixture
def gap_pdp_html() -> str:
    """Slice of Gap PDP HTML containing a Fabric & care block."""
    return _load_text("gap", "pdp_html.txt")


@pytest.fixture
def uniqlo_listing_page1() -> dict:
    return _load_json("uniqlo", "listing_page1.json")


@pytest.fixture
def uniqlo_listing_page2() -> dict:
    return _load_json("uniqlo", "listing_page2.json")


@pytest.fixture
def uniqlo_pdp_html() -> str:
    return _load_text("uniqlo", "pdp_html.txt")


@pytest.fixture
def hollister_apollo_html() -> str:
    """SSR HTML containing a window['APOLLO_STATE_…'] = {…} blob."""
    return _load_text("hollister", "ssr_apollo_state.html")


@pytest.fixture
def hollister_pdp_html() -> str:
    return _load_text("hollister", "pdp_html.txt")


@pytest.fixture
def ae_bootstrap_headers() -> dict:
    """Captured Akamai-validated header bundle for AE post-bootstrap.
    NOT a real JWT — just enough shape for tests to mock _bootstrap_session."""
    return _load_json("ae", "bootstrap_headers.json")


@pytest.fixture
def ae_listing_page1() -> dict:
    return _load_json("ae", "listing_page1.json")


@pytest.fixture
def ae_pdp_response() -> dict:
    return _load_json("ae", "pdp_response.json")
