"""
Uniqlo retail scraper for trndly trend signals.

Pulls Uniqlo's full women + men catalog directly from Uniqlo's internal
listing API (no browser, no scrolling, no clicking). The endpoint returns
the catalog paginated up to 100 items per page, and each item carries
its own colors and PDP slug — everything we need to build
items_uniqlo.csv without visiting any product detail page.

OUTPUT
------
items_uniqlo.csv  — one row per (product × color variant). Schema mirrors
items_gap.csv (additive provenance fields stay Gap-specific; for Uniqlo
`web_product_type` is "" since the API doesn't expose a category label).

LISTING API
-----------
GET https://www.uniqlo.com/us/api/commerce/v5/en/products
  ?path={genderId},,,
  &genderId={genderId}
  &offset={n}&limit=100
  &httpFailure=true

Response (relevant slice):
  {
    "status": "ok",
    "result": {
      "pagination": {"total": 698, "offset": 0, "count": 100},
      "items": [
        {
          "productId": "E482195-000",
          "l1Id":      "482195",
          "name":      "Ribbed Cropped Bra Top",
          "genderName": "WOMEN",
          "colors": [
            {"code": "COL18", "displayCode": "18",
             "name": "WINE", "filterCode": "RED"},
            ...
          ],
          ...
        }, ...
      ]
    }
  }

Page size is server-capped at 100 (limit=120 → HTTP 400). Auth is unnecessary
— bare User-Agent + Accept-Language is enough.

PIPELINE SHAPE
--------------
Phase 1   — paginate the listing API. Fetch offset=0 first to learn
            `pagination.total`, then schedule offsets [100, 200, ...] in
            parallel under a Semaphore. Per-page retry on 429/5xx. Dedup
            per-target on `productId` (the API's ranking shuffle can return
            the same product in two cursor windows; ~3-4% drift is normal).

Phase 1.5 — (optional, default ON) For each unique productId whose title
            alone yields material=None, fetch the PDP HTML and regex-extract
            the embedded "composition" field from the inline Next.js JSON.
            Re-extract material from title + composition.

Phase 2   — Project each (productId × color) combo to a row via
            `_combo_to_row`, write streaming CSV via `StreamingItemWriter`.

CROSS-LISTING DEDUP
-------------------
Per-target dedup on `productId`. Cross-target the same productId with
different `gender` is kept as two rows (e.g. unisex caps that appear in
both genders). Resume key is (style_id, cc_id, gender), unique per row.

Setup
-----
  pip install httpx pandas

Usage
-----
  python uniqlo_scraper.py                            # full catalog + enrichment
  python uniqlo_scraper.py --concurrency 8            # faster API + PDP fan-out
  python uniqlo_scraper.py --max-products-per-page 5  # smoke test
  python uniqlo_scraper.py --no-enrich-pdp            # skip PDP material pass
  python uniqlo_scraper.py --resume                   # continue an interrupted run
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

# Single shop-all listing per gender — collapses the old 9-PLP fan-out
# (women new-arrivals + tops + bottoms + dresses + sweaters + men's
# equivalents) into 2 calls.
UNIQLO_TARGETS: list[dict] = [
    {"genderId": "22210", "gender": "women", "label": "women shop all"},
    {"genderId": "22211", "gender": "men",   "label": "men shop all"},
]

API_URL = "https://www.uniqlo.com/us/api/commerce/v5/en/products"
API_PAGE_SIZE = 100  # server caps at 100 — limit=120 returns HTTP 400.
API_BASE_PARAMS: dict[str, str] = {
    "httpFailure": "true",
    "imageRatio":  "3x4",
}
API_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin":          "https://www.uniqlo.com",
    "Referer":         "https://www.uniqlo.com/",
}

from pipelines.collectors._http_utils import (  # noqa: E402
    CSV_FIELDNAMES,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_RETRYABLE_STATUSES as RETRYABLE_STATUSES,
    StreamingItemWriter,
    request_with_retry as _request_with_retry,
)


async def _fetch_listing_page(
    client: httpx.AsyncClient,
    gender_id: str,
    offset: int,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    verbose: bool = True,
) -> dict | None:
    """One GET against the Uniqlo catalog API. Returns parsed JSON or None.
    Uniqlo returns `result.pagination.total` and `result.items[]` — see module
    docstring for the response shape.
    """
    params = {
        **API_BASE_PARAMS,
        "path":     f"{gender_id},,,",
        "genderId": gender_id,
        "offset":   str(offset),
        "limit":    str(API_PAGE_SIZE),
    }
    resp = await _request_with_retry(
        client, API_URL, params=params, max_attempts=max_attempts,
        label=f"api gender={gender_id} offset={offset}", verbose=verbose,
    )
    return resp.json() if resp is not None else None


async def _fetch_listings_for_target(
    client: httpx.AsyncClient,
    target: dict,
    semaphore: asyncio.Semaphore,
    verbose: bool = True,
) -> tuple[list[dict], int]:
    """Paginate one (genderId, gender) target to completion.

    Returns (combos, total_count) where combos is a list of one dict per
    (productId, color) pair (deduped on the pair within this target), and
    total_count is the API's reported `pagination.total` (the completeness
    oracle).
    """
    gender_id = target["genderId"]
    gender    = target["gender"]
    label     = target["label"]

    # First page tells us total + page count.
    async with semaphore:
        first = await _fetch_listing_page(client, gender_id, offset=0, verbose=verbose)
    if first is None or first.get("status") != "ok":
        print(f"  [{label}] FAILED to fetch offset 0 — aborting this target")
        return [], 0

    pagination = first.get("result", {}).get("pagination") or {}
    total_count = int(pagination.get("total", 0))
    print(f"  [{label}] total={total_count} pageSize={API_PAGE_SIZE}")

    pages: list[dict | None] = [first]
    if total_count > API_PAGE_SIZE:
        async def fetch_one(off: int) -> dict | None:
            async with semaphore:
                return await _fetch_listing_page(client, gender_id, offset=off, verbose=verbose)
        offsets = list(range(API_PAGE_SIZE, total_count, API_PAGE_SIZE))
        rest = await asyncio.gather(*[fetch_one(o) for o in offsets])
        pages.extend(rest)

    failed_offsets = [i * API_PAGE_SIZE for i, p in enumerate(pages) if p is None]
    if failed_offsets:
        print(f"  [{label}] WARNING: offsets {failed_offsets} failed permanently")

    # Dedup per-target on (productId, color.code). Across cursor windows the
    # API can shuffle — same productId may appear in two pages.
    combos: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for page_data in pages:
        if not page_data or page_data.get("status") != "ok":
            continue
        for item in page_data.get("result", {}).get("items", []) or []:
            product_id = str(item.get("productId") or "")
            l1_id      = str(item.get("l1Id") or "")
            name       = item.get("name") or ""
            for color in item.get("colors", []) or []:
                cc_code = str(color.get("code") or "")
                key = (product_id, cc_code)
                if key in seen:
                    continue
                seen.add(key)
                combos.append({
                    "product_id":     product_id,
                    "l1_id":          l1_id,
                    "name":           name,
                    "cc_code":        cc_code,
                    "cc_display":     str(color.get("displayCode") or ""),
                    "color_name":     color.get("name") or "",
                    "color_filter":   color.get("filterCode") or "",
                    "gender":         gender,
                })

    unique_products = len({c["product_id"] for c in combos})
    if total_count and unique_products < total_count:
        print(
            f"  [{label}] {unique_products}/{total_count} unique products "
            f"({total_count - unique_products} short — API ranking-shuffle drift, normal)"
        )
    elif total_count:
        print(f"  [{label}] collected {unique_products}/{total_count} unique products ✓")

    return combos, total_count


async def _scrape_uniqlo_via_api(
    targets: list[dict] = UNIQLO_TARGETS,
    concurrency: int = 6,
    max_products_per_page: int | None = None,
    verbose: bool = True,
) -> tuple[list[dict], dict[str, int]]:
    """Run all targets concurrently. Returns (combos, totals_by_label) where
    `totals_by_label` is the API's reported `pagination.total` per target.
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
    for target, (combos, total_count) in zip(targets, results):
        if max_products_per_page is not None:
            # Cap by unique productIds (not raw rows) so a 5-color hoodie
            # doesn't eat the whole budget.
            unique_pids: list[str] = []
            keep_ids: set[str] = set()
            for c in combos:
                if c["product_id"] not in keep_ids and len(keep_ids) < max_products_per_page:
                    keep_ids.add(c["product_id"])
                    unique_pids.append(c["product_id"])
            combos = [c for c in combos if c["product_id"] in keep_ids]
        all_combos.extend(combos)
        totals[target["label"]] = total_count
    return all_combos, totals


