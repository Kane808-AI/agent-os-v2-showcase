# ADR 0010: Authenticate completion truth and serialize critical writes

**Status:** accepted

**Date:** 2026-07-29

## Context

Append-only rows prevent revision but do not prove that a storage writer used
the governed transition to create them. Child-side scope checks do not prevent a
referenced parent from moving afterward. SQLite deferred transactions also
allow authority or source state to change after validation but before the first
write, and a backup can race with the migration it is intended to protect.

## Decision

Each completion receives an HMAC-SHA-256 attestation over the verification,
attempt identity, and exact evidence hashes. The 256-bit signing key remains
outside SQLite in an owner-only file that is copied as a required backup
companion. Terminal attempt and work entry requires an attestation, and
completion reads independently verify its signature. Migration refuses to
retroactively sign pre-existing terminal truth.

Parent-side triggers reject scope changes or deletion that would orphan a
scoped child. Current-state diagnostics also re-run relationship and signature
integrity checks.

Plan materialization acquires an immediate SQLite write transaction before
reading its prerequisites. Explicit migration holds the same write reservation
across schema validation, backup, and every pending migration.

## Consequences

A database-only writer cannot manufacture a completion claim with public data.
Losing the external key makes completion truth unverifiable and is reported as
unhealthy; the key and database must therefore be backed up and restored
together. Concurrent authority changes serialize before or after
materialization, and concurrent writes cannot enter the backup/migration gap.
