# Agent OS v2 Authoritative Build Plan

**Product:** Agent OS v2 Platform

**Updated:** 2026-08-03

**Authority:** This file controls build sequence. The requirements registry
controls requirement status and verification evidence.

Agent OS v1 Prototype and OpenClaw Legacy remain separate, named systems. No
legacy capability is considered migrated merely because a similar file or
agent exists in v2.

## Status vocabulary

- `complete`: exit criteria are implemented and tested.
- `in-progress`: implementation has started but exit criteria are not all met.
- `planned`: accepted scope that has not started.
- `blocked`: work cannot continue without a named external dependency.
- `deferred`: intentionally removed from the active sequence.

## Build sequence

| Goal | Status | Outcome | Exit criteria |
| --- | --- | --- | --- |
| 1. Clean v2 foundation | complete | Separate, headless, business-agnostic product boundary | v1 and OpenClaw are explicitly named; kernel, departments, packs, and tenant configuration are separated |
| 2. Durable runtime slice | complete | Tenant-scoped event intake and safe simulated execution | Idempotency, policy, audit, atomic outcomes, and dashboard tests pass |
| 3. Autonomous work loop | complete | Objectives can produce durable, restart-safe work without an inbound message | Queue, leases, retry limits, recovery, prioritization, and simulated execution tests pass |
| 4. Bounded intelligence pilot | complete | Evidence-backed plans can be evaluated before work is created | Capability checks, cited evidence, deterministic evaluation replay, and candidate-memory gates pass |
| 5. Control and recovery foundation | complete | The build has an authoritative plan, traceability, schema history, backup, and recovery controls | Initial Git baseline exists; requirement IDs are unique; schema migration and backup tests pass; recovery runbook exists |
| 6. Agent organization and constitutions | complete | Atlas, business owners, specialists, verifier, research, finance, and platform agents have complete role contracts | Every agent has identity, mission, boundaries, tools, inputs, outputs, escalation, metrics, and evaluation fixtures |
| 7. Knowledge governance and migration | complete | Approved legacy knowledge becomes governed, retrievable v2 knowledge | Sources are inventoried and classified; provenance, freshness, conflict, and promotion rules are tested; Etsy, TikTok, GHL/Whitelabel, and relevant business knowledge are migrated selectively |
| 8. Execution truth and outcome verification | complete | Claimed work is distinguished from attempted, externally observed, and verified results | Completion truth is cryptographically attested and append-only; bidirectional parent scope, independent verification, serialized stale-state checks, schema attestation, and no-false-completion tests pass |
| 9. Approval, risk, and financial controls | complete | Consequential activity remains within explicit authority and budgets | Goal 8 bounded acceptance is recorded; approval lifecycle, segregation of duties, spend limits, finance restrictions, and emergency stop tests pass |
| 10. LLM routing and provider abstraction | complete | Models are selected by capability, policy, health, sensitivity, and budget | Central catalog, versioned decisions, compatible fallbacks, circuit breakers, cost telemetry, credential isolation, and routing incident regressions pass |
| 11. Real-model shadow runtime | complete | Ephemeral model calls can propose work without external side effects | Provider adapters, prompt/context controls, structured outputs, evaluation replay, canaries, and failure isolation pass |
| 12. Affiliate-marketing shadow loop | complete | Northwind proves objective-driven autonomous operation on one fast-feedback revenue stream | Affiliate offer research, recommendation, content, attribution, conversion measurement, verification, and learning run in shadow mode without publishing or spend |
| 13. Revenue-stream and department expansion | complete | Reusable modules cover the remaining active portfolio | Digital marketing/consulting including SEO/GEO, YouTube, apps, physical products, broader commerce, finance/accounting, sales, operations, research, engineering, and QA have accepted capability packs |
| 14. Production and resale hardening | complete | A business can qualify an isolated, supportable Agent OS instance before activation | Tenant packaging, onboarding, observability, disaster recovery, security review, cost model, upgrade path, and controlled legacy cutover gates pass |
| 15. Production Pilot 1 foundation | complete | One isolated tenant can run a real read-only aggregate canary locally before any paid hosting decision | Real PostgreSQL role-bound RLS, normalized one-shot import, guarded local container execution, immutable container, optional isolated-project GCP definitions, and failure-isolation tests pass |
| 16. Pilot operations and project control | in-progress | Source backup, testing, security, recovery, and read-only pilot work advance through explicit managed gates | A canonical private GitHub remote matches local HEAD; protected CI runs unit, PostgreSQL, and secret checks; every milestone records owner, evidence, risks, backup state, and next action; four non-overlapping weekly canaries complete before a value decision |
| 17. First live channel and basic operator tasks | in-progress | Atlas handles basic operator requests over one live channel while every outbound action remains proposal-gated | Telegram inbound messages from the verified owner become tenant-scoped intake events; outbound replies exist only as `OutboundChannelProposal` records executed after an explicit human approval decision; a read-only email triage adapter summarizes and categorizes without any send, move, label, or delete capability; adapter capabilities never exceed `inbound.read`, `outbound.propose`, `state.read`; the dashboard remains the canonical control plane; inbound content is treated as data with prompt-injection regression tests; channel identity is distinct from the OpenClaw legacy bot |
| 18. Affiliate operations pilot | planned | The Goal 12 affiliate shadow loop operates on one real business with every external effect behind an explicit gate | The owner can file affiliate work (offer research, content drafting) from the live channel as Goal 9 approval-gated work items; agents produce real research and draft outputs as proposals; each external connection is added one at a time with its own owner approval, local credential, and read-only-before-write progression; publishing or spending exists only as an approved one-shot execution with recorded evidence; live-found defects feed regression tests the same week they are found |

