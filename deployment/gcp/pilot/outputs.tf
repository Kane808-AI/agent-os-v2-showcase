output "artifact_repository" {
  value = google_artifact_registry_repository.pilot.name
}

output "cloud_sql_connection_name" {
  value = google_sql_database_instance.pilot.connection_name
}

output "status_service_uri" {
  value = try(google_cloud_run_v2_service.status[0].uri, null)
}

output "canary_job_name" {
  value = try(google_cloud_run_v2_job.canary[0].name, null)
}

output "service_accounts" {
  value = {
    runtime   = google_service_account.runtime.email
    migration = google_service_account.migration.email
    backup    = google_service_account.backup.email
  }
}
