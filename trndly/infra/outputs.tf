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
