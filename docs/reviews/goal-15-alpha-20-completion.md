# Goal 15 alpha.20 completion

**Decision:** GO for separately authorized isolated GCP provisioning

**Date:** 2026-07-31

**Release:** `2.0.0-alpha.20`

## Exit-criteria result

Goal 15 is complete at the code and local-integration layer. The repository now
contains a real PostgreSQL adapter for the bounded Goal 13 aggregate and Goal 14
qualification/cutover surface, a role-bound forced-RLS schema, normalized
Pinterest/Amazon read-only import, an authenticated metadata-only status
service, unprivileged immutable container packaging, and validated GCP
Terraform for an isolated project.

The PostgreSQL integration exercised two tenants and distinct runtime logins,
proved cross-tenant denial and inability to claim another scope, enforced
append-only evidence, imported and independently verified the live-shaped
Pinterest/Amazon aggregate, and replayed all eight Goal 14 gates plus an ordered
nonexecuting cutover. A deliberately failed verifier rolled the complete
serializable import back without stranded evidence. The base SQLite suite
remains green.

Terraform was formatted, initialized without a backend, and validated against
Terraform 1.15.8 and the locked Google provider 7.40.0. The pilot container was
built locally from the official Python 3.14.6 image pinned at
`sha256:cea0e6040540fb2b965b6e7fb5ffa00871e632eef63719f0ea54bca189ce14a6`.
No image was pushed and no Terraform plan or apply was executed.

## Preserved controls

The worker has no Pinterest, Amazon, Etsy, browser, publisher, messaging,
advertising, financial, or scheduling client. It consumes only an already
exported exact JSON aggregate. Goal 10 routing, provider policies, credential
isolation, explicit fallback, circuit state, and usage telemetry are unchanged;
the canary makes no model call. Goal 11 remains the only future real-model
shadow path. Goal 14 external side effects and legacy disablement remain false.

Cloud runtime, migration, and backup identities and secrets are distinct.
Terraform creates secret containers but never secret versions or credential
values. The status service has no public invoker grant. The canary job is manual,
unscheduled, zero-retry, and returns metadata only. Cloud resources, billing,
external account access, and legacy changes remain outside this completion.

## Verification

```text
Local suite: 245 tests, OK (5 PostgreSQL integration tests skipped by default)
PostgreSQL suite: 5 tests, OK
Terraform fmt: clean
Terraform validate: Success
Container build: Success
```

## Post-completion operating decision

A later no-apply review measured the regional GCP foundation at approximately
USD 110 per month. Decision 0021 rejects that as the default first-pilot cost.
The selected next step is the guarded local PostgreSQL profile with no cloud
billing. The validated GCP topology remains an optional future production path;
it is not authorized for apply.