## LLM routing placement

The bounded acceptance of Goal 8 hardening commit `c7a8bff` is recorded in
`docs/reviews/goal-8-alpha-11-bounded-acceptance.md`. Goal 9 may proceed.
Production persistence and hostile-administrator hardening remain Goal 14
work; they do not reopen the local-adapter Goal 8 gate without a concrete
regression.

Goal 9's first bounded slice is `2.0.0-alpha.12`: approval-held work receives
an immutable request, requires a separate authorized human decision, expires
safely, can be rejected or revoked, and is rechecked immediately before
execution. A business-scoped emergency stop blocks event planning, discovery,
claiming, simulated completion, and external execution attempts. Goal 9
remains in progress until spend limits and finance restrictions are enforced
and tested.

`2.0.0-alpha.13` makes money movement and irreversible financial commitments
globally forbidden. An authority envelope cannot override that prohibition,
and migration 8 rejects forged external financial attempts at the storage
boundary. Goal 9 still requires cumulative spend-envelope accounting before it
can close.

`2.0.0-alpha.14` closes Goal 9. Migration 9 adds immutable, period-, platform-,
account-, action-, and currency-scoped spend envelopes plus conservative
append-only commitments. Current authority, emergency-stop state, approval,
and remaining cumulative budget are checked in one serialized transaction
before an external spend attempt can be recorded. The decision is recorded in
`docs/reviews/goal-9-alpha-14-completion.md`; Goal 10 may proceed.

`2.0.0-alpha.15` closes Goal 10. Migration 10 adds the immutable model catalog,
activation events, tenant provider policy revisions and credential references,
versioned route and usage evidence, and scoped circuit state. The deterministic
router holds incompatible work, requires a new linked decision for fallback,
derives integer cost telemetry, and admits one serialized cooldown probe.
Legacy routing lessons are mapped to executable regressions without importing
legacy provider configuration. The decision is recorded in
`docs/reviews/goal-10-alpha-15-completion.md`; Goal 11 may proceed.

`2.0.0-alpha.16` closes Goal 11. Migration 11 adds immutable one-shot shadow
claims, terminal outcome evidence, and versioned offline evaluation replay. The
OpenAI and Anthropic adapters consume exact Goal 10 decisions, resolve only the
bound credential outside SQLite, disable tools, independently validate strict
structured output, and record one existing usage outcome before explicit
fallback is eligible. Public synthetic canaries exercise the same isolated
path. Prompt, context, output, and secret content are not persisted. The
decision is recorded in `docs/reviews/goal-11-alpha-16-completion.md`; Goal 12
may proceed.

