# Handoff — Implement Phase 2 (static serving) + Phase 3 (private MLflow)

> Self-contained execution brief for the next agent. Prepared 2026-06-23, after
> Phases 0 + 1 + the §12 per-tick-checkpoint refactor landed and were committed
> on `serving-redesign`. Decisions here were reviewed and are locked — do not
> re-litigate. Source of truth for detail: [`serving-redesign.md`](serving-redesign.md).

You are continuing the `trndly` serving-redesign build. Phases 0, 1, and the §12
per-tick-checkpoint refactor are **done and committed**; your job is Phase 2 and
Phase 3. This is greenfield work added to an already-scaffolded repo. 

## Read first (required)
- `trndly/docs/serving-redesign.md` — the full build plan. Read §5 (security,
  non-negotiable), §6 (IaC), §7 (sequence), and §8 Phase 2 + Phase 3 (concrete
  moves, includes near-verbatim HCL hints).
- Repo: root `/Users/jackcdawson/Desktop/trndly`, app dir `trndly/`, Terraform in
  `trndly/infra/`. Branch `serving-redesign`. GCP project `ml-ops-491417`
  (#117350290566), region `us-central1`. Verify ADC identity before any cloud
  call (the active gcloud config is often the wrong project).

## How to work
- **Do all the free, no-apply work first** (writing `.tf` files, deletions, CI
  YAML, Dockerfile). Then present a summary and **STOP at each cost gate** for
  explicit user approval.
- **APPLY POLICY (critical):** `terraform apply`, `firebase deploy`,
  `gcloud builds submit`, and any `gcloud` that creates/alters cloud resources
  **cost money and create real infra — get explicit user confirmation before
  each.** These are marked 💲 below.
- Small commits following the existing convention (`Phase 2 (n/n): …`,
  `Phase 3 (n/n): …`). **Never merge to main.** Commit the free/local work
  without asking; for anything applied, let the user drive.
- **Tests:** run with `~/.trndly-venv312/bin/python -m pytest tests -q` from
  `trndly/` (the repo's own `.venv` is broken). Green baseline = **275 passed,
  3 deselected**. After any test run, confirm `git status` is clean — tests must
  not mutate committed `data/`.
- Before applying any Terraform, verify the exact resource/argument names against
  the **current** `terraform-provider-google`/`-google-beta` docs (some beta
  resource names shift between provider versions).

## Infra already in place — REUSE, do not duplicate
`trndly/infra/` (Phase 0) already provides: **all APIs** for both phases (`run`,
`sqladmin`, `secretmanager`, `artifactregistry`, `cloudbuild`,
`servicenetworking`, `vpcaccess`, `firebase`, `firebasehosting`, `firestore`,
`identitytoolkit` — in `apis.tf`); **both providers** (`google` +
`google-beta ~> 6.0` in `versions.tf`/`providers.tf`); the **private versioned
remote-state bucket** via a separate `bootstrap/` config (`prevent_destroy`); the
**Cloud Build SA** with project-level `artifactregistry.writer`
(`build_identity.tf`, exported as `local.cloudbuild_sa_member`); shared vars
`project_id`/`project_number`/`region`/`zone` (`terraform.tfvars`). New resources
must `depends_on = [google_project_service.apis]` and reuse
`var.project_id`/`var.region` — never hardcode, never re-declare
APIs/providers/backend.

## Non-negotiable constraints (§5)
- MLflow Cloud Run is **private = NO `allUsers`/`allAuthenticatedUsers`
  `run.invoker` binding** (there is no `--allow-unauthenticated` in Cloud Run v2
  HCL; private is simply the absence of that binding).
- `sa-mlflow` gets **exactly three least-privilege grants**: `roles/cloudsql.client`
  (project), `roles/storage.objectAdmin` on the **one** artifacts bucket
  (bucket-scoped `google_storage_bucket_iam_member`),
  `roles/secretmanager.secretAccessor` on the **one** DB-password secret. Never the
  default Compute SA.
- DB password lives in **Secret Manager**, injected into Cloud Run at deploy —
  never in the image or git.
- Artifacts + state buckets: **UBLA + public-access-prevention enforced**.
- MLflow server command uses **`--artifacts-destination` (NOT
  `--default-artifact-root`)** — the latter makes clients write GCS directly,
  breaking the proxy/least-priv design.
- Provider split: `google-beta` **only** for the three `google_firebase_*`
  resources; everything else stays on stable `google`.
- CI deploy MUST be `needs: [test]` — the `tests/serving` golden diff is the only
  thing catching lag-join drift (`contracts.py` doesn't cover it). Never deploy on
  red.

## Locked decisions (do not re-ask)
- **Hosting URL = `https://trndly.web.app`** → `site_id = "trndly"` (a brand label,
  deliberately ≠ the project id; immutable once created). Appears available
  (`trndly.web.app` currently returns Firebase's "Site Not Found"); confirm at
  apply time with `firebase hosting:sites:create trndly` or the console. If taken,
  fall back to `trndly-app` / `gettrndly`. (`trndly.app` is an optional custom
  domain — see the optional section below.)
- CI auth = **Workload Identity Federation** (`google-github-actions/auth@v2` + a
  dedicated deploy SA). The `FIREBASE_TOKEN` path is deprecated — do not use it.
- First Phase-2 deploy is **manual `firebase deploy`**; wire CI after.
- MLflow **3.14.0**; Cloud SQL **POSTGRES_15**, tier **db-f1-micro**.
- Cloud SQL networking = **public IP with EMPTY `authorized_networks`** (access only
  via the IAM-gated `/cloudsql` Auth Proxy from Cloud Run). Not private-IP/VPC.

## Out of scope (do NOT build)
- **Phase 4** (MLflow lifecycle: `train`→log, `evaluate`→`champion` alias,
  `predict`→load). But Phase 3 must leave MLflow **reachable** for it (export
  outputs; bind `run.invoker` to the operator only — see below).
- **Phase 5** (Firestore + Auth, persistent per-user inventory). Inventory stays
  client-side session state. Do **not** add a user DB or auth-gate the predictions.
  The static `fingerprint.json` bundle (public CDN object; client does the 5-D
  lookup; UI already shows only the user's inventory items) is the serving model and
  is correct as-is.

---

## Phase 2 — Static serving live (ordered)

1. **(free) Pre-flight.** `cd infra && terraform init && terraform plan` shows no
   drift. Confirm the `firebasehosting` API + `google-beta` provider already exist
   — **do not re-add them**. Confirm `frontend/data/*.json` (the publisher's
   canonical output) are committed.
2. **(free) `infra/firebase.tf`** — `google_firebase_project`,
   `google_firebase_web_app`, `google_firebase_hosting_site`, **all
   `provider = google-beta`**, `site_id = "trndly"` (brand label, immutable,
   ≠ project id), `depends_on` the Firebase API. Append 3 outputs to `outputs.tf`:
   site_id, `default_url`, web_app_id.
3. **(free) Pre-apply collision check.** Adding Firebase to an existing project can
   auto-create a default Hosting site at `site_id == project_id`. Run
   `firebase hosting:sites:list`; also confirm the chosen `trndly` site is free
   (`firebase hosting:sites:create trndly`, or the console). If a site already
   exists at your chosen id, `terraform import google_firebase_hosting_site.default
   <project>/<site_id>` **before** the apply rather than letting it collide.
4. 💲 **APPLY GATE** — `terraform plan -out tf.plan` → user approval →
   `terraform apply tf.plan`. Expect exactly the 3 Firebase resources + 3 outputs,
   nothing destroyed. **Enabling Firebase on the project is irreversible.** ~$0
   ongoing (free Hosting tier).
5. **(free) `firebase.json` + `.firebaserc`** at the app root (`trndly/`).
   `public: "frontend"`; **`Cache-Control: no-cache` on `/data/*.json`**
   (load-bearing — the publisher overwrites the canonical JSON in place each tick;
   without this, users see last month's data); `ignore` parquet/csv/node_modules.
   `.firebaserc` maps the `trndly` hosting target to the site_id `trndly`.
6. 💲 **First deploy (CLI)** — ensure `frontend/data/*.json` is current (already
   committed from Phase 1, or run `python -m pipelines.monthly.publish`), then
   `firebase deploy --only hosting:trndly --project ml-ops-491417` (use ADC,
   **never a committed SA key**). Validate: `curl -I
   https://trndly.web.app/data/trends.json` **and** `/data/fingerprint.json` both
   show `Cache-Control: no-cache`; SPA renders at `https://trndly.web.app` with no
   console fetch errors. Write this up as a runbook (`infra/README.md` or
   `docs/runbooks/deploy-hosting.md`).
7. **(free) Delete the dead container.** From `trndly/`: `git rm Dockerfile
   .dockerignore` (paths are app-dir-relative — **not** `trndly/`-prefixed).
8. **(free) Finish §2.5 remediation.** `rm backend/services/.env` (it's
   untracked/gitignored — a plain `rm`, the last remediation item). Remove
   `python-dotenv` from `requirements.txt` (zero importers). **Keep
   `fastapi`/`uvicorn`** — `tests/serving/test_publish.py` imports
   `backend.services.scheduleServer` (→ fastapi), and that test is the deploy gate.
9. **(free, then 💲 for its identity) CI** — `.github/workflows/deploy-hosting.yml`:
   a `test` job (`pytest tests/serving`, the gate) and a `deploy` job
   (`needs: [test]`, push-to-main only, WIF auth, `firebase deploy`). The WIF
   pool/provider + deploy SA + `roles/firebasehosting.admin` must be provisioned
   first — **decide with the user** whether that's a new `infra/ci_identity.tf`
   (a 💲 apply) or a one-time console step.

### Phase 2.5 (optional, later) — custom domain `trndly.app`
Not required for go-live and not part of the locked Phase 2 scope. If/when wanted:
register `trndly.app` at a registrar (~$12–20/yr; `.app` is a Google-run gTLD,
HSTS-preloaded so HTTPS is mandatory — Firebase auto-provisions the managed cert),
then in Firebase Hosting add it as a **custom domain** on the `trndly` site
(`firebase hosting:sites` / console → add custom domain → add the TXT verification
+ A/AAAA records to DNS → wait for cert provisioning, up to ~24h). No Terraform
resource is required; this is a CLI/console + DNS step. `trndly.web.app` keeps
working alongside the custom domain.

## Phase 3 — Private MLflow (ordered; its Cloud SQL is the slow long pole)

1. **(free) `infra/mlflow/Dockerfile.mlflow`** — `FROM python:3.12-slim`; pin
   `mlflow==3.14.0`, `psycopg2-binary`, `gunicorn`, **`google-cloud-storage`** (NOT
   `gcsfs` — the repo's `gcsfs` breaks the proxied-GCS server path). Let the Cloud
   Run `args` drive the `mlflow server` command (keep the image generic). Add a
   sibling `.dockerignore`.
2. 💲 **`infra/mlflow.tf`: Artifact Registry repo** — `google_artifact_registry_repository
   "mlflow"` (DOCKER, `var.region`). In the **same apply**, tighten the build
   identity: add a repo-scoped `google_artifact_registry_repository_iam_member`
   granting `artifactregistry.writer` to the existing `local.cloudbuild_sa_member`
   (reuse it, don't reconstruct the member string) and **remove** the project-level
   `google_project_iam_member.cloudbuild_artifactregistry_writer` from
   `build_identity.tf`.
3. 💲 **Build + push** — `gcloud builds submit --tag
   us-central1-docker.pkg.dev/ml-ops-491417/mlflow/mlflow:3.14.0 --file
   Dockerfile.mlflow .` (immutable tag, not `:latest`). Must precede the Cloud Run
   apply. **Retry once on a transient 403** (IAM propagation lag after the binding
   change).
4. 💲 **Artifacts bucket** — `google_storage_bucket "mlflow_artifacts"`,
   `uniform_bucket_level_access = true`, `public_access_prevention = "enforced"`,
   versioning on. Name `${var.project_id}-mlflow-artifacts`.
5. 💲 **Secret Manager** — add `hashicorp/random` to `versions.tf
   required_providers` (an intentional edit to a Phase-0 file), `random_password`
   (length 32, `special = false` so it's URI-safe), `google_secret_manager_secret` +
   `_version` for the DB password.
6. 💲 **Cloud SQL** — `google_sql_database_instance` (POSTGRES_15, db-f1-micro,
   `ipv4_enabled = true`, **omit `authorized_networks` entirely**,
   `deletion_protection = true`, backups on) + `_database` + `_user` (password =
   `random_password.result`). Expose `connection_name` via a local. *(Slow to
   provision — start this apply early.)*
7. **(free) `sa-mlflow`** — `google_service_account "mlflow"`.
8. **(free) Three scoped IAM bindings** — project `cloudsql.client`; bucket-scoped
   `storage.objectAdmin` on the artifacts bucket only; secret-scoped
   `secretmanager.secretAccessor` on the DB secret only. Nothing else.
9. 💲 **Cloud Run v2 service** — `template.service_account = sa-mlflow`;
   `template.volumes.cloud_sql_instance.instances = [connection_name]` +
   `volume_mounts` at `/cloudsql`; `env DB_PASSWORD` via
   `value_source.secret_key_ref` (secret_id, version `latest`); `args =
   ["mlflow","server","--backend-store-uri","postgresql+psycopg2://mlflow:$(DB_PASSWORD)@/mlflow?host=/cloudsql/<CONN>","--serve-artifacts","--artifacts-destination","gs://${bucket}/mlflow","--host","0.0.0.0","--port","8080"]`;
   `ports.container_port = 8080`. Image tag must already be pushed (step 3). Verify
   the exact HCL shapes against current provider docs before apply.
10. **(free) Private invoker** — `google_cloud_run_v2_service_iam_member`
    `run.invoker` to the **operator only** (`user:jacksoncdawson@gmail.com`).
    **Defer** the `sa-tick` binding to Phase 4 (that SA isn't scaffolded — don't
    reference a non-existent SA). **No `allUsers`.**
11. **(free) Outputs** — export `mlflow_service_uri` (`.uri`), the artifacts bucket
    name, and the SQL `connection_name`, so Phase 4 can wire reachability by
    attribute.
12. 💲 **Validate** — `gcloud run services proxy mlflow`, set `MLFLOW_TRACKING_URI`
    to the local proxy, `mlflow.create_experiment(...)`, log a param + a small
    artifact; confirm rows land in Cloud SQL and the object appears under
    `gs://…-mlflow-artifacts/mlflow/`. If the artifact write fails → suspect
    `--default-artifact-root` crept in or `objectAdmin` isn't bucket-scoped; if the
    run fails to register → suspect the `/cloudsql` socket path or the
    `$(DB_PASSWORD)` interpolation.

## Execution order
Do the free local work for **both** phases first and present it for review (no
spend). Then run the 💲 gates — recommend kicking off the **Phase 3 Cloud SQL apply
early** (slowest), and the Phase 2 apply whenever (gets the demo live fastest).
Confirm the order with the user before the first apply.
