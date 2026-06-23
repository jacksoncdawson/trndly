variable "project_id" {
  description = "GCP project ID."
  type        = string
}

variable "project_number" {
  description = "GCP project number (used to construct Google-managed service-account emails)."
  type        = string
}

variable "region" {
  description = "Default region for regional resources (Cloud Run, Cloud SQL, Artifact Registry)."
  type        = string
  default     = "us-central1"
}

variable "zone" {
  description = "Default zone within the region."
  type        = string
  default     = "us-central1-a"
}
