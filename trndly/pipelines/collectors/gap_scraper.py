"""
Gap retail scraper for trndly trend signals.

Pulls Gap's "Shop All Styles" catalog for men and women directly from Gap's
internal listing API (no browser, no scrolling, no clicking) and optionally
enriches material extraction by fetching each PDP's "Fabric & care" block
over plain HTTPS. End-to-end run is ~17s for the full ~5,200-row catalog.

OUTPUTS
-------
items_gap.csv  — one row per (product × color variant). Schema:

  scraped_at, retailer,
  style_id, cc_id, web_product_type,        (provenance from Gap API)
  title, gender,
  color_raw, product_type_raw, material_raw, graphical_appearance_raw,
  color_master_id, color_spectrum_id, gender_id,
  product_type_id, product_group_id, material_id, graphical_appearance_id

items_gap.csv is consumed by `build_live_cube.py`, which builds the live
fingerprint + univariate parquets that merge into the historical cubes.

LISTING API
-----------
GET https://api.gap.com/commerce/search/products/v2/cc
  ?cid={category_id}&department={dept_id}
  &pageSize=200&pageNumber={n}
  &vendor=constructorio&brand=gap&locale=en_US&market=us
  &session_id=1&ignoreInventory=false
  &includeMarketingFlagsDetails=true&enableDynamicPhoto=true

Response (relevant slice):
  {
    "totalColors": 1145,                   # truth for completeness
    "pagination": {"pageNumberTotal":"6"}, # how many pages to fetch
    "products": [
      {
        "styleId": "737295",
        "styleName": "Adult VintageSoft Classic Joggers",
        "webProductType": "mens pants",
        "styleColors": [
          {"ccId":"737295122", "ccName":"Blue",
           "ccShortDescription":"Tapestry navy blue", ...},
          ...
        ]
      }, ...
    ]
  }

Page size is server-capped at 200. Auth is unnecessary — a plain User-Agent
+ Accept-Language header is enough. The single value `totalColors` is the
canonical completeness oracle (matches the "X Results" header in the UI).

PIPELINE SHAPE
--------------
Phase 1   — paginate the listing API. Fetch page 0 of each target to learn
            `pageNumberTotal`, then schedule the rest in parallel under a
            shared Semaphore (default concurrency=6). Per-page retry on
            429/5xx with exponential backoff + jitter. Pure httpx; no
            browser. `_fetch_listing_page`, `_fetch_listings_for_target`,
            `_scrape_gap_via_api`.

Phase 1.5 — (optional, default ON) For each unique style_id whose title
            alone yields material=None, fetch the PDP HTML and regex-extract
            its embedded "Fabric & care" bullets. Re-extract material from
            title + bullets. Drops material_unknown rate from ~14% → ~1.7%.
            ~10–30s for ~230 PDPs at concurrency 8. Disable with
            --no-enrich-pdp. `_fetch_pdp_fabric`,
            `_enrich_materials_via_pdps`.

Phase 2   — Project each (product × color) combo to a row via
            `_combo_to_row`, write streaming CSV via `StreamingItemWriter`.
            Atomic rename on clean exit. Then build_live_cube aggregates
            items_*.csv into the live fingerprint + univariate parquets.

KEY EXTRACTION CHOICES
----------------------
- title source:   `styleName` (Gap's own product name)
- color source:   `ccShortDescription` (e.g. "Tapestry navy blue") preferred
                  over `ccName` ("Blue") — richer for COLOR_KEYWORDS.
- product_type:   `extract_product_type(title) or extract_product_type(web_product_type)`
                  — webProductType ("mens pants", "womens bras") fills in
                  Gap-specific labels the title misses (e.g. "Modern Straight
                  Khakis"). The intimates/swim/sleepwear keywords added to
                  feature_lookups.PRODUCT_TYPE_KEYWORDS rely on this.
- material:       `extract_material(title)` first; if None and PDP enrichment
                  is enabled, re-extract on `title + fabric_bullets`. Known
                  limitation: extract_material is keyword-priority, so blends
                  like "98% Cotton, 2% Elastane" mis-bucket as polyester.
- graphical:      derived from color label first, falls back to title.

CROSS-LISTING DEDUP
-------------------
Some products (unisex hoodies etc.) appear in BOTH the men's and women's
catalogs. Per-target dedup is on (style_id, cc_id); cross-target the same
(style_id, cc_id) with different `gender` is kept as two rows so each PLP's
count matches the API's own `totalColors`. The resume key is therefore
(style_id, cc_id, gender), which is unique per row.

CHECKPOINTING
-------------
Rows are streamed to items_gap_partial.csv as they're produced. On clean
exit the partial file is atomically renamed to the final path. If --resume
is passed and the partial exists, its (style_id, cc_id, gender) keys are
loaded and skipped.

Setup
-----
  pip install httpx pandas

Usage
-----
  python gap_scraper.py                            # full catalog + enrichment
  python gap_scraper.py --concurrency 8            # faster API + PDP fan-out
  python gap_scraper.py --max-products-per-page 5  # smoke test
  python gap_scraper.py --no-enrich-pdp            # skip PDP material pass
  python gap_scraper.py --resume                   # continue an interrupted run
  python gap_scraper.py --strict                   # exit nonzero on shortfall
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import datetime
import os
import random
import re
import sys
import time
from pathlib import Path

import httpx
import pandas as pd

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
# Targets and API config                                                        #
# --------------------------------------------------------------------------- #

# (cid, department) per gender. The pair maps 1:1 to the URL fragment used by
# the human-facing PLP — `#pageId=0&department=N` plus the `cid=` query param.
GAP_TARGETS: list[dict] = [
    {"cid": "1127944", "department": "75",  "gender": "men",   "label": "men shop all"},
    {"cid": "1127938", "department": "136", "gender": "women", "label": "women shop all"},
]

API_URL = "https://api.gap.com/commerce/search/products/v2/cc"
API_PAGE_SIZE = 200  # server caps at 200 — pageSize=1000 returns HTTP 400.
API_BASE_PARAMS: dict[str, str] = {
    "ignoreInventory":             "false",
    "vendor":                      "constructorio",
    "session_id":                  "1",
    "includeMarketingFlagsDetails": "true",
    "enableDynamicPhoto":          "true",
    "brand":                       "gap",
    "locale":                      "en_US",
    "market":                      "us",
}
API_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin":          "https://www.gap.com",
    "Referer":         "https://www.gap.com/",
}

from pipelines.collectors._http_utils import (  # noqa: E402
    CSV_FIELDNAMES,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_RETRYABLE_STATUSES as RETRYABLE_STATUSES,
    StreamingItemWriter,
    request_with_retry as _request_with_retry,
)


# --------------------------------------------------------------------------- #
# API client                                                                    #
# --------------------------------------------------------------------------- #


async def _fetch_listing_page(
    client: httpx.AsyncClient,
    cid: str,
    department: str,
    page_number: int,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    verbose: bool = True,
) -> dict | None:
    """One GET against the catalog API. Returns parsed JSON or None."""
    params = {
        **API_BASE_PARAMS,
        "cid":         cid,
        "department":  department,
        "pageSize":    str(API_PAGE_SIZE),
        "pageNumber":  str(page_number),
    }
    resp = await _request_with_retry(
        client, API_URL, params=params, max_attempts=max_attempts,
        label=f"api cid={cid} p={page_number}", verbose=verbose,
    )
    return resp.json() if resp is not None else None


async def _fetch_listings_for_target(
    client: httpx.AsyncClient,
    target: dict,
    semaphore: asyncio.Semaphore,
    verbose: bool = True,
) -> tuple[list[dict], int]:
    """Paginate one (cid, department) pair to completion.

    Returns (combos, total_colors) where combos is a list of
    {style_id, style_name, web_product_type, cc_id, cc_name,
     cc_short_description, gender} dicts deduped on (style_id, cc_id),
     and total_colors is the API's reported total (the completeness oracle).
    """
    cid    = target["cid"]
    dept   = target["department"]
    gender = target["gender"]
    label  = target["label"]

    # First page tells us total + page count.
    async with semaphore:
        first = await _fetch_listing_page(client, cid, dept, 0, verbose=verbose)
    if first is None:
        print(f"  [{label}] FAILED to fetch page 0 — aborting this target")
        return [], 0

    total_colors = int(first.get("totalColors", 0))
    pagination   = first.get("pagination") or {}
    page_total   = int(pagination.get("pageNumberTotal", 1))
    print(f"  [{label}] totalColors={total_colors} pages={page_total}")

    pages: list[dict | None] = [first]
    if page_total > 1:
        async def fetch_one(pn: int) -> dict | None:
            async with semaphore:
                return await _fetch_listing_page(client, cid, dept, pn, verbose=verbose)
        rest = await asyncio.gather(*[fetch_one(pn) for pn in range(1, page_total)])
        pages.extend(rest)

    failed_offsets = [i for i, p in enumerate(pages) if p is None]
    if failed_offsets:
        print(f"  [{label}] WARNING: pages {failed_offsets} failed permanently")

    combos: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for page_data in pages:
        if not page_data:
            continue
        for prod in page_data.get("products", []) or []:
            style_id    = str(prod.get("styleId") or "")
            style_name  = prod.get("styleName") or ""
            web_pt      = prod.get("webProductType") or ""
            for sc in prod.get("styleColors", []) or []:
                cc_id = str(sc.get("ccId") or "")
                key = (style_id, cc_id)
                if key in seen:
                    continue
                seen.add(key)
                combos.append({
                    "style_id":             style_id,
                    "style_name":           style_name,
                    "web_product_type":     web_pt,
                    "cc_id":                cc_id,
                    "cc_name":              sc.get("ccName") or "",
                    "cc_short_description": sc.get("ccShortDescription") or "",
                    "gender":               gender,
                })

    if total_colors and len(combos) < total_colors:
        print(
            f"  [{label}] short by {total_colors - len(combos)} "
            f"(got {len(combos)}/{total_colors}). Pages with no data: {failed_offsets or 'none'}"
        )
    elif total_colors:
        print(f"  [{label}] collected {len(combos)}/{total_colors} color combos ✓")

    return combos, total_colors


async def _scrape_gap_via_api(
    targets: list[dict] = GAP_TARGETS,
    concurrency: int = 6,
    max_products_per_page: int | None = None,
    verbose: bool = True,
) -> tuple[list[dict], dict[str, int]]:
    """Run all targets concurrently (each target paginates internally). Returns
    (combos, totals_by_label) where `totals_by_label` is the API's reported
    totalColors per target — used by the caller for the completeness assertion.
    """
    timeout = httpx.Timeout(connect=10, read=30, write=15, pool=15)
    limits  = httpx.Limits(max_connections=max(concurrency * 2, 16))
    sem     = asyncio.Semaphore(concurrency)

    async with httpx.AsyncClient(headers=API_HEADERS, timeout=timeout, limits=limits) as client:
        results = await asyncio.gather(*[
            _fetch_listings_for_target(client, t, sem, verbose=verbose) for t in targets
        ])

    all_combos: list[dict] = []
    totals: dict[str, int] = {}
    for target, (combos, total_colors) in zip(targets, results):
        if max_products_per_page is not None:
            combos = combos[:max_products_per_page]
        all_combos.extend(combos)
        totals[target["label"]] = total_colors
    return all_combos, totals


# --------------------------------------------------------------------------- #
# PDP material enrichment (2nd pass)                                            #
# --------------------------------------------------------------------------- #

# PDP HTML embeds a "Fabric & care" block inside a serialized JS string. The
# JSON inside is escaped: `"` → `\"`, `&` → `&`. So matching needs
# `\\"` for each escaped quote and `\\u0026` for the encoded ampersand.
PDP_URL_TEMPLATE = "https://www.gap.com/browse/product.do?pid={style_id}"
FABRIC_BULLETS_RE = re.compile(
    r'\\"label\\":\\"Fabric \\u0026 care\\".*?\\"bullets\\":\[(.*?)\]', re.S
)
ESCAPED_STR_RE = re.compile(r'\\"((?:[^"\\]|\\.)*?)\\"')
PDP_HEADERS = {**API_HEADERS, "Accept": "text/html,application/xhtml+xml,*/*"}


async def _fetch_pdp_fabric(
    client: httpx.AsyncClient,
    style_id: str,
    semaphore: asyncio.Semaphore,
    verbose: bool = True,
) -> str:
    """Fetch one PDP and return the joined "Fabric & care" bullets text.
    Returns "" on fetch failure or when the regex doesn't match (e.g. PDP
    has no fabric block at all). The caller treats both cases identically.
    """
    url = PDP_URL_TEMPLATE.format(style_id=style_id)
    async with semaphore:
        resp = await _request_with_retry(
            client, url, label=f"pdp/{style_id}", verbose=verbose,
        )
    if resp is None:
        return ""
    m = FABRIC_BULLETS_RE.search(resp.text)
    if not m:
        return ""
    bullets: list[str] = []
    for esc in ESCAPED_STR_RE.findall(m.group(1)):
        try:
            bullets.append(bytes(esc, "utf-8").decode("unicode_escape"))
        except Exception:
            bullets.append(esc)
    return " ".join(bullets)


async def _enrich_materials_via_pdps(
    style_ids: list[str],
    concurrency: int = 6,
    verbose: bool = True,
) -> dict[str, str]:
    """Concurrently fetch PDPs for each unique styleId. Returns
    {style_id -> fabric_text} with empty entries omitted.
    """
    if not style_ids:
        return {}
    timeout = httpx.Timeout(connect=10, read=30, write=15, pool=15)
    limits  = httpx.Limits(max_connections=max(concurrency * 2, 16))
    sem     = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(headers=PDP_HEADERS, timeout=timeout, limits=limits) as client:
        results = await asyncio.gather(*[
            _fetch_pdp_fabric(client, sid, sem, verbose=verbose) for sid in style_ids
        ])
    return {sid: text for sid, text in zip(style_ids, results) if text}


# --------------------------------------------------------------------------- #
# Per-combo attribute extraction (pure)                                         #
# --------------------------------------------------------------------------- #

def _combo_to_row(
    combo: dict,
    scraped_at: str,
    retailer: str = "gap",
    enriched_material_by_style: dict[str, str] | None = None,
) -> dict:
    """Project one (product × color) combo into the items_gap.csv row shape,
    populating every lookup-table ID. Pure; no I/O.

    Strategy:
      - title source: styleName (Gap's own product name)
      - color source: ccShortDescription if present (e.g. "Tapestry navy blue"),
        else ccName ("Blue"). Short description matches richer keywords in
        feature_lookups.COLOR_KEYWORDS.
      - material source: PDP fabric block (authoritative) when enrichment
        fetched it; otherwise title with category-default fallback. The
        percentage-aware extractor handles blends like "98% Cotton, 2%
        Elastane" → cotton.
      - graphical: derived from color label first; fall back to title.
    """
    title       = combo["style_name"]
    color_label = combo["cc_short_description"] or combo["cc_name"] or ""
    gender      = combo["gender"]
    web_pt      = combo.get("web_product_type") or ""

    category = extract_category(title)
    enriched = ""
    if enriched_material_by_style is not None:
        enriched = enriched_material_by_style.get(combo["style_id"], "")
    if enriched:
        material = extract_material(enriched, inferred_category=category)
    else:
        material = extract_material(title, inferred_category=category)
    # Title-based product_type misses Gap-specific labels like "Modern Straight
    # Khakis" (no keyword for "khaki"). Gap's own webProductType ("mens pants",
    # "womens bras", "body lounge bottoms") is unambiguous and covers the gap.
    product_type   = extract_product_type(title) or extract_product_type(web_pt)
    base_graphical = extract_graphical_appearance(title)

    color = extract_color(color_label) or extract_color(title)
    graphical = extract_graphical_appearance(color_label)
    if graphical == "Solid":
        graphical = base_graphical

    color_raw = color_label or "unknown"
    return {
        "scraped_at":               scraped_at,
        "retailer":                 retailer,
        "style_id":                 combo["style_id"],
        "cc_id":                    combo["cc_id"],
        "web_product_type":         combo["web_product_type"],
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
    """Re-read whatever's been written for end-of-run summary stats."""
    if not path.exists():
        return []
    with path.open("r", newline="") as f:
        return list(csv.DictReader(f))


# --------------------------------------------------------------------------- #
# Frequency counting / normalization (unchanged from prior version)             #
# --------------------------------------------------------------------------- #

def count_attribute_frequencies(
    rows: list[dict],
) -> tuple[dict[str, dict[str, int]], int]:
    """One row per (product × color). Group by (title, gender) so a 5-color
    tee contributes once per category/material but once per distinct color.
    """
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
        # In the API path color_raw is a swatch label; map to canonical bucket.
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
    counts: dict[str, dict[str, int]],
    total_items: int,
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
    default_items = items_csv_path_for("gap")
    parser = argparse.ArgumentParser(
        description="Scrape Gap via the internal listing API. Writes "
                    "items_gap.csv (one row per product-color variant). "
                    "build_live_cube.py aggregates items_*.csv into "
                    "live_fingerprint_<YYYY-MM>.parquet + live_univariate_<YYYY-MM>.parquet."
    )
    parser.add_argument(
        "--items-path", default=str(default_items),
        help="Where to write the raw items CSV (one row per product-color variant).",
    )
    parser.add_argument(
        "--concurrency", type=int, default=6,
        help="Concurrent API page fetches and PDP fetches (default 6).",
    )
    parser.add_argument(
        "--max-products-per-page", type=int, default=None,
        help="Cap rows per target (post-API, post-dedup). None = no cap. "
             "Use a small value (e.g. 5) for smoke tests.",
    )
    parser.add_argument(
        "--resume", action="store_true",
        help="If items_gap_partial.csv exists, skip already-written "
             "(style_id, cc_id, gender) keys and append the rest.",
    )
    parser.add_argument(
        "--strict", action="store_true",
        help="Exit non-zero if collected rows < API totalColors for any target.",
    )
    parser.add_argument(
        "--enrich-pdp", dest="enrich_pdp", action="store_true", default=True,
        help="Default ON. After Phase 1, fetch each PDP whose material can't be "
             "extracted from title alone, parse the embedded 'Fabric & care' "
             "block, and use it for material extraction. Adds ~10–30s to a "
             "full run; reduces material_raw='unknown' from ~14%% to ~1.7%%.",
    )
    parser.add_argument(
        "--no-enrich-pdp", dest="enrich_pdp", action="store_false",
        help="Skip the PDP enrichment pass (faster smoke testing).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    items_path = Path(args.items_path).expanduser().resolve()
    items_path.parent.mkdir(parents=True, exist_ok=True)

    cap_msg = "no cap" if args.max_products_per_page is None else f"max {args.max_products_per_page}/target"
    print(
        f"Gap retail scraper (API mode)\n"
        f"  targets:     {len(GAP_TARGETS)}  concurrency: {args.concurrency}  ({cap_msg})\n"
        f"  output:      {items_path}\n"
        f"  resume:      {args.resume}\n"
        f"  enrich-pdp:  {args.enrich_pdp}\n"
    )

    scraped_at = datetime.date.today().isoformat()
    start = time.perf_counter()

    print("Phase 1: paginating Gap catalog API ...")
    combos, totals = asyncio.run(_scrape_gap_via_api(
        targets=GAP_TARGETS,
        concurrency=args.concurrency,
        max_products_per_page=args.max_products_per_page,
    ))

    # Phase 1.5 (optional): enrich material via PDP fabric blocks for products
    # whose title alone yields material=None. Per-style_id, not per-row, since
    # one PDP covers all color variants of that product.
    enriched: dict[str, str] = {}
    if args.enrich_pdp:
        unknown_style_ids: list[str] = []
        seen: set[str] = set()
        for combo in combos:
            sid = combo["style_id"]
            if sid in seen:
                continue
            seen.add(sid)
            # Enrich every product whose title doesn't carry an explicit
            # fabric keyword. Products that resolve via category-default
            # (e.g. tops → cotton) are still enriched so synthetic-fabric
            # tops don't lock in a wrong answer before the PDP can speak.
            if not has_explicit_material_keyword(combo["style_name"]):
                unknown_style_ids.append(sid)
        if unknown_style_ids:
            print(
                f"\nPhase 1.5: enriching material for {len(unknown_style_ids)} "
                f"products via PDP fabric blocks ..."
            )
            t0 = time.perf_counter()
            enriched = asyncio.run(_enrich_materials_via_pdps(
                unknown_style_ids, concurrency=args.concurrency, verbose=False,
            ))
            print(
                f"  enriched {len(enriched)}/{len(unknown_style_ids)} PDPs "
                f"in {time.perf_counter()-t0:.1f}s "
                f"({len(unknown_style_ids) - len(enriched)} returned no fabric block)"
            )
        else:
            print("\nPhase 1.5: no products need PDP enrichment (skipping).")

    print(f"\nPhase 2: writing items CSV ({len(combos)} combos to project) ...")
    written = 0
    skipped = 0
    with StreamingItemWriter(items_path, resume=args.resume) as writer:
        for combo in combos:
            if writer.already_have(combo["style_id"], combo["cc_id"], combo["gender"]):
                skipped += 1
                continue
            writer.write(_combo_to_row(
                combo, scraped_at=scraped_at, retailer="gap",
                enriched_material_by_style=enriched,
            ))
            written += 1
    elapsed = time.perf_counter() - start

    if args.resume and skipped:
        print(f"  [resume] skipped {skipped} previously written rows; appended {written}")

    # End-of-run summary uses the final CSV (handles --resume cleanly).
    rows = _read_existing_rows(items_path)
    print(f"\nWrote {len(rows)} rows → {items_path}")
    mins, secs = divmod(elapsed, 60)
    print(f"Elapsed: {int(mins)}m {secs:.1f}s")

    # Completeness check (skipped when --max-products-per-page is in effect,
    # since the row count is capped, not the API).
    print("\nCompleteness vs Gap API totalColors:")
    short = False
    for target in GAP_TARGETS:
        label  = target["label"]
        gender = target["gender"]
        seen   = sum(1 for r in rows if r.get("gender") == gender)
        total  = totals.get(label, 0)
        delta  = total - seen
        if args.max_products_per_page is not None:
            ok = "(capped)"
        elif delta <= 0:
            ok = "OK"
        else:
            ok = "SHORT"
            short = True
        print(f"  {label:<18} got {seen:>5} / api {total:>5}   {ok}")

    # Attribute coverage summary (informational).
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
