# ADR 0009: Make durable truth append-only and attest actual schema

**Status:** accepted

**Date:** 2026-07-29

## Context

Storage-transition validation does not protect a completion, event, or
evaluation after its row has been committed. Public hashes can be recomputed by
a storage writer. Independent review also showed that validating a child row's
own tenant/business pair does not prove that its referenced parent has the same
scope, and that a valid-looking ledger does not prove its declared schema
objects exist.

## Decision

The SQLite adapter adds append-only triggers for events, evidence, workflow
runs, audit records, plan evaluations, and outcome verifications. Terminal
attempt and work state is immutable. Structured plans permit only the
`accepted` to `materialized` status transition without content changes.

Every scoped parent reference is checked against the parent's tenant and
business. Plan materialization recomputes plan and evaluation hashes and
re-evaluates current evidence, capability assignment, and authority in the same
transaction.

Schema health compares the ledger's declared version with a canonical manifest
of actual tables, indexes, and triggers. Existing databases are never migrated
by initialization or ordinary runtime commands. The only migration entry point
creates and verifies a backup before applying pending migrations.

## Consequences

Corrections to append-only truth require a new superseding record, not an
update. A database with missing, altered, or unexpected schema objects is
treated as drift even when its ledger checksums are valid. Operators must run
the explicit migration command before starting any runtime against older state.
