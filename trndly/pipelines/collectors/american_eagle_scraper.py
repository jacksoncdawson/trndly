"""
American Eagle retail scraper for trndly trend signals.

Pulls AE's catalog directly from AE's internal listing API. The API and PDP
material endpoints are behind Akamai bot protection AND require a JWT
bearer token, so the run begins with a one-time Playwright bootstrap that
harvests both: the JWT (from the Authorization header of the page's own
catalog XHR) and the Akamai cookies. Everything after the ~8s bootstrap is
pure httpx.

OUTPUT
------
items_american_eagle.csv  — one row per (product × color variant). Schema
mirrors items_gap.csv. Gap-specific provenance fields (`style_id`,
`cc_id`) are reused; `web_product_type` stays "" for AE.

LISTING API
-----------
GET https://www.ae.com/ugp-api/browse/v1/category/{category_id}
  ?offset={n}
Headers: Authorization: Bearer <JWT>, aesite: AEO_US, aelang: en_US,
         channeltype: WEB, Accept: application/vnd.api+json,
         + Akamai cookies (_abck, bm_sz, etc.)

Response (relevant slice — JSON:API style):
  {
    "meta": {"offset": 0, "rows": 30, "totalProducts": 565},
    "data": {...},
    "included": [
      {"type": "product",
       "id": "0366_6455_807",
       "attributes": {
         "displayName": "AE Double Take Tube Top",
         "url": "/p/women/tank-tops-tube-tops/tube-tops/...",
         "colorSwatches": [
           {"id": "0366_6455_807", "name": "Orange Flare", ...}, ...
         ],
         ...
       }}, ...
    ]
  }

Page size is hard-locked at 30 server-side (no override works). JWT TTL is
1800s (30 min) — covers a full run.

PIPELINE SHAPE
--------------
Phase 0   — one-time Playwright bootstrap: open one PLP, capture JWT
            from outgoing XHR's Authorization header, harvest Akamai
            cookies. ~8s.
Phase 1   — paginate the listing API for each of 9 cat IDs (no clean
            "shop all" exists for AE — the 9-PLP set is the canonical
            target list). Concurrency capped at 3 because Akamai rate-
            limits aggressive fan-out (~37% 403s at concurrency 8).
            Cross-PLP dedup on (product_id, color_id, gender).
Phase 1.5 — (optional, default ON) For each unique product whose title
            alone yields no explicit fabric keyword, GET
            /ugp-api/browse/v1/product/{id} and read
            data.attributes.copySections.material.bullets[0].
Phase 2   — Project each (product_id × color) combo to a row.

Setup
-----
  pip install httpx playwright pandas
  playwright install chromium

Usage
-----
  python american_eagle_scraper.py
  python american_eagle_scraper.py --concurrency 3            # default; AE is sensitive
  python american_eagle_scraper.py --max-products-per-page 5  # smoke test
  python american_eagle_scraper.py --no-enrich-pdp            # skip PDP material pass
  python american_eagle_scraper.py --resume
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import datetime
import os
import random
import sys
import time
from pathlib import Path

import httpx
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

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
from pipelines.training.feature_contract import (  # noqa: E402
    FEATURE_TYPES,
)

# --------------------------------------------------------------------------- #
# Targets and API config                                                        #
# --------------------------------------------------------------------------- #

# AE has no clean "shop all" per gender — the 9-PLP set is the canonical
# target list. Cross-PLP overlap is heavy (a women's tee can be in
# new-arrivals AND tops AND dresses), so dedup on (product_id, color_id,
# gender) is essential.
AE_TARGETS: list[dict] = [
    {"cat_id": "brg_dyn_hqya6u718b", "gender": "women", "label": "women new arrivals"},
    {"cat_id": "brg_dyn_fiqvft6w17", "gender": "men",   "label": "men new arrivals"},
    {"cat_id": "cat10049",           "gender": "women", "label": "women tops"},
    {"cat_id": "cat10025",           "gender": "men",   "label": "men tops"},
    {"cat_id": "cat10051",           "gender": "women", "label": "women bottoms"},
    {"cat_id": "cat10027",           "gender": "men",   "label": "men bottoms"},
    {"cat_id": "cat1320034",         "gender": "women", "label": "women dresses"},
    {"cat_id": "cat4260032",         "gender": "women", "label": "women outerwear"},
    {"cat_id": "cat380145",          "gender": "men",   "label": "men outerwear"},
]

API_BASE = "https://www.ae.com/ugp-api/browse/v1"
LISTING_URL_TEMPLATE = API_BASE + "/category/{cat_id}"
PRODUCT_URL_TEMPLATE = API_BASE + "/product/{product_id}"
PDP_PAGE_URL_TEMPLATE = "https://www.ae.com{url_path}"
API_PAGE_SIZE = 30  # server-fixed; cannot be overridden.

# These static headers come from the AE webapp's own outgoing XHRs. The
# bootstrap pass captures them along with the JWT, but they're fixed values
# so we hard-code them as defaults.
STATIC_API_HEADERS = {
    "Accept":         "application/vnd.api+json",
    "aesite":         "AEO_US",
    "aelang":         "en_US",
    "channeltype":    "WEB",
}
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# AE returns 403 from the Akamai edge under heavy concurrency. Treat 403 as
# retryable on top of the usual transient set.
RETRYABLE_STATUSES = {403, 408, 425, 429, 500, 502, 503, 504}
DEFAULT_MAX_ATTEMPTS = 5
# AE recon found ~37% 403s at concurrency 8; recommend 3-4. Default is 3.
DEFAULT_CONCURRENCY = 3

CSV_FIELDNAMES = [
    "scraped_at", "retailer",
    "style_id", "cc_id", "web_product_type",
    "title", "gender",
    "color_raw", "product_type_raw", "material_raw", "graphical_appearance_raw",
    "color_master_id", "color_spectrum_id", "gender_id",
    "product_type_id", "product_group_id", "material_id", "graphical_appearance_id",
]


# --------------------------------------------------------------------------- #
# Phase 0 — Playwright bootstrap                                                #
# --------------------------------------------------------------------------- #

async def _bootstrap_session(
    verbose: bool = True,
) -> tuple[dict[str, str], dict[str, str]]:
    """Open one Playwright session, navigate to a PLP, wait for the page's
    own catalog XHR to fire, capture its FULL request headers (including
    `Authorization: Bearer <JWT>`, `sec-ch-ua-*`, `sec-fetch-*`, the static
    AE headers like `aesite`, etc.) and harvest all browser cookies.

    Returns (api_headers, cookies_dict) — the headers tuple is what to pass
    to subsequent httpx calls. Akamai validates the full browser fingerprint
    (sec-ch-ua etc.) — the minimal `Authorization + aesite + Accept` set
    fails with 403, so we forward the headers verbatim.

    Raises RuntimeError if the catalog XHR doesn't fire within ~45s.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        raise RuntimeError(
            "playwright not installed. Run: pip install playwright && playwright install chromium"
        )

    captured: dict[str, dict[str, str]] = {}

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1440, "height": 900},
            locale="en-US",
        )
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )

        page = await context.new_page()

        def on_request(req):
            if "/ugp-api/browse/v1/category/" in req.url and not captured:
                # Snapshot every header AE saw for this XHR. Drop HTTP/2
                # pseudo-headers (start with ":") which httpx can't replay.
                captured["headers"] = {
                    k: v for k, v in req.headers.items() if not k.startswith(":")
                }

        page.on("request", on_request)

        bootstrap_url = "https://www.ae.com/us/en/c/women/new-arrivals/brg_dyn_hqya6u718b"
        if verbose:
            print(f"  [bootstrap] navigating to {bootstrap_url}")
        try:
            await page.goto(bootstrap_url, wait_until="domcontentloaded", timeout=45_000)
        except Exception as exc:
            await browser.close()
            raise RuntimeError(f"bootstrap navigation failed: {exc}") from exc

        for _ in range(60):
            if "headers" in captured:
                break
            await asyncio.sleep(0.5)

        if "headers" not in captured:
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            for _ in range(20):
                if "headers" in captured:
                    break
                await asyncio.sleep(0.5)

        cookies_list = await context.cookies()
        cookies_dict = {c["name"]: c["value"] for c in cookies_list}
        await browser.close()

        if "headers" not in captured:
            raise RuntimeError(
                "bootstrap could not capture catalog XHR headers — AE may "
                "have changed their auth flow or the bootstrap URL no longer "
                "fires a category request."
            )

        api_headers = captured["headers"]
        # Sanity-check for the bearer token; fail fast if missing.
        if not api_headers.get("authorization", "").startswith("Bearer "):
            raise RuntimeError("captured headers don't contain Bearer token")
        if verbose:
            jwt_len = len(api_headers["authorization"]) - len("Bearer ")
            print(
                f"  [bootstrap] captured {len(api_headers)} headers "
                f"(JWT {jwt_len} chars), {len(cookies_dict)} cookies"
            )
        return api_headers, cookies_dict


