# Bootstrap config — provisions ONLY the remote-state bucket that the root
# module's GCS backend depends on. Kept separate (and on LOCAL state) so
# Terraform never manages the backend it relies on. Applied once.
terraform {
  required_version = ">= 1.5"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
}
