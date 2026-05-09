# Retail collectors

API-first scrapers for **Gap**, **Hollister**, **Uniqlo**, and **American
Eagle** that produce raw per-(product × color) rows in
`items_<retailer>.csv`. A separate aggregator (`build_live_cube.py`) reads
every items file and builds two parquets — a 5-D fingerprint cube and a
long-format univariate cube — under `data/processed/`. Schemas mirror the
historical cubes from `notebooks/1_aggregate_historical.ipynb` so
`notebook 1b` can `pd.concat` historical + live rows into a single merged
universe.

## Architecture

```
gap_scraper.py      ─┐
uniqlo_scraper.py    ├─►  items_<retailer>.csv  (one row per product × color)
american_eagle_..    │           │
hollister_scraper.py ┘           ▼
                          build_live_cube.py
                                 │
                                 ├─► data/processed/live_fingerprint_<YYYY-MM>.parquet
                                 └─► data/processed/live_univariate_<YYYY-MM>.parquet
                                                  │
                                                  ▼
                                  notebooks/1b_scrape_aggregate_live.ipynb
                                  (read historical_*.parquet + glob(live_*_*.parquet);
                                   pd.concat with dedup on (month, fingerprint, source))
                                                  │
                                                  ▼
                                  data/processed/merged_fingerprint.parquet
                                  data/processed/merged_univariate.parquet
                                                  │
                                                  ▼
                       hmn_seasonal_processor.py / scheduleServer / Notebooks 2–5
```

Each scraper:

1. **Phase 1** — paginates the retailer's internal listing API (or
  server-rendered HTML for Hollister) to get every product in scope.
   Per-target dedup; per-page retries with exponential backoff + jitter.
2. **Phase 1.5** *(optional, default ON)* — for products whose title
  doesn't carry an explicit fabric keyword, fetch the PDP and extract
   the composition string ("78% Polyester, 17% Lyocell, 5% Spandex").
3. **Phase 2** — project each (product × color) combo into the shared
  18-column items schema. Stream-write to a partial CSV; atomic-rename
   on clean exit.

`build_live_cube.py` reads every `items_*.csv`, derives `month` from
`scraped_at` (truncated to month-start), groups by the 5 historical
fingerprint dims (`product_type_id`, `gender_id`, `color_master_id`,
`graphical_appearance_id`, `material_id`), and writes both cubes with
`source='live'` Categoricals. The univariate cube emits one row per
`(month, dimension, level_id)` for the same 5 dims — `color_spectrum`
and `product_group` are dropped from live output (mostly noise / fully
derivable from `product_type`).

## items_.csv schema (18 columns)


| Column                     | Source                                                           | Notes                                                    |
| -------------------------- | ---------------------------------------------------------------- | -------------------------------------------------------- |
| `scraped_at`               | runtime                                                          | ISO date — month-truncated by build_live_cube            |
| `retailer`                 | constant                                                         | `gap` / `uniqlo` / `american_eagle` / `hollister`        |
| `style_id`                 | retailer's product id                                            | unique per base product                                  |
| `cc_id`                    | retailer's color/swatch id                                       | unique per (product, color)                              |
| `web_product_type`         | retailer category label                                          | Gap-only ("mens pants", "womens bras"); empty for others |
| `title`                    | retailer product name                                            | source for product_type / material extraction            |
| `gender`                   | derived from target                                              | `women` / `men`                                          |
| `color_raw`                | retailer color label                                             | rich form when available (e.g. "Tapestry navy blue")     |
| `product_type_raw`         | `extract_product_type(title)` (with `web_product_type` fallback) | from `feature_lookups.PRODUCT_TYPE_KEYWORDS`             |
| `material_raw`             | PDP composition (if enriched) → title fallback                   | percentage-aware                                         |
| `graphical_appearance_raw` | `extract_graphical_appearance(color, then title)`                | defaults to `Solid`                                      |
| `*_id` columns (7)         | per-feature-value lookup IDs                                     | resolves against `data/processed/lookup.csv`             |


