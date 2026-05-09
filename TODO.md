# TODO

Forward-looking work list for the trndly retail-collectors + cube pipeline.
Last updated: 2026-05-08.

---

## Current state — what just landed (so you know what NOT to redo)

### This round (Unisex gender translation in the live cube)

`gender_id=2` (Unisex) used to be unreachable from the live cube
because every scraper's `*_TARGETS` hardcoded `women`/`men`. Truly
unisex SKUs (most basics, hats, accessories) appear in both catalogs
and were emitted as TWO rows (one tagged `women`, one `men`) instead
of ONE `unisex` row — double-counting them in share-of-articles signals.

Verified count from existing items_*.csv: same-(retailer, style_id,
cc_id) appearing in both M and W catalogs:

| Retailer  | Pairs | Total unique SKUs |
| --------- | ----: | ----------------: |
| Gap       |   430 |             4,700 |
| Uniqlo    |   542 |             2,398 |
| AE        |   103 |             2,860 |
| Hollister |     4 |            20,749 |
| **Total** | **1,079** |               |

Fix shipped:

- New
  [`collapse_unisex(items)`](trndly/pipelines/collectors/build_live_cube.py)
  runs in `build_live_cube.main()` between `load_items` and the cube
  builders. Collapses same-(retailer, style_id, cc_id) M+W pairs into
  a single `gender='unisex', gender_id=2` row (women's row wins;
  men's row dropped; `gender` and `gender_id` rewritten).
- `_DELIBERATELY_UNREACHABLE_LOOKUP_IDS["gender"] = {2}` removed in
  [feature_lookups.py](trndly/pipelines/collectors/feature_lookups.py)
  — the validator now expects gender_id=2 reachable, which it is.
- New tests:
  [tests/scrapers/test_build_live_cube.py](trndly/tests/scrapers/test_build_live_cube.py)
  — 6 cases covering empty/no-pair, pure pair, partial overlap,
  already-unisex passthrough, cross-retailer non-collision, and a
  cube round-trip preserving the per-month share-sum invariant.

End-to-end on 2026-05 data:

- **Live cube gender_id distribution: 16,575 women / 1,079 unisex /
  13,053 men** (3.5% unisex share).
- Total articles: 31,786 → 30,707 (1,079 duplicates folded).
- `merged_fingerprint.parquet` rebuilt via notebook 1b; gender_id=2
  now flows from both historical AND live sources.
- [SCHEMA.md](trndly/data/processed/SCHEMA.md) cardinality matrix
  refreshed; gender allow-list section documents that the gap closed.

**176 passing / 5 skip / 3 deselected / 1 xfailed.**

The model wasn't retrained: `RandomForestRegressor` consumes
`share_t + lags + month_of_year` only, so the cube row-count shift
manifests as a small `share_t` change in affected fingerprints —
picked up naturally on the next training round.

### Previous in this session (`_http_utils.py` refactor + EDA cleanup)

The four scrapers had ~150 lines of duplicated HTTP-retry +
streaming-CSV-writer code each. Pulled the common shape into
[trndly/pipelines/collectors/_http_utils.py](trndly/pipelines/collectors/_http_utils.py)
exporting:

- `request_with_retry(client, url, *, params=None, max_attempts=5,
  retryable_statuses=DEFAULT_RETRYABLE_STATUSES, ...)`
- `StreamingItemWriter(final_path, *, resume=False, fieldnames=CSV_FIELDNAMES)`
- `CSV_FIELDNAMES`, `DEFAULT_RETRYABLE_STATUSES`, `DEFAULT_MAX_ATTEMPTS`

Switchover:

- gap + uniqlo: drop helpers, import everything from `_http_utils`.
- hollister: same, but exposes `_request_with_retry =
  partial(request_with_retry, retryable_statuses=DEFAULT |
  {403})` because Akamai sometimes 403s under load.
- AE: drops `StreamingItemWriter`/`CSV_FIELDNAMES`, **keeps its own
  `_request_with_retry`** because the 1.5**attempt backoff is a
  deliberate Akamai-stickiness tuning that differs from the shared
  2**(attempt-1) schedule. Documented inline.

New tests: [tests/scrapers/test_http_utils.py](trndly/tests/scrapers/test_http_utils.py)
— 10 cases covering retry-on-200, retry-then-succeed-on-429, no-retry
on non-retryable, exhaustion, 403 unlocked via custom set, network-error
retry, plus the writer's atomic-rename / resume / fresh-clobber /
partial-preserved-on-exception paths.

EDA cleanup: deleted `trndly/EDA/combine_signals_explore.ipynb` (stale
post-cutover, no current reader).

**170 passing / 5 skip / 3 deselected / 1 xfailed.**

### Previous in this session (Phase 3 follow-up: re-scrape end-to-end verification)

The dict expansion shipped previously was unverified against retailer
text. Ran `run_all.sh` (gap, uniqlo, AE, hollister) ~30 min wall, then
notebook 1b → merged + `/options` simulation:

- **Material universe: 9 → 31 live IDs** (up from 9; max reachable is
31 given the 3-ID allow-list + velour-not-in-catalog-this-month).
All 22 expansion buckets populate from real retailer text:
jersey, lace, viscose, crepe, twill, mesh, satin, chiffon, faux fur,
lyocell, fleece, modal, canvas, corduroy, cashmere, nylon, suede,
velvet, shearling, tulle, acrylic, tencel.
- **Product_type: 52 → 54 live IDs** (Hat/brim, Headband — modest as
expected; new types are accessory-niche).
- `**/options` materials: 8 → 30 entries**; categories: 51 → 53.
- No regressions; live fingerprint cube grew 3,365 → 3,846 rows
because previously-collapsed fingerprints (e.g. denim vs leather
jackets in same fingerprint) now distinguish.

New verification helpers:

- [pipelines/collectors/_universe_smoke.py](trndly/pipelines/collectors/_universe_smoke.py)
— re-resolves saved CSV titles through current `feature_lookups`,
prints a per-retailer/aggregate universe diff. Runs in ~3s, no
network. Cheap pre-flight before any future re-scrape round.
- [pipelines/collectors/_universe_diff.py](trndly/pipelines/collectors/_universe_diff.py)
— diffs a `live_*_<MONTH>.parquet` against a `.pre_followup`
snapshot. Cross-checks lost IDs against
`_DELIBERATELY_UNREACHABLE_LOOKUP_IDS`; exit 1 on regression.

[SCHEMA.md](trndly/data/processed/SCHEMA.md) cardinality table refreshed.

### Previous in this session (Phase 3: drop `avg_price_t` from the fingerprint model)

The TODO entry framed `avg_price=NaN` as the biggest hidden risk. Audit
landed differently: the **training-side** assertion in notebook 2
(`assert fp_train["avg_price_t"].notna().all()`) was structurally
guaranteed today by calendar-strict's 9-month requirement (live=1 month,
historical=23 months, no overlap → live rows can't qualify). The hidden
risk was on the **inference side**: when live accumulates ≥4 contiguous
months, `build_fingerprint_inference_rows` would feed `avg_price_t=NaN`
into the trained `RandomForestRegressor`, which sklearn 1.4+ silently
routes down a "missing" branch — biased finite output, no crash.

Fix shipped:

- `avg_price_t` removed from `FINGERPRINT_FEATURE_COLS` in
[_gen_2_feature_notebook.py:251](trndly/notebooks/_gen_2_feature_notebook.py)
and the regenerated [2_feature_processing.ipynb](trndly/notebooks/2_feature_processing.ipynb).
`extra_at_t` no longer pulls `avg_price` into the training frame; the
`notna()` assertion is gone (no longer applicable).
- `avg_price_t` removed from `build_fingerprint_inference_rows` in
[text_forecast.py](trndly/pipelines/serving/text_forecast.py).
- Notebook 5 doc-block updated.
- Notebook 2 re-run → `training_fingerprint.parquet` now 5-feature.
- Notebook 3 re-run → new `fingerprint_model.joblib`
(`feature_names_in_ = ['month_of_year', 'share_t', 'share_lag1', 'share_lag2', 'share_lag3']`). Holdout WMAE ≈ unchanged (0.000099 vs.
0.000099 prior) — the feature wasn't carrying real signal.
- New regression test
`tests/test_trndly.py::test_avg_price_t_is_not_a_model_feature`
asserts `avg_price_t` is absent from both `training_run.json` and
`training_fingerprint.parquet`. Reintroducing it forces the test
author to consciously delete this guard.
- `avg_price` column **stays in the live cube schema** for future
analytics; just no longer plumbed into the model.

160 passing / 5 skipped / 3 deselected / 1 xfailed.

### Previous in this session (Phase 3: dimension-universe expansion)

**Audit-grade reference**:
[trndly/data/processed/SCHEMA.md](trndly/data/processed/SCHEMA.md) is now
the single doc covering what's in `data/processed/`, which lookup IDs
each source populates, and which are deliberately unreachable from live.
Refresh it whenever the allow-list or live cardinality changes
materially.

The audit (plan file:
`~/.claude/plans/take-a-look-at-virtual-elephant.md`) confirmed the live
scrapers were collapsing two lookup-csv dimensions hard:

- **material**: 8 reachable / 35 lookup → 31 reachable
- **product_type**: 62 reachable / 95 lookup → 68 reachable
- (gender Unisex still unreached — deferred; see "Pinned")

Historical data already covered the full universe (built from H&M
transactions); the merge in notebook 1b preserves it, but `/options` filters
to `source='live'` so users only saw the live snapshot's narrow set.

Changes in [feature_lookups.py](trndly/pipelines/collectors/feature_lookups.py):

- `MATERIAL_TO_ID` widened from 8 buckets to 31 (jersey, lace, viscose,
crepe, twill, mesh, satin, chiffon, faux fur, velour, lyocell, fleece,
modal, canvas, corduroy, cashmere, nylon, suede, velvet, shearling, tulle,
acrylic, tencel — added).
- `MATERIAL_KEYWORDS` rerouted: `jersey/velvet/cashmere → wool/knit/silk` was
the *collapse*, not the design. Each fabric now resolves to its own bucket.
Imitation leather/suede deliberately kept in `leather` (HM-only buckets).
- `PRODUCT_TYPE_TO_ID` gained 7 new entries: Night gown(43), Hat/brim(57),
Tie(72), Felt hat(85), Straw hat(87), Bootie(92), Headband(93). Keywords
added for each — specific phrases ("fedora", "wide-brim hat", "panama hat",
"bootie", "necktie", "nightgown") placed before generic fall-throughs
("hat", "boot") so the new IDs win.
- `PRODUCT_TYPE_TO_GROUP_ID` updated for the 7 new types.
- New `_DELIBERATELY_UNREACHABLE_LOOKUP_IDS` constant documents the IDs we
intentionally don't surface (HM-cat artifacts, near-duplicates, niche).
- New `_warn_unreachable_lookup_ids()` runs at import time alongside the
existing forward-direction validator. Soft warning (UnreachableLookupID
Warning), so adding a new lookup ID later doesn't break every importer
until coverage lands.

Tests (159 passing + 5 skip + 3 deselected + 1 xfail):

- `tests/scrapers/test_feature_lookups.py` — added 14 material extractor
cases, 14 product_type cases, 7 product_group cases, 13
MATERIAL_TO_ID-coverage cases.
- `tests/test_trndly.py` — added
`test_unreachable_lookup_ids_match_documented_allowlist` (positive — full
closure invariant) and
`test_unreachable_lookup_ids_warning_fires_on_drift` (negative — removes a
dict entry, asserts the warning fires).

End-to-end verification still required: re-run any scraper → build_live_cube
→ notebook 1b, then confirm `merged_univariate.parquet`'s live-source
material/category cardinality grows. The dict expansion is necessary but
not sufficient — fabric strings only resolve to the new buckets if the
keyword path or the percentage-aware path actually fires on real PDP text.

### Previous round (Phase 2: scraper test suite)

- New test layout: [trndly/tests/scrapers/](trndly/tests/scrapers/) +
[trndly/tests/fixtures/](trndly/tests/fixtures/)[/](trndly/tests/fixtures/) +
[tests/conftest.py](trndly/tests/conftest.py).
- **Mock library**: `pytest-httpx` (added to
[requirements.txt](trndly/requirements.txt)). All four scrapers'
pagination, `_combo_to_row`, PDP fabric extraction, and resume
semantics are covered with mocked HTTP responses.
- **AE Playwright** — no real Playwright in tests. The
`_bootstrap_session` mock returns a captured header bundle from
[tests/fixtures/ae/bootstrap_headers.json](trndly/tests/fixtures/ae/bootstrap_headers.json).
- **Hollister** adds dedicated `_parse_apollo_state` tests (positive
  - 2 negative) so renaming the Apollo prefix or breaking JSON shape
  fails loudly.
- `**feature_lookups.extract_*`**: 70 parametric cases pinning the
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

- `**data/processed/` filenames now describe pipeline stage explicitly:**
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
Replaced by `build_live_cube.py` writing `live_`* cubes.
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

*Empty — see "Pinned" below for next-up candidates when you reprioritize.*

---

## Pinned (out of scope until reprioritized)

- **Auto-rebootstrap AE on 401.** AE's JWT has ~30-min TTL.
- **Real per-fingerprint forecasting.** New design: target columns
become `+1month`..`+6months` (replacing the named
`next_week`/`next_month`/etc. in current `TIMEFRAMES`). Train only on
rows with full ±3 / +6 months of history. Logic to roll the time-
series cube forward each new month. Significant retraining +
feature-contract change.
- `**category` model-feature rename.** Drop the  
`dimension='product_type'` → `feature_type='category'` alias once the  
model is retrained (couples to forecasting redesign above).

---

## Brittle areas (carry from previous handoff — still apply)

In rough order of "most likely to break first":

### AE Akamai fingerprint check (HIGH)

[american_eagle_scraper.py](trndly/pipelines/collectors/american_eagle_scraper.py)
requires a one-time Playwright bootstrap that captures the **full set**
of browser headers (`sec-ch-ua-`*, `sec-fetch-*`, `aesite`, `aelang`,
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


| Retailer  | Pattern                                                                       | Lives in                                      |
| --------- | ----------------------------------------------------------------------------- | --------------------------------------------- |
| Gap       | `\\"label\\":\\"Fabric \\u0026 care\\".*?\\"bullets\\":\[(.*?)\]`             | `gap_scraper.py:FABRIC_BULLETS_RE`            |
| Uniqlo    | `"composition"\s*:\s*"((?:.                                                   | [^"])*)"`                                     |
| AE        | JSON path `data["data"]["attributes"]["copySections"]["material"]["bullets"]` | `american_eagle_scraper.py:_fetch_pdp_fabric` |
| Hollister | `"fabricDetails":"((?:[^"]                                                    | .)*)"`                                        |


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


| Symptom                                                    | Most likely cause                                           | Where to look                                                      |
| ---------------------------------------------------------- | ----------------------------------------------------------- | ------------------------------------------------------------------ |
| `ModuleNotFoundError: No module named 'httpx'`             | Wrong Python                                                | Use `/opt/anaconda3/bin/python`                                    |
| `ValueError: feature_lookups.py drift vs ...` at import    | Hand-edited `*_TO_ID` doesn't match `lookup.csv`            | The diff in the error message                                      |
| Hollister `productTotalCount=0`                            | TLS fingerprint changed (or HTML rewritten)                 | `hollister_scraper.py:_parse_apollo_state`                         |
| AE 100% 403s                                               | Akamai tightened or Playwright Chromium too old             | `american_eagle_scraper.py:_bootstrap_session`                     |
| Material unknowns spike to ~14%                            | PDP fabric regex broke (retailer changed PDP HTML)          | The `*_RE` constants in each scraper                               |
| Live cube share-sums fail invariant                        | `build_live_cube.py` upstream got NaN IDs                   | `validate_live_*_frame` in feature_contract.py raises with details |
| `/options` returns empty `colors`/`categories`/`materials` | `merged_*.parquet` missing or `source='live'` empty in cube | Run nb 1b; check `MERGED_UNIVARIATE_PATH` env var                  |
| `merged_*.parquet` regenerated but stale data in serving   | scheduleServer caches cube at startup                       | Restart server; future TODO: file-watch reload                     |


### Documentation

- [collectors/README.md](trndly/pipelines/collectors/README.md) — full
pipeline doc; updated this round.
- Module docstrings on each scraper.
- [feature_contract.py](trndly/pipelines/training/feature_contract.py)
— the cube + lookup contract.
- [data/processed/lookup.csv](trndly/data/processed/lookup.csv) —
canonical feature universe; `*_TO_ID` dicts are validated against it
at import.
- [data/processed/SCHEMA.md](trndly/data/processed/SCHEMA.md) —
per-dimension reachability audit (lookup vs. historical vs. live vs.
merged) plus the deliberately-unreachable allow-list rationale.

