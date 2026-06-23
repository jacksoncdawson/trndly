output "state_bucket_name" {
  description = "Name of the remote-state bucket. Copy this into the root module's backend.tf `bucket` field."
  value       = google_storage_bucket.tfstate.name
}

output "state_bucket_url" {
  description = "gs:// URL of the remote-state bucket."
  value       = google_storage_bucket.tfstate.url
}
