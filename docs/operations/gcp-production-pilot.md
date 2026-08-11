# GCP Production Pilot 1 runbook

**Deferred:** Decision 0021 selects the guarded local-first pilot. Do not use
this runbook unless the owner later accepts a paid-hosting budget explicitly.

This runbook stops before billable provisioning or any external-account test.
Those actions require a separate operator authorization.

## 1. Verify locally

Install the project dependency and run both suites:

```bash
python3 -m unittest discover -s tests -v
scripts/test_postgresql_pilot.sh
```

The integration script uses the pinned PostgreSQL image, a tmpfs database, a
localhost-only port, generated roles and scopes, and stops the container on
exit.

Build the container from its pinned default base:

```bash
docker build -f deployment/container/Dockerfile.pilot \
  -t agent-os-pilot:local .
```

Validate `deployment/gcp/pilot` with Terraform. Do not run `apply` during a
code review.

## 2. Establish the isolated project

Create a new project whose ID contains `agent-os-v2`, attach a pilot billing
budget and alerts, and select one region. Never select the configured legacy
project. Grant the human infrastructure operator only the permissions needed
for an independently reviewed plan/apply.

## 3. Build and record immutable artifacts

Push the locally tested image only to the new Artifact Registry repository.
Use the returned `@sha256:` digest in Terraform. Preserve the source commit,
base-image digest, image digest, Terraform provider lock, and plan file as the
release evidence.

## 4. Provision and onboard without activation

The first plan and apply must keep `deploy_runtime = false`. It creates the
protected foundation and empty secret containers, not the status service or
canary job. Create separate PostgreSQL runtime, migration, and backup logins
without writing their passwords into Terraform state. Through the
migration/admin boundary:

1. call `PostgreSQLPilotStore.apply_schema(admin_dsn)`;
2. call `bootstrap_scope` with the tenant, business, operations producer,
   independent QA verifier, and human owner;
3. call `grant_runtime_role` for the one scoped runtime login; and
4. place each DSN in its matching Secret Manager secret as a numeric version.

The runtime DSN should target the mounted Cloud SQL Unix socket. Record schema
checksum and binding evidence without exposing the DSN.

Push the tested image, create the required numeric secret versions, set
`deploy_runtime = true`, and review a second plan before creating Cloud Run.

## 5. Run the first canary

Prepare a read-only export outside Agent OS and normalize only aggregate counts
into `deployment/reference/pilot-canary.example.json`. Review that it contains
no person-level data, credential, token, destination mutation, or instruction.
Create one pinned canary-input secret version, update the reviewed Terraform
variable, and manually execute the job once.

Accept only a `verified` metadata result. `inconclusive` is a valid safe result
when outbound clicks are zero. Any schema, scope, freshness, arithmetic,
database, or verifier failure stops the pilot. Do not retry automatically.

## 6. Observe and roll back

Use an explicitly authorized identity to view `/readyz` and `/` on the Cloud
Run service. Confirm metadata-only output, Cloud SQL backup/PITR health, cost,
and zero external side effects. Rollback means stop job execution, preserve
evidence, restore/verify the database if required, and leave legacy systems
unchanged. It never means disabling or editing a legacy process.

## Stop conditions

Stop immediately for a wrong project, mutable image or secret alias, public
invoker, cross-scope visibility, unexpected network/account access, plaintext
credential in state/logs, nonzero automatic retry, unverified aggregate, or
any proposal to publish, message, spend, change a link, or move money.
