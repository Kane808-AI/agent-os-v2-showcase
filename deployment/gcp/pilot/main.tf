locals {
  services = toset([
    "artifactregistry.googleapis.com",
    "cloudkms.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "sqladmin.googleapis.com",
  ])
  runtime_inputs_ready = (
    var.runtime_image != "" &&
    var.runtime_dsn_secret_version != "" &&
    var.canary_input_secret_version != ""
  )
}

resource "google_project_service" "pilot" {
  for_each           = local.services
  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

resource "google_artifact_registry_repository" "pilot" {
  location        = var.region
  repository_id   = "agent-os-v2-pilot"
  format          = "DOCKER"
  description     = "Immutable Agent OS v2 pilot images"
  deletion_policy = "PREVENT"

  docker_config {
    immutable_tags = true
  }

  depends_on = [google_project_service.pilot]
}

resource "google_service_account" "runtime" {
  account_id   = "aos-pilot-runtime"
  display_name = "Agent OS v2 pilot runtime"
}

resource "google_service_account" "migration" {
  account_id   = "aos-pilot-migration"
  display_name = "Agent OS v2 pilot migration"
}

resource "google_service_account" "backup" {
  account_id   = "aos-pilot-backup"
  display_name = "Agent OS v2 pilot backup"
}

resource "google_project_iam_member" "runtime_roles" {
  for_each = toset([
    "roles/cloudsql.client",
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
  ])
  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_project_iam_member" "migration_sql" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.migration.email}"
}

resource "google_project_iam_member" "backup_roles" {
  for_each = toset([
    "roles/cloudsql.client",
    "roles/cloudsql.viewer",
  ])
  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.backup.email}"
}

resource "google_secret_manager_secret" "database_roles" {
  for_each            = toset(["runtime", "migration", "backup"])
  secret_id           = "aos-pilot-postgres-${each.key}-dsn"
  deletion_protection = true
  replication {
    auto {}
  }
  depends_on = [google_project_service.pilot]
}

resource "google_secret_manager_secret" "canary_input" {
  secret_id           = "aos-pilot-normalized-canary-input"
  deletion_protection = true
  replication {
    auto {}
  }
  depends_on = [google_project_service.pilot]
}

resource "google_secret_manager_secret_iam_member" "database_access" {
  for_each = {
    runtime   = google_service_account.runtime.email
    migration = google_service_account.migration.email
    backup    = google_service_account.backup.email
  }
  project   = var.project_id
  secret_id = google_secret_manager_secret.database_roles[each.key].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${each.value}"
}

resource "google_secret_manager_secret_iam_member" "canary_input" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.canary_input.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_kms_key_ring" "truth" {
  name       = "agent-os-v2-truth"
  location   = var.region
  depends_on = [google_project_service.pilot]
}

