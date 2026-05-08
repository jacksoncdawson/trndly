# TODO

Forward-looking work list for the trndly retail-collectors + cube pipeline.
Last updated: 2026-05-08.

---

## Current state — what just landed (so you know what NOT to redo)

The schema-convergence rewrite is done. Live retail data flows into the
same fingerprint + univariate cube format the H&M historical pipeline
uses, and notebook 1b merges them cleanly.

- `combine_trend_signals.py` is gone. `trend_signals.csv` is gone.
  Replaced by [build_live_cube.py](trndly/pipelines/collectors/build_live_cube.py)
  which writes `live_monthly_fingerprint.parquet` +
  `live_monthly_univariate.parquet` to `data/processed/`.
- [feature_lookups.py](trndly/pipelines/collectors/feature_lookups.py) now
  has an import-time `_assert_lookup_csv_matches_dicts()` validator that
  raises on drift between the hand-typed `*_TO_ID` dicts and
  `data/processed/lookup.csv`. New product_types added: Cap=88,
  Umbrella=81, Bucket hat=83. `Jacquardf` typo fixed.
- AE color unknown rate dropped from 13.9% → 3.6% (floor is ~2.4% from
  ~67 "Multi"/"Floral"/"Tie Dye" rows that are genuinely multi-color).
  Uniqlo product_type unknown dropped from 7.2% → 0.3%.
- [scheduleServer.py](trndly/backend/services/scheduleServer.py) and
  [hmn_seasonal_processor.py](trndly/pipelines/collectors/hmn_seasonal_processor.py)
  now read the cube via `load_trend_lookup_from_univariate` in
  feature_contract.py. The loader aliases `dimension='product_type'` →
  `feature_type='category'` to preserve the trained sklearn model's
  feature column names.
- `google_trends_collector.py` parked at
  [_deferred/](trndly/pipelines/collectors/_deferred/) (still functional;
  no consumer wired today).

Tests: 13 pass (10 in `trndly/tests/test_trndly.py`, 3 in root
`tests_trndly.py`). Plan file at
`~/.claude/plans/this-is-good-stuff-elegant-pancake.md`.

---

## TODO — quick wins (under an hour)

