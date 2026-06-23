# Deferred collectors

Modules parked here are functionally complete *in intent* but **not currently
wired into the pipeline**. They live here instead of being deleted because the
work they do is real and likely valuable later — we just don't have a consumer
for it yet.

> **Heads-up before you try to run anything in here:** "deferred" does not mean
> "drop-in ready." These modules were written against an earlier version of the
> codebase and have since drifted out of sync with the current contracts/paths.
> Reviving one means re-integrating it, not just running it. See the per-module
> notes below for the specific blockers.

## Contents

### `google_trends_collector.py`

Pulls Google search-interest series via pytrends. For every color, category,
and material keyword it fetches ~90 days of daily search interest (pytrends
`today 3-m`) and reduces it to a single `current` column — the 7-day mean
(days 0–6), normalized 0–1. The intent: the
model takes *current* trend signals as input and predicts the best listing
timeframe — so no forward/future trend columns are computed here.

**Why it's parked:** the previous *combine* flow blended this signal into a
`trend_signals.csv` alongside retailer catalog counts. The current live-cube
flow (`../build_live_cube.py`) is built **exclusively from retailer scrape
data**, so nothing reads a Google Trends signal today.

**Current state — does NOT run as-is.** The module imports
`DEFAULT_MISSING_SCORE` and `validate_trend_signals_frame` from
`pipelines.contracts` (top of file), but neither symbol exists in
`pipelines/contracts.py` anymore — so the module raises `ImportError` at import
time. The contracts module today only exposes the live-cube / predictions
validators (`validate_live_fingerprint_frame`,
`validate_live_univariate_frame`, `validate_predictions_univariate_frame`,
`validate_predictions_fingerprint_frame`). The module's own docstring and
default `--output-path`
(`pipelines/training/synthetic_data/trend_signals.csv`) are also stale: there
is no `pipelines/training/` directory, no `synthetic_data/` directory, and no
`trend_signals.csv` anywhere in the repo, and `scheduleServer.py` serves
precomputed predictions parquets — not a trend-signals CSV. Reviving the module
therefore requires (1) reintroducing or replacing the missing contract helpers,
(2) picking a real output target, and (3) wiring an actual consumer — not just
running the script.

**Open question for revival (the hard part is the consumer story, not the
fetcher):** how should a Google Trends signal feed the model? Candidate
designs:

- a separate column carried alongside the live cube,
- a blend with retailer catalog counts at a fixed weight, or
- a first-class feature in the training cube.

None of these is decided. The fetch logic itself is sound and a good starting
point, but it should be treated as a reference implementation to port forward,
not a script to dust off and run.