# --------------------------------------------------------------------------- #
# PDP material enrichment (2nd pass)                                            #
# --------------------------------------------------------------------------- #

# PDP HTML embeds the product JSON inline (Next.js __NEXT_DATA__). The
# "composition" field carries the fabric string. The escape level is single-
# JSON (one backslash-escaped layer), so a normal JSON-string regex matches.
PDP_URL_TEMPLATE = "https://www.uniqlo.com/us/en/products/{product_id}"
COMPOSITION_RE = re.compile(r'"composition"\s*:\s*"((?:\\.|[^"\\])*)"')
PDP_HEADERS = {**API_HEADERS, "Accept": "text/html,application/xhtml+xml,*/*"}


async def _fetch_pdp_fabric(
    client: httpx.AsyncClient,
    product_id: str,
    semaphore: asyncio.Semaphore,
    verbose: bool = True,
) -> str:
    """Fetch one PDP and return the decoded composition string. Returns ""
    on fetch failure or when the regex doesn't match.
    """
    url = PDP_URL_TEMPLATE.format(product_id=product_id)
    async with semaphore:
        resp = await _request_with_retry(
            client, url, label=f"pdp/{product_id}", verbose=verbose,
        )
    if resp is None:
        return ""
    m = COMPOSITION_RE.search(resp.text)
    if not m:
        return ""
    raw = m.group(1)
    # Decode \uXXXX, \", \\, etc. (single-layer JSON escapes).
    try:
        decoded = bytes(raw, "utf-8").decode("unicode_escape")
    except Exception:
        decoded = raw
    # The composition string sometimes carries embedded HTML <br>; flatten.
    return decoded.replace("<br>", " | ").replace("&lt;br&gt;", " | ")


