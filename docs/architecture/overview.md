# Agent OS v2 Architecture

**Status:** binding foundation contract  
**Version:** 2.0.0-alpha.1  
**Live-system impact:** none

## Purpose

Agent OS v2 operates businesses against explicit commercial objectives. It must
discover useful work, select experiments, execute within bounded authority,
verify outcomes, and learn from evidence without requiring a permanently open
model session.

## Runtime topology

```text
Channels and platform events
           |
           v
Channel Gateway / Event API
           |
           v
Identity + Tenant Boundary
           |
           v
Deterministic Policy Engine
           |
           v
Atlas Portfolio Orchestrator
           |
     +-----+-------------------+
     |                         |
Business Owner            Approval Hold
     |
Specialist workflow
     |
Verifier / QA
     |
Outcome + metrics + experience
     |
State, audit, memory candidates, dashboard
```

## Always-on definition

Always-on components are lightweight services:

- event receivers;
- scheduler;
- durable queue;
- workflow checkpoint store;
- policy engine;
- audit writer;
- health monitor; and
- dashboard API.

Atlas and specialist model invocations are ephemeral. An event or cadence wakes
them with durable context, they perform bounded work, write state, and exit.

## Core services

### Event Gateway

Normalizes Slack, Telegram, Discord, Teams, email, webhooks, schedules, metrics,
and dashboard interactions into one event envelope. Channel integrations contain
no business logic.

### Identity and tenancy

Maps external identities to an Agent OS user, role, tenant, and allowed business
entities. An event without a resolved tenant is rejected before model execution.

### Policy engine

Evaluates action class, tenant, business, actor, risk, budget, recipient,
platform, and authority envelope. Its decision is deterministic and auditable.

### Atlas

Atlas owns portfolio orchestration: sensing, prioritization, dispatch, tracking,
verification, and escalation. Atlas does not replace accountable business
owners or specialist departments.

### Business owners

Each active business has one accountable owner with a commercial objective,
scorecard, budget, authority envelope, experiment queue, and stop conditions.

### Workflow runtime

The domain contracts are runtime-neutral. The first orchestration adapter will
evaluate LangGraph with PostgreSQL checkpointing for durable execution,
interrupts, and recovery. LangGraph-specific objects may not leak into tenant,
event, policy, memory, or finance contracts.

### State and storage

- PostgreSQL: tenants, identities, events, checkpoints, authority, budgets,
  goals, experiments, metrics, structured memory, audit, and finance metadata.
- Version-controlled Markdown: approved strategy, decisions, playbooks, and
  human-readable knowledge.
- Artifact storage: generated media, reports, exports, and evidence referenced
  by immutable identifiers.

## Dashboard and channels

The custom dashboard is canonical. Slack is the default business interaction
adapter. Telegram remains optional for an owner. Discord and Teams are adapters
selected per client. An approval performed through any channel updates the same
core approval record.

## Product isolation

The kernel cannot contain:

- client names;
- personal usernames;
- absolute user paths;
- accounting vendor assumptions;
- platform account identifiers;
- affiliate tags;
- phone numbers;
- industry legal rules; or
- client-specific revenue priorities.

Those belong in packs or client configuration.

## Initial deployment model

The Northwind deployment is a single-tenant reference implementation. Resold
installations are client-owned deployments with client-owned credentials. The
data model remains tenant-namespaced even in a single-tenant installation so
hosted multi-tenant deployment remains possible without redesigning contracts.
