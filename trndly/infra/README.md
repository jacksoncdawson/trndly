# trndly infrastructure (Terraform)

Greenfield IaC for the [serving redesign](../docs/serving-redesign.md). **All
infrastructure is Terraform** (plan §6); content and images are deployed via
CLI/CI (`firebase deploy`, `gsutil`, Cloud Build image push), never Terraform.

The root module is applied **incrementally per phase**:

| Phase | Adds | Files |
|-------|------|-------|
| 0 — Foundations | Project APIs + Cloud Build identity | `apis.tf`, `build_identity.tf` |
| 2 — Static serving | Firebase project + Hosting site (beta provider) | *(added later)* |
| 3 — MLflow (private) | Cloud Run v2 + Cloud SQL + GCS artifacts + `sa-mlflow` | *(added later)* |
| 5 — Dynamic tier | Firestore + Identity Platform / Firebase Auth | *(added later)* |

## Layout

```
infra/
├── bootstrap/        # SEPARATE config: creates the remote-state bucket (local state, applied once)
├── versions.tf       # terraform + provider version pins (google + google-beta)
├── providers.tf      # provider config (project, region)
├── backend.tf        # GCS backend → the bootstrap bucket
├── variables.tf      # project_id, project_number, region, zone
├── terraform.tfvars  # non-secret values (committed)
├── apis.tf           # Phase 0: google_project_service for the full API set
├── build_identity.tf # Phase 0: Cloud Build SA → artifactregistry.writer
└── outputs.tf
```

## Security posture (plan §5/§6)

- **Remote state holds secrets** (the Phase 3 DB password + SA details render
  into state). The state bucket is therefore **private, versioned, uniform
  bucket-level access, public-access-prevention enforced**, and is kept in a
  separate bootstrap config with `prevent_destroy = true` so Terraform never
  manages the backend it depends on.
- **No public + unauthenticated compute.** The Phase 3 MLflow Cloud Run service
  gets **no `allUsers` `run.invoker`** binding. Static serving (Phase 2) is
  public but read-only files with no compute behind them.
- **Least-privilege dedicated SAs**, never the default Compute SA. Secrets live
  in Secret Manager, injected at deploy.

## First-time setup

Terraform uses Application Default Credentials. Authenticate as a principal with
rights on the project (the bucket name and project are pinned in `*.tfvars`):

```sh
gcloud auth application-default login
```

**1. Bootstrap the remote-state bucket (once, local state):**

```sh
cd infra/bootstrap
terraform init
terraform apply        # creates gs://ml-ops-491417-tfstate (prevent_destroy)
```

**2. Initialize + apply the root module (GCS backend):**

```sh
cd infra
terraform init         # configures the GCS backend against the bucket above
terraform apply        # Phase 0: enables APIs + grants the build SA
```

Later phases add resources to this same root module and re-run `terraform apply`.

## Notes

- `.terraform.lock.hcl` **is** committed (pins provider versions/hashes); state
  files and `.terraform/` are git-ignored.
- The active `gcloud config` project is irrelevant — the provider pins
  `project = ml-ops-491417` explicitly. Verify ADC identity before applying.
