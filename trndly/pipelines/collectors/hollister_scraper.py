"""
Hollister retail scraper for trndly trend signals.

Pulls Hollister's full women + men catalog directly from the SSR'd HTML
of two shop-all PLPs. The product catalog is embedded as an Apollo GraphQL
cache inside a `<script>` block — there's no separate JSON API to call.
Plain `httpx` over HTTP/1.1 with a desktop Chrome User-Agent passes
Akamai's edge fingerprint check (curl and HTTP/2 are blocked, but httpx
default behavior works).

OUTPUT
------
items_hollister.csv  — one row per (product × color variant). Schema
mirrors items_gap.csv. `web_product_type` stays "" for Hollister.

LOAD-BEARING DETAIL
-------------------
**Use httpx defaults: HTTP/1.1, Accept-Encoding gzip+brotli.** Do NOT set
`http2=True`. Akamai's bot check is satisfied by httpx's TLS fingerprint
but rejects HTTP/2 from anything that isn't a real browser. If the
implementation ever switches to HTTP/2 or to an HTTP client with a
curl-like fingerprint, you'll get 403 + "Bad Request / Reference ID..."
and need to fall back to the Playwright cookie-bootstrap pattern (the
scaffolding for that is in /tmp/hollister_bootstrap_test.py from the
recon).

PIPELINE SHAPE
--------------
Phase 1   — paginate the two shop-all PLPs. Each HTML response carries
            `productTotalCount` and `totalPages` in its embedded Apollo
            state. Walk `start in (0, 90, ..., 90*(totalPages-1))` in
            parallel under a Semaphore.

Phase 1.5 — (optional, default ON) For each unique productPageUrl whose
            title yields no explicit fabric keyword, GET the PDP HTML and
            regex-extract `"fabricDetails":"..."`.

Phase 2   — Project each (product × color) combo into the items CSV.

CROSS-LISTING DEDUP
-------------------
Per-target dedup on (product_id, swatch_id). Cross-target the same
(product_id, swatch_id, gender) is unique by construction (Hollister
products are gendered; same product never appears in both shop-alls).

Setup
-----
  pip install httpx pandas

Usage
-----
  python hollister_scraper.py
  python hollister_scraper.py --concurrency 6
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
HTTP_HEADERS = {
    "User-Agent":      USER_AGENT,
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Apollo state script-block marker. The Hollister page embeds the full
# catalog cache as: window['APOLLO_STATE__catalog-mfe-web-service-CategoryPageFrontEnd-config'] = {...};
APOLLO_STATE_PREFIX = "APOLLO_STATE__catalog-mfe-web-service-CategoryPageFrontEnd-config"
APOLLO_ASSIGN_RE = re.compile(rf"{re.escape(APOLLO_STATE_PREFIX)}[^=]*=\s*", re.S)

# PDP fabric extraction.
PDP_FABRIC_RE = re.compile(r'"fabricDetails":"((?:[^"\\]|\\.)*)"')

from functools import partial

from pipelines.collectors._http_utils import (  # noqa: E402
    CSV_FIELDNAMES,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_RETRYABLE_STATUSES,
    StreamingItemWriter,
    request_with_retry,
)

# Hollister includes 403 because Akamai sometimes 403s under load and yields
# on retry — request_with_retry's default set is transient (408/425/429/5xx).
RETRYABLE_STATUSES = DEFAULT_RETRYABLE_STATUSES | {403}
_request_with_retry = partial(request_with_retry, retryable_statuses=RETRYABLE_STATUSES)


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
    client: httpx.AsyncClient,
    slug: str,
    start: int,
    verbose: bool = True,
) -> str | None:
    url = PLP_URL_TEMPLATE.format(slug=slug)
    params = {"start": str(start)} if start else None
    resp = await _request_with_retry(
        client, url, params=params,
        label=f"plp slug={slug} start={start}", verbose=verbose,
    )
    return resp.text if resp is not None else None


async def _fetch_listings_for_target(
    client: httpx.AsyncClient,
    target: dict,
    semaphore: asyncio.Semaphore,
    verbose: bool = True,
) -> tuple[list[dict], int]:
    slug   = target["slug"]
    gender = target["gender"]
    label  = target["label"]

    async with semaphore:
        first_html = await _fetch_listing_page(client, slug, start=0, verbose=verbose)
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
            async with semaphore:
                html = await _fetch_listing_page(client, slug, start=start, verbose=verbose)
            if html is None:
                return []
            st = _parse_apollo_state(html)
            if st is None:
                return []
            combos, _, _ = _extract_combos_from_apollo(st, gender)
            return combos
        starts = [PAGE_SIZE * p for p in range(1, total_pages)]
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
    targets: list[dict] = HOLLISTER_TARGETS,
    concurrency: int = 6,
    max_products_per_page: int | None = None,
    verbose: bool = True,
) -> tuple[list[dict], dict[str, int]]:
    timeout = httpx.Timeout(connect=10, read=45, write=15, pool=15)
    limits  = httpx.Limits(max_connections=max(concurrency * 2, 16))
    sem     = asyncio.Semaphore(concurrency)

    # Critical: HTTP/1.1 (httpx default). Do NOT pass http2=True.
    async with httpx.AsyncClient(headers=HTTP_HEADERS, timeout=timeout, limits=limits) as client:
        results = await asyncio.gather(*[
            _fetch_listings_for_target(client, t, sem, verbose=verbose) for t in targets
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

async def _fetch_pdp_fabric(
    client: httpx.AsyncClient,
    url_path: str,
    semaphore: asyncio.Semaphore,
    verbose: bool = True,
) -> str:
    """Fetch one Hollister PDP HTML and return the joined fabricDetails
    strings. Empty string on miss.
    """
    if not url_path:
        return ""
    url = PDP_BASE + url_path if url_path.startswith("/") else url_path
    async with semaphore:
        resp = await _request_with_retry(
            client, url, label=f"pdp/{url_path[:60]}", verbose=verbose,
        )
    if resp is None:
        return ""
    matches = PDP_FABRIC_RE.findall(resp.text)
    bullets: list[str] = []
    for raw in matches:
        if not raw:
            continue
        try:
            decoded = bytes(raw, "utf-8").decode("unicode_escape")
        except Exception:
            decoded = raw
        if "%" in decoded:
            bullets.append(decoded)
    # Multiple swatch variants of the same product can each contribute a
    # fabricDetails string. Joining them lets the percentage-aware
    # extractor see all components.
    return " ".join(bullets)


async def _enrich_materials_via_pdps(
    items: list[tuple[str, str]],   # (product_id, url_path)
    concurrency: int = 6,
    verbose: bool = True,
) -> dict[str, str]:
    if not items:
        return {}
    timeout = httpx.Timeout(connect=10, read=45, write=15, pool=15)
    limits  = httpx.Limits(max_connections=max(concurrency * 2, 16))
    sem     = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(headers=HTTP_HEADERS, timeout=timeout, limits=limits) as client:
        results = await asyncio.gather(*[
            _fetch_pdp_fabric(client, url_path, sem, verbose=verbose) for _pid, url_path in items
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
    parser.add_argument("--concurrency", type=int, default=6)
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
        f"Hollister retail scraper (HTML/Apollo mode)\n"
        f"  targets:     {len(HOLLISTER_TARGETS)}  concurrency: {args.concurrency}  ({cap_msg})\n"
        f"  output:      {items_path}\n"
        f"  resume:      {args.resume}\n"
        f"  enrich-pdp:  {args.enrich_pdp}\n"
    )

    scraped_at = datetime.date.today().isoformat()
    start = time.perf_counter()

    print("Phase 1: paginating Hollister shop-all PLPs ...")
    combos, totals = asyncio.run(_scrape_hollister_via_html(
        targets=HOLLISTER_TARGETS,
        concurrency=args.concurrency,
        max_products_per_page=args.max_products_per_page,
    ))

    enriched: dict[str, str] = {}
    if args.enrich_pdp:
        # Enrich every product whose title doesn't carry an explicit fabric
        # keyword. Keyed by product_id; each gets one PDP fetch even if the
        # product has many color variants.
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
            enriched = asyncio.run(_enrich_materials_via_pdps(
                unknown_pairs, concurrency=args.concurrency, verbose=False,
            ))
            print(
                f"  enriched {len(enriched)}/{len(unknown_pairs)} PDPs "
                f"in {time.perf_counter()-t0:.1f}s "
                f"({len(unknown_pairs) - len(enriched)} returned no fabric data)"
            )
        else:
            print("\nPhase 1.5: no products need PDP enrichment (skipping).")

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