async def _enrich_materials_via_pdps(
    product_ids: list[str],
    concurrency: int = 6,
    verbose: bool = True,
) -> dict[str, str]:
    """Concurrently fetch PDPs for each unique productId. Returns
    {product_id -> composition_text} with empty entries omitted.
    """
    if not product_ids:
        return {}
    timeout = httpx.Timeout(connect=10, read=30, write=15, pool=15)
    limits  = httpx.Limits(max_connections=max(concurrency * 2, 16))
    sem     = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(headers=PDP_HEADERS, timeout=timeout, limits=limits) as client:
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
    retailer: str = "uniqlo",
    enriched_material_by_product: dict[str, str] | None = None,
) -> dict:
    """Project one (product × color) combo into the items_uniqlo.csv row.

    Strategy mirrors gap_scraper:
      - title source: API `name` field
      - color source: color.name preferred (e.g. "WINE"), fall back to
        filterCode (e.g. "RED")
      - material source: title first; if title yields None and PDP enrichment
        has a composition string for this productId, re-extract on
        title + composition (uses the new percentage-aware extract_material
        in feature_lookups, so blends like "96% Cotton, 4% Spandex" map to
        cotton instead of polyester).
      - graphical: derived from color label first, fall back to title.

    style_id is the canonical PDP slug (`productId`, e.g. "E482195-000").
    cc_id is `{l1Id}-{displayCode}` for global uniqueness across products.
    web_product_type stays "" — Uniqlo's API doesn't expose a category label.
    """
    title       = combo["name"]
    color_name  = combo["color_name"] or ""
    color_label = color_name or combo["color_filter"] or ""
    gender      = combo["gender"]

    category = extract_category(title)
    # Authoritative material source order:
    #   1. PDP composition (if enrichment ran for this product) — never wrong
    #      about actual fabric content.
    #   2. Title with category-default fallback — always has *some* answer
    #      when category is recognized but title has no fabric word; the
    #      default (e.g. tops → cotton) can mis-classify synthetic-fabric
    #      products like AIRism polyester tees.
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
    cc_id = f"{combo['l1_id']}-{combo['cc_display']}" if combo.get("l1_id") else combo.get("cc_code", "")
    return {
        "scraped_at":               scraped_at,
        "retailer":                 retailer,
        "style_id":                 combo["product_id"],
        "cc_id":                    cc_id,
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
# Frequency counting / normalization                                            #
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
    default_items = items_csv_path_for("uniqlo")
    parser = argparse.ArgumentParser(
        description="Scrape Uniqlo via the internal listing API. Writes "
                    "items_uniqlo.csv (one row per product-color variant). "
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
        help="Cap unique products per target. None = no cap. "
             "Use a small value (e.g. 5) for smoke tests.",
    )
    parser.add_argument(
        "--resume", action="store_true",
        help="If items_uniqlo_partial.csv exists, skip already-written "
             "(style_id, cc_id, gender) keys and append the rest.",
    )
    parser.add_argument(
        "--strict", action="store_true",
        help="Exit non-zero if collected unique products < API total for any target.",
    )
    parser.add_argument(
        "--enrich-pdp", dest="enrich_pdp", action="store_true", default=True,
        help="Default ON. After Phase 1, fetch each PDP whose material can't be "
             "extracted from title alone, parse the embedded 'composition' JSON "
             "field, and use it for material extraction.",
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
        f"Uniqlo retail scraper (API mode)\n"
        f"  targets:     {len(UNIQLO_TARGETS)}  concurrency: {args.concurrency}  ({cap_msg})\n"
        f"  output:      {items_path}\n"
        f"  resume:      {args.resume}\n"
        f"  enrich-pdp:  {args.enrich_pdp}\n"
    )

    scraped_at = datetime.date.today().isoformat()
    start = time.perf_counter()

    print("Phase 1: paginating Uniqlo catalog API ...")
    combos, totals = asyncio.run(_scrape_uniqlo_via_api(
        targets=UNIQLO_TARGETS,
        concurrency=args.concurrency,
        max_products_per_page=args.max_products_per_page,
    ))

    enriched: dict[str, str] = {}
    if args.enrich_pdp:
        # Enrich every product whose title doesn't contain an explicit fabric
        # keyword. Products that resolve via category-default (e.g. tops →
        # cotton) are still enriched so synthetic-fabric tops don't lock in
        # a wrong answer before the PDP can speak.
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
                f"products via PDP composition fields ..."
            )
            t0 = time.perf_counter()
            enriched = asyncio.run(_enrich_materials_via_pdps(
                unknown_pids, concurrency=args.concurrency, verbose=False,
            ))
            print(
                f"  enriched {len(enriched)}/{len(unknown_pids)} PDPs "
                f"in {time.perf_counter()-t0:.1f}s "
                f"({len(unknown_pids) - len(enriched)} returned no composition)"
            )
        else:
            print("\nPhase 1.5: no products need PDP enrichment (skipping).")

    print(f"\nPhase 2: writing items CSV ({len(combos)} combos to project) ...")
    written = 0
    skipped = 0
    with StreamingItemWriter(items_path, resume=args.resume) as writer:
        for combo in combos:
            row = _combo_to_row(
                combo, scraped_at=scraped_at, retailer="uniqlo",
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

    print("\nCompleteness vs Uniqlo API total (unique productIds per target):")
    short = False
    for target in UNIQLO_TARGETS:
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
        print(f"  {label:<18} got {unique_ids:>4} / api {total:>4}   {ok}")

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
