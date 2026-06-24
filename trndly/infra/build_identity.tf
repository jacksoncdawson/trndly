# Build identity (plan §6). Image push to Artifact Registry stays CLI/Cloud
# Build; the build SA needs writer rights to push the MLflow image.
#
# Phase 3 tightening (DONE): the writer grant is now REPO-SCOPED
# (`google_artifact_registry_repository_iam_member.cloudbuild_writer` in
# mlflow.tf), so the broad project-level `roles/artifactregistry.writer` grant
# that Phase 0 carried here has been removed. This file now only exports the
# build SA member string (reused by mlflow.tf and outputs.tf).
#
# Uses the committed var.project_number directly (not a data.google_project
# lookup) — same value, but no plan-time cloudresourcemanager round-trip and a
# single source of truth for the project number.
locals {
  cloudbuild_sa_member = "serviceAccount:${var.project_number}@cloudbuild.gserviceaccount.com"
}