# --------------------------------------------------------------------------- #
# HTTP helper                                                                   #
# --------------------------------------------------------------------------- #

async def _request_with_retry(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    label: str = "",
    verbose: bool = True,
) -> httpx.Response | None:
    """GET with exponential-backoff retry on 403 (Akamai) / 429 / 5xx /
    network. Returns Response on success, None when retries are exhausted.
    """
    for attempt in range(1, max_attempts + 1):
        try:
            r = await client.get(url, params=params, follow_redirects=True)
            if r.status_code == 200:
                return r
            if r.status_code in RETRYABLE_STATUSES:
                # Slightly more aggressive backoff for AE because Akamai
                # 403s are sticky if you hammer.
                wait = (1.5 ** attempt) + random.uniform(0.2, 1.0)
                if verbose:
                    print(
                        f"    [http] {label} got {r.status_code}, "
                        f"retry {attempt}/{max_attempts} in {wait:.1f}s"
                    )
                await asyncio.sleep(wait)
                continue
            if verbose:
                print(f"    [http] {label} non-retryable {r.status_code}: {r.text[:200]}")
            return None
        except (httpx.TimeoutException, httpx.RequestError) as exc:
            wait = (1.5 ** attempt) + random.uniform(0.2, 1.0)
            if verbose:
                print(
                    f"    [http] {label} {type(exc).__name__}: "
                    f"retry {attempt}/{max_attempts} in {wait:.1f}s"
                )
            await asyncio.sleep(wait)
    if verbose:
        print(f"    [http] {label} GAVE UP after {max_attempts} attempts")
    return None


