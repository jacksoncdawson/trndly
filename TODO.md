# TODO

Forward-looking work list for the trndly retail-collectors + cube pipeline.
Last updated: 2026-05-08.

---

## Current state — what just landed (so you know what NOT to redo)

### This round (Phase 2: scraper test suite)

- New test layout: [trndly/tests/scrapers/](trndly/tests/scrapers/) +
  [trndly/tests/fixtures/<retailer>/](trndly/tests/fixtures/) +
  [tests/conftest.py](trndly/tests/conftest.py).
- **Mock library**: `pytest-httpx` (added to
  [requirements.txt](trndly/requirements.txt)). All four scrapers'
  pagination, `_combo_to_row`, PDP fabric extraction, and resume
  semantics are covered with mocked HTTP responses.
- **AE Playwright** — no real Playwright in tests. The
  `_bootstrap_session` mock returns a captured header bundle from
  [tests/fixtures/ae/bootstrap_headers.json](trndly/tests/fixtures/ae/bootstrap_headers.json).
- **Hollister** adds dedicated `_parse_apollo_state` tests (positive
  + 2 negative) so renaming the Apollo prefix or breaking JSON shape
  fails loudly.
- **`feature_lookups.extract_*`**: 70 parametric cases pinning the
  keyword-priority resolution order (color, product_type, material,
  graphical_appearance, color_spectrum, product_group). Future keyword
  additions can't silently flip established mappings.
- **Live tests** behind `pytest -m live` (registered in
  [pytest.ini](trndly/pytest.ini)). Three structural smoke checks
  (Hollister Apollo state, Gap listing, Uniqlo listing) — no magic
  thresholds, just "did the response parse and contain the expected
  shape." Default pytest run skips them.
- Test totals: **109 passing + 5 skipping (model artifacts) +
  3 deselected (live) + 1 xfailed** by default;
  `pytest -m live` adds 3 more passing against real retailer sites.
- Documentation: new [tests/README.md](trndly/tests/README.md);
  collectors/README has a Testing section pointing at it.

### Previous round (Phase 1: data/processed/ rename + restructure)

- **`data/processed/` filenames now describe pipeline stage explicitly:**
  `historical_*.parquet` (immutable, notebook 1) →
  `live_*_<YYYY-MM>.parquet` (per snapshot month, build_live_cube) →
  `merged_*.parquet` (always rebuilt, notebook 1b) →
  `training_*.parquet` + `training_run.json` (notebook 2).
- `build_live_cube.py` now writes one parquet per snapshot month
  (e.g., `live_fingerprint_2026-05.parquet`). Multi-month inputs emit
  multiple files. Re-running within a month overwrites that month's file.
- Notebook 1b reads `historical_*.parquet` (immutable) + globs every
  `live_*_*.parquet`, concats with dedup, writes `merged_*.parquet`.
  **No more `.bak.<timestamp>` files** — historical is immutable so 1b
  can't lose data by overwriting.
- Notebook 2 now reads `merged_*.parquet` directly (was reading orphan
  `processed_*.parquet` files that nothing wrote). Same lag/target/
  split/weight prep, just from the canonical input.
- `paths.py` adds `live_fingerprint_path_for(month)` /
  `live_univariate_path_for(month)` helpers and
  `discover_live_*_parquets()` glob discoverers.
- `scheduleServer` + `text_forecast` + `hmn_seasonal_processor` now read
  `merged_*.parquet`. Env var: `MERGED_UNIVARIATE_PATH` (was
  `LIVE_UNIVARIATE_PATH`). CLI flag: `--merged-univariate-path` (was
  `--live-univariate-path`).
- All 5 notebook generators regenerated. Tests updated; 12 pass +
  5 skip (waiting on data) + 1 xfail.

### Previous round (schema convergence)

- `combine_trend_signals.py` is gone; `trend_signals.csv` is gone.
  Replaced by `build_live_cube.py` writing `live_*` cubes.
- `feature_lookups.py` has an import-time
  `_assert_lookup_csv_matches_dicts()` validator that raises on drift
  between hand-typed `*_TO_ID` dicts and `data/processed/lookup.csv`.
  New product_types: Cap=88, Umbrella=81, Bucket hat=83. `Jacquardf`
  typo fixed.