The `*_id` columns are the canonical universe for the cubes. `lookup.csv`
is the single source of truth; `feature_lookups._assert_lookup_csv_matches_dicts()`
runs at module import and raises if the hand-written `*_TO_ID` dicts
drift from the CSV. The companion `_warn_unreachable_lookup_ids()`
emits an `UnreachableLookupIDWarning` if a lookup ID becomes unreachable
from the dicts and isn't in `_DELIBERATELY_UNREACHABLE_LOOKUP_IDS`.

**Full per-dimension audit — which IDs each pipeline source actually
populates, and which are deliberately unreachable from live — lives in
[`data/processed/SCHEMA.md`](../../data/processed/SCHEMA.md).** Read that
before adding/removing buckets so you don't accidentally collapse a
dimension or shadow an existing keyword.

## Live cube schemas (data/processed/live_*_.parquet)

`build_live_cube.py` writes ONE parquet per snapshot month, named with
the cube's `month` value formatted `YYYY-MM` (always month-start since
`scraped_at` is truncated). Re-running within May overwrites
`live_*_2026-05.parquet`; June produces `live_*_2026-06.parquet`.

`**live_fingerprint_<YYYY-MM>.parquet`** — 11 columns, grain=`(month, 5 IDs)`:

`month` (datetime64[ns]), `month_of_year` (int8), `source`
(category[historical, live]), `product_type_id`, `gender_id`,
`color_master_id`, `graphical_appearance_id`, `material_id` (int8),
`n_articles` (int32), `share_articles` (float32, sums to 1.0 ± 1e-3 per
month), `avg_price` (float32, NaN — price not scraped).

`**live_univariate_<YYYY-MM>.parquet**` — 7 columns, grain=`(month, dimension, level_id)`:

`month`, `month_of_year`, `source`, `dimension` (category — uses all 7
historical dim names so concat preserves dtype, but only 5 emitted by
live), `level_id` (int8), `n_articles` (int32), `share_articles`
(float32, sums to 1.0 ± 1e-3 per `(month, dimension)`).

Both schemas are byte-compatible with `notebooks/1_aggregate_historical.ipynb`'s
`historical_*.parquet` outputs — `source` is the only differing value.
Notebook 1b does
`pd.concat([historical, *live_globbed]).drop_duplicates(subset=['month', *fp_cols, 'source'], keep='last')`
to produce `merged_*.parquet`.

## Scrapers in this folder


| Script                                                 | Bot protection               | Listing source                               | Rough wall-clock                                             |
| ------------------------------------------------------ | ---------------------------- | -------------------------------------------- | ------------------------------------------------------------ |
| [gap_scraper.py](gap_scraper.py)                       | none                         | `api.gap.com/commerce/search/products/v2/cc` | **~17s** for ~5,200 rows                                     |
| [uniqlo_scraper.py](uniqlo_scraper.py)                 | none                         | `uniqlo.com/us/api/commerce/v5/en/products`  | **~30s** for ~3,000 rows (with PDP enrichment)               |
| [american_eagle_scraper.py](american_eagle_scraper.py) | Akamai + JWT                 | `ae.com/ugp-api/browse/v1/category/{cat_id}` | **~2-3min** (JWT bootstrap + 9 cat fan-out at concurrency 3) |
| [hollister_scraper.py](hollister_scraper.py)           | Akamai (httpx defaults pass) | SSR HTML with embedded Apollo state          | **~5min** for ~21,000 rows                                   |



| Helper                                                 | Role                                                                                                                        |
| ------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------- |
| [build_live_cube.py](build_live_cube.py)               | Aggregate every `items_*.csv` into per-month `live_<role>_<YYYY-MM>.parquet` files                                          |
| [feature_lookups.py](feature_lookups.py)               | Shared keyword maps + ID dicts for `extract_color`, `extract_material`, etc. Validates against `lookup.csv` at import time. |
| [hmn_seasonal_processor.py](hmn_seasonal_processor.py) | H&M historical → train/val/test labels (consumes `merged_univariate.parquet`)                                               |


