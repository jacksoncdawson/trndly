variable "project_id" {
  description = "GCP project that owns the Terraform remote-state bucket."
  type        = string
}

variable "state_bucket_name" {
  description = "Globally-unique name for the remote-state bucket. The root module's backend.tf must reference this exact name."
  type        = string
}

variable "state_bucket_location" {
  description = "Location for the state bucket (multi-region 'US' or a single region)."
  type        = string
  default     = "US"
}