`2.0.0-alpha.17` closes Goal 12. Migration 12 adds an objective-bound,
append-only affiliate shadow lifecycle for read-only offer snapshots,
deterministic recommendation, Goal 11-attested content proposals, historical
attribution replay, conversion measurement, independent QA verification, and
candidate-only learning. Content and experiments remain `proposed` and
`shadow`; no durable transition can publish, alter an affiliate link, contact a
partner or audience, modify payout details, or spend. The decision is recorded
in `docs/reviews/goal-12-alpha-17-completion.md`; Goal 13 may proceed.

`2.0.0-alpha.18` closes Goal 13. Migration 13 records deterministic acceptance
of 13 business-neutral capability packs and privacy-safe aggregate performance
evidence with independent verification. Digital marketing/consulting including
SEO/GEO, YouTube, applications, physical products, commerce, finance,
accounting, sales, operations, customer success, research, engineering, and QA
are explicitly mapped across the Northwind portfolio without activation. The
dashboard remains canonical; Slack, Telegram, Discord, Teams, and email expose
replaceable proposal-only contracts with no send operation. Aggregate evidence
is directional and cannot replace Goal 12 event-level attribution or create
learning. The decision is recorded in
`docs/reviews/goal-13-alpha-18-completion.md`; Goal 14 may proceed.

`2.0.0-alpha.19` closes Goal 14 at the reusable platform layer. Migration 14
adds append-only, release-scoped evidence for eight independently verified
production gates and ordered capability cutover rehearsals. Tenant packages
require immutable releases and images, PostgreSQL role separation, hosted
secret references, external KMS attestations, authenticated TLS, and
metadata-only telemetry. Resilience covers crash cases, deterministic
state-machine fuzzing, backup/restore, PITR, RPO/RTO, upgrade canary, and
rollback. Complete qualification permits only a read-only canary; external side
effects and legacy disablement remain structurally false. Tests validate the
gate, not a fictional hosted deployment. The decision is recorded in
`docs/reviews/goal-14-alpha-19-completion.md`.

`2.0.0-alpha.20` closes Goal 15 at the code and local-integration layer. A
bounded PostgreSQL adapter now carries the accepted Goal 13 aggregate and Goal
14 qualification/cutover surfaces behind database-login-bound forced RLS,
append-only records, database-enforced scope, independent verification, and
schema attestation. An exact normalized Pinterest/Amazon export can be imported
once by an unscheduled zero-retry worker and observed through an
IAM-authenticated metadata-only service. The unprivileged image and GCP
Terraform are digest/version pinned and validated for a new isolated project;
the configured legacy project is explicitly rejected. No project, billable
resource, secret version, image push, external account call, or legacy change
was made. The decision is recorded in
`docs/reviews/goal-15-alpha-20-completion.md`. The next step is a separately
approved plan/apply and hosted canary, not an implied activation.

The first post-Goal-15 cost review found that the original regional Cloud SQL
profile would cost about USD 110 per month, almost entirely for an always-on HA
database. That price is not a Goal 15 requirement and is disproportionate to a
one-shot shadow canary. Decision 0021 therefore selects the guarded local-first
profile: persistent PostgreSQL on an internal Docker network, a 1 GiB database
ceiling, a 50 GiB host-free-space stop line, capped logs, and seven rotating
logical backups. The isolated GCP project remains empty with billing disabled.
Paid hosting is deferred until measured value and a separately accepted budget
justify production qualification. The first local normalized Pinterest/Amazon
aggregate canary was completed on 2026-07-31 for the closed 2026-07-01 through
2026-07-30 reporting window. It returned `verified`, created independently
verified append-only evidence, remained read-only, and reported external side
effects disabled. The private normalized source and backups remain ignored local
runtime data. An isolated, no-host-port, memory-only restore rehearsal completed
on 2026-07-31 with the live and restored schema checksum, forced-RLS coverage,
record counts, and real-canary hash matching exactly. The temporary restore
container was removed afterward. The next gate is copying a verified backup off
the Mac to an explicitly approved private destination. That gate completed with
a byte-identical, checksum-verified iCloud Drive copy and a caught-up iCloud sync
status on 2026-07-31. The next step is a small cadence of manual read-only
canaries to measure value; paid hosting and production qualification remain held.

