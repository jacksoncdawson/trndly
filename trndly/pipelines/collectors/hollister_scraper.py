"""
Hollister retail scraper for trndly trend signals.

Pulls Hollister's full women + men catalog from the SSR'd HTML of two
shop-all PLPs. The product catalog is embedded as an Apollo GraphQL cache
inside a `<script>` block — there's no clean public JSON API to call.

ALL FETCHING GOES THROUGH A HEADLESS BROWSER (Playwright/Chromium).
------------------------------------------------------------------
Hollister (an Abercrombie & Fitch property) is now behind an Akamai edge
fingerprint/IP-reputation check that serves a deterministic **403**
("Bad Request / Reference ID", 149-byte body) to httpx/curl for EVERY
client shape — UA/header/HTTP-version tweaks do NOT fix it, and there are
no `_abck`/`bm_sz` handoff cookies to harvest into httpx. The only thing
that passes is a real headless Chromium issuing a full page navigation.

  * `page.goto("…/shop/us/womens")`            → 200, Apollo state present.
  * `page.goto("…/shop/us/womens?start=90")`   → 200, the NEXT 90 products.
  * `context.request.get(PLP, params={start})` → **403** (bare APIRequest-
    Context is fingerprinted even with full browser headers / referer /
    sec-fetch-*). So we paginate via real navigations, NOT replayed XHRs.

(History: this file used to claim "httpx HTTP/1.1 passes Akamai." That was
true once; it is now FALSE. Akamai tightened the edge check and the old
httpx path silently returned `[], 0` and wrote a header-only CSV — the
2026-06 incident. Do not reintroduce httpx fetching here.)

OUTPUT
------
items_hollister.csv  — one row per (product × color variant). Schema
mirrors items_gap.csv. `web_product_type` stays "" for Hollister.

PIPELINE SHAPE
--------------
Phase 0   — launch ONE headless Chromium + context for the whole run
            (mirrors american_eagle_scraper._bootstrap_session: browser
            UA, `--disable-blink-features=AutomationControlled`,
            navigator.webdriver hidden). Image/font requests are aborted
            via routing so navigations are fast and gentle on the site.

Phase 1   — paginate the two shop-all PLPs by full navigation. The first
            response carries `productTotalCount`/`totalPages` in its Apollo
            state; we then walk `start in (90, 180, …, 90*(totalPages-1))`.
            A small pool of browser pages gives bounded concurrency; each
            page navigates its assigned `start` offsets sequentially.

Phase 1.5 — (optional, default ON) For each unique productPageUrl whose
            title yields no explicit fabric keyword, navigate to the PDP
            (browser, NOT httpx — PDPs are Akamai-protected too) and
            regex-extract `"fabricDetails":"…"`.

Phase 2   — Project each (product × color) combo into the items CSV.

CROSS-LISTING DEDUP
-------------------
Per-target dedup on (product_id, swatch_id). Cross-target the same
(product_id, swatch_id, gender) is unique by construction (Hollister
products are gendered; same product never appears in both shop-alls).

Setup
-----
  pip install playwright pandas
  playwright install chromium

Usage
-----
  python hollister_scraper.py
  python hollister_scraper.py --concurrency 4       # browser pages in flight
  python hollister_scraper.py --max-products-per-page 5
  python hollister_scraper.py --no-enrich-pdp
  python hollister_scraper.py --resume
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import datetime
import json
import re
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from pipelines.paths import items_csv_path_for  # noqa: E402

from pipelines.collectors.feature_lookups import (  # noqa: E402
    COLOR_MASTER_TO_ID,
    GENDER_TO_ID,
    GRAPHICAL_APPEARANCE_TO_ID,
    MATERIAL_TO_ID,
    PRODUCT_TYPE_TO_ID,
    extract_category,
    extract_color,
    extract_color_spectrum_id,
    extract_graphical_appearance,
    extract_material,
    extract_product_group_id,
    extract_product_type,
    has_explicit_material_keyword,
)
from pipelines.contracts import (  # noqa: E402
    FEATURE_TYPES,
)

# --------------------------------------------------------------------------- #
# Targets and HTTP config                                                       #
# --------------------------------------------------------------------------- #

# Two shop-all PLPs collapse the old 9-PLP fan-out (women new-arrivals +
# tops + bottoms + dresses + outerwear + men's equivalents). Each carries
# the canonical productTotalCount in its Apollo state.
HOLLISTER_TARGETS: list[dict] = [
    {"slug": "womens", "gender": "women", "label": "women shop all"},
    {"slug": "mens",   "gender": "men",   "label": "men shop all"},
]

PLP_URL_TEMPLATE = "https://www.hollisterco.com/shop/us/{slug}"
PDP_BASE = "https://www.hollisterco.com"
PAGE_SIZE = 90  # server-fixed; URL pagination via ?start=N (multiples of 90).

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Navigation timeout per page.goto, ms. Hollister PLPs are ~1.5 MB of SSR
# HTML; with image/font requests aborted, DOMContentLoaded lands well under
# this, but the first navigation per page warms the connection.
NAV_TIMEOUT_MS = 60_000
# Default browser-page concurrency. Each page navigates sequentially; this
# is how many pages navigate at once. 4 is gentle and reliable.
DEFAULT_CONCURRENCY = 4
# Retry a navigation this many times before giving up on a single offset.
NAV_MAX_ATTEMPTS = 3
# Static-asset request types / hosts to abort so navigations stay fast and
# light on the live site. The Apollo state we parse is in the document HTML,
# so none of this is needed.
_BLOCK_RESOURCE_TYPES = {"image", "media", "font"}
_BLOCK_URL_RE = re.compile(
    r"img\.hollisterco\.com"
    r"|cdn\.gladly\.com"
    r"|signifyd\.com"
    r"|\.(?:png|jpe?g|webp|gif|svg|woff2?|ttf|mp4)(?:\?|$)",
    re.I,
)

# Apollo state script-block marker. The Hollister page embeds the full
# catalog cache as: window['APOLLO_STATE__catalog-mfe-web-service-CategoryPageFrontEnd-config'] = {...};
APOLLO_STATE_PREFIX = "APOLLO_STATE__catalog-mfe-web-service-CategoryPageFrontEnd-config"
APOLLO_ASSIGN_RE = re.compile(rf"{re.escape(APOLLO_STATE_PREFIX)}[^=]*=\s*", re.S)

# PDP fabric extraction.
PDP_FABRIC_RE = re.compile(r'"fabricDetails":"((?:[^"\\]|\\.)*)"')

from pipelines.collectors._http_utils import (  # noqa: E402
    CSV_FIELDNAMES,
    StreamingItemWriter,
)


# --------------------------------------------------------------------------- #
# Browser session (Playwright) — owns one Chromium + context per run           #
# --------------------------------------------------------------------------- #

class HollisterBrowser:
    """Async context manager owning ONE headless Chromium + browser context
    for the whole run. All fetching (PLP pagination + PDP enrichment) goes
    through page navigations on this context, because Hollister's Akamai edge
    403s every non-browser client shape (httpx, curl, even Playwright's bare
    APIRequestContext). Mirrors american_eagle_scraper._bootstrap_session's
    launch flags / anti-automation tweaks.

    `fetch_html(url)` runs a `page.goto` with retries and returns the rendered
    HTML, or None if every attempt failed. It acquires a page from a small
    pool (`concurrency` pages) so up to `concurrency` navigations run at once;
    each page navigates sequentially (a single page can only be on one URL).
    """

    def __init__(self, concurrency: int = DEFAULT_CONCURRENCY, verbose: bool = True) -> None:
        self.concurrency = max(1, concurrency)
        self.verbose = verbose
        self._pw = None
        self._browser = None
        self._context = None
        self._pages: asyncio.Queue = asyncio.Queue()

    async def __aenter__(self) -> "HollisterBrowser":
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise RuntimeError(
                "playwright not installed. Run: "
                "pip install playwright && playwright install chromium"
            ) from exc

        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        self._context = await self._browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1440, "height": 900},
            locale="en-US",
        )
        await self._context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        # Drop heavy/irrelevant requests; the catalog data is in the HTML.
        async def _route(route):
            req = route.request
            if req.resource_type in _BLOCK_RESOURCE_TYPES or _BLOCK_URL_RE.search(req.url):
                try:
                    await route.abort()
                except Exception:
                    await route.continue_()
            else:
                await route.continue_()

        await self._context.route("**/*", _route)

        for _ in range(self.concurrency):
            page = await self._context.new_page()
            page.set_default_navigation_timeout(NAV_TIMEOUT_MS)
            await self._pages.put(page)
        if self.verbose:
            print(f"  [browser] launched headless Chromium, {self.concurrency} pages")
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._browser is not None:
            await self._browser.close()
        if self._pw is not None:
            await self._pw.stop()

    async def fetch_html(self, url: str, *, label: str = "", verbose: bool | None = None) -> str | None:
        verbose = self.verbose if verbose is None else verbose
        page = await self._pages.get()
        try:
            for attempt in range(1, NAV_MAX_ATTEMPTS + 1):
                try:
                    resp = await page.goto(url, wait_until="domcontentloaded")
                    status = resp.status if resp is not None else 0
                    if status == 200:
                        return await page.content()
                    if verbose:
                        print(
                            f"    [nav] {label or url} got {status}, "
                            f"retry {attempt}/{NAV_MAX_ATTEMPTS}"
                        )
                except Exception as exc:  # navigation timeout / target closed
                    if verbose:
                        print(
                            f"    [nav] {label or url} {type(exc).__name__}: "
                            f"retry {attempt}/{NAV_MAX_ATTEMPTS}"
                        )
                if attempt < NAV_MAX_ATTEMPTS:
                    await asyncio.sleep(1.5 * attempt)
            if verbose:
                print(f"    [nav] {label or url} GAVE UP after {NAV_MAX_ATTEMPTS} attempts")
            return None
        finally:
            await self._pages.put(page)


# --------------------------------------------------------------------------- #
# Apollo-state parsing                                                          #
# --------------------------------------------------------------------------- #

def _parse_apollo_state(html: str) -> dict | None:
    """Locate the Apollo state assignment in the SSR HTML and decode the
    JSON object that follows. Uses `JSONDecoder.raw_decode` so we don't
    need to brace-match — it stops at the end of the first valid JSON
    object. Returns None if the marker isn't present.
    """
    m = APOLLO_ASSIGN_RE.search(html)
    if not m:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(html[m.end():])
        return obj
    except (json.JSONDecodeError, ValueError):
        return None


def _extract_combos_from_apollo(
    state: dict, gender: str,
) -> tuple[list[dict], int, int]:
    """From a parsed Apollo state, pull (combos, total_products, total_pages).

    Hollister structure:
      state["CACHE"]["ROOT_QUERY"]["category({...})"] is a Category object
      with `productTotalCount`, `pagination.totalPages`, and
      `products({"cacheEmpty":false})` — a list of {__ref: "Product:..."}.
      Each Product cache entry lives at `state["CACHE"]["Product:<id>"]`
      and has `swatchList: [{__ref:"ProductSwatch:..."}]`.

    Using the per-page Category's products list (not every Product:* in
    the cache) keeps recommendation-cache pollution out of the count.
    """
    total_products = 0
    total_pages = 0
    products_refs: list[dict] = []

    cache = state.get("CACHE") or {}
    rq = cache.get("ROOT_QUERY") or {}
    for k, v in rq.items():
        if not k.startswith("category(") or not isinstance(v, dict):
            continue
        if "productTotalCount" in v and not total_products:
            try:
                total_products = int(v["productTotalCount"])
            except (TypeError, ValueError):
                pass
        pagination = v.get("pagination") or {}
        if "totalPages" in pagination and not total_pages:
            try:
                total_pages = int(pagination["totalPages"])
            except (TypeError, ValueError):
                pass
        for fk, fv in v.items():
            if fk.startswith("products(") and isinstance(fv, list):
                products_refs.extend(r for r in fv if isinstance(r, dict) and "__ref" in r)
        # The two category(...) cache entries (cacheEmpty=false / =true)
        # carry the same data; one is enough.
        if products_refs:
            break

    combos: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for ref in products_refs:
        prod_key = ref["__ref"]
        prod = cache.get(prod_key)
        if not isinstance(prod, dict):
            continue
        product_id = str(prod.get("id") or "")
        name = prod.get("name") or ""
        url_path = prod.get("productPageUrl") or ""
        # Resolve swatch refs.
        swatches = prod.get("swatchList") or []
        if not isinstance(swatches, list):
            swatches = []
        if not swatches:
            # Fallback: synthesize one combo from the product alone with
            # whatever color signal we have on the product itself.
            color_label = prod.get("colorFamily") or ""
            key = (product_id, "default")
            if key in seen:
                continue
            seen.add(key)
            combos.append({
                "product_id": product_id,
                "name":       name,
                "url_path":   url_path,
                "cc_id":      "default",
                "color_name": color_label,
                "gender":     gender,
            })
            continue
        for sw_ref in swatches:
            if not isinstance(sw_ref, dict):
                continue
            sw_key = sw_ref.get("__ref")
            sw = cache.get(sw_key) if sw_key else None
            if not isinstance(sw, dict):
                continue
            cc_id = str(sw.get("id") or sw_key or "")
            color_name = sw.get("name") or ""
            key = (product_id, cc_id)
            if key in seen:
                continue
            seen.add(key)
            combos.append({
                "product_id": product_id,
                "name":       name,
                "url_path":   url_path,
                "cc_id":      cc_id,
                "color_name": color_name,
                "gender":     gender,
            })

    return combos, total_products, total_pages


# --------------------------------------------------------------------------- #
# Phase 1 — listing pagination                                                  #
# --------------------------------------------------------------------------- #

async def _fetch_listing_page(
    browser: HollisterBrowser,
    slug: str,
    start: int,
    verbose: bool = True,
) -> str | None:
    """Navigate to one PLP page (start=0 or start=N) and return the rendered
    HTML. Pagination is `?start=N` (multiples of PAGE_SIZE); start=0 omits the
    param. Goes through `browser.fetch_html` (real navigation) — `?start=N`
    via a bare APIRequestContext 403s, so navigation is mandatory.
    """
    url = PLP_URL_TEMPLATE.format(slug=slug)
    if start:
        url = f"{url}?start={start}"
    return await browser.fetch_html(
        url, label=f"plp slug={slug} start={start}", verbose=verbose,
    )


async def _fetch_listings_for_target(
    browser: HollisterBrowser,
    target: dict,
    verbose: bool = True,
) -> tuple[list[dict], int]:
    slug   = target["slug"]
    gender = target["gender"]
    label  = target["label"]

    first_html = await _fetch_listing_page(browser, slug, start=0, verbose=verbose)
    if first_html is None:
        print(f"  [{label}] FAILED to fetch start=0 — aborting this target")
        return [], 0

    state = _parse_apollo_state(first_html)
    if state is None:
        print(f"  [{label}] FAILED to find Apollo state in HTML — aborting")
        return [], 0
    first_combos, total_products, total_pages = _extract_combos_from_apollo(state, gender)
    if not total_pages and total_products:
        total_pages = max(1, (total_products + PAGE_SIZE - 1) // PAGE_SIZE)
    print(f"  [{label}] productTotalCount={total_products} totalPages={total_pages}")

    pages_combos: list[list[dict]] = [first_combos]
    if total_pages > 1:
        async def fetch_one(start: int) -> list[dict]:
            html = await _fetch_listing_page(browser, slug, start=start, verbose=verbose)
            if html is None:
                return []
            st = _parse_apollo_state(html)
            if st is None:
                return []
            combos, _, _ = _extract_combos_from_apollo(st, gender)
            return combos
        starts = [PAGE_SIZE * p for p in range(1, total_pages)]
        # browser.fetch_html bounds concurrency via its internal page pool;
        # gather all offsets and let the pool serialize them.
        rest = await asyncio.gather(*[fetch_one(s) for s in starts])
        pages_combos.extend(rest)

    # Per-target dedup on (product_id, cc_id).
    combos: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for batch in pages_combos:
        for c in batch:
            key = (c["product_id"], c["cc_id"])
            if key in seen:
                continue
            seen.add(key)
            combos.append(c)

    unique_products = len({c["product_id"] for c in combos})
    if total_products and unique_products < total_products:
        delta = total_products - unique_products
        pct = delta / max(total_products, 1) * 100
        print(
            f"  [{label}] {unique_products}/{total_products} unique products "
            f"({delta} short, {pct:.1f}%)"
        )
    elif total_products:
        print(f"  [{label}] collected {unique_products}/{total_products} unique products ✓")

    return combos, total_products


async def _scrape_hollister_via_html(
    browser: HollisterBrowser,
    targets: list[dict] = HOLLISTER_TARGETS,
    max_products_per_page: int | None = None,
    verbose: bool = True,
) -> tuple[list[dict], dict[str, int]]:
    """Paginate every target through the shared browser. The browser's page
    pool bounds how many navigations run at once; targets are gathered so
    both genders' offsets compete for the same pool.
    """
    results = await asyncio.gather(*[
        _fetch_listings_for_target(browser, t, verbose=verbose) for t in targets
    ])

    all_combos: list[dict] = []
    totals: dict[str, int] = {}
    for target, (combos, total) in zip(targets, results):
        if max_products_per_page is not None:
            keep_ids: set[str] = set()
            kept: list[dict] = []
            for c in combos:
                if c["product_id"] not in keep_ids:
                    if len(keep_ids) < max_products_per_page:
                        keep_ids.add(c["product_id"])
                if c["product_id"] in keep_ids:
                    kept.append(c)
            combos = kept
        all_combos.extend(combos)
        totals[target["label"]] = total
    return all_combos, totals


# --------------------------------------------------------------------------- #
# Phase 1.5 — PDP material enrichment                                           #
# --------------------------------------------------------------------------- #

def _parse_fabric_from_html(html: str) -> str:
    """Extract + join the percentage-bearing ``fabricDetails`` strings from PDP
    HTML. Pure (no I/O) so it's unit-testable; the browser fetch lives in
    ``_fetch_pdp_fabric``. Multiple swatch variants of the same product can each
    contribute a fabricDetails string — joining them lets the percentage-aware
    extractor see all components.
    """
    bullets: list[str] = []
    for raw in PDP_FABRIC_RE.findall(html):
        if not raw:
            continue
        try:
            decoded = bytes(raw, "utf-8").decode("unicode_escape")
        except Exception:
            decoded = raw
        if "%" in decoded:
            bullets.append(decoded)
    return " ".join(bullets)


async def _fetch_pdp_fabric(
    browser: HollisterBrowser,
    url_path: str,
    verbose: bool = True,
) -> str:
    """Navigate to one Hollister PDP and return the joined fabricDetails
    strings. Empty string on miss. PDPs are Akamai-protected like the PLPs,
    so this goes through the browser too (not httpx).
    """
    if not url_path:
        return ""
    url = PDP_BASE + url_path if url_path.startswith("/") else url_path
    html = await browser.fetch_html(url, label=f"pdp/{url_path[:60]}", verbose=verbose)
    if html is None:
        return ""
    return _parse_fabric_from_html(html)


async def _enrich_materials_via_pdps(
    browser: HollisterBrowser,
    items: list[tuple[str, str]],   # (product_id, url_path)
    verbose: bool = True,
) -> dict[str, str]:
    if not items:
        return {}
    # The browser's page pool bounds concurrency; gather all PDP navigations.
    results = await asyncio.gather(*[
        _fetch_pdp_fabric(browser, url_path, verbose=verbose) for _pid, url_path in items
    ])
    return {pid: text for (pid, _u), text in zip(items, results) if text}


# --------------------------------------------------------------------------- #
# Per-combo attribute extraction (pure)                                         #
# --------------------------------------------------------------------------- #

def _combo_to_row(
    combo: dict,
    scraped_at: str,
    retailer: str = "hollister",
    enriched_material_by_product: dict[str, str] | None = None,
) -> dict:
    title       = combo["name"]
    color_label = combo["color_name"] or ""
    gender      = combo["gender"]

    category = extract_category(title)
    composition = ""
    if enriched_material_by_product is not None:
        composition = enriched_material_by_product.get(combo["product_id"], "")
    if composition:
        material = extract_material(composition, inferred_category=category)
    else:
        material = extract_material(title, inferred_category=category)

    product_type   = extract_product_type(title)
    base_graphical = extract_graphical_appearance(title)

    color = extract_color(color_label) or extract_color(title)
    graphical = extract_graphical_appearance(color_label)
    if graphical == "Solid":
        graphical = base_graphical

    color_raw = color_label or "unknown"
    return {
        "scraped_at":               scraped_at,
        "retailer":                 retailer,
        "style_id":                 combo["product_id"],
        "cc_id":                    combo["cc_id"],
        "web_product_type":         "",
        "title":                    title,
        "gender":                   gender,
        "color_raw":                color_raw,
        "product_type_raw":         product_type or "unknown",
        "material_raw":             material or "unknown",
        "graphical_appearance_raw": graphical,
        "color_master_id":          COLOR_MASTER_TO_ID.get(color or "", 0),
        "color_spectrum_id":        extract_color_spectrum_id(color_raw),
        "gender_id":                GENDER_TO_ID.get(gender, 2),
        "product_type_id":          PRODUCT_TYPE_TO_ID.get(product_type or "", 0),
        "product_group_id":         extract_product_group_id(product_type),
        "material_id":              MATERIAL_TO_ID.get(material or "", 0),
        "graphical_appearance_id":  GRAPHICAL_APPEARANCE_TO_ID.get(graphical, 1),
    }


def _read_existing_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open("r", newline="") as f:
        return list(csv.DictReader(f))


# --------------------------------------------------------------------------- #
# Frequency counting                                                            #
# --------------------------------------------------------------------------- #

def count_attribute_frequencies(
    rows: list[dict],
) -> tuple[dict[str, dict[str, int]], int]:
    counts: dict[str, dict[str, int]] = {ft: {} for ft in FEATURE_TYPES}
    products: dict[tuple[str, str], dict] = {}
    for item in rows:
        key = (item.get("title", ""), item.get("gender", ""))
        if key not in products:
            material_raw = item.get("material_raw")
            products[key] = {
                "category": extract_category(item.get("title", "")),
                "material": material_raw if material_raw and material_raw != "unknown" else None,
                "colors":   set(),
            }
        color_label = item.get("color_raw", "")
        canon = extract_color(color_label) if color_label and color_label != "unknown" else None
        if canon:
            products[key]["colors"].add(canon)
    for prod in products.values():
        if prod["category"]:
            counts["category"][prod["category"]] = counts["category"].get(prod["category"], 0) + 1
        if prod["material"]:
            counts["material"][prod["material"]] = counts["material"].get(prod["material"], 0) + 1
        for c in prod["colors"]:
            counts["color"][c] = counts["color"].get(c, 0) + 1
    return counts, len(products)


def normalize_counts(
    counts: dict[str, dict[str, int]], total_items: int,
) -> dict[str, dict[str, float]]:
    denom = max(total_items, 1)
    return {
        ft: {value: round(count / denom, 6) for value, count in vals.items()}
        for ft, vals in counts.items()
    }


KNOWN_FEATURE_VALUES: dict[str, list[str]] = {
    "color":    [
        "black", "white", "blue", "red", "green", "beige", "pink", "gray", "navy", "brown", "purple",
        "yellow", "orange", "metal",
    ],
    "category": ["pants", "shorts", "skirt", "dress", "tops", "outerwear", "shoes", "accessories"],
    "material": ["cotton", "denim", "linen", "silk", "wool", "polyester", "leather", "knit"],
}


# --------------------------------------------------------------------------- #
# Orchestration — one browser for Phase 1 (PLPs) + Phase 1.5 (PDPs)             #
# --------------------------------------------------------------------------- #

async def _run_scrape(
    *,
    concurrency: int,
    max_products_per_page: int | None,
    enrich_pdp: bool,
) -> tuple[list[dict], dict[str, int], dict[str, str]]:
    """Open ONE browser for the whole run; paginate the PLPs (Phase 1) and,
    if requested, enrich materials via PDP navigation (Phase 1.5) on the same
    browser. Returns (combos, per-target totals, enriched material map).
    """
    async with HollisterBrowser(concurrency=concurrency) as browser:
        print("Phase 1: paginating Hollister shop-all PLPs ...")
        combos, totals = await _scrape_hollister_via_html(
            browser,
            targets=HOLLISTER_TARGETS,
            max_products_per_page=max_products_per_page,
        )

        enriched: dict[str, str] = {}
        if enrich_pdp:
            # Enrich every product whose title doesn't carry an explicit
            # fabric keyword. Keyed by product_id; each gets one PDP fetch
            # even if the product has many color variants.
            unknown_pairs: list[tuple[str, str]] = []
            seen: set[str] = set()
            for c in combos:
                pid = c["product_id"]
                if pid in seen:
                    continue
                seen.add(pid)
                if not has_explicit_material_keyword(c["name"]):
                    unknown_pairs.append((pid, c["url_path"]))
            if unknown_pairs:
                print(
                    f"\nPhase 1.5: enriching material for {len(unknown_pairs)} "
                    f"products via PDP fabricDetails ..."
                )
                t0 = time.perf_counter()
                enriched = await _enrich_materials_via_pdps(
                    browser, unknown_pairs, verbose=False,
                )
                print(
                    f"  enriched {len(enriched)}/{len(unknown_pairs)} PDPs "
                    f"in {time.perf_counter()-t0:.1f}s "
                    f"({len(unknown_pairs) - len(enriched)} returned no fabric data)"
                )
            else:
                print("\nPhase 1.5: no products need PDP enrichment (skipping).")

    return combos, totals, enriched


# --------------------------------------------------------------------------- #
# CLI                                                                           #
# --------------------------------------------------------------------------- #

def parse_args() -> argparse.Namespace:
    default_items = items_csv_path_for("hollister")
    parser = argparse.ArgumentParser(
        description="Scrape Hollister via SSR HTML + Apollo state. Writes "
                    "items_hollister.csv. build_live_cube.py aggregates "
                    "items_*.csv into live_*_<YYYY-MM>.parquet cubes."
    )
    parser.add_argument("--items-path", default=str(default_items))
    parser.add_argument(
        "--concurrency", type=int, default=DEFAULT_CONCURRENCY,
        help=f"Browser pages navigating at once (default {DEFAULT_CONCURRENCY}). "
             "Each page navigates its offsets sequentially.",
    )
    parser.add_argument("--max-products-per-page", type=int, default=None)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument(
        "--enrich-pdp", dest="enrich_pdp", action="store_true", default=True,
    )
    parser.add_argument("--no-enrich-pdp", dest="enrich_pdp", action="store_false")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    items_path = Path(args.items_path).expanduser().resolve()
    items_path.parent.mkdir(parents=True, exist_ok=True)

    cap_msg = "no cap" if args.max_products_per_page is None else f"max {args.max_products_per_page}/target"
    print(
        f"Hollister retail scraper (Playwright/Apollo mode)\n"
        f"  targets:     {len(HOLLISTER_TARGETS)}  concurrency: {args.concurrency}  ({cap_msg})\n"
        f"  output:      {items_path}\n"
        f"  resume:      {args.resume}\n"
        f"  enrich-pdp:  {args.enrich_pdp}\n"
    )

    scraped_at = datetime.date.today().isoformat()
    start = time.perf_counter()

    try:
        combos, totals, enriched = asyncio.run(_run_scrape(
            concurrency=args.concurrency,
            max_products_per_page=args.max_products_per_page,
            enrich_pdp=args.enrich_pdp,
        ))
    except RuntimeError as exc:
        print(f"  scrape failed: {exc}")
        sys.exit(2)

    print(f"\nPhase 2: writing items CSV ({len(combos)} combos to project) ...")
    written = 0
    skipped = 0
    with StreamingItemWriter(items_path, resume=args.resume) as writer:
        for combo in combos:
            row = _combo_to_row(
                combo, scraped_at=scraped_at, retailer="hollister",
                enriched_material_by_product=enriched,
            )
            if writer.already_have(row["style_id"], row["cc_id"], row["gender"]):
                skipped += 1
                continue
            writer.write(row)
            written += 1
    elapsed = time.perf_counter() - start

    if args.resume and skipped:
        print(f"  [resume] skipped {skipped} previously written rows; appended {written}")

    rows = _read_existing_rows(items_path)
    print(f"\nWrote {len(rows)} rows → {items_path}")
    mins, secs = divmod(elapsed, 60)
    print(f"Elapsed: {int(mins)}m {secs:.1f}s")

    print("\nCompleteness vs Hollister productTotalCount (per target):")
    short = False
    for target in HOLLISTER_TARGETS:
        label  = target["label"]
        gender = target["gender"]
        unique_ids = len({r.get("style_id") for r in rows if r.get("gender") == gender})
        total = totals.get(label, 0)
        delta = total - unique_ids
        if args.max_products_per_page is not None:
            ok = "(capped)"
        elif delta <= 0:
            ok = "OK"
        elif delta / max(total, 1) < 0.05:
            ok = "OK (drift)"
        else:
            ok = "SHORT"
            short = True
        print(f"  {label:<18} got {unique_ids:>5} / api {total:>5}   {ok}")

    counts, total_products = count_attribute_frequencies(rows)
    scores = normalize_counts(counts, total_items=total_products)
    print(f"\nDistinct (title, gender) products: {total_products}")
    print("Attribute coverage:")
    for feature_type in FEATURE_TYPES:
        found = len(counts.get(feature_type, {}))
        total = len(KNOWN_FEATURE_VALUES.get(feature_type, []))
        print(f"  {feature_type}: {found}/{total} values seen")
        for value, score in sorted(scores.get(feature_type, {}).items(), key=lambda x: -x[1]):
            cnt = counts[feature_type].get(value, 0)
            print(f"    {value:<15} score={score:.3f}  (count={cnt})")

    if short and args.strict:
        sys.exit(1)


if __name__ == "__main__":
    main()
