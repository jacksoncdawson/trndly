# Test suite

Unit + integration tests for trndly. Mocked HTTP via `pytest-httpx`;
opt-in real-network smoke tests behind `pytest -m live`.

## Running

```bash
cd /Users/jackcdawson/Desktop/trndly/trndly

# Default — fast, no network. Excludes `live` marker.
/opt/anaconda3/bin/python -m pytest

# Just the scraper unit tests (mocked)
/opt/anaconda3/bin/python -m pytest tests/scrapers -q

# Live smoke tests against real retailer sites
/opt/anaconda3/bin/python -m pytest -m live -q

# Both
/opt/anaconda3/bin/python -m pytest -m "live or not live" -q
```

The default-skip is configured in `pytest.ini` (`addopts = -m "not live"`).

## Layout

```
tests/
├── conftest.py                  shared fixtures (path setup + per-retailer loaders)
├── pytest.ini  (../)            marker registration + asyncio mode
├── test_trndly.py               cube + lookup + feature_contract tests
├── tests_trndly.py  (../../)    legacy root-level test file (3 tests)
├── fixtures/
│   ├── gap/                     listing_page1..3.json + pdp_html.txt
│   ├── uniqlo/                  listing_page1.json + pdp_html.txt
│   ├── hollister/               ssr_apollo_state.html + pdp_html.txt
│   └── ae/                      bootstrap_headers.json + listing_page1.json + pdp_response.json
└── scrapers/
    ├── test_feature_lookups.py  parametric tests for extract_color/material/product_type/...
    ├── test_gap.py              pagination + _combo_to_row + PDP fabric + resume
    ├── test_uniqlo.py           same coverage matrix
    ├── test_hollister.py        +1: Apollo state parsing (3 cases)
    ├── test_ae.py               +1: mock _bootstrap_session
    └── test_live.py             marked @pytest.mark.live — opt-in only
```

## Per-scraper coverage matrix

Every scraper test file covers (modulo retailer-specific shape):

1. **Pagination** — feed mocked listing API responses, assert all combos
   collected and deduped per-target.
2. **`_combo_to_row`** — feed a synthetic combo dict, assert every one of
   the 18 `items_<retailer>.csv` columns is populated correctly with IDs
   resolved via `feature_lookups`.
3. **PDP fabric extraction** — mock the PDP URL, assert the regex / JSON
   path returns the expected composition string.
4. **Resume semantics** — write a partial CSV, instantiate
   `StreamingItemWriter(resume=True)`, assert `already_have(...)` reports
   prior keys.

Scraper-specific extras:
- **Hollister** adds `_parse_apollo_state` tests (positive + 2 negative)
  and `_extract_combos_from_apollo` end-to-end.
- **AE** adds a `_bootstrap_session` mock test (we don't run real
  Playwright in tests — too slow/fragile; the fixture is a captured
  header bundle).

## Live tests (`pytest -m live`)

Runs three structural smoke checks against the real retailer URLs:

- **Hollister Apollo state** — fetch `/shop/us/womens`, assert response
  is >50KB, `_parse_apollo_state` returns non-None, `productTotalCount > 0`,
  combos extracted. **Catches Akamai tightening, HTTP/2 default change,
  Apollo-prefix renames.** No magic thresholds.
- **Gap listing API** — fetch first page; assert `totalColors` and
  `products` keys present and non-empty.
- **Uniqlo listing API** — same structural check on `result.items`.

Skip in CI by default (`addopts = -m "not live"`). Run manually before
shipping a release or when investigating a "scraper returned 0 rows"
report.

## Adding a new fixture

Hand-crafted minimal JSONs for unit tests. Real-captured + anonymized
"goldens" for higher-fidelity cases (rotate every few months as schemas
drift). Drop into `tests/fixtures/<retailer>/` and add a loader fixture
to `conftest.py`.

PDP HTML fixtures must contain the exact backslash-escape patterns the
scraper regexes match against. For Gap specifically the ampersand must
be encoded as `&` (not `&` literal) — see
`tests/fixtures/gap/pdp_html.txt`.

## Refresh policy

When a retailer changes their schema and a scraper starts producing
empty / bad data:

1. Run the live test for that retailer: `pytest -m live -k <retailer>`.
   If it fails with a clear error, that's the diagnostic.
2. Capture a fresh response (browser devtools → Network → save as JSON
   or HTML) and replace the fixture under `tests/fixtures/<retailer>/`.
   Anonymize: strip auth tokens, randomize product IDs, trim to ≤3
   products.
3. Re-run unit tests to confirm the parser still works on the new shape.
