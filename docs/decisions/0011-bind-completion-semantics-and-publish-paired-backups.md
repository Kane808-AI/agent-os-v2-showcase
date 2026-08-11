# ADR 0011: Bind completion semantics and publish paired backups

**Status:** accepted

**Date:** 2026-07-30

## Context

Independent re-review of `2.0.0-alpha.10` found four remaining gaps. A valid
completion signature did not cover the work semantics or objective scope that
the completion claimed. Terminal-state triggers guarded status updates but not
terminal inserts. SQLite schema attestation proved the current schema shape,
but could not prove that an owner-level DDL process had never temporarily
removed and restored a guard. Database backup and truth-key copy were also
separate operations that could select different points in key history.

## Decision

Schema migration 6 introduces completion-attestation payload version 2.
Signatures now cover immutable work semantics, work scope, and the objective
scope in addition to the attempt, verification decision, and evidence hashes.
Databases containing version-1 terminal truth are not upgraded automatically;
that truth requires independent re-verification.

Terminal `INSERT` and terminal status transitions both require a matching
version-2 attestation. Normal SQLiteStore connections install a SQLite
authorizer that denies schema-changing operations. Only new-state
initialization and the explicit backup-first migration path receive
schema-control connections.

The SQLite adapter's enforceable untrusted-writer boundary is direct DML
against the canonical schema. An operating-system owner or other process that
can change application code, replace the external key, modify the database
file directly, or open an independent DDL-capable connection is a trusted
control-plane principal and is outside this adapter's containment boundary.
Database-contained triggers cannot prove that such a principal never
temporarily removed them. Deployments must therefore restrict database, key,
and migration authority at the operating-system boundary.

Backups capture the validated source key once, stage the database and key under
private temporary names, validate the staged pair, publish the key before the
database path becomes visible, and validate the published pair before
returning. A failed publication removes the incomplete destination.

## Consequences

Changing a completed work action, financial attributes, actor, target account,
or objective/work scope invalidates both completion claims and diagnostics,
even if the canonical schema is restored afterward. Direct insertion of
terminal attempts or work without an attestation is rejected.

Migration 6 is append-only and does not rewrite migration 5. Version-1
completion truth cannot silently acquire the stronger meaning of a version-2
signature. SQLite remains a local adapter; stronger containment of a hostile
database administrator requires an external control plane or a production
database with separately administered roles and audit facilities.
