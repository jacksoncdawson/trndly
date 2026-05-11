"""Live retail-site smoke tests. **NOT run by default** — opt in with
`pytest -m live`. Use sparingly; each test makes a real HTTPS request.

These exist to catch failure modes the mocked tests can't:
  - Hollister Akamai fingerprint tightens → silent 0-product responses.
  - A retailer renames an Apollo prefix or JSON path.
  - SSL/TLS handshake regression in httpx.

No magic numbers (e.g. `assert productTotalCount > 1500`) — assertions
are *structural*: parser returns a non-None state with non-empty products.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from pipelines.collectors import gap_scraper as gs
from pipelines.collectors import hollister_scraper as hs
from pipelines.collectors import uniqlo_scraper as us


pytestmark = pytest.mark.live


# --------------------------------------------------------------------------- #
# Hollister structural sanity check                                             #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_hollister_apollo_state_parses_live():
    """Catches the failure modes:
      - Akamai blocks → 149-byte response with no Apollo state.
      - Hollister renames the Apollo prefix → parser returns None.
      - HTTP/2 default change → 403.
    """
    url = "https://www.hollisterco.com/shop/us/womens"
    async with httpx.AsyncClient(headers=hs.HTTP_HEADERS, timeout=30) as client:
        resp = await client.get(url, follow_redirects=True)
    assert resp.status_code == 200, f"got {resp.status_code} (Akamai may be blocking)"
    assert len(resp.text) > 50_000, (
        f"response only {len(resp.text)} bytes — Akamai may be returning a stub"
    )
    state = hs._parse_apollo_state(resp.text)
    assert state is not None, (
        "Apollo state did not parse — Hollister may have renamed the marker. "
        f"Constant: hs.APOLLO_STATE_PREFIX = {hs.APOLLO_STATE_PREFIX!r}"
    )
    combos, total, pages = hs._extract_combos_from_apollo(state, "women")
    assert total > 0, "productTotalCount = 0 — page rendered but no catalog"
    assert len(combos) > 0, "Apollo state parsed but no combos extracted"


# --------------------------------------------------------------------------- #
# Gap listing API smoke                                                         #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_gap_listing_api_responds_with_products():
    """Hit one Gap listing target's first page; assert the response has
    the expected top-level keys + a non-empty products list."""
    target = gs.GAP_TARGETS[0]  # men shop all
    async with httpx.AsyncClient(headers=gs.API_HEADERS, timeout=30) as client:
        page = await gs._fetch_listing_page(
            client, target["cid"], target["department"], 0, verbose=False,
        )
    assert page is not None, "Gap listing API returned None — connection failure?"
    assert "totalColors" in page, "Gap response missing totalColors — schema drift?"
    assert "products" in page, "Gap response missing products list — schema drift?"
    assert isinstance(page["products"], list)
    assert len(page["products"]) > 0, "Gap returned 0 products — catalog empty?"


# --------------------------------------------------------------------------- #
# Uniqlo listing API smoke                                                      #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_uniqlo_listing_api_responds_with_items():
    target = us.UNIQLO_TARGETS[0]
    async with httpx.AsyncClient(headers=us.API_HEADERS, timeout=30) as client:
        page = await us._fetch_listing_page(
            client, target["genderId"], offset=0, verbose=False,
        )
    assert page is not None, "Uniqlo listing API returned None"
    assert page.get("status") == "ok", f"Uniqlo status={page.get('status')}"
    assert "result" in page
    items = page.get("result", {}).get("items", [])
    assert len(items) > 0, "Uniqlo returned 0 items"