1. **Fix run_all.sh re-run mid-script edit footgun.** The scrapers run
   sequentially in bash; if you edit the script while it's running, bash
   may pick up the new tail (`set -e` doesn't help here). Trivial fix:
   read the whole script into a temp string before executing — or
   accept that you shouldn't edit it mid-flight.
2. **CI sanity test for Hollister fingerprint.** A 5-line script that
   hits `https://www.hollisterco.com/shop/us/womens` and asserts
   `productTotalCount > 1500` and body size > 500KB. Catches the
   "Akamai tightened" failure mode early — see "Brittle areas" below.
3. **Decide the AE "Multi" / "Floral" / "Tie Dye" treatment.** Currently
   ~67 + 4 + 2 rows stay `color_master_id=0` (Unknown). Options:
   leave as-is (current), or add a canonical "multi" color to
   `lookup.csv` (id=14? — would need a downstream consumer story).
4. **Inspect `EDA/combine_signals_explore.ipynb`.** Likely stale post-
   cutover. Either delete it or annotate as historical EDA.

## TODO — medium (half-day each)

5. **Extract shared `_http_utils.py`.** `_request_with_retry`,
   `StreamingItemWriter`, the streaming partial+atomic-rename machinery
   are duplicated ~150 lines per scraper. Carried over from the previous
   handoff — still worthwhile.
6. **Auto-rebootstrap AE on 401.** AE's JWT has ~30-minute TTL. A full
   AE run is 3–6 min so one bootstrap covers it, but long-running tests
   or chained runs can expire it. When 401 is seen mid-run, re-run the
   Playwright bootstrap and continue.
7. **Validate `avg_price=NaN` doesn't break notebook 2 / 3.** The live
   cube emits `avg_price=NaN` (price isn't scraped). Notebook 2 was
   trained on historical-only where `avg_price` is non-null in 100% of
   rows. If it fails on NaN, fix in notebook 2 (filter to
   `source='historical'` for training, or impute median per fingerprint).
   The Plan agent flagged this as the biggest hidden risk during cutover.
8. **Schema versioning on cubes.** No `_schema_version` field on the
   parquets. Adding `_schema_version=1` and bumping it when columns
   change is cheap insurance against silent downstream breakage.

## TODO — larger (1+ day)

9. **Real per-fingerprint forecasting.** The pinned design from this
   round: target columns become `+1month`, `+2months`, …, `+6months`
   (not the named `next_week`/`next_month`/etc. in current
   `TIMEFRAMES`). Train only on rows with full ±3 / +6 months of
   history. Logic to roll the time-series cube forward each new month.
   This is a significant retraining + feature-contract change. Pinned
   from both this round and the previous handoff.
10. **`category` model-feature rename.** Today
    `load_trend_lookup_from_univariate` aliases the cube's
    `dimension='product_type'` to `feature_type='category'` so the
    trained sklearn model's `category_current` column keeps working.
    When the model is retrained (see #9), drop the alias and rename
    feature columns to `product_type_current`. Coordinate with the new
    UI branch the user mentioned.
11. **A fifth retailer.** Recipe is well-established. Pick something
    with known apparel breadth (Old Navy, Banana Republic, J. Crew,
    Madewell). Recon → API discovery → implementation. ~4-6h.
12. **Add Google Trends back as a parallel signal** (not blended into
    the catalog count). The collector is parked at
    [_deferred/google_trends_collector.py](trndly/pipelines/collectors/_deferred/google_trends_collector.py).
    Open question is the consumer story — separate column? Distinct
    `dimension='google_search'` in the univariate cube? Decide before
    wiring.
13. **`/predict` endpoint refresh story.** No auto-refresh today —
    `live_monthly_univariate.parquet` is read once at server start.
    If you want pickup without restart, add a polling thread or
    file-watch signal handler to call `reload_trend_data()` again.

---

## Brittle areas (carry from previous handoff — still apply)

In rough order of "most likely to break first":

### AE Akamai fingerprint check (HIGH)

[american_eagle_scraper.py](trndly/pipelines/collectors/american_eagle_scraper.py)
requires a one-time Playwright bootstrap that captures the **full set**
of browser headers (`sec-ch-ua-*`, `sec-fetch-*`, `aesite`, `aelang`,
`channeltype`, `Authorization: Bearer <JWT>`). Captured headers pin to
whatever Chrome version Playwright is running — if Akamai later
validates against a *current* Chrome, you'll get silent 403s on httpx.

**Detection**: `Phase 1` log shows `[http] api ... got 403, retry ...` spam.
**Fix**: re-run Playwright bootstrap with a Chrome devtools network
capture, diff request headers, update `STATIC_API_HEADERS`. Recon
scaffolding was at `/tmp/ae_debug.py` (gone now; preserve next time).

### Hollister TLS/HTTP fingerprint (MEDIUM)

[hollister_scraper.py](trndly/pipelines/collectors/hollister_scraper.py)
only works because plain `httpx` over HTTP/1.1 happens to satisfy
Akamai's edge fingerprint. `curl` (any version) gets 403.
`httpx.AsyncClient(http2=True)` gets 403. If `httpx` changes its default
TLS handshake or someone "improves" the client to use HTTP/2, the
scraper silently dies.

**Detection**: `productTotalCount=0 totalPages=0` and a 149-byte response
body. The CI sanity test from #2 above would catch this immediately.

### Hollister Apollo-state parsing

Catalog data lives inside
`window['APOLLO_STATE__catalog-mfe-web-service-CategoryPageFrontEnd-config'] = {...}`
in the SSR HTML. If Hollister renames the variable or wraps it
differently, parsing returns `None` and Hollister's items file becomes
empty. The relevant constant is `APOLLO_STATE_PREFIX` at the top of
[hollister_scraper.py](trndly/pipelines/collectors/hollister_scraper.py).

### PDP fabric regex per retailer

Each retailer's PDP fabric extraction depends on a regex that matches
very specific JSON-string structure:

| Retailer | Pattern | Lives in |
|---|---|---|
| Gap | `\\"label\\":\\"Fabric \\u0026 care\\".*?\\"bullets\\":\[(.*?)\]` | `gap_scraper.py:FABRIC_BULLETS_RE` |
| Uniqlo | `"composition"\s*:\s*"((?:\\.|[^"\\])*)"` | `uniqlo_scraper.py:COMPOSITION_RE` |
| AE | JSON path `data["data"]["attributes"]["copySections"]["material"]["bullets"]` | `american_eagle_scraper.py:_fetch_pdp_fabric` |
| Hollister | `"fabricDetails":"((?:[^"\\]|\\.)*)"` | `hollister_scraper.py:PDP_FABRIC_RE` |

If any retailer changes their PDP serialization (Next.js upgrade, new
structure), enrichment silently returns empty strings → `material_raw`
unknown rate jumps from ~2% to ~14%.

**Detection**: `Phase 1.5` log shows `enriched 0/N PDPs`.

### Material extraction edge case

`"100% Cotton (25% Recycled Cotton Fiber)"` (Uniqlo's UT graphic tees) —
the percentage extractor finds 100% Cotton AND 25% "Uses Recycled
Cotton Fiber" → polyester (because "recycled" matches first). Cotton
still wins because 100 > 25 — but a future "improvement" to the regex
could flip a bunch of UT tees from cotton → polyester.

### `feature_lookups.py` ID drift

The validator added in this round catches drift between the hand-typed
`*_TO_ID` dicts and `lookup.csv` at module import. **If you edit
either, the import will raise — fix the diff before the scrapers can
run.** Negative test in
[tests/test_trndly.py::test_lookup_consistency_validator_detects_drift](trndly/tests/test_trndly.py).

---

## Useful pointers

### Conventions

- **Python**: `/opt/anaconda3/bin/python` is the only env with
  `httpx + playwright + pandas + pyarrow` installed. The system
  `python` (homebrew) lacks `httpx`.
- **Cwd matters**: every scraper expects to be run from `trndly/`
  (the inner one), not the project root. `run_all.sh` `cd`s itself.
- **CSV partial files**: any `items_<retailer>_partial.csv` in
  `synthetic_data/` is a half-written run. `--resume` will pick it up;
  otherwise it'll be clobbered on next run.
- **Plan file (this round)**:
  `~/.claude/plans/this-is-good-stuff-elegant-pancake.md`.

### Smoke commands

```bash
cd /Users/jackcdawson/Desktop/trndly/trndly

# Single retailer (full catalog + PDP enrichment ON by default)
PYTHON=/opt/anaconda3/bin/python /opt/anaconda3/bin/python pipelines/collectors/gap_scraper.py

# All four sequentially, then build live cubes
PYTHON=/opt/anaconda3/bin/python bash pipelines/collectors/run_all.sh

# Just rebuild the cubes from existing items_*.csv
/opt/anaconda3/bin/python pipelines/collectors/build_live_cube.py

# Merge live → historical via notebook 1b
/opt/anaconda3/bin/python notebooks/_run_notebook.py notebooks/1b_scrape_aggregate_live.ipynb

# Test integrity
/opt/anaconda3/bin/python -m pytest tests/test_trndly.py -q
```

### Where to look when X fails

| Symptom | Most likely cause | Where to look |
|---|---|---|
| `ModuleNotFoundError: No module named 'httpx'` | Wrong Python | Use `/opt/anaconda3/bin/python` |
| `ValueError: feature_lookups.py drift vs ...` at import | Hand-edited `*_TO_ID` doesn't match `lookup.csv` | The diff in the error message |
| Hollister `productTotalCount=0` | TLS fingerprint changed (or HTML rewritten) | `hollister_scraper.py:_parse_apollo_state` |
| AE 100% 403s | Akamai tightened or Playwright Chromium too old | `american_eagle_scraper.py:_bootstrap_session` |
| Material unknowns spike to ~14% | PDP fabric regex broke (retailer changed PDP HTML) | The `*_RE` constants in each scraper |
| Live cube share-sums fail invariant | `build_live_cube.py` upstream got NaN IDs | `validate_live_*_frame` in feature_contract.py raises with details |
| `/options` returns empty `colors`/`categories`/`materials` | `live_monthly_univariate.parquet` missing or `source='live'` empty | Run `build_live_cube.py`; check `LIVE_UNIVARIATE_PATH` env var |

### Documentation

- [collectors/README.md](trndly/pipelines/collectors/README.md) — full
  pipeline doc; updated this round.
- Module docstrings on each scraper.
- [feature_contract.py](trndly/pipelines/training/feature_contract.py)
  — the cube + lookup contract.
- [data/processed/lookup.csv](trndly/data/processed/lookup.csv) —
  canonical feature universe; `*_TO_ID` dicts are validated against it
  at import.
