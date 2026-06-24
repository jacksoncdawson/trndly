output "project_id" {
  description = "GCP project ID."
  value       = var.project_id
}

output "region" {
  description = "Default region for regional resources."
  value       = var.region
}

output "enabled_apis" {
  description = "APIs enabled by this module."
  value       = sort([for s in google_project_service.apis : s.service])
}

output "cloudbuild_sa_member" {
  description = "IAM member for the Cloud Build service account (granted artifactregistry.writer)."
  value       = local.cloudbuild_sa_member
}

# --- Phase 2 — Static serving (Firebase Hosting) ---

output "hosting_site_id" {
  description = "Firebase Hosting site id (brand label; the URL is https://<site_id>.web.app)."
  value       = google_firebase_hosting_site.default.site_id
}

output "hosting_default_url" {
  description = "Default https URL served by the Firebase Hosting site."
  value       = google_firebase_hosting_site.default.default_url
}

output "firebase_web_app_id" {
  description = "Firebase Web App id (for the SPA's optional Firebase SDK config in Phase 5)."
  value       = google_firebase_web_app.default.app_id
}

# --- Phase 3 — Private MLflow (reachability for Phase 4 lifecycle wiring) ---

output "mlflow_service_uri" {
  description = "Private MLflow Cloud Run URL (callers need run.invoker + an ID token; not public)."
  value       = google_cloud_run_v2_service.mlflow.uri
}

output "mlflow_artifacts_bucket" {
  description = "GCS bucket backing MLflow artifacts (proxied via the server)."
  value       = google_storage_bucket.mlflow_artifacts.name
}

output "mlflow_sql_connection_name" {
  description = "Cloud SQL connection name (project:region:instance) for the /cloudsql socket."
  value       = google_sql_database_instance.mlflow.connection_name
}