Goal 16 makes ongoing delivery discipline part of the build instead of relying
on the user to request each operational step. `docs/PROJECT_STATUS.md` is the
current control board and `docs/operations/project-management.md` defines the
milestone gate. The first audit found the local Git history healthy but without
a remote. A separate private `Kane808-AI/agent-os-v2` repository now preserves
all v2 branches, remote SHA parity is proven, and the first pinned,
least-privilege unit/PostgreSQL/secret workflow passed. GitHub's free plan does
not enforce branch protection on private repositories. Decision 0022 keeps the
source private and zero-cost, records that limitation, and requires the project
manager's PR-only/no-force operational gate. The remaining Goal 16 learning
gate is four non-overlapping weekly read-only canaries with paired backups.

Goal 17 turns the Goal 13 proposal-only channel contracts into one live,
owner-facing surface. Goal 16's remaining canaries are date-gated rather than
effort-gated, so Goal 17 may run in parallel without touching the canary
track. Scope is deliberately bounded: one Telegram identity owned by v2 and
distinct from the OpenClaw legacy bot, inbound-to-intake wiring, human-approved
proposal execution for outbound replies, and a read-only email triage adapter.
The `proposal-only` execution boundary in `src/agent_os/communications.py` is
load-bearing and must not be widened; any capability beyond `inbound.read`,
`outbound.propose`, and `state.read` fails the goal. Inbound channel content is
untrusted data, never instructions, and the goal cannot close without
prompt-injection regressions proving that boundary.

Goal 18 takes the Goal 12 affiliate shadow loop live on one real business,
selected by the owner at goal start. The rationale is measured, not assumed:
live operation of the Goal 17 channel surfaced three real defects in one
evening that document review had not, so live-found defects are an explicit
exit criterion feeding regression tests. The gate pattern is the one Goal 17
proved: everything is a proposal until an explicit owner decision, every
external connection arrives one at a time with its own approval and local
credential, read precedes write on every integration, and publishing or spend
executes only as an approved one-shot with recorded evidence. Goal 16's
canaries continue on their calendar as the measurement side of the same
affiliate domain; Goal 18 must not touch the canary pipeline or its source
windows.

After the model-shadow foundation in Goals 10–11, Goal 12 starts with affiliate
marketing. Its observable offer, click, attribution, and conversion events
provide a faster learning loop than SEO/GEO ranking changes. The affiliate
pilot remains read-only and proposal-only until its shadow evaluations pass;
it does not publish content, alter links, contact partners, or spend money.

Routing is a platform service, not an Atlas preference and not a model name
inside an agent constitution. Agents declare requirements such as reasoning,
tool use, structured output, modality, sensitivity, latency, and cost ceiling.
The router records why a particular versioned provider/model was selected.

No opaque automatic route, silent downgrade, production `latest` alias, or
unverified fallback is permitted. If no compatible healthy model exists, the
workflow holds safely. Provider credentials and policies are tenant-isolated.

Legacy OpenClaw routing documents, usage records, and incident history are
inputs to tests. Legacy model IDs, OAuth/session assumptions, and wrapper
configuration are not copied as product configuration.

## Change control

1. New scope receives a requirement ID before implementation.
2. A requirement may move to `verified` only with a named evidence artifact.
3. Architectural changes require a decision record.
4. Schema changes are append-only migrations; an applied migration is never
   edited.
5. Live legacy systems remain read-only migration sources until a separately
   approved cutover.
6. Model, prompt, tool, policy, and knowledge changes are versioned and
   independently reversible.
