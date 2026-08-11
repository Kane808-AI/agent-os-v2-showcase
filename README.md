# Agent OS v2 Platform

Agent OS v2 is a headless, event-driven operating system for autonomous business
operations. It is designed as a resellable platform: the kernel is
business-agnostic, departments are optional modules, industry behavior lives in
packs, and each client supplies isolated configuration and credentials.

> **About this repository.** This is the public showcase snapshot of a private
> production codebase. The reference tenant has been renamed to the fictional
> "Northwind" agency, and client-specific identifiers, local paths, and cloud
> project details were generalized before publishing. Everything else is the
> real system: 345 passing tests, the architecture decision records, the review
> trail, and the CI pipeline are unchanged. The canonical private repository
> continues active development.

**Status:** Goals 1–15 are complete at `2.0.0-alpha.20`; Goal 16 project control
is in progress. The zero-cost local PostgreSQL pilot completed a verified real
read-only Pinterest/Amazon aggregate canary, an isolated restore rehearsal, and
a checksum-verified iCloud backup. Production qualification, paid GCP hosting,
legacy cutover, and external side effects remain held. Source history is now
backed up both as a verified iCloud Git bundle and in the canonical private
GitHub repository. Pinned remote CI is green; paid private-branch enforcement
remains held under the zero-cost policy, with an explicit PR-only operating
control in its place. The current learning gate is the four-run weekly canary
cadence. See
[`docs/PROJECT_STATUS.md`](docs/PROJECT_STATUS.md) for the live control board.

## System lineage

| Name | Meaning |
| --- | --- |
| Agent OS v2 Platform | This clean product build and future source of truth |
| Agent OS v1 Prototype | Frozen prototype that still powers live jobs |
| OpenClaw Legacy | Legacy knowledge, scripts, and remaining runtime dependencies |

Neither legacy system is renamed, moved, or disabled until a documented cutover
proves its replacement and rollback.

## Product boundaries

```text
platform kernel
  -> departments
    -> industry packs
      -> client configuration
```

- **Kernel:** events, orchestration, policy, state, identity, audit, recovery.
- **Departments:** Finance, Accounting, Sales, Marketing, Operations, Customer
  Success, Product, Research, Engineering, and QA.
- **Industry packs:** reusable procedures and metrics for a type of business.
- **Client configuration:** objectives, accounts, thresholds, branding, and
  integrations for exactly one tenant.
- **Reference implementation:** Northwind proves the system but never defines the
  kernel.

## Foundation principles

1. The dashboard is the control plane. Slack, Telegram, Discord, Teams, and
   email are replaceable channel adapters.
2. Atlas is event-driven. The durable system is always on; an LLM session is
   not.
3. Business owners discover and pursue work against goals and budgets.
4. A deterministic policy engine—not an LLM—decides whether an action is
   automatic, notify-and-proceed, approval-required, or forbidden.
5. Operational state and audit records live in structured storage. Markdown,
   optionally viewed through Obsidian, holds human-readable approved knowledge.
6. Learning is evidence-driven and promoted through evaluation. Agents cannot
   rewrite safety policy, permissions, or their own evaluation criteria.
7. Every tenant and legal entity is isolated by construction.
8. Financial access is read-only by default. Reconciliation and recommendations
   are autonomous; money movement is not.

## Repository map

```text
src/agent_os/              executable kernel contracts
agents/                    shared constitution, role contracts, SOUL files, and evals
departments/               reusable business-department modules
packs/                     industry and reference packs
docs/architecture/         binding architectural contracts
docs/decisions/            architecture decision records
docs/requirements/         master requirements and verification traceability
docs/operations/           operator and recovery runbooks
knowledge/                 governed catalog and scoped reference-pack knowledge
migrations/                legacy inventory and cutover ledger
deployment/                production qualification and tenant-package contracts
tests/                     executable contract tests
```

The authoritative build sequence is
[`docs/BUILD_PLAN.md`](docs/BUILD_PLAN.md). New scope must receive a requirement
ID in [`docs/requirements/registry.json`](docs/requirements/registry.json)
before implementation.

## Verify the foundation

```bash
python3 -m unittest discover -s tests -v
```

## Run the local runtime slice

The first vertical slice is local and side-effect free:

```bash
PYTHONPATH=src python3 -m agent_os.cli demo
PYTHONPATH=src python3 -m agent_os.cli serve-dashboard
```

The demo writes ignored runtime state under `state/`. The dashboard binds to
`127.0.0.1:8765` by default. Authorized actions are marked `simulated`; this
repository contains no external executor.

