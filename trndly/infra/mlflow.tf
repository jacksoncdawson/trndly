# Phase 3 — Private MLflow (plan §5/§8). Cloud Run v2 (private) + Cloud SQL
# Postgres + GCS artifacts (proxied) + a dedicated least-privilege SA.
#
# SECURITY (non-negotiable, plan §5):
#   * Private = simply NO allUsers/allAuthenticatedUsers run.invoker binding.
#     The only invoker is the operator (below). sa-tick is deferred to Phase 4.
#   * sa-mlflow gets EXACTLY three grants: cloudsql.client (project),
#     objectAdmin on the ONE artifacts bucket, secretAccessor on the ONE DB
#     secret. Never the default Compute SA.
#   * DB password lives in Secret Manager, injected at deploy — never in the
#     image or git. (It does render into TF state; the private+versioned state
#     bucket is the control — plan §5.)
#   * --artifacts-destination (NOT --default-artifact-root): the server proxies
#     GCS so only sa-mlflow touches the bucket; clients need only run.invoker.

# --- Artifact Registry (MLflow image home) ---

resource "google_artifact_registry_repository" "mlflow" {
  project       = var.project_id
  location      = var.region
  repository_id = "mlflow"
  format        = "DOCKER"
  description   = "Private MLflow server images (immutable tags)."

  depends_on = [google_project_service.apis]
}

# Tighten the Cloud Build identity: grant artifactregistry.writer ONLY on this
# repo (reusing local.cloudbuild_sa_member from build_identity.tf), replacing the
# project-level grant that build_identity.tf used to hold (now removed there).
resource "google_artifact_registry_repository_iam_member" "cloudbuild_writer" {
  project    = var.project_id
  location   = google_artifact_registry_repository.mlflow.location
  repository = google_artifact_registry_repository.mlflow.repository_id
  role       = "roles/artifactregistry.writer"
  member     = local.cloudbuild_sa_member
}

# --- Artifacts bucket (fresh; UBLA + public-access-prevention enforced) ---

resource "google_storage_bucket" "mlflow_artifacts" {
  project                     = var.project_id
  name                        = "${var.project_id}-mlflow-artifacts"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false

  versioning {
    enabled = true
  }

  depends_on = [google_project_service.apis]
}

# --- DB password: random → Secret Manager ---

resource "random_password" "db" {
  length  = 32
  special = false # URI-safe: the password goes into the backend-store-uri.
}

resource "google_secret_manager_secret" "db_password" {
  project   = var.project_id
  secret_id = "mlflow-db-password"

  replication {
    auto {}
  }

  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_version" "db_password" {
  secret      = google_secret_manager_secret.db_password.id
  secret_data = random_password.db.result
}

# --- Cloud SQL (Postgres 15, db-f1-micro; public IP, NO authorized networks) ---
# Slow to provision (~10 min) — start this apply early (handoff "execution order").

resource "google_sql_database_instance" "mlflow" {
  project          = var.project_id
  name             = "mlflow"
  database_version = "POSTGRES_15"
  region           = var.region

  # Terraform-level guard against accidental `terraform destroy`.
  deletion_protection = true

  settings {
    tier              = "db-f1-micro"
    availability_type = "ZONAL"
    disk_autoresize   = true

    ip_configuration {
      ipv4_enabled = true
      # No authorized_networks block at all: the only access path is the
      # IAM-gated /cloudsql Auth Proxy socket from Cloud Run.
    }

    backup_configuration {
      enabled = true
    }
  }

  depends_on = [google_project_service.apis]
}

resource "google_sql_database" "mlflow" {
  project  = var.project_id
  name     = "mlflow"
  instance = google_sql_database_instance.mlflow.name
}

resource "google_sql_user" "mlflow" {
  project  = var.project_id
  name     = "mlflow"
  instance = google_sql_database_instance.mlflow.name
  password = random_password.db.result
}

# --- Dedicated runtime SA + exactly three scoped grants ---

resource "google_service_account" "mlflow" {
  project      = var.project_id
  account_id   = "sa-mlflow"
  display_name = "MLflow Cloud Run runtime (least privilege)"

  depends_on = [google_project_service.apis]
}

# 1/3 — connect to Cloud SQL (project-scoped capability).
resource "google_project_iam_member" "mlflow_cloudsql_client" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.mlflow.email}"
}

# 2/3 — read/write artifacts on the ONE bucket only (bucket-scoped).
resource "google_storage_bucket_iam_member" "mlflow_artifacts_admin" {
  bucket = google_storage_bucket.mlflow_artifacts.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.mlflow.email}"
}

# 3/3 — read the ONE DB-password secret only (secret-scoped).
resource "google_secret_manager_secret_iam_member" "mlflow_db_secret_accessor" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.db_password.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.mlflow.email}"
}

# --- Cloud Run v2 service (private; image tag must already be pushed) ---

resource "google_cloud_run_v2_service" "mlflow" {
  project  = var.project_id
  name     = "mlflow"
  location = var.region

  # Allow `terraform destroy` to tear this down (default is true in recent
  # provider versions). Privacy is enforced by the IAM binding below, not this.
  deletion_protection = false

  template {
    service_account = google_service_account.mlflow.email

    scaling {
      min_instance_count = 0 # scale to zero between uses
      max_instance_count = 1
    }

    volumes {
      name = "cloudsql"
      cloud_sql_instance {
        instances = [google_sql_database_instance.mlflow.connection_name]
      }
    }

    containers {
      image = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.mlflow.repository_id}/mlflow:3.14.0"

      # The image is generic; the full server command lives here. `$(DB_PASSWORD)`
      # is a Cloud Run runtime env substitution (NOT Terraform `${}`) — it is
      # replaced with the secret value at container start.
      args = [
        "mlflow", "server",
        "--backend-store-uri", "postgresql+psycopg2://${google_sql_user.mlflow.name}:$(DB_PASSWORD)@/${google_sql_database.mlflow.name}?host=/cloudsql/${google_sql_database_instance.mlflow.connection_name}",
        "--serve-artifacts",
        "--artifacts-destination", "gs://${google_storage_bucket.mlflow_artifacts.name}/mlflow",
        "--host", "0.0.0.0",
        "--port", "8080",
      ]

      ports {
        container_port = 8080
      }

      env {
        name = "DB_PASSWORD"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.db_password.secret_id
            version = "latest"
          }
        }
      }

      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "1Gi"
        }
      }
    }
  }

  # The service can't start until the secret version exists and sa-mlflow can
  # read it + reach Cloud SQL; the image must be pushed to the repo first.
  depends_on = [
    google_secret_manager_secret_version.db_password,
    google_secret_manager_secret_iam_member.mlflow_db_secret_accessor,
    google_project_iam_member.mlflow_cloudsql_client,
    google_storage_bucket_iam_member.mlflow_artifacts_admin,
    google_artifact_registry_repository_iam_member.cloudbuild_writer,
  ]
}

# --- Private invoker: operator only. NO allUsers. (sa-tick → Phase 4.) ---

resource "google_cloud_run_v2_service_iam_member" "mlflow_invoker_operator" {
  project  = var.project_id
  name     = google_cloud_run_v2_service.mlflow.name
  location = google_cloud_run_v2_service.mlflow.location
  role     = "roles/run.invoker"
  member   = "user:jacksoncdawson@gmail.com"
}
