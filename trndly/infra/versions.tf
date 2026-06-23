# Root module. Applied incrementally per phase (plan §6): Phase 0 lands APIs +
# build identity; later phases add Firebase Hosting (2), MLflow on Cloud Run +
# Cloud SQL (3), and the dynamic tier (5) as additional .tf files here.
#
# Provider split (plan §6): `google-beta` is needed ONLY for the Firebase
# project/web-app/hosting-site resources in Phase 2. Cloud Run, Cloud SQL,
# Storage, Secret Manager, Firestore, Identity Platform all live in the stable
# `google` provider — they are NOT beta-pinned.
terraform {
  required_version = ">= 1.5"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = "~> 6.0"
    }
  }
}
