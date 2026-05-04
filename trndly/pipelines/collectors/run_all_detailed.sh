#!/bin/bash
# Run all 4 scrapers in --detailed mode sequentially.
# Intended to be kicked off before bed and left to run overnight.
#
# Usage:
#   cd trndly/pipelines/collectors
#   bash run_all_detailed.sh
#
# Each scraper visits up to 50 products per listing page (9 pages each).
# Estimated runtime: 1.5–3 hours total depending on page load times.
#
# Output files (all gitignored):
#   synthetic_data/items_gap.csv
#   synthetic_data/items_hollister.csv
#   synthetic_data/items_uniqlo.csv
#   synthetic_data/items_american_eagle.csv
#
# After this finishes, open 1c_live_items_aggregate.ipynb and run it.

set -e
cd "$(dirname "$0")"

echo "======================================================"
echo " Detailed scrape — all 4 retailers"
echo " Started: $(date)"
echo "======================================================"

echo ""
echo ">>> [1/4] Gap"
python gap_scraper.py --detailed --headless true
echo "    Done: $(date)"

echo ""
echo ">>> [2/4] Hollister"
python hollister_scraper.py --detailed --headless true
echo "    Done: $(date)"

echo ""
echo ">>> [3/4] Uniqlo"
python uniqlo_scraper.py --detailed --headless true
echo "    Done: $(date)"

echo ""
echo ">>> [4/4] American Eagle"
python american_eagle_scraper.py --detailed --headless true
echo "    Done: $(date)"

echo ""
echo "======================================================"
echo " All scrapers complete: $(date)"
echo " Next: open Notebooks/1c_live_items_aggregate.ipynb"
echo "======================================================"
