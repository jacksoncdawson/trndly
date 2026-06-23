# Remote state in the GCS bucket created by ../bootstrap (plan §6). Backend
# blocks cannot interpolate variables, so the bucket name is a literal and MUST
# match bootstrap/terraform.tfvars `state_bucket_name`.
#
# First-time setup: apply ../bootstrap, then run `terraform init` here to
# configure this backend (no `-migrate-state` is needed for a fresh root state;
# use it only if the root was first applied with local state).
terraform {
  backend "gcs" {
    bucket = "ml-ops-491417-tfstate"
    prefix = "trndly/root"
  }
}
