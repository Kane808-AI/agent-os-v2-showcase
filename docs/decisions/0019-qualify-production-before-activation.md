# ADR 0019: Qualify production before activation

**Status:** Accepted

**Date:** 2026-07-31

## Context

Goals 1–13 establish isolated local truth, governed autonomy, real-model shadow
calls, and business capability packs. They do not prove that a hosted database,
secret system, backup, upgrade, or legacy replacement is safe. Goal 8 also
deferred hostile-administrator and production crash-consistency risks.

## Decision

Production readiness is an append-only, independently verified chain of eight
release-scoped qualifications. Packages require PostgreSQL role separation,
hosted secret references, external KMS attestations, authenticated TLS, and
metadata-only telemetry. Recovery requires crash, fuzz, restore, PITR, RPO, and
RTO evidence. Upgrade requires backup, migration, canary, and rollback
rehearsals.

Passing all gates permits only a read-only canary. It does not enable external
side effects. Legacy migration is capability-by-capability, QA-verified,
human-approved, rollback-first, and append-only. The v2 repository contains no
operation that disables or deletes legacy runtime state.

## Consequences

- A reseller can use one business-neutral contract for every tenant.
- Database administration alone cannot satisfy production truth authority;
  external KMS separation is mandatory.
- Readiness can be audited, backed up, restored, and recomputed by `doctor`.
- A process starting successfully is not production acceptance.
- Real hosting evidence is supplied per deployment; repository tests validate
  the gate and do not claim a live tenant exists.
