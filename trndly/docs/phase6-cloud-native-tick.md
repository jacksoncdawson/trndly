# Phase 6 — Cloud-native tick (scheduled, idempotent, GCS-only)

- **Status: PROPOSED — REVIEW REQUIRED before implementing.** Do not build until
  accepted. Sequenced **after Phase 4**.
- Date raised: 2026-06-24
- Promotes the scattered fragments — [serving-redesign.md](serving-redesign.md)
  §11 "tick cadence automation", §12.5 "GCS mapping", and architecture.md
  "Future: storage migration" — into one owned, sequenced phase.
- Hard prerequisite: **[ADR 0001](decisions/0001-cloud-tick-cdn-refresh.md)**
  (the GCS→CDN serving-refresh decision) must be accepted first; it is the
  serving sub-decision *inside* this phase.

## Goal (the end state)

The monthly tick runs **in the cloud on a schedule**, is **idempotent**, and
reads/writes **all data in GCS** — no local `data/` dependency for a production
run. A scheduled execution produces a new month's forecasts and refreshes the
CDN with **no human in the loop**.

## Why this is its own phase (not a follow-on note)

Today the tick is a local CLI; only its *outputs* reach the cloud. Phases 0–5
never move the tick or its data off the developer's machine. Making the pipeline
cloud-native touches storage, packaging, scheduling, identity, and the
serving-refresh path at once — too much to leave as a §11 footnote.

## What is already done toward it
- **Idempotency: built.** The §12 per-tick-checkpoint refactor gives per-month
  `_SUCCESS` markers; `run` is a no-op when `ticks/<month>/_SUCCESS` exists
  unless `--force` (§12.4). This semantics must carry over to GCS unchanged.
- **Storage chokepoint: ready.** `pipelines/paths.py` is the single seam, so
  local→GCS is a contained backend switch, not a scatter-gun change.
- **GCS layout: designed** (§12.5), not implemented.

## Dependencies
- **Phase 4** (lifecycle wiring) — the cloud tick must authenticate to private
  MLflow via the **ID-token path** (audience = service URL); that auth + the
  champion-flip logic are Phase 4. A scheduled tick is what makes unattended
  champion-flips real.
- **Phase 0 infra** — done (state bucket, APIs, providers).
- **ADR 0001** — accepted, for the GCS→Hosting serving refresh.
- Independent of **Phase 5** (dynamic tier): no Firestore/Auth dependency, so
  Phase 6 may land before, after, or alongside Phase 5.

## Concrete moves (ordered)
1. **Data bucket (Terraform).** `gs://<project>-trndly-data` (or reuse the
   existing `trndly-data` — decide at review), UBLA + public-access-prevention +
   Object Versioning, private. Bind `sa-tick` read/write.
2. **Repoint storage local→GCS in `paths.py`.** Make the backend switchable
   (`gs://` vs local) via env/config so the same code runs locally for dev and on
   GCS in the cloud. Cover `ticks/`, `processed/`, `reference/lookup.csv`.
   Confirm `_SUCCESS`/idempotency works against GCS object existence.
3. **One-time data migration.** Upload current local `data/` (reference, the
   latest tick checkpoint, the committed seed) to the bucket; verify the first
   cloud run reads it and produces an identical `published/` (reuse the golden
   diff as the gate).
4. **Containerize the tick.** A new `Dockerfile` for the tick (we deleted the old
   serving one in Phase 2) → Cloud Build → Artifact Registry, run as a
   **Cloud Run Job** (`gcloud run jobs`), not a Service.
5. **`sa-tick` + least-privilege IAM (Terraform).** Data-bucket read/write,
   MLflow `run.invoker` (Phase 4), Secret access only if needed, and — per
   ADR 0001 — `firebasehosting.admin` if the job performs the CDN deploy.
6. **Schedule it.** Cloud Scheduler (monthly cron) → invoke the Cloud Run Job via
   OIDC (Scheduler SA with `run.invoker` on the job). Manual `gcloud run jobs
   execute` stays available; idempotency makes re-runs safe.
7. **Serving refresh (ADR 0001).** After a successful tick, assemble
   SPA(git) + latest `gs://…/ticks/<M>/published/` and `firebase deploy`.

## Acceptance criteria
- A Cloud Scheduler trigger runs the Job end-to-end and a new month's forecasts
  go live on `https://trndly.web.app` with no human action.
- A second run for an already-`_SUCCESS` month is a no-op without `--force`,
  **on GCS**.
- A clean cloud run has **zero dependency on local `data/`**.
- The `tests/serving` golden diff still passes (parity preserved through the
  storage switch).
- `terraform plan` clean; no `allUsers`; `sa-tick` least-privilege.

## Future implications & ripple effects (note before building)
- **Reverses the "commit canonical JSON" decision.** The Phase-1 turnkey-demo
  choice (commit `frontend/data/*.json`) becomes stale once GCS is the source of
  truth (ADR 0001 step 1). **Watch the golden test:** `tests/serving` reads
  *committed* fixtures and must stay hermetic for CI — keep its inputs committed
  even after the runtime JSON stops being committed. Decide explicitly whether a
  tiny committed seed remains for local dev.
- **Local dev story changes.** Devs either point `paths.py` at GCS (needs creds)
  or run against a local seed/emulator. Keep the local CLI path working — don't
  make GCS the *only* way to run the tick.
- **Notebook coupling is the long tail of "entirely off local."** §12.6 flags
  the 214 MB notebook-only `transactions.parquet` and the `historical_*`/`live_*`
  couplings. Truly removing local data forces either moving these to GCS or
  explicitly scoping them out (notebooks stay a local/offline concern). Decide
  the boundary; don't let "entirely" silently exclude them.
- **Two deployers to the same Hosting site** (the tick job + code-change CI).
  ADR 0001's assemble-from-GCS rule prevents stale-data clobbering; add a
  concurrency guard so a code deploy and a tick deploy can't race to publish
  different site versions in the same window.
- **MLflow auth shifts from proxy to ID-token.** Dev uses
  `gcloud run services proxy`; the cloud Job must mint an ID token (audience =
  MLflow service URL) — this is the Phase-4 mechanism, now exercised unattended.
- **Cost.** A data bucket (pennies), Cloud Run Job executions (per-run, minutes),
  Cloud Scheduler (negligible). The standing cost stays the Cloud SQL instance.
  No always-on compute is added (the Job scales to zero between months).
- **Failure/rollback.** GCS `ticks/` is immutable + versioned; rollback = re-run
  the deploy with a prior month's `published/`. Define alerting on a failed
  scheduled run (a silent monthly failure means a stale CDN).
- **Security surface grows** by exactly one SA (`sa-tick`) plus the Scheduler→Job
  OIDC invocation — keep both least-privilege; never `allUsers`/`allTokens`.

## Out of scope for Phase 6
- Model performance / champion quality (separate track, §11).
- BigQuery/analytics external tables (§10 — optional, not serving).
- Phase 5 dynamic tier (independent).

## Open questions for review
1. New data bucket vs reuse the existing `trndly-data`?
2. Keep a committed local seed for dev + the golden test, or fully GCS?
3. Where do the notebook intermediates land — GCS, or explicitly out of scope?
4. Does the tick Job itself deploy to Hosting, or hand off to the CI (per the
   ADR 0001 alternatives)?
5. Alerting/observability for failed scheduled runs — what and where?
