# ADR 0003: Autonomy is a durable work loop, not a persistent model session

**Status:** accepted  
**Date:** 2026-07-28

## Context

Agent OS v2 must discover and pursue work without waiting for a user message.
Treating a chat or model process as "always on" would recreate the interruption
and recovery failures that motivated the clean rebuild.

## Decision

Autonomy is implemented as:

- scheduled reviews of durable, measurable objectives;
- persisted and prioritized work items;
- tenant-scoped orchestrator and assignee identities;
- deterministic authority decisions before enqueue and before execution;
- atomic queue claims with expiring leases;
- bounded retries and terminal failures; and
- supervised polling that can resume from database state.

The first executor and discovery planner remain deterministic and simulated.

## Consequences

- The system can prove self-directed control flow without risking external
  side effects.
- Restarts do not erase objectives, work, attempts, holds, or outcomes.
- Model and integration adapters can be added behind established boundaries.
- Production still needs PostgreSQL, process supervision, observability, and
  deployment hardening before multiple machines execute real work.
