# Remote-state bucket. Applied ONCE, on local state, before the root module is
# initialized. Kept separate so Terraform never manages the GCS backend it
# depends on. `prevent_destroy` guards against an accidental `terraform destroy`
# wiping the state of every other phase.
#
# Security (plan §5/§6): the bucket holds the DB password + SA details that
# render into state, so it is private, versioned, uniform-access, and has
# public-access-prevention enforced. It is bound only to the Terraform runner's
# own ADC identity (no extra IAM here).

provider "google" {
  project = var.project_id
}

resource "google_storage_bucket" "tfstate" {
  name     = var.state_bucket_name
  project  = var.project_id
  location = var.state_bucket_location

  # Plan §5: private by default.
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  # Never silently delete state.
  force_destroy = false

  versioning {
    enabled = true
  }

  # Keep state history bounded: retain recent versions, prune the deep tail.
  lifecycle_rule {
    condition {
      num_newer_versions = 20
    }
    action {
      type = "Delete"
    }
  }

  lifecycle {
    prevent_destroy = true
  }
}
