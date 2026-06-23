# Project APIs (plan §6, Phase 0). Enabled up front for the whole build so later
# phases can `apply` without a separate enablement step. `disable_on_destroy =
# false`: destroying one phase's resources must never tear down an API another
# phase (or another project workload) still needs.
locals {
  gcp_apis = toset([
    # Enablement plumbing (required for google_project_service itself).
    "cloudresourcemanager.googleapis.com",
    "serviceusage.googleapis.com",
    "iam.googleapis.com",
    # Storage (state bucket already exists; artifacts/Hosting buckets later).
    "storage.googleapis.com",
    # Phase 3 — MLflow runtime + backend.
    "run.googleapis.com",
    "sqladmin.googleapis.com",
    "secretmanager.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
    # Phase 3 upgrade path — private-IP Cloud SQL + VPC connector (plan §6).
    "servicenetworking.googleapis.com",
    "vpcaccess.googleapis.com",
    # Phase 2 — Firebase Hosting.
    "firebase.googleapis.com",
    "firebasehosting.googleapis.com",
    # Phase 5 — dynamic tier (Firestore + Identity Platform / Firebase Auth).
    "firestore.googleapis.com",
    "identitytoolkit.googleapis.com",
  ])
}

resource "google_project_service" "apis" {
  for_each = local.gcp_apis

  project = var.project_id
  service = each.value

  disable_on_destroy = false
}
