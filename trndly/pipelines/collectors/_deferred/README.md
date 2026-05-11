# Deferred collectors

Modules parked here are functionally complete but **not currently wired into
the pipeline**. They live here instead of being deleted because the work
they do is real and likely valuable later — we just don't have a consumer
for it yet.

## Contents

### `google_trends_collector.py`

Pulls Google search-interest series via pytrends. The previous combine
flow blended this signal into `trend_signals.csv` alongside retailer
catalog counts; the new live-cube flow (`../build_live_cube.py`) is built
exclusively from retailer scrape data. To bring Google Trends back as a
*parallel* signal, see HANDOFF.md item #10 — the open question is the
consumer story (separate column? blend at fixed weight? feature in the
training cube?).

The module itself is untouched and runnable; just nothing reads its
output today.