- AE color unknown rate dropped from 13.9% → 3.6% (floor is ~2.4% from
  ~67 "Multi"/"Floral"/"Tie Dye" rows that are genuinely multi-color).
  Uniqlo product_type unknown dropped from 7.2% → 0.3%.
- `load_trend_lookup_from_univariate` aliases `dimension='product_type'`
  → `feature_type='category'` to preserve the trained sklearn model's
  feature column names.
- `google_trends_collector.py` parked at
  [_deferred/](trndly/pipelines/collectors/_deferred/) (still functional;
  no consumer wired today).

Plan files at `~/.claude/plans/this-is-good-stuff-elegant-pancake.md`
(Phase 1 + 2 outline, both in same file).

---

## Active TODO

### Extract shared `_http_utils.py` (NOW UNBLOCKED by Phase 2)

`_request_with_retry`, `StreamingItemWriter`, the streaming partial+
atomic-rename machinery are duplicated ~150 lines per scraper. Phase 2
unblocks this — refactoring with coverage means regressions are
caught immediately instead of slipping silently.

Sketch:
- New `trndly/pipelines/collectors/_http_utils.py` exporting
  `request_with_retry()` and `StreamingItemWriter`.
- Each scraper imports from there instead of redefining.
- The retry-status-code set differs per retailer (Hollister/AE include
  403; Gap/Uniqlo don't) — parameterize it.
- Tests don't change shape — they keep importing the scraper module
  and using whatever `request_with_retry` resolves to.

### `avg_price=NaN` end-to-end validation + tests

Notebook 2 now reads `merged_*` which includes NaN-price live rows.
Needs investigation:

- Run notebook 2 + 3 against the merged cube; verify no silent NaN
  drops and no NaN predictions.
- If it fails, decide the fix in notebook 2 itself (filter to
  `source='historical'` for training, or impute median price per
  fingerprint). Plan agent flagged this as the biggest hidden risk
  during the previous cutover; **still unresolved**.
- Add tests: `prepare_training_frame` handles NaN `avg_price_t` rows
  the way we want; inference on NaN-price fingerprints doesn't return
  NaN predictions.

### EDA notebook cleanup

Delete [trndly/EDA/combine_signals_explore.ipynb](trndly/EDA/combine_signals_explore.ipynb)
— stale post-cutover, no current reader.

---

## Pinned (out of scope until reprioritized)

- **Schema versioning on cubes.** Add `_schema_version=1` on the
  parquets. Cheap insurance against silent downstream breakage.
- **Auto-rebootstrap AE on 401.** AE's JWT has ~30-min TTL.
- **Real per-fingerprint forecasting.** New design: target columns
  become `+1month`..`+6months` (replacing the named
  `next_week`/`next_month`/etc. in current `TIMEFRAMES`). Train only on
  rows with full ±3 / +6 months of history. Logic to roll the time-
  series cube forward each new month. Significant retraining +
  feature-contract change.
- **`category` model-feature rename.** Drop the
  `dimension='product_type'` → `feature_type='category'` alias once the
  model is retrained (couples to forecasting redesign above).
- **A fifth retailer** (Old Navy / Banana Republic / J. Crew /
  Madewell, etc.).
- **Google Trends as parallel signal.** Collector parked in
  [_deferred/](trndly/pipelines/collectors/_deferred/). Open question
  is the consumer story (separate column? distinct
  `dimension='google_search'`?).
- **`/predict` endpoint refresh story.** Currently no auto-refresh —
  cube is read once at server start. Add polling thread or file-watch
  on `merged_*.parquet`.

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
capture, diff request headers, update `STATIC_API_HEADERS`.

### Hollister TLS/HTTP fingerprint (MEDIUM)

[hollister_scraper.py](trndly/pipelines/collectors/hollister_scraper.py)
only works because plain `httpx` over HTTP/1.1 happens to satisfy
Akamai's edge fingerprint. If `httpx` changes its default TLS handshake
or someone "improves" the client to use HTTP/2, the scraper silently
dies.

**Detection**: `productTotalCount=0 totalPages=0` and a 149-byte response
body. Caught immediately by the `pytest -m live` Hollister structural
sanity check (Phase 2 deliverable).

### Hollister Apollo-state parsing

Catalog data lives inside
`window['APOLLO_STATE__catalog-mfe-web-service-CategoryPageFrontEnd-config'] = {...}`
in the SSR HTML. If Hollister renames the variable or wraps it
differently, parsing returns `None` and Hollister's items file becomes
empty. Constant: `APOLLO_STATE_PREFIX` at the top of
[hollister_scraper.py](trndly/pipelines/collectors/hollister_scraper.py).

### PDP fabric regex per retailer

Each retailer's PDP fabric extraction depends on a regex matching
specific JSON-string structure:

| Retailer | Pattern | Lives in |
|---|---|---|
| Gap | `\\"label\\":\\"Fabric \\u0026 care\\".*?\\"bullets\\":\[(.*?)\]` | `gap_scraper.py:FABRIC_BULLETS_RE` |
| Uniqlo | `"composition"\s*:\s*"((?:\\.|[^"\\])*)"` | `uniqlo_scraper.py:COMPOSITION_RE` |
| AE | JSON path `data["data"]["attributes"]["copySections"]["material"]["bullets"]` | `american_eagle_scraper.py:_fetch_pdp_fabric` |
| Hollister | `"fabricDetails":"((?:[^"\\]|\\.)*)"` | `hollister_scraper.py:PDP_FABRIC_RE` |

If any retailer changes their PDP serialization, enrichment silently
returns empty strings → `material_raw` unknown rate jumps from ~2% to
~14%.

### `feature_lookups.py` ID drift

The validator added in the previous round catches drift between the
hand-typed `*_TO_ID` dicts and `lookup.csv` at module import. **If you
edit either, the import will raise — fix the diff before the scrapers
can run.** Negative test in
[tests/test_trndly.py::test_lookup_consistency_validator_detects_drift](trndly/tests/test_trndly.py).

---

## Useful pointers

### Conventions

- **Python**: `/opt/anaconda3/bin/python` is the only env with
  `httpx + playwright + pandas + pyarrow` installed.
- **Cwd matters**: every scraper expects to be run from `trndly/`
  (the inner one), not the project root. `run_all.sh` `cd`s itself.
- **CSV partial files**: any `items_<retailer>_partial.csv` in
  `synthetic_data/` is a half-written run. `--resume` will pick it up.
- **Plan file (this round)**:
  `~/.claude/plans/this-is-good-stuff-elegant-pancake.md`.

### Smoke commands

```bash
cd /Users/jackcdawson/Desktop/trndly/trndly

# Single retailer (full catalog + PDP enrichment ON by default)
PYTHON=/opt/anaconda3/bin/python /opt/anaconda3/bin/python pipelines/collectors/gap_scraper.py

# All four sequentially, then build per-month live cubes
PYTHON=/opt/anaconda3/bin/python bash pipelines/collectors/run_all.sh

# Just rebuild the live cubes from existing items_*.csv
/opt/anaconda3/bin/python pipelines/collectors/build_live_cube.py

# Merge historical + live → merged_*.parquet via notebook 1b
/opt/anaconda3/bin/python notebooks/_run_notebook.py notebooks/1b_scrape_aggregate_live.ipynb

# Build training_*.parquet + training_run.json via notebook 2
/opt/anaconda3/bin/python notebooks/_run_notebook.py notebooks/2_feature_processing.ipynb

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
| `/options` returns empty `colors`/`categories`/`materials` | `merged_*.parquet` missing or `source='live'` empty in cube | Run nb 1b; check `MERGED_UNIVARIATE_PATH` env var |
| `merged_*.parquet` regenerated but stale data in serving | scheduleServer caches cube at startup | Restart server; future TODO: file-watch reload |

### Documentation

- [collectors/README.md](trndly/pipelines/collectors/README.md) — full
  pipeline doc; updated this round.
- Module docstrings on each scraper.
- [feature_contract.py](trndly/pipelines/training/feature_contract.py)
  — the cube + lookup contract.
- [data/processed/lookup.csv](trndly/data/processed/lookup.csv) —
  canonical feature universe; `*_TO_ID` dicts are validated against it
  at import.
