# Production and resale hardening

Goal 14 makes production readiness an evidence-backed, fail-closed decision. It
does not treat a configuration file, successful process start, or model claim
as proof that a tenant is safe to operate.

## Qualification is not activation

The production qualification service records eight independent gates for one
tenant, business, and immutable release:

1. packaging;
2. onboarding;
3. persistence;
4. security;
5. observability;
6. recovery;
7. cost; and
8. upgrade.

Each gate binds an artifact hash, boolean checks, a scoped operations producer,
and a different scoped QA verifier. A failed check produces `held`. Readiness
requires a passed record for all eight gates at the same release. Even then the
only eligible mode is `read_only_canary`; the result always reports
`external_side_effects_enabled: false`.

Qualification evidence is append-only. Migration 14 rejects self-verification,
cross-tenant actors, contradictory decisions, mutable cutover plans, and any
attempt to enable external side effects or legacy disablement. `doctor`
recomputes check hashes and replays the cutover sequence.

## Isolated tenant package

`TenantDeploymentManifest` requires one tenant and business, an immutable
release and image digest, PostgreSQL, three distinct runtime/migration/backup
database role references, a hosted secret manager, external KMS attestations,
TLS, OIDC or SAML, and metadata-only telemetry. Values use opaque
`secretref://` identifiers; plaintext connection strings, tokens, passwords,
and private keys are rejected.

`TenantPackageBuilder` stages private files, sets directory mode `0700` and
file mode `0600`, and atomically publishes only after all files are complete.
A failure removes the staging directory and leaves no partial target. The
package contains references, not resolved credentials, and cannot deploy or
activate itself.

The versioned contract is in `deployment/tenant-package.schema.json`; the
common gate is `deployment/production-qualification-policy.json`.

## Production persistence and administrator containment

SQLite remains the development adapter. A production qualification requires a
PostgreSQL environment that demonstrates:

- row-level tenant isolation;
- distinct runtime, migration, and backup roles;
- crash-consistent transactions;
- append-only truth controls and audit evidence;
- point-in-time recovery; and
- an external KMS attestation key outside database and host-administrator
  custody.

The KMS requirement addresses the deferred hostile database/host administrator
risk: control of the database cannot also grant authority to forge authenticated
truth. Production credentials are resolved by the deployment environment and
are never persisted in the Agent OS database or package.

## Observability and support

The operational profile has one exact metadata-only metric set: availability,
queue depth, oldest work age, error rate, model cost, backup age, schema
version, and emergency-stop state. Prompt, context, output, business payload,
and secret content are prohibited from logs and traces. Alert destinations are
opaque references.

The integer cost model combines fixed, storage, operation, and existing model
usage costs, applies an explicit markup, and holds qualification when the
tenant's monthly limit would be exceeded. It does not create a spend envelope
or authorize spending.

## Resilience and upgrades

Recovery evidence must name PostgreSQL row-level isolation and external KMS,
cover at least eight crash points and 128 state-machine fuzz cases with zero
failures, validate a backup hash, pass restore integrity and point-in-time
recovery, and meet declared RPO/RTO ceilings.

An upgrade qualifies only against the current schema version with an immutable
release artifact, pre-upgrade backup, migration rehearsal, canary, and rollback
rehearsal. Upgrades remain operator-controlled; no command silently migrates an
existing database.

## Legacy cutover

Every capability receives its own plan against either Agent OS v1 Prototype or
OpenClaw Legacy. The only modes are `read_only`, `proposal`, and `shadow`.
Stages are append-only and ordered:

```text
inventoried -> shadow_compared -> recovery_verified -> approved
           -> canary_observed -> rolled_back (when needed)
```

Comparison and recovery require QA. Approval requires a scoped human business
owner or operations actor. Migration 14 permanently fixes
`legacy_disable_allowed` and `external_side_effects_enabled` to false. This
repository therefore qualifies and observes a replacement before a separate,
future operator action can disable legacy state; it contains no disable or
delete operation.

## Boundary

Goal 14 supplies the reusable package, gates, evidence model, and runbooks. It
does not choose a hosting vendor, install a connector, resolve a credential,
deploy an environment, disable a legacy process, publish content, contact a
person, spend, or move money. Each real tenant must supply genuine environment
evidence to pass the same gates; test fixtures demonstrate enforcement rather
than claiming a live production deployment.
