# ADR 0008: Enforce foundation trust at the storage boundary

**Status:** accepted

**Date:** 2026-07-29

## Context

An independent audit after Goal 8 found that several service-layer invariants
could be bypassed through lower-level storage methods. It also found event
concurrency, unledgered schema adoption, actor-insensitive authority,
rejected-plan materialization, and uncertain-attempt recovery gaps.

Passing happy-path tests did not establish that the durable transition itself
was safe.

## Decision

Agent OS will duplicate consequential validation at the deepest atomic storage
transition and add database triggers for tenant/business ownership.

Event intake receives canonical fingerprints and processing leases. Evidence
receipts require registered, versioned issuers and exact-target binding.
Outcome verification, plan materialization, and authority decisions recompute
their prerequisites from durable state. Attempted but unobserved external
writes enter bounded read-only reconciliation. Unledgered non-empty databases
are refused, diagnostics are non-mutating, and migrations require a backup.

## Consequences

Storage methods are intentionally stricter and some previously accepted local
fixtures must declare actor or capability entitlement. Historical unledgered
databases require controlled import rather than automatic adoption. Legacy
Goal 8 evidence without an issuer registration cannot support a new completion
claim.

These costs are accepted because approval and financial controls in Goal 9
must not rely on caller discipline for isolation, authority, or execution
truth.
