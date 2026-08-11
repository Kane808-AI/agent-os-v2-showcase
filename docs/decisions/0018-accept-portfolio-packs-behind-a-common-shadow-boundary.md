# ADR 0018: Accept portfolio packs behind a common shadow boundary

**Status:** accepted

**Date:** 2026-07-31

## Context

Goal 12 proved one affiliate loop, but the active portfolio also needs digital
marketing and consulting, YouTube, applications, physical products, broader
commerce, Finance, Accounting, Sales, Operations, Customer Success, Research,
Engineering, and QA. Implementing each as unrelated code would duplicate
authority decisions and make safety depend on module-specific interpretation.

The first live-data shadow acceptance also showed that Pinterest and affiliate
reporting may expose aggregate totals without person-level click/conversion
identity. Treating those totals as Goal 12 events would fabricate attribution;
discarding them would prevent honest directional evaluation.

## Decision

All Goal 13 modules use one versioned capability-pack policy and deterministic
acceptance evaluator. The policy fixes the required portfolio, permits only
read-only, proposal, and simulated modes, requires read-only inputs and an
independent verifier, and globally forbids production writes, publishing,
contact, spend, link/payout mutation, and money movement.

Aggregate platform reports use a separate append-only, privacy-safe evidence
contract. Independent QA may verify arithmetic, evidence scope, and sample
sufficiency, but every record remains `directional_aggregate`, explicitly
non-causal, and unable to satisfy Goal 12 event attribution or create learning.

The dashboard stays canonical. External communication channels are replaceable
proposal-only descriptors and expose no send operation.

## Consequences

Every active business and revenue stream can reference the same enforceable
module shape, evaluation semantics, observability, and recovery controls.
Changing a pack changes its hash and requires a new acceptance. A missing pack
or weakened common policy fails the catalog rather than silently reducing
coverage.

These are accepted capabilities, not activated production integrations. The
aggregate path supports honest reporting but cannot measure incrementality or
individual attribution. Real connectors, schedules, hosted credentials,
outbound channel adapters, publishers, deployment, and cutover remain Goal 14.