# --------------------------------------------------------------------------- #
# Phase 1 — listing API                                                         #
# --------------------------------------------------------------------------- #

async def _fetch_listing_page(
    client: httpx.AsyncClient,
    cat_id: str,
    offset: int,
    verbose: bool = True,
) -> dict | None:
    url = LISTING_URL_TEMPLATE.format(cat_id=cat_id)
    resp = await _request_with_retry(
        client, url, params={"offset": str(offset)},
        label=f"api cat={cat_id} offset={offset}", verbose=verbose,
    )
    return resp.json() if resp is not None else None


async def _fetch_listings_for_target(
    client: httpx.AsyncClient,
    target: dict,
    semaphore: asyncio.Semaphore,
    verbose: bool = True,
) -> tuple[list[dict], int]:
    """Paginate one (cat_id, gender) target. Returns (combos, total_products)."""
    cat_id = target["cat_id"]
    gender = target["gender"]
    label  = target["label"]

    async with semaphore:
        first = await _fetch_listing_page(client, cat_id, offset=0, verbose=verbose)
    if first is None:
        print(f"  [{label}] FAILED to fetch offset 0 — aborting this target")
        return [], 0

    meta = first.get("meta") or {}
    total = int(meta.get("totalProducts", 0))
    print(f"  [{label}] total={total} pageSize={API_PAGE_SIZE}")

    pages: list[dict | None] = [first]
    if total > API_PAGE_SIZE:
        async def fetch_one(off: int) -> dict | None:
            async with semaphore:
                return await _fetch_listing_page(client, cat_id, offset=off, verbose=verbose)
        offsets = list(range(API_PAGE_SIZE, total, API_PAGE_SIZE))
        rest = await asyncio.gather(*[fetch_one(o) for o in offsets])
        pages.extend(rest)

    failed = [i * API_PAGE_SIZE for i, p in enumerate(pages) if p is None]
    if failed:
        print(f"  [{label}] WARNING: offsets {failed} failed permanently")

    combos: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for page_data in pages:
        if not page_data:
            continue
        for entry in page_data.get("included", []) or []:
            if entry.get("type") != "product":
                continue
            product_id = str(entry.get("id") or "")
            attrs      = entry.get("attributes") or {}
            display    = attrs.get("displayName") or ""
            url_path   = attrs.get("url") or ""
            swatches   = attrs.get("colorSwatches") or []
            if not product_id or not swatches:
                continue
            for sw in swatches:
                cc_id = str(sw.get("id") or "")
                cc_name = sw.get("name") or ""
                key = (product_id, cc_id)
                if key in seen:
                    continue
                seen.add(key)
                combos.append({
                    "product_id": product_id,
                    "name":       display,
                    "url_path":   url_path,
                    "cc_id":      cc_id,
                    "color_name": cc_name,
                    "gender":     gender,
                })

    unique_products = len({c["product_id"] for c in combos})
    if total and unique_products < total:
        print(
            f"  [{label}] {unique_products}/{total} unique products "
            f"({total - unique_products} short)"
        )
    elif total:
        print(f"  [{label}] collected {unique_products}/{total} unique products ✓")

    return combos, total


