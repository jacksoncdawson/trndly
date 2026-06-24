# ADR 0001 — How the cloud tick refreshes the CDN (data-refresh path)

- **Status: PROPOSED — REVIEW REQUIRED before implementing.** Do **not** build
  Phase 11 (tick cadence automation) until this is accepted.
- Date raised: 2026-06-24
- Affects: deferred Phase 11 (Cloud Scheduler + Cloud Run Job tick) and
  [serving-redesign.md](../serving-redesign.md) §4 / §11 / §12.5.
- Supersedes nothing yet; refines §12.5's "the CDN serves only the latest tick's
  `published/` JSON" by specifying the *bridge* that sentence leaves implicit.

## Context — the conflict

Two serving-refresh models exist, and they do **not** compose as-is:

**Model A — what is BUILT today (git-driven).**
```
local CLI tick → publish.py → frontend/data/*.json (committed to git)
  → merge to main → CI (.github/workflows/deploy-hosting.yml) `firebase deploy` → CDN
```
Git is the data source of truth. The Phase-2 CI is correct for this.

**Model B — the cloud TARGET (deferred, §11/§12.5).**
```
Cloud Scheduler → Cloud Run Job (tick)
  → writes immutable gs://<data-bucket>/ticks/<YYYY-MM>/published/*.json (+ _SUCCESS)
  → ??? the published JSON must still reach Firebase Hosting
```

The `???` is the gap. In Model B the committed `frontend/data/*.json` goes
**stale**, and a CI deploy triggered by an unrelated SPA code change would
re-upload that stale committed JSON, **clobbering** the fresh tick data.

## The hard constraint

**Firebase Hosting serves only its own uploaded content — it cannot serve
directly from a GCS bucket.** Serving static files straight from GCS is a
different product (Cloud CDN + an HTTPS load balancer with a GCS *backend
bucket*), which we deliberately did not build and which conflicts with §5
(data buckets stay private: UBLA + public-access-prevention enforced).

⟹ Regardless of where the tick runs, **something must `firebase deploy` the
latest `published/*.json` into Hosting.** GCS is never what the browser hits;
Hosting's CDN is. So Model B needs an explicit GCS→Hosting deploy step.

## Proposed decision (for review)

Make **GCS `ticks/<YYYY-MM>/published/` the single data source of truth**, and
make the deploy step assemble its bundle from two sources no matter who triggers
it:

```
deploy bundle = SPA (from the repo)  +  latest gs://…/ticks/<M>/published/*.json (from GCS)
        ▲                                              ▲
  code changes → CI on merge to main ─────────────────┤  both assemble the SAME bundle,
  monthly data → Cloud Run Job (tick) ────────────────┘  pulling data from GCS → no clobber
                                  → firebase deploy --only hosting:trndly → CDN
```

Concretely, when Phase 11 lands:
1. **Stop committing `frontend/data/*.json` to git.** It becomes a local-dev
   seed only; GCS is canonical. (Reverses the Phase-1 "commit canonical JSON for
   a turnkey demo" decision — call this out at review.)
2. **CI deploy gains a "fetch latest `published/` from GCS" step** before
   `firebase deploy`, so a *code* deploy never re-uploads stale data.
3. **The tick job deploys too**: after writing its GCS checkpoint, it assembles
   the same bundle and `firebase deploy`s, authenticated by its own SA.

Result: one data source (GCS), two deploy triggers (schedule + code change),
neither overwrites the other with stale JSON.

## Alternatives considered

- **Serve directly from GCS via Cloud CDN + LB backend bucket** (drop Firebase
  Hosting for data). Rejected: duplicates the CDN we already run, forces the data
  bucket public or signed-URL'd (conflicts with §5 private + PAP), and abandons
  the Phase-2 Hosting investment.
- **Cloud job commits the JSON back to git**, letting the existing CI deploy it.
  Rejected (tentatively): bot-commit loops, a git push credential in the job, and
  indirection — though it keeps a single deployer. Revisit if the assemble-from-
  GCS step proves awkward.
- **CI deploys SPA-only and never touches `/data`.** Not possible cleanly: a
  Firebase Hosting deploy replaces the *whole* site version, so "SPA-only" would
  drop the data unless the data is re-supplied — which is exactly the
  assemble-from-GCS step above.

## Open questions to resolve at review

1. Drop committed `frontend/data/*.json` entirely, or keep a tiny committed seed
   for offline/local dev and CI's golden test? (The `tests/serving` golden test
   currently reads committed fixtures — confirm it stays hermetic.)
2. Where does the tick job get the SPA to bundle — `git checkout` in the job, or
   the SPA baked into the job image / a separate artifact?
3. Deploy identity & least privilege: the tick job's SA (`sa-tick`, **not yet
   scaffolded**) needs `roles/firebasehosting.admin` + read on the data bucket;
   it also needs MLflow `run.invoker` for Phase 4. Keep these scoped.
4. Concurrency / ordering: guard against a code-deploy and a tick-deploy racing
   to publish different site versions in the same window.
5. Rollback: GCS `ticks/` is immutable + versioned, so rollback = re-deploy a
   prior month's `published/`. Confirm that's the intended recovery path.

## What is true today (so this ADR isn't mistaken for built reality)

- Model A is live and internally consistent: local tick → committed JSON →
  CI/manual `firebase deploy`. Phases 2–3 are deployed.
- §12.5's `ticks/<M>/` layout is currently a **local** `data/ticks/` convention;
  the `gs://<data-bucket>/ticks/` mapping is **aspirational** (no cloud tick, no
  data bucket provisioned, no scheduler/job). All of Model B is unbuilt and
  blocked on this ADR being accepted.