The `_deferred/` directory holds modules that are functionally complete
but parked because no consumer reads them today. See
[_deferred/README.md](_deferred/README.md). Currently:
[_deferred/google_trends_collector.py](_deferred/google_trends_collector.py)
— Google Trends search-interest collector that the previous combine flow
blended into `trend_signals.csv`. To revive as a parallel signal, see
HANDOFF.md item #10.

## Setup

```bash
pip install httpx pandas pyarrow playwright
playwright install chromium            # (AE bootstrap pass)
```

Three of the four scrapers (Gap, Uniqlo, Hollister) are pure `httpx` and
don't need Playwright at runtime. AE's `--enrich-pdp` and listing path
both go through the same Playwright-bootstrapped `httpx` client.

## Quick start

```bash
cd trndly/pipelines/collectors

# Single retailer (full catalog + PDP material enrichment, default ON)
python gap_scraper.py
python uniqlo_scraper.py
python hollister_scraper.py
python american_eagle_scraper.py

# Faster smoke test (5 products per target, no enrichment)
python gap_scraper.py --max-products-per-page 5 --no-enrich-pdp

# All four sequentially, then build live cubes
bash run_all.sh
```

After all scrapers have written their `items_<retailer>.csv`:

```bash
python build_live_cube.py
```

Outputs: `data/processed/live_fingerprint_<YYYY-MM>.parquet` +
`data/processed/live_univariate_<YYYY-MM>.parquet` (one parquet per
snapshot month). Run `notebooks/1b_scrape_aggregate_live.ipynb` to
glob those + read `historical_*.parquet` and write the always-rebuilt
`merged_*.parquet`.

## Common CLI flags (all four scrapers)


| Flag                        | Default                               | Purpose                                                   |
| --------------------------- | ------------------------------------- | --------------------------------------------------------- |
| `--items-path PATH`         | `synthetic_data/items_<retailer>.csv` | per-color row CSV                                         |
| `--concurrency N`           | `6` (Gap/Uniqlo/Hollister), `3` (AE)  | concurrent API/PDP fetches                                |
| `--max-products-per-page N` | none                                  | cap rows per target (smoke test)                          |
| `--resume`                  | off                                   | continue from `items_<retailer>_partial.csv` if it exists |
| `--strict`                  | off                                   | exit non-zero if completeness check fails                 |
| `--enrich-pdp`              | **on**                                | run PDP material enrichment (Phase 1.5)                   |
| `--no-enrich-pdp`           | —                                     | skip PDP enrichment for faster smoke runs                 |


## Testing

