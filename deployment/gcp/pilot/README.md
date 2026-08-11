# GCP Production Pilot 1

This Terraform root describes a new, isolated Agent OS v2 project. It is not
safe to point at the legacy OpenClaw project, and variable validation rejects
that project ID. Terraform creates no secret versions and contains no
credentials.

## What it defines

- an Artifact Registry repository for digest-pinned images;
- distinct runtime, migration, and backup service accounts and secret
  containers;
- a regional PostgreSQL 16 Cloud SQL instance with PITR, retained backups, and
  deletion protection;
- an HSM-backed asymmetric KMS signing key whose signing grant belongs only to
  the runtime identity;
- an IAM-authenticated Cloud Run status service with no `allUsers` grant; and
- an unscheduled, zero-retry Cloud Run Job for one normalized read-only canary
  import.

The database has a public endpoint only for the Cloud SQL connector. It does
not accept a raw application IP allowlist. The runtime connects through the
mounted `/cloudsql` socket.

## Deliberately external prerequisites

Create the new project and attach billing explicitly. Do not reuse
`openclaw-legacy-000000`. Build the image from
`deployment/container/Dockerfile.pilot`, push it to the new repository, and
record its registry digest. Create the runtime, migration, and backup database
roles through an authenticated administration boundary. Populate pinned Secret
Manager versions out of band; do not pass passwords to Terraform.

The canary-input secret must contain only the exact normalized contract in
`deployment/reference/pilot-canary.example.json`. It is an immutable input
snapshot, not a Pinterest or Amazon credential. Delete or disable old input
versions according to the pilot retention policy after evidence is recorded.

## Local validation only

```bash
terraform init -backend=false
terraform fmt -check -recursive
terraform validate
```

Copy `terraform.tfvars.example` outside the repository and replace the project
and scope placeholders. Keep `deploy_runtime = false` for the first reviewed
plan/apply. That phase creates the protected foundation and empty secret
containers, but no Cloud Run service or job. After database onboarding, secret
version creation, and image push, set `deploy_runtime = true` with the real
digest and numeric versions and produce a second reviewed plan. `terraform
plan` and `terraform apply` remain separate operator actions because they create
billable cloud resources. Running the Cloud Run Job is another separate action;
this module never schedules it.

The KMS resource establishes the Goal 14 authority separation. This bounded
Goal 15 canary does not create Goal 8 completion claims, so it does not pretend
to test a hosted completion-signing adapter.
