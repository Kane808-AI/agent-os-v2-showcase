# ADR 0002: Prove the control path with a local runtime slice

**Status:** accepted  
**Date:** 2026-07-28

## Context

Agent OS v2 needs durable execution, but adopting a workflow framework before
proving identity, authority, audit, and idempotency would hide product decisions
inside framework-specific state.

## Decision

The first runtime slice uses:

- the Python standard library;
- SQLite as a replaceable local persistence adapter;
- deterministic Atlas planning;
- simulated terminal execution; and
- a loopback, read-only dashboard.

## Consequences

- The slice can run and test without network access or credentials.
- PostgreSQL and LangGraph are not rejected; they are deferred until their
  interfaces are constrained by proven domain behavior.
- No output from this slice can mutate an external system.