## Run the autonomous-loop demo

This demo creates a measurable objective, lets Atlas discover and assign one
bounded task, and completes it through the simulated executor without an
inbound event:

```bash
PYTHONPATH=src python3 -m agent_os.cli autonomy-demo
PYTHONPATH=src python3 -m agent_os.cli serve-dashboard
```

For a continuous local headless process:

```bash
PYTHONPATH=src python3 -m agent_os.cli run-worker
```

The worker is restart-safe because objectives, queue state, leases, attempts,
and outcomes are persisted. It still needs a process supervisor for production
uptime.

## Run the bounded-intelligence pilot

The first vertical pilot uses local evidence and the Northwind qualified-lead
playbook to create, evaluate, and execute a structured simulated plan:

```bash
PYTHONPATH=src python3 -m agent_os.cli intelligence-demo
PYTHONPATH=src python3 -m agent_os.cli serve-dashboard
```

The dashboard shows cited evidence, plan evaluation, derived work, and candidate
memory. No model provider or external executor is enabled.

## Inspect and back up local state

```bash
PYTHONPATH=src python3 -m agent_os.cli doctor
PYTHONPATH=src python3 -m agent_os.cli backup-state
```

The local database keeps an append-only schema migration ledger. Schema version
13 includes routing and model-shadow evidence, the affiliate replay chain,
capability-pack acceptance, and directional aggregate performance evidence;
completion attestations still keep their signing key outside SQLite. Back up and restore the generated
`.truth-key` companion with the database. Backups stage, validate, and publish
the database/key pair before reporting success. See
[`docs/operations/recovery.md`](docs/operations/recovery.md).

## Execution truth and verification

Goal 8 separates queued, attempted, externally observed, and independently
verified work. Immutable evidence receipts carry source, observation time,
validity, payload, and a canonical content hash. External attempts require a
fresh target precondition; completion claims require fresh post-attempt
read-back evidence and a separate in-scope QA actor:

```text
claimed -> attempted -> observed -> verified | disproved | inconclusive
```

The milestone defines and tests the adapter boundary but does not activate an
external executor. Existing demos remain explicitly `simulated` and cannot
produce a completion claim. See
[`docs/architecture/execution-truth.md`](docs/architecture/execution-truth.md).

## Independent foundation hardening

The post-Goal-8 review moved trust checks into the durable transitions they
protect. Event intake now has exact fingerprints and single-owner processing
leases. Tenant/business ownership is guarded by storage validation and database
triggers. Evidence requires registered versioned issuers and exact-target
read-back. Plans, evaluations, authority decisions, work, and candidate memory
are revalidated atomically before materialization. Uncertain external attempts
enter bounded read-only reconciliation. A second independent pass adds
append-only truth and event records, exact parent/child scope constraints,
current-authority revalidation, actual-schema attestation, and exclusive
backup-first migration. A third adversarial pass adds externally keyed
completion attestations, parent-side relationship guards, and serialized
materialization and migration boundaries. A fourth pass binds completion
signatures to work semantics and scope, covers terminal inserts, defines the
SQLite DDL authority boundary, and publishes verified database/key backup
pairs.

Diagnostics no longer initialize or migrate state. Use `doctor` for read-only
inspection and `migrate-state` for an explicit backup-first migration. See
[`docs/architecture/foundation-hardening.md`](docs/architecture/foundation-hardening.md).

## Current milestone

Milestone 1 established the clean v2 product and contract boundary. Runtime
Slice 1 now proves:

- durable, tenant-scoped event intake and safe retry recovery;
- validation of both inbound and downstream agent identities;
- deterministic, default-deny authority decisions;
- simulated execution, approval holds, and rejection states;
- atomic workflow and audit persistence; and
- a loopback-only, read-only operational dashboard.

Runtime Slice 2 adds objective-driven discovery, agent assignment, priority
queues, leases, restart recovery, bounded retries, and autonomous-loop
dashboard visibility. Runtime Slice 3 adds capability-scoped, evidence-backed
planning, evaluation replay, and gated candidate learning through the Northwind
pilot. The control-and-recovery milestone adds the authoritative build plan,
requirements traceability, an append-only schema ledger, integrity diagnostics,
verified backups, and the initial Git recovery point. Goal 6 adds 19 reusable,
versioned role constitutions, distinct SOUL files, boundary evaluations, prompt
composition, and an inert Northwind assignment map. Goal 7 adds a governed
knowledge catalog, source-hash inventory, scoped and purpose-bound retrieval,
freshness and conflict controls, and six selectively synthesized Northwind
candidate records covering Etsy, TikTok, GHL/WhiteLabel, and Pinterest
affiliate operations. Goal 8 adds immutable execution evidence receipts, fresh
precondition and read-back checks, distinct attempted/observed/verified states,
independent QA enforcement, and completion claims that fail closed without
verified evidence. Defining an agent, importing candidate knowledge, or
recording an execution contract does not activate it, grant tools, or authorize
a procedure. The independent foundation review additionally closes direct
storage bypasses, replay concurrency, unsafe schema adoption, rejected-plan
materialization, actor-insensitive authority, and uncertain-attempt recovery.
Live channel, banking, advertising, and business-system adapters remain
deliberately absent. Model adapters exist only behind the inert, proposal-only
shadow boundary and are not configured or invoked by repository demos.