resource "google_kms_crypto_key" "truth" {
  name            = "completion-truth-signing"
  key_ring        = google_kms_key_ring.truth.id
  purpose         = "ASYMMETRIC_SIGN"
  rotation_period = "7776000s"

  version_template {
    algorithm        = "EC_SIGN_P256_SHA256"
    protection_level = "HSM"
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "google_kms_crypto_key_iam_member" "runtime_signer" {
  crypto_key_id = google_kms_crypto_key.truth.id
  role          = "roles/cloudkms.signerVerifier"
  member        = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_sql_database_instance" "pilot" {
  name                = "agent-os-v2-pilot"
  region              = var.region
  database_version    = "POSTGRES_16"
  deletion_protection = true

  settings {
    tier                        = "db-custom-1-3840"
    availability_type           = "REGIONAL"
    disk_type                   = "PD_SSD"
    disk_size                   = 20
    disk_autoresize             = true
    deletion_protection_enabled = true
    connector_enforcement       = "REQUIRED"

    backup_configuration {
      enabled                        = true
      point_in_time_recovery_enabled = true
      transaction_log_retention_days = 7
      start_time                     = "09:00"

      backup_retention_settings {
        retained_backups = 14
        retention_unit   = "COUNT"
      }
    }

    ip_configuration {
      ipv4_enabled = true
      ssl_mode     = "ENCRYPTED_ONLY"
    }

    maintenance_window {
      day          = 7
      hour         = 10
      update_track = "stable"
    }
  }

  depends_on = [google_project_service.pilot]
}

resource "google_sql_database" "pilot" {
  name     = "agent_os_pilot"
  instance = google_sql_database_instance.pilot.name

  lifecycle {
    prevent_destroy = true
  }
}

resource "google_cloud_run_v2_service" "status" {
  count               = var.deploy_runtime ? 1 : 0
  name                = "agent-os-v2-pilot-status"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = true

  template {
    service_account = google_service_account.runtime.email
    timeout         = "30s"

    scaling {
      min_instance_count = 0
      max_instance_count = 2
    }

    containers {
      image = var.runtime_image

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
      }

      env {
        name  = "AOS_AUTH_PROXY"
        value = "cloud-run-iam"
      }
      env {
        name  = "AOS_TENANT_ID"
        value = var.tenant_id
      }
      env {
        name  = "AOS_BUSINESS_ID"
        value = var.business_id
      }
      env {
        name  = "AOS_POSTGRES_DSN_FILE"
        value = "/var/run/secrets/postgres/dsn"
      }

      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }
      volume_mounts {
        name       = "postgres-dsn"
        mount_path = "/var/run/secrets/postgres"
      }

      startup_probe {
        initial_delay_seconds = 2
        timeout_seconds       = 3
        period_seconds        = 5
        failure_threshold     = 12
        http_get {
          path = "/readyz"
          port = 8080
        }
      }
    }

    volumes {
      name = "cloudsql"
      cloud_sql_instance {
        instances = [google_sql_database_instance.pilot.connection_name]
      }
    }
    volumes {
      name = "postgres-dsn"
      secret {
        secret = google_secret_manager_secret.database_roles["runtime"].secret_id
        items {
          version = var.runtime_dsn_secret_version
          path    = "dsn"
          mode    = 0444
        }
      }
    }
  }

  depends_on = [
    google_project_service.pilot,
    google_secret_manager_secret_iam_member.database_access,
  ]

  lifecycle {
    precondition {
      condition     = local.runtime_inputs_ready
      error_message = "Runtime deployment requires a digest-pinned image and numeric secret versions."
    }
  }
}

resource "google_cloud_run_v2_service_iam_member" "operator" {
  for_each = var.deploy_runtime ? var.operator_invoker_members : toset([])
  project  = var.project_id
  location = google_cloud_run_v2_service.status[0].location
  name     = google_cloud_run_v2_service.status[0].name
  role     = "roles/run.invoker"
  member   = each.value
}

resource "google_cloud_run_v2_job" "canary" {
  count               = var.deploy_runtime ? 1 : 0
  name                = "agent-os-v2-pilot-canary"
  location            = var.region
  deletion_protection = true

  template {
    template {
      service_account = google_service_account.runtime.email
      timeout         = "300s"
      max_retries     = 0

      containers {
        image   = var.runtime_image
        command = ["python", "-m", "agent_os.pilot_canary"]

        env {
          name  = "AOS_POSTGRES_DSN_FILE"
          value = "/var/run/secrets/postgres/dsn"
        }
        env {
          name  = "AOS_CANARY_INPUT_FILE"
          value = "/var/run/secrets/canary/input.json"
        }

        volume_mounts {
          name       = "cloudsql"
          mount_path = "/cloudsql"
        }
        volume_mounts {
          name       = "postgres-dsn"
          mount_path = "/var/run/secrets/postgres"
        }
        volume_mounts {
          name       = "canary-input"
          mount_path = "/var/run/secrets/canary"
        }
      }

      volumes {
        name = "cloudsql"
        cloud_sql_instance {
          instances = [google_sql_database_instance.pilot.connection_name]
        }
      }
      volumes {
        name = "postgres-dsn"
        secret {
          secret = google_secret_manager_secret.database_roles["runtime"].secret_id
          items {
            version = var.runtime_dsn_secret_version
            path    = "dsn"
            mode    = 0444
          }
        }
      }
      volumes {
        name = "canary-input"
        secret {
          secret = google_secret_manager_secret.canary_input.secret_id
          items {
            version = var.canary_input_secret_version
            path    = "input.json"
            mode    = 0444
          }
        }
      }
    }
  }

  depends_on = [
    google_project_service.pilot,
    google_secret_manager_secret_iam_member.database_access,
    google_secret_manager_secret_iam_member.canary_input,
  ]

  lifecycle {
    precondition {
      condition     = local.runtime_inputs_ready
      error_message = "Runtime deployment requires a digest-pinned image and numeric secret versions."
    }
  }
}