async def _scrape_ae_via_api(
    api_headers: dict,
    cookies: dict,
    targets: list[dict] = AE_TARGETS,
    concurrency: int = DEFAULT_CONCURRENCY,
    max_products_per_page: int | None = None,
    verbose: bool = True,
) -> tuple[list[dict], dict[str, int]]:
    """Run all 9 targets concurrently (each target paginates serially under
    the shared Semaphore). Cross-target dedup on (product_id, cc_id, gender)
    happens after.
    """
    timeout = httpx.Timeout(connect=10, read=30, write=15, pool=15)
    limits  = httpx.Limits(max_connections=max(concurrency * 2, 8))
    sem     = asyncio.Semaphore(concurrency)

    async with httpx.AsyncClient(
        headers=api_headers, cookies=cookies, timeout=timeout, limits=limits,
    ) as client:
        results = await asyncio.gather(*[
            _fetch_listings_for_target(client, t, sem, verbose=verbose) for t in targets
        ])

    # Cross-target dedup on (product_id, cc_id, gender). Same product can be
    # in multiple PLPs of the same gender — dedup. Across genders (rare for
    # AE), keep both rows like Gap's unisex case.
    all_combos: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    totals: dict[str, int] = {}
    for target, (combos, total) in zip(targets, results):
        totals[target["label"]] = total
        added = 0
        for c in combos:
            key = (c["product_id"], c["cc_id"], c["gender"])
            if key in seen:
                continue
            seen.add(key)
            if max_products_per_page is not None and added >= max_products_per_page * 5:
                # Cap is applied per target (loose: 5 colors × max_products);
                # exact dedup happens post-cap.
                break
            all_combos.append(c)
            added += 1
    return all_combos, totals


# --------------------------------------------------------------------------- #
# Phase 1.5 — PDP material enrichment via product-detail API                    #
# --------------------------------------------------------------------------- #