## Governed model routing and shadow runtime

Goal 10 provides an offline model-routing control plane. Agent constitutions
remain provider-neutral; the router translates their capabilities plus
work-specific sensitivity, context, independence, and cost limits into an
immutable selection or safe hold. Provider access requires a tenant-scoped
credential reference and policy revision. Failures are recorded before an
explicit fallback can be selected, and provider health is isolated through
durable circuit breakers.

Goal 11 adds a separate one-shot runtime that consumes a selected decision
without choosing another model. It resolves only that decision's credential
binding outside SQLite, composes a sensitivity- and token-bounded versioned
prompt, invokes the exact provider with no tools, validates strict JSON locally,
and records one usage outcome. Prompt, context, output, and secret content are
not retained. A provider failure never invokes fallback; callers must request a
new linked Goal 10 decision after failure evidence exists.

Public synthetic canaries exercise the real adapter path, while versioned
evaluation fixtures replay entirely offline. No provider credential, worker,
or canary schedule is enabled by default, and the test suite injects local fake
transports rather than making provider network calls. Real shadow calls incur
provider usage but cannot execute business actions. See
[`docs/architecture/model-routing.md`](docs/architecture/model-routing.md) and
[`docs/architecture/model-shadow-runtime.md`](docs/architecture/model-shadow-runtime.md).

## Northwind affiliate shadow loop

Goal 12 binds the first revenue-stream proof to an active `affiliate_sales`
objective and an in-scope commerce, marketing, or growth producer. The loop
accepts only explicit read-only offer and analytics sources, freezes research
at recommendation and observations at measurement, and replays only historical
windows. Generated content must exactly match a successful Goal 11 output and
the selected offer's channel, destination, disclosure, and approved claims.

Conversion evidence requires a prior same-subject click. Measurement is
recomputed from immutable observations, then a different scoped QA actor must
verify it before the result can become candidate episodic memory. The learning
explicitly disclaims incrementality and execution authority. Repeating a run
creates a new isolated evidence chain; it does not publish or activate the
proposal. See
[`docs/architecture/affiliate-shadow-loop.md`](docs/architecture/affiliate-shadow-loop.md).

## Portfolio capability packs

Goal 13 accepts reusable packs for digital marketing/consulting with SEO/GEO,
YouTube, applications, physical products, commerce, Finance, Accounting, Sales,
Operations, Customer Success, Research, Engineering, and QA. Every pack uses
read-only evidence, non-executing modes, independent verification, and common
global prohibitions. The Northwind portfolio mapping is inert and activates no
actor, credential, connector, schedule, or side effect.

Aggregate Pinterest, affiliate-network, or similar platform totals use a
separate privacy-safe contract with no subject identity. QA can verify their
arithmetic and source binding, but the evidence stays directional and cannot
become Goal 12 conversion evidence or learning. Slack, Telegram, Discord,
Teams, and email are replaceable proposal-only descriptors; only the dashboard
is canonical and there is no send operation. See
[`docs/architecture/portfolio-capabilities.md`](docs/architecture/portfolio-capabilities.md).

## Production qualification

Goal 14 packages one tenant and business against an immutable release and
image, PostgreSQL with separate runtime/migration/backup roles, hosted secret
references, external KMS attestations, authenticated TLS, and metadata-only
telemetry. Packaging, onboarding, persistence, security, observability,
recovery, cost, and upgrade must all have independent operations/QA evidence at
the same release before the system reports `read_only_canary` eligibility.

Cutover plans advance one capability at a time through inventory, shadow
comparison, recovery verification, scoped human approval, and canary
observation. They are append-only and cannot enable writes or disable the
legacy capability. See
[`docs/architecture/production-hardening.md`](docs/architecture/production-hardening.md)
and [`docs/operations/production-deployment.md`](docs/operations/production-deployment.md).
