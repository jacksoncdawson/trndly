#!/bin/bash
# Run all 4 retail scrapers sequentially, then build the live cubes.
#
# Usage:
#   cd trndly/pipelines/collectors
#   bash run_all.sh
#
# Each scraper writes only items_<retailer>.csv. The aggregator
# (build_live_cube.py) reads every items_*.csv and produces:
#   data/processed/live_monthly_fingerprint.parquet
#   data/processed/live_monthly_univariate.parquet
# Run notebooks/1b_scrape_aggregate_live.ipynb afterwards to merge those
# into the canonical historical cubes consumed by training + serving.
#
# Estimated wall-clock:
#   gap        ~17s   (~5,200 rows)
#   uniqlo     ~30s   (~3,000 rows; 100% PDP enrichment hit rate)
#   american_eagle  ~3min  (Playwright JWT bootstrap + Akamai-throttled fan-out)
#   hollister  ~5min  (~21,000 rows; ~250s for ~2,200 PDP enrichment fetches)
#   build cube ~3s
#   ─────────────────
#   total      ~9-10 min
#
# All scrapers default to --enrich-pdp (PDP material enrichment ON).
# Drop to --no-enrich-pdp for a faster smoke (~2-3x quicker, ~14% material unknown).

set -e
cd "$(dirname "$0")"

echo "======================================================"
echo " Retail collectors — full run"
echo " Started: $(date)"
echo "======================================================"

echo ""
echo ">>> [1/4] Gap"
${PYTHON:-python} gap_scraper.py
echo "    Done: $(date)"

echo ""
echo ">>> [2/4] Uniqlo"
${PYTHON:-python} uniqlo_scraper.py
echo "    Done: $(date)"

echo ""
echo ">>> [3/4] American Eagle"
${PYTHON:-python} american_eagle_scraper.py
echo "    Done: $(date)"

echo ""
echo ">>> [4/4] Hollister"
${PYTHON:-python} hollister_scraper.py
echo "    Done: $(date)"

echo ""
echo ">>> [+1] Build live cubes from items_*.csv"
${PYTHON:-python} build_live_cube.py

echo ""
echo "======================================================"
echo " All done: $(date)"
echo " Outputs:"
echo "   pipelines/training/synthetic_data/items_<retailer>.csv  (4 files)"
echo "   data/processed/live_monthly_fingerprint.parquet"
echo "   data/processed/live_monthly_univariate.parquet"
echo " Next: run notebooks/1b_scrape_aggregate_live.ipynb to merge"
echo " into the canonical monthly_*.parquet cubes."
echo "======================================================"
