# Production Pilot 1 architecture

Goal 15 turns the reusable Goal 14 qualification contracts into a deployable,
still-nonexecuting pilot foundation. The allowed data path is deliberately
narrow:

```text
manually exported Pinterest aggregate + Amazon aggregate
  -> exact normalized JSON contract
  -> one-shot local container (current) or Cloud Run Job (deferred)
  -> role-bound PostgreSQL RLS scope
  -> independent aggregate verification
  -> metadata-only local status (current) or authenticated service (deferred)
```

There is no reverse path to Pinterest or Amazon. The image contains no account
connector, browser automation, publisher, affiliate-link editor, advertising
buyer, messenger, financial executor, or scheduler. The worker can write only
append-only evidence, accepted capability-pack results, aggregate verification,
qualification, and nonexecuting cutover records in the pilot database.

## Persistence and scope

The PostgreSQL adapter intentionally implements only the Goal 13 aggregate and
Goal 14 qualification/cutover surface. Unsupported runtime operations are
absent. Every runtime login is bound by an administrator-owned table to exactly
one tenant and business. SECURITY DEFINER scope functions derive the RLS scope
from `session_user`; the application cannot claim a different tenant through a
session variable. RLS is both enabled and forced, while foreign keys, triggers,
append-only rules, deterministic hashes, and independent actor checks remain
database-enforced.

Schema application, identity onboarding, and runtime-role binding require an
administrative DSN and are not exposed by either container entrypoint. The
runtime receives one pinned Secret Manager version as a file and never returns
or logs the DSN.

## Hosted boundary

The status service trusts only Cloud Run IAM, returns metadata, disables all
HTTP mutation methods, and caps scaling. The canary job is manual, unscheduled,
and has zero retries. Runtime, migration, and backup cloud and database duties
stay distinct. Cloud SQL provides regional availability, retained backups,
PITR, and deletion protection. Terraform never stores a secret value.

Goal 10 model routing and Goal 11 real-model shadow execution are unchanged.
This data-only canary invokes neither. If later pilot work invokes a model, it
must consume the existing exact route, credential binding, explicit fallback,
and usage telemetry contracts rather than adding another provider path.

## Selected local-first boundary

Decision 0021 makes the first real canary local because the reviewed regional
Cloud SQL profile would cost about USD 110 per month. The local PostgreSQL
container has no published host port and joins an internal Docker network. The
one-shot worker joins that network only for an explicit command. Its normalized
input is mounted read-only, credentials are ignored private files with mode
`0600`, logs are capped, and the database is held when it exceeds 1 GiB or the
Mac has less than 50 GiB free. Seven verified logical backups are retained.

This profile proves the PostgreSQL login/RLS, append-only, atomic import, and
independent-verification path. It does not claim hosted secret management,
external KMS, TLS/OIDC edge authentication, a cloud SLA, or full Goal 14
production qualification. Those gates remain held until a later paid-hosting
decision.
