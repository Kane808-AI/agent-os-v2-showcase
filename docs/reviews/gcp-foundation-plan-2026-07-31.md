# GCP foundation plan review — 2026-07-31

## Status

**Deferred.** Decision 0021 selects the zero-cost local PostgreSQL pilot. This
plan remains review evidence for a possible future production qualification;
it is not the current execution path and is not authorized for apply.

## Original verdict

Conditionally acceptable for an explicitly authorized foundation apply. The
plan is not authorized for apply by this review. Billing remains detached, no
Terraform resource has been created, and runtime deployment is disabled.

Before an apply, the operator must explicitly authorize linking the Northwind
billing account and must establish a USD 150 monthly pilot budget with alerts
at 50%, 90%, and 100%. A budget alert is not a hard spending cap. The operator
must then regenerate and compare the plan; an old saved plan must not be used
after billing or source changes.

## Isolated project

- Project: `agent-os-v2-pilot-example`
- Project number: `000000000000`
- Display name: `Agent OS v2 Pilot`
- Organization: `example.com` (`000000000000`)
- Lifecycle: `ACTIVE`
- Billing: disabled; no billing account attached
- Legacy project: not selected, modified, or referenced by the plan

Google automatically enabled its normal new-project service set when the
project was created. The Terraform plan would additionally enable only
Artifact Registry, Cloud KMS, Cloud Run, Secret Manager, and Cloud SQL Admin.
No planned API has been applied.

## Reproducible plan inputs

- Terraform: 1.15.8, pinned container digest
  `sha256:7ae513256f7ce67879e218ae8593d6fbe216ec9e123abe6c94e4e10704857963`
- Google provider: 7.40.0 from the committed lock file
- Region: `us-west1`
- Tenant: `tenant-northwind-pilot`
- Business: `business-northwind-pilot`
- `deploy_runtime = false`
- Saved local plan SHA-256:
  `75af7d8b2fb11883cfd5f065d448a622c44756095a597bc27a71098322fc414d`
- Result: **28 to add, 0 to change, 0 to destroy**

The saved plan is temporary review evidence, not an apply authorization. It
contains no credential or secret value. A short-lived operator access token was
passed only to the Terraform process and was not written to the repository.

## Planned foundation

The 28 additions comprise five API enablements, one immutable Artifact Registry
repository, one regional PostgreSQL 16 Cloud SQL instance and database, one HSM
asymmetric signing key and key ring, three service accounts, four empty Secret
Manager containers, and their least-privilege IAM memberships.

The database is a `db-custom-1-3840` regional HA instance with 20 GiB SSD,
automatic storage growth, seven days of point-in-time recovery logs, 14 retained
backups, and both Terraform and provider-level deletion protection. Although it
has a public endpoint for the managed connector, `connector_enforcement =
"REQUIRED"` rejects direct database connections. There is no IP allowlist.

The first apply would create no Cloud Run service, no canary job, no image, no
secret version, and no public invoker. Those require populated out-of-band
secret versions, a digest-pinned image, and a separately reviewed second plan.

## IAM review

| Identity | Project roles | Resource-level access |
| --- | --- | --- |
| Runtime service account | Cloud SQL Client, Logs Writer, Monitoring Metric Writer | Access only to runtime DSN and normalized canary input; sign/verify only on the completion-truth key |
| Migration service account | Cloud SQL Client | Access only to migration DSN |
| Backup service account | Cloud SQL Client, Cloud SQL Viewer | Access only to backup DSN |

The plan grants no Owner, Editor, Service Account Token Creator, Secret Manager
Admin, Cloud SQL Admin, Artifact Registry writer, billing role, or anonymous
principal. Runtime, migration, and backup credentials remain isolated. No
Terraform resource contains a password or a secret version.

## Projected monthly cost

This estimate uses public Google Cloud list prices for Oregon and 730 hours per
month. It excludes taxes, negotiated discounts, unrelated usage on the billing
account, unexpected log or network volume, and future database growth.

| Component | Estimate |
| --- | ---: |
| Regional Cloud SQL: 1 HA vCPU and 3.75 GiB memory | $98.62 |
| 20 GiB HA SSD | $6.80 |
| One active elliptic-curve HSM key version | $2.50 |
| Example 20 GiB of used backups | $1.60 |
| Secret Manager, registry, and APIs at pilot volume | $0.00–$0.50 |
| Cloud Run in this foundation plan | $0.00 |
| **Expected small-data foundation** | **about $110/month** |
| **Review budget envelope** | **$110–$120/month** |

The database is the dominant always-on charge. Its disk can autosize, backup
usage can grow, and an idle public IPv4 address can add up to $7.30 per month.
Each HSM rotation creates another active version; previous versions must be
disabled under the evidence-retention procedure after verification or the HSM
charge grows by about $2.50 per active version per month.

A shared-core database would be cheaper but is not covered by the Cloud SQL SLA
and would weaken the Goal 14 production-qualification posture. This review keeps
the regional HA design.

## Safety and next gates

1. Obtain explicit authorization for the named billing account, the USD 150
   monthly alert budget, and this estimated cost envelope.
2. Link billing, create/verify the budget alerts, regenerate the foundation
   plan, compare it to this review, and only then request apply authorization.
3. Apply the foundation with `deploy_runtime = false`.
4. Create login-bound PostgreSQL roles and RLS scopes, populate numeric secret
   versions out of band, build and push the tested digest-pinned image, and
   review the separate runtime plan.
5. Run one manual, zero-retry canary using a human-reviewed normalized
   Pinterest or Amazon export. Do not connect to either external account or API.

At every stage, publishing, messaging, purchasing, changing links, moving
money, scheduling retries, or contacting Pinterest/Amazon is out of scope and
is a stop condition.

## Pricing references

- <https://cloud.google.com/sql/pricing>
- <https://cloud.google.com/kms/pricing>
- <https://cloud.google.com/secret-manager/pricing>
- <https://cloud.google.com/artifact-registry/pricing>
- <https://cloud.google.com/run/pricing>
