# Runbook — Deploy static serving to Firebase Hosting (Phase 2)

The serving path is **static**: the monthly tick's `publish` stage writes
browser-ready JSON into `frontend/data/`, and the SPA + JSON ship to Firebase
Hosting (CDN, SSL, same-origin). No compute behind it. URL:
`https://trndly.web.app`.

> 💲 Steps marked 💲 create/alter cloud resources. Get explicit approval first.
> Identity = **Application Default Credentials** (`gcloud auth application-default
> login`) — never a committed SA key. Verify ADC is the right project before any
> cloud call (the active `gcloud config` is often `deployml-example`, the wrong
> project; Terraform pins `ml-ops-491417` explicitly, but the firebase CLI uses
> `--project`).

## Prerequisites (one-time)

```sh
# Firebase CLI (NOT installed by default on this machine).
npm install -g firebase-tools
firebase --version            # >= 13 (uses ADC; no FIREBASE_TOKEN)

# ADC for both terraform and firebase.
gcloud auth application-default login
gcloud auth application-default print-access-token >/dev/null && echo "ADC OK"
```

> **Phased applies in one root module.** `infra/` is a single root module that
> now contains Phase 2 (firebase.tf, ci_identity.tf) **and** Phase 3 (mlflow.tf)
> resources. A bare `terraform apply` would create everything at once. To honor
> the per-phase cost gates, the Phase 2 applies below are **`-target`ed**. The
> full untargeted `apply` is run last (Phase 3), at which point it also drops the
> now-removed project-level Cloud Build writer grant in favor of the repo-scoped
> one (see infra/mlflow.tf).

## 1. Provision the Hosting site (Terraform)

`infra/firebase.tf` creates `google_firebase_project` + `_web_app` +
`_hosting_site` (all on the **beta** provider; `site_id = "trndly"`).

### 1a. Pre-apply collision check (free)
Adding Firebase to an existing project can auto-create a default Hosting site at
`site_id == project_id`. Confirm the `trndly` id is free:

```sh
firebase projects:list
firebase hosting:sites:list --project ml-ops-491417
firebase hosting:sites:create trndly --project ml-ops-491417   # errors if taken
```

- If `trndly` is taken, fall back to `trndly-app` / `gettrndly` (edit `site_id`
  in `firebase.tf`, the `target` in `firebase.json`, and the site id in
  `.firebaserc`).
- If a site already exists at the chosen id (e.g. the auto-created default),
  **import it before applying** instead of letting it collide:
  ```sh
  cd infra
  terraform import google_firebase_hosting_site.default ml-ops-491417/trndly
  ```

### 1b. 💲 Apply gate (targeted — Firebase only)
```sh
cd infra
terraform init   # first time: configures the GCS backend + installs providers
terraform plan -out tf.plan \
  -target=google_firebase_project.default \
  -target=google_firebase_web_app.default \
  -target=google_firebase_hosting_site.default
# expect exactly 3 Firebase resources created, 0 destroyed
terraform apply tf.plan
```
**Enabling Firebase on the project is irreversible.** Ongoing cost ≈ $0 (free
Hosting tier).

## 2. 💲 First deploy (manual CLI)

```sh
cd trndly

# Ensure the published JSON is current (already committed from Phase 1; or:)
# python -m pipelines.monthly.publish

firebase deploy --only hosting:trndly --project ml-ops-491417
```

`firebase.json` (`public: "frontend"`) serves the SPA at the web root and sets
**`Cache-Control: no-cache` on `/data/**`** — load-bearing: the publisher
overwrites the canonical JSON in place each tick, so without revalidation users
would see last month's trends.

### Validate
```sh
curl -sI https://trndly.web.app/data/trends.json      | grep -i cache-control   # → no-cache
curl -sI https://trndly.web.app/data/fingerprint.json | grep -i cache-control   # → no-cache
curl -sI https://trndly.web.app/                       | grep -i content-type    # → text/html
```
Then open `https://trndly.web.app` and confirm Trends/forecast render with **no
console fetch errors**.

## 3. CI auto-deploy (Workload Identity Federation)

`.github/workflows/deploy-hosting.yml` runs `pytest tests/serving` (the lag-join
gate) then deploys on push to `main`, authenticating **keylessly** via WIF.

### 3a. 💲 Provision the CI identity
Either apply `infra/ci_identity.tf` (recommended — IaC) **or** create the WIF
pool/provider + deploy SA in the console. The TF path (targeted, so it doesn't
pull in Phase 3):

```sh
cd infra
terraform apply \
  -target=google_iam_workload_identity_pool.github \
  -target=google_iam_workload_identity_pool_provider.github \
  -target=google_service_account.github_deploy \
  -target=google_project_iam_member.github_deploy_hosting_admin \
  -target=google_service_account_iam_member.github_wif_user
terraform output ci_wif_provider     # → WIF_PROVIDER
terraform output ci_deploy_sa_email  # → DEPLOY_SA
```

The provider's `attribute_condition` trusts **only** `jacksoncdawson/trndly` —
do not relax it.

### 3b. Wire the GitHub repo
In GitHub → repo → Settings → Secrets and variables → Actions → **Variables**,
add (these are not secret):

| Variable       | Value                              |
|----------------|------------------------------------|
| `WIF_PROVIDER` | output of `terraform output ci_wif_provider` |
| `DEPLOY_SA`    | output of `terraform output ci_deploy_sa_email` |

Push to `main` (or run the workflow via `workflow_dispatch`) to deploy.

## Phase 2.5 (optional, later) — custom domain `trndly.app`
Not required for go-live. Register `trndly.app` (~$12–20/yr; `.app` is
HSTS-preloaded, HTTPS mandatory — Firebase auto-provisions the cert), then in
Firebase Hosting add it as a **custom domain** on the `trndly` site (console →
add custom domain → add TXT verification + A/AAAA records to DNS → wait for cert,
up to ~24h). No Terraform resource needed. `trndly.web.app` keeps working
alongside it.

## Troubleshooting
- **`Cache-Control` not `no-cache`** → the `headers` block in `firebase.json`
  didn't apply; confirm `source: "/data/**"` and redeploy.
- **403 on deploy** → the deploy SA lacks `roles/firebasehosting.admin`, or the
  WIF `attribute.repository` doesn't match `jacksoncdawson/trndly`. If the 403 is
  on **project resolution / serviceusage** (not hosting itself — some
  firebase-tools versions probe project metadata under ADC), add
  `roles/serviceusage.serviceUsageConsumer` (and/or `roles/firebase.viewer`) to
  the deploy SA — **do not** broaden to `roles/firebase.admin`. Confirm the true
  minimum against the installed firebase-tools version.
- **SPA loads but data is stale** → CDN served a cached copy; `no-cache` forces
  revalidation, so check the response headers actually carry it.
