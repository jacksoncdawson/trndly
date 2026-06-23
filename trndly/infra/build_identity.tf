# Build identity (plan §6, Phase 0). Image push to Artifact Registry stays CLI/
# Cloud Build; the build SA needs writer rights to push the MLflow image.
#
# The Artifact Registry repo itself is created in Phase 3, so a repo-scoped
# binding cannot exist yet. We grant project-level artifactregistry.writer to
# the legacy Cloud Build SA here; Phase 3 can tighten this to a repo-scoped
# `google_artifact_registry_repository_iam_member` and drop this binding.
data "google_project" "this" {
  project_id = var.project_id
}

locals {
  cloudbuild_sa_member = "serviceAccount:${data.google_project.this.number}@cloudbuild.gserviceaccount.com"
}

resource "google_project_iam_member" "cloudbuild_artifactregistry_writer" {
  project = var.project_id
  role    = "roles/artifactregistry.writer"
  member  = local.cloudbuild_sa_member

  depends_on = [google_project_service.apis]
}
