# ADR 0002 — Persistent backfill cube (anchor priors as an aggregate input)

- **Status: ACCEPTED (2026-06-24)** — implemented in the same change.
- Supersedes the in-place `scripts/backfill_anchor_lags.py` patch behavior.
- Related: the 2026-06 anchor incident (anchor fell back to `2020-08`); the
  `_find_eligible_anchor` requirement; [serving-redesign.md](../serving-redesign.md) §12.

## Context

The forecaster's `_find_eligible_anchor` ([predict.py](../../pipelines/monthly/predict.py))
needs **4 contiguous months** (anchor + 3 lags). The shipped data has a ~5-year
gap between the historical Kaggle block (2018-10 → 2020-08) and the first live
scrape (2026-05), so out of the box the only contiguous run is the historical
tail and the anchor falls back to **2020-08** — temporally wrong.

`scripts/backfill_anchor_lags.py` manufactured synthetic priors for the three
months before the latest live month (`share_t_live × mean(hist[lag]/hist[live])`)
and wrote them **in place** into the tick's `merged_*.parquet`, marked
`source='backfill'`. The fatal flaw: **`aggregate` rebuilds `merged_*` from
scratch every tick and clobbers the backfill**, so it had to be re-run, in the
right order, every single tick. The June 2026 tick skipped it → no `backfill`
rows → anchor `2020-08`, and the bad month nearly published.

## Decision

Make the synthetic priors a **persistent input artifact that `aggregate` unions
in**, instead of a post-aggregate in-place patch.

```
data/processed/
  historical_{fingerprint,univariate}.parquet   source='historical'  (notebook 1)
  live_{fingerprint,univariate}_<YYYY-MM>.parquet source='live'       (build_cube)
  backfill_{fingerprint,univariate}.parquet      source='backfill'    (generated ONCE)  ← NEW
        ▼
aggregate: merged = historical ∪ live ∪ backfill   (dedup keep='last')
        ▼
predict: _find_eligible_anchor now sees 2026-02..06 contiguous → anchors the
         latest REAL live month, never the 2020-08 tail
```

1. **`backfill_anchor_lags.py` becomes a one-time generator.** It reads
   `historical_*` + the latest live month, computes the synthetic priors
   (unchanged math), normalizes shares per synthetic month, and writes **only**
   the `source='backfill'` rows to `data/processed/backfill_{fp,uv}.parquet`. It
   no longer touches any tick's `merged_*`.
2. **`aggregate` unions the backfill artifact** as a third source (when present).
   No month overlap with live/historical, so the existing
   `(month, …, source)` dedup is safe.
3. **`predict` fails loud** ([predict.py](../../pipelines/monthly/predict.py)):
   the prior soft-warn becomes a hard error when the eligible anchor is older
   than the latest **real** (non-backfill) live month, unless
   `TRNDLY_ALLOW_STALE_ANCHOR=1`. The persistent artifact is the fix; this is the
   backstop so a missing/misloaded artifact can never silently reproduce the
   2020-08 fallback.
4. **The artifact is committed** (gitignore exception for
   `data/processed/backfill_*.parquet`, which is otherwise ignored) so the demo
   anchors correctly from a clean checkout — consistent with committing
   `data/ticks/2026-05` and the raw-items seeds. Pegged to the 2026-05 live month.

## Why this is better
- **Idempotent / no ordering trap.** No "must re-run backfill after aggregate or
  it silently breaks." June's failure mode becomes impossible.
- **No clobber.** `aggregate` consumes the artifact; it never overwrites it.
- **Fits §12's immutable-checkpoint model** and the Phase 6 goal — the artifact
  is a stable reference input (and a clean GCS object when storage moves cloud).

## Consequences / things to know
- **The `share_t` scaling is frozen.** Priors are pegged to the 2026-05 live
  month's `share_t`. Acceptable: they are synthetic, sit at the back of the lag
  window, and **self-retire** — once 4 real contiguous live months exist (~2026-08)
  `_find_eligible_anchor` advances to a real anchor and the backfill rows go inert.
- **Dedup priority (defensive).** Backfill months (2026-02/03/04) never overlap
  real months, so today there is no conflict. If that ever changes, real must win
  over backfill for a `(month, key)` — noted so a future change can't average
  synthetic into real (aggregate mean-pools duplicate `(month,key)` downstream).
- **`lags_synthetic`** in `health.json` correctly reflects the presence of
  `source='backfill'` rows — unchanged.

## Out of scope / known follow-ups
- **Hollister scraper (Akamai 403).** Separately diagnosed (2026-06): a
  structural, fingerprint-agnostic Akamai edge block — every client shape 403s,
  `robots.txt` included. A re-run will not work; the fix is the Playwright
  cookie-bootstrap (or a TLS-impersonating client + residential egress). Deferred.
  The scrape-completeness guard already prevents a silent header-only publish.
- Auto-running the generator inside the tick is unnecessary now (the artifact is
  persistent); revisit only if the pegged-to-2026-05 freeze ever needs refreshing.