async def _fetch_pdp_fabric(
    client: httpx.AsyncClient,
    product_id: str,
    semaphore: asyncio.Semaphore,
    verbose: bool = True,
) -> str:
    """Fetch one product-detail API response and return the fabric bullet.
    AE puts fabric composition at:
      data.attributes.copySections.material.bullets[0]
    Returns "" on fetch failure or when the bullets list is empty / non-fabric.
    """
    url = PRODUCT_URL_TEMPLATE.format(product_id=product_id)
    async with semaphore:
        resp = await _request_with_retry(
            client, url, label=f"pdp/{product_id}", verbose=verbose,
        )
    if resp is None:
        return ""
    try:
        data = resp.json()
        bullets = data["data"]["attributes"]["copySections"]["material"]["bullets"]
    except (KeyError, TypeError, ValueError):
        return ""
    if not isinstance(bullets, list):
        return ""
    # First fabric-shaped bullet (contains "%"); skip "Machine wash" / "Imported".
    for b in bullets:
        if isinstance(b, str) and "%" in b:
            return b
    return ""


async def _enrich_materials_via_pdps(
    api_headers: dict,
    cookies: dict,
    product_ids: list[str],
    concurrency: int = DEFAULT_CONCURRENCY,
    verbose: bool = True,
) -> dict[str, str]:
    if not product_ids:
        return {}
    timeout = httpx.Timeout(connect=10, read=30, write=15, pool=15)
    limits  = httpx.Limits(max_connections=max(concurrency * 2, 8))
    sem     = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(
        headers=api_headers, cookies=cookies, timeout=timeout, limits=limits,
    ) as client:
        results = await asyncio.gather(*[
            _fetch_pdp_fabric(client, pid, sem, verbose=verbose) for pid in product_ids
        ])
    return {pid: text for pid, text in zip(product_ids, results) if text}


# --------------------------------------------------------------------------- #
# Per-combo attribute extraction (pure)                                         #
# --------------------------------------------------------------------------- #

def _combo_to_row(
    combo: dict,
    scraped_at: str,
    retailer: str = "american_eagle",
    enriched_material_by_product: dict[str, str] | None = None,
) -> dict:
    """Project one (product × color) combo into the items_*.csv row shape."""
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


# --------------------------------------------------------------------------- #
# Streaming CSV writer                                                          #
# --------------------------------------------------------------------------- #

