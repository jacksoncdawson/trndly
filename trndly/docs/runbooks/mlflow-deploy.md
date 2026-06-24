# Runbook — Deploy private MLflow (Phase 3)

Rebuilds MLflow clean: **Cloud Run v2 (private)** + **Cloud SQL Postgres** +
**GCS artifacts (proxied)** + a dedicated least-privilege **sa-mlflow**. No
public endpoint — access is IAM-gated (`run.invoker` + an ID token). Replaces the
old compromised public VM.

> 💲 Every step here creates/alters cloud resources — get explicit approval.
> Identity = ADC (`gcloud auth application-default login`); verify it points at
> `ml-ops-491417` (the active `gcloud config` is often the wrong project; the TF
> provider pins the project, but `gcloud builds submit` uses the active config —
> pass `--project ml-ops-491417`).
>
> **Single root module:** `infra/` holds Phase 2 + Phase 3 together. The
> Cloud-SQL-early step is `-target`ed; the rest is a final full `terraform apply`
> (which also swaps the project-level Cloud Build writer grant for the
> repo-scoped one — see infra/build_identity.tf / mlflow.tf).

## Execution order (Cloud SQL is the long pole — start it first)

### 1. 💲 Provision the Artifact Registry repo + repo-scoped build identity
The image push (step 2) needs the repo to exist and the Cloud Build SA to have
writer on it.

```sh
cd infra
terraform apply \
  -target=google_artifact_registry_repository.mlflow \
  -target=google_artifact_registry_repository_iam_member.cloudbuild_writer
```

### 2. 💲 Kick off Cloud SQL (slow — ~10 min — run it now, in parallel)
```sh
terraform apply -target=google_sql_database_instance.mlflow
```

### 3. 💲 Build + push the image (immutable tag)
`gcloud builds submit --tag` only builds a file named exactly `Dockerfile`, so
the named `Dockerfile.mlflow` is built via `infra/mlflow/cloudbuild.yaml` (an
explicit `docker build -f`):
```sh
cd infra/mlflow
gcloud builds submit --config cloudbuild.yaml --project ml-ops-491417 .
# pushes us-central1-docker.pkg.dev/ml-ops-491417/mlflow/mlflow:3.14.0
```
**Retry once on a transient 403** — IAM propagation lag after the repo-scoped
writer binding from step 1.

### 4. 💲 Apply the rest (full untargeted apply)
With the repo, image, and SQL instance in place, apply everything else — bucket,
secret + version, DB + user, sa-mlflow + 3 grants, Cloud Run service, operator
invoker. This is also where the old project-level Cloud Build writer grant is
**destroyed** (replaced by the repo-scoped one).

```sh
cd infra
terraform plan -out tf.plan    # review: ~no allUsers anywhere; 1 project-level binding destroyed
terraform apply tf.plan
```

Expected to be **created**: artifacts bucket, secret + version, SQL db + user,
sa-mlflow, 3 IAM bindings, Cloud Run service, operator `run.invoker`.
Expected to be **destroyed**: `google_project_iam_member.cloudbuild_artifactregistry_writer`.

## 5. 💲 Validate (round-trip a run through Cloud SQL + GCS)

MLflow is **private** — reach it through an authenticated local proxy:

```sh
# Terminal A — local proxy (uses your ADC; you hold run.invoker as the operator).
gcloud run services proxy mlflow --region us-central1 --project ml-ops-491417
# → serving on http://127.0.0.1:8080
```

```sh
# Terminal B — log a param + a small artifact.
export MLFLOW_TRACKING_URI=http://127.0.0.1:8080
python - <<'PY'
import mlflow
mlflow.set_experiment("smoke")
with mlflow.start_run():
    mlflow.log_param("hello", "world")
    with open("/tmp/smoke.txt", "w") as f:
        f.write("ok")
    mlflow.log_artifact("/tmp/smoke.txt")
print("logged")
PY
```

Confirm:
- The run + param appear in the MLflow UI (proxied at 127.0.0.1:8080) → backed by
  **Cloud SQL**.
- The artifact object exists under
  `gs://ml-ops-491417-mlflow-artifacts/mlflow/…`.

```sh
gsutil ls -r gs://ml-ops-491417-mlflow-artifacts/mlflow/
```

### Failure triage
- **Artifact write fails** → `--default-artifact-root` crept in instead of
  `--artifacts-destination`, or `objectAdmin` isn't bucket-scoped on sa-mlflow.
- **Run won't register** → the `/cloudsql/<CONN>` socket path is wrong, or the
  `$(DB_PASSWORD)` env substitution didn't resolve (check the secret version +
  `secretAccessor` binding).
- **403 invoking the service** → you lack `run.invoker` (only the operator is
  bound; `allUsers` is intentionally absent — that's the privacy control).

## Out of scope (Phase 4)
Lifecycle wiring (`train`→log, `evaluate`→`champion` alias, `predict`→load) and
the `sa-tick` `run.invoker` binding for unattended ticks. Phase 3 only leaves
MLflow **reachable**; the outputs (`mlflow_service_uri`, `mlflow_artifacts_bucket`,
`mlflow_sql_connection_name`) are how Phase 4 wires it by attribute.