Per-scraper unit tests live at [trndly/tests/scrapers/](../../tests/scrapers/).
Mocked HTTP via [pytest-httpx](https://pypi.org/project/pytest-httpx/);
opt-in real-network smoke tests behind `pytest -m live`. Each scraper
test file covers pagination, `_combo_to_row`, PDP fabric extraction,
and resume semantics.

```bash
cd trndly
/opt/anaconda3/bin/python -m pytest                    # default: fast, no network
/opt/anaconda3/bin/python -m pytest tests/scrapers -q  # just scraper unit tests
/opt/anaconda3/bin/python -m pytest -m live -q         # live smoke checks against real sites
```

The live tests catch the failure modes the mocked tests can't —
Hollister Akamai tightening, retailer schema renames, HTTP/2 default
changes — using **structural** assertions only (no magic count
thresholds). See [tests/README.md](../../tests/README.md) for the full
test inventory + fixture refresh policy.

## Operational notes

- **Listing-API drift is normal.** Gap and Uniqlo report `total`* fields
that drift by ~3–5% between page fetches due to inventory churn or
ranking shuffles across cursor windows. Drop in real catalog content
is rare; we report drift in the per-retailer summary as `OK (drift)`.
- **AE rate-limits aggressive concurrency.** At `--concurrency 8`,
Akamai 403s reach ~37%. Default 3 is safe; the retry+backoff handles
bursts. The Playwright bootstrap captures the FULL set of browser
headers (`sec-ch-ua-`*, `sec-fetch-*`, `aesite`, etc.), not just the
JWT — Akamai validates the full fingerprint.
- **Hollister fingerprint is load-bearing.** Plain `httpx` over HTTP/1.1
with a desktop Chrome User-Agent passes Akamai's edge check. Do NOT
set `http2=True`; do NOT switch to `curl` — both are blocked.
- **Resume after crash** is supported on every scraper. The streaming
writer keeps a `<items>_partial.csv` open during the run and atomic-
renames to the final path on clean exit. With `--resume`, prior
`(style_id, cc_id, gender)` keys in the partial file are skipped on
the next invocation.
- **Live cube semantics: snapshot, not running tally.** Within-month
re-runs replace prior `(month, fingerprint, source='live')` rows in
the merged universe (notebook 1b's `keep='last'` enforces this). Items
dropped from the catalog between runs are dropped from the cube.

## Known limitations

- **Material extraction is keyword-priority** (with a percentage-aware
pre-pass). Some compositions still mis-bucket; net unknown rate is
≤ 2% across all four retailers.
- `**color_master` unknowns ~3.5% on AE.** AE markets denim wash colors
("Bordeaux", "Heather Frost", "Mint", "Bordeaux") that the keyword
list in `feature_lookups.py` covers — but ~67 catalog rows labeled
literally "Multi" are genuinely multi-colored and stay Unknown by
design (no canonical `color_master` value for "multi").
- `**graphical_appearance` is dominated by "Solid"** (~50–75% per
retailer). Most apparel is solid-colored; the keyword set is additive
in `feature_lookups.GRAPHICAL_APPEARANCE_KEYWORDS` if a retailer
starts using a new pattern term.
- **Future-timeframe forecasting deferred.** The univariate cube only
carries `current` shares per (month, dimension, level_id);
`feature_contract.load_trend_lookup_from_univariate` returns
`DEFAULT_MISSING_SCORE` for `next_week` / `next_month` /
`three_months` / `six_months`. Real per-fingerprint forecasting from
the historical cube's seasonality lives is a separate problem (see
HANDOFF.md).

## Per-retailer notes

### Gap

- API: `api.gap.com/commerce/search/products/v2/cc`. Two targets
(women/men shop-all). `totalColors` field is the completeness oracle.
- Page size capped server-side at 200.
- PDP fabric: regex against escaped JSON `"label":"Fabric & care"`
block in PDP HTML.
- Adds three Gap-specific columns to items: `style_id` (Gap's `styleId`),
`cc_id` (Gap's `ccId`), `web_product_type` (Gap's `webProductType` —
used as a fallback signal for `extract_product_type`).

### Uniqlo

- API: `uniqlo.com/us/api/commerce/v5/en/products`. Two targets
(women/men shop-all keyed by `genderId`). `pagination.total` is the
completeness oracle.
- Page size capped server-side at 100.
- PDP fabric: regex against the inline Next.js JSON's `"composition"`
field. Decoded from the JSON-string escape level.

### American Eagle

- API: `ae.com/ugp-api/browse/v1/category/{cat_id}` for listing,
`/product/{id}` for material. `meta.totalProducts` is the
completeness oracle.
- Page size hard-locked at 30 server-side.
- 9 category targets (no clean shop-all); cross-PLP overlap heavy, so
cross-target dedup on `(product_id, cc_id, gender)` is essential.
- One-time Playwright bootstrap captures the JWT bearer token and the
Akamai cookies + sec-* request headers. Subsequent fetches are pure
`httpx`.
- Concurrency default 3 (Akamai 403s become common above 4).

### Hollister

- No separate JSON API — the catalog is embedded in the SSR HTML's
Apollo GraphQL cache. Two shop-all targets (`/shop/us/womens` and
`/shop/us/mens`).
- Page size hard-locked at 90; pagination via `?start=N`.
- `productTotalCount` (in the embedded Apollo state) is the
completeness oracle.
- PDP fabric: regex against `"fabricDetails":"Body:60% Cotton, ..."`
strings in the PDP HTML (multiple panel labels per product are joined
before extraction).
- HTTP/1.1 only; HTTP/2 is blocked by Akamai.

