# Retail scrapers

Playwright-based collectors for **Gap**, **Hollister**, **Uniqlo**, and **American Eagle**. Each script walks category / new-arrival listing pages in a browser, derives normalized attributes (color, category, material, etc.), and writes:

- **`trend_signals_<retailer>.csv`** — proportion scores per feature bucket (for merging into the combined `trend_signals.csv`).
- **`trend_signals_<retailer>_meta.json`** — `{"total_items": N}` used as a weight when combining retailers.
- **`items_<retailer>.csv`** — one row per product–color (or product) when you use **`--detailed`**, with raw labels and lookup IDs.

All default output paths sit under:

`trndly/pipelines/training/synthetic_data/`

## Prerequisites

```bash
pip install playwright pandas
playwright install chromium
```

Run scrapers from this directory so imports resolve:

```bash
cd trndly/pipelines/collectors
```

## Quick start

**Detailed (slower, richer)** — visits product detail pages for colors, materials, and graphical appearance; **`items_*.csv`** gets one row per color variant where supported:

**THIS IS FOR EVERY ITEM POSSIBLE (WILL TAKE A WHILE TO RUN)**

```bash
python gap_scraper.py --detailed
python hollister_scraper.py --detailed
python uniqlo_scraper.py --detailed
python american_eagle_scraper.py --detailed
```


**Smoke test** (few PDPs per category):

**THIS IS TO RUN TO SEE IF IT WORKS -- CAN MAX TO 3**
```bash
python gap_scraper.py --detailed --max-products 5 --headless false
```

## Common CLI flags

These flags are shared across the four scrapers (names and defaults match; see each script’s `--help` for wording).

| Flag | Purpose |
|------|---------|
| `--detailed` | Turn on PDP visits and detailed **`items_*.csv`** rows. |
| `--max-products N` | Cap PDPs **per listing page** in detailed mode (default **50**). |
| `--headless false` | Open a visible Chromium window (often helps bot challenges). |
| `--sleep SEC` | Pause between listing pages (default **~3** s). |
| `--output-path PATH` | Override **`trend_signals_<retailer>.csv`**. |
| `--items-path PATH` | Override **`items_<retailer>.csv`**. |
| `--existing-path PATH` | Optional existing trend file to **blend** with scraped scores. |
| `--blend-weight W` | Blend weight for scraped vs existing (default **0.5**). |

**Hollister only:** `--debug` — dumps tile HTML when swatches are missing (selector maintenance).

## Combine retailers into one trend file

After you have one or more **`trend_signals_<retailer>.csv`** files in `synthetic_data`, merge them into a single **`trend_signals.csv`** (size-weighted by each `_meta.json` `total_items`):

```bash
python combine_trend_signals.py
```

Optional: explicit inputs and output:

```bash
python combine_trend_signals.py \
  --input trend_signals_gap.csv \
  --input trend_signals_hollister.csv \
  --output-path trend_signals.csv
```

Default combined output: **`trndly/pipelines/training/synthetic_data/trend_signals.csv`**.

## Run all four in detailed mode overnight

```bash
bash run_all_detailed.sh
```

Runs Gap → Hollister → Uniqlo → American Eagle with **`--detailed`** and **`--headless true`**. Expect on the order of **1–3+ hours** depending on network and caps.

## Operational notes

- **Trend horizons:** The CSV includes `current`, `next_week`, `next_month`, etc. Retail scrapers only compute a **snapshot** in **`current`**; missing horizon columns are filled to match the schema. Treat **`current`** as the live signal unless you add a separate forecast source.
- **Listing vs detailed:** For **American Eagle**, listing pages often contribute **few or no** swatch/API colors; **`--detailed`** is important for full color coverage in both **`items_*.csv`** and merged trend counts when configured that way in code.
- **Hollister** may serve bot challenges in headless mode; use **`--headless false`** if listing or PDPs come back empty.
- **URL dedupe:** In detailed mode, product URLs are deduplicated before the PDP loop so the same style listed under multiple color tiles is not scraped repeatedly.

## Scripts in this folder

| Script | Role |
|--------|------|
| `gap_scraper.py` | Gap |
| `hollister_scraper.py` | Hollister |
| `uniqlo_scraper.py` | Uniqlo |
| `american_eagle_scraper.py` | American Eagle |
| `combine_trend_signals.py` | Merge per-retailer **`trend_signals_*.csv`** → **`trend_signals.csv`** |
| `run_all_detailed.sh` | Sequential **`--detailed`** run for all four |

Supporting modules include **`scrape_color_utils.py`**, **`scrape_url_utils.py`**, and retailer-specific attribute maps aligned with **`trndly/EDA/data/lookup.csv`**.
