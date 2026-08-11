# ADR 0015: Govern model routing as a deterministic control plane

**Status:** accepted

**Date:** 2026-07-30

## Context

Agent constitutions need different model capabilities, but embedding providers
or models in those files would couple role identity to volatile infrastructure.
Opaque automatic routing and silent failover also make cost, compatibility, and
failure behavior impossible to attribute. Provider access and budgets differ by
tenant, while a provider or credential incident must not cascade across tenants.

## Decision

Migration 10 introduces a central immutable model catalog with explicit
activation, append-only tenant provider policy revisions, isolated credential
references, immutable routing decisions, append-only usage and health events,
and durable scoped circuit state.

The router deterministically filters by capability, policy, health,
sensitivity, independence, and budget, then orders compatible candidates using
a stable ranking. An unavailable compatible route produces a hold. A fallback
requires a recorded non-success outcome and a new linked decision; adapters may
not silently change models.

Only opaque `vault://` or `env://` references cross the storage boundary.
Provider calls and credential resolution remain outside Goal 10 and will be
implemented behind the Goal 11 adapter boundary.

## Consequences

Every model choice can be replayed against its catalog, request, policy, health,
and price evidence. Catalog and policy evolution does not rewrite prior
decisions. Rate-limit and provider failures are bounded by circuits, while
authentication failures remain tenant isolated. Integer usage telemetry makes
provider budget checks deterministic.

The local SQLite circuit row is mutable because one cooldown probe requires a
serialized claim. Health events remain append-only evidence of each resulting
state. Production PostgreSQL must preserve the same transaction and isolation
semantics.

Goal 11 cannot bypass this service: a real provider adapter consumes a selected
decision, resolves its credential reference outside SQLite, records exactly one
outcome, and requests an explicit fallback when appropriate.