class StreamingItemWriter:
    def __init__(self, final_path: Path, resume: bool = False) -> None:
        self.final_path   = final_path
        self.partial_path = final_path.with_name(final_path.stem + "_partial.csv")
        self._resume      = resume
        self._existing: set[tuple[str, str, str]] = set()
        self._handle      = None
        self._writer      = None

    def __enter__(self) -> "StreamingItemWriter":
        if self._resume and self.partial_path.exists():
            with self.partial_path.open("r", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    self._existing.add((
                        row.get("style_id", ""),
                        row.get("cc_id", ""),
                        row.get("gender", ""),
                    ))
            print(f"  [resume] loaded {len(self._existing)} prior keys from {self.partial_path}")
            self._handle = self.partial_path.open("a", newline="")
            self._writer = csv.DictWriter(self._handle, fieldnames=CSV_FIELDNAMES)
        else:
            if self.partial_path.exists():
                self.partial_path.unlink()
            self._handle = self.partial_path.open("w", newline="")
            self._writer = csv.DictWriter(self._handle, fieldnames=CSV_FIELDNAMES)
            self._writer.writeheader()
            self._handle.flush()
        return self

    def already_have(self, style_id: str, cc_id: str, gender: str) -> bool:
        return (style_id, cc_id, gender) in self._existing

    def write(self, row: dict) -> None:
        self._writer.writerow(row)
        self._handle.flush()

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._handle is not None:
            self._handle.close()
        if exc_type is None:
            os.replace(self.partial_path, self.final_path)


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
# CLI                                                                           #
# --------------------------------------------------------------------------- #

def parse_args() -> argparse.Namespace:
    _synth = Path(__file__).resolve().parents[1] / "training" / "synthetic_data"
    default_items = _synth / "items_american_eagle.csv"
    parser = argparse.ArgumentParser(
        description="Scrape American Eagle via the internal listing API "
                    "(Playwright bootstrap for JWT + Akamai cookies, then "
                    "pure httpx). Writes items_american_eagle.csv."
    )
    parser.add_argument("--items-path", default=str(default_items))
    parser.add_argument(
        "--concurrency", type=int, default=DEFAULT_CONCURRENCY,
        help=f"Concurrent API page fetches (default {DEFAULT_CONCURRENCY}). "
             "AE Akamai 403s become common above 4.",
    )
    parser.add_argument(
        "--max-products-per-page", type=int, default=None,
        help="Loose cap per target. Use small value for smoke tests.",
    )
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
        f"American Eagle retail scraper (API mode)\n"
        f"  targets:     {len(AE_TARGETS)}  concurrency: {args.concurrency}  ({cap_msg})\n"
        f"  output:      {items_path}\n"
        f"  resume:      {args.resume}\n"
        f"  enrich-pdp:  {args.enrich_pdp}\n"
    )

    scraped_at = datetime.date.today().isoformat()
    start = time.perf_counter()

    print("Phase 0: Playwright bootstrap (capture browser headers + Akamai cookies) ...")
    try:
        api_headers, cookies = asyncio.run(_bootstrap_session())
    except RuntimeError as exc:
        print(f"  bootstrap failed: {exc}")
        sys.exit(2)

    print("\nPhase 1: paginating AE catalog API across 9 targets ...")
    combos, totals = asyncio.run(_scrape_ae_via_api(
        api_headers=api_headers,
        cookies=cookies,
        targets=AE_TARGETS,
        concurrency=args.concurrency,
        max_products_per_page=args.max_products_per_page,
    ))

    enriched: dict[str, str] = {}
    if args.enrich_pdp:
        unknown_pids: list[str] = []
        seen: set[str] = set()
        for combo in combos:
            pid = combo["product_id"]
            if pid in seen:
                continue
            seen.add(pid)
            if not has_explicit_material_keyword(combo["name"]):
                unknown_pids.append(pid)
        if unknown_pids:
            print(
                f"\nPhase 1.5: enriching material for {len(unknown_pids)} "
                f"products via product-detail API ..."
            )
            t0 = time.perf_counter()
            enriched = asyncio.run(_enrich_materials_via_pdps(
                api_headers=api_headers, cookies=cookies,
                product_ids=unknown_pids, concurrency=args.concurrency,
                verbose=False,
            ))
            print(
                f"  enriched {len(enriched)}/{len(unknown_pids)} PDPs "
                f"in {time.perf_counter()-t0:.1f}s "
                f"({len(unknown_pids) - len(enriched)} returned no fabric bullet)"
            )
        else:
            print("\nPhase 1.5: no products need PDP enrichment (skipping).")

    print(f"\nPhase 2: writing items CSV ({len(combos)} combos to project) ...")
    written = 0
    skipped = 0
    with StreamingItemWriter(items_path, resume=args.resume) as writer:
        for combo in combos:
            row = _combo_to_row(
                combo, scraped_at=scraped_at, retailer="american_eagle",
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

    print("\nCompleteness vs AE API totalProducts (per target):")
    short = False
    for target in AE_TARGETS:
        label  = target["label"]
        gender = target["gender"]
        # Count unique products per gender that contain rows from this label.
        # We can't easily attribute rows back to specific cat IDs in the CSV
        # (we deduped across targets), so report by gender.
        unique_for_gender = len({r.get("style_id") for r in rows if r.get("gender") == gender})
        total = totals.get(label, 0)
        print(f"  {label:<22} api total={total:>4}  (gender unique={unique_for_gender})")
    # Cross-PLP overlap means per-target completeness is hard to verify per
    # row. The right gate is "we got >= total of any single PLP per gender".

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


if __name__ == "__main__":
    main()
