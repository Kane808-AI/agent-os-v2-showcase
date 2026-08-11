# Execution Truth and Outcome Verification

**Status:** binding
**Version:** 1

## Truth states

Agent OS records intent, execution, observation, and verification as different
facts:

```text
proposed/queued
  -> claimed
  -> attempted
  -> externally observed
  -> independently verified | disproved | inconclusive
```

`simulated` remains a separate terminal state for local control-flow tests. It
never implies an external attempt, observation, or completed outcome.

Only `verified` supports a completion claim. A completion claim contains the
work ID, attempt ID, observed result, evidence receipt references, verification
timestamp, verification state, and verifier identity.

## Evidence receipts

An evidence receipt is an immutable, tenant- and business-scoped record with:

- work and optional execution-attempt identity;
- evidence kind, source system, source reference, and capturing actor;
- observation time and explicit validity deadline;
- JSON evidence payload; and
- a canonical SHA-256 content hash.

Operational preconditions, external read-backs, and machine checks also require
a registered issuer that binds source system, evidence kind, actor, scope, and
adapter/check version. A caller-selected evidence label is never sufficient.

Reusing a receipt ID with different content is rejected. Executor narration and
artifacts may help diagnose work, but only an external read-back or a versioned
machine check can support outcome verification.

## Stale-state checks

An external attempt requires a fresh precondition receipt for the exact target.
The receipt must precede the attempt and remain valid when the attempt begins.
This is the adapter-independent optimistic concurrency boundary: a future
adapter must observe the target immediately before acting and use the same
state/version in its conditional write when the external system supports one.

Post-attempt evidence must not predate the attempt. It must remain valid at the
verification time. Expired evidence leaves the work `observed`; it cannot be
promoted to `verified`.

Evidence must name the exact attempted target. Verification uses the latest
valid receipt from each applicable issuer and fails on contradictory facts.

## Independent verification

The verifier:

- is a distinct actor from the producer;
- is enabled within the same tenant and business;
- holds an assurance role (`qa`, `qa-verifier`, or `verifier`);
- evaluates named evidence receipts under a versioned policy; and
- compares explicit expected facts with the externally observed result.

A producing agent cannot verify its own work. A generic executor or queue
resolver cannot write verification truth. Verified and disproved decisions are
terminal and immutable.

## Durability and audit

Goal 8 adds append-only schema migration 2 with separate execution-attempt,
evidence-receipt, and outcome-verification tables. Attempt, observation, and
verification state transitions update the work item and append an audit record
in the same transaction.

The local dashboard presents these records separately so an operator can see
whether work was only attempted, externally observed, or independently
verified.

An attempted write that has no read-back enters bounded, leased reconciliation.
Reconciliation reads by target and idempotency key; it never retries the
uncertain external write.

## Current boundary

This milestone defines and tests the truth boundary but adds no live executor,
credential, browser, CRM, advertising, messaging, or financial adapter.
Existing runtime actions remain simulated. A future adapter must preserve
idempotency, live leases, authority revalidation, target preconditions,
read-back evidence, tenant isolation, and independent verification.

## Acceptance criteria

1. Evidence receipts are immutable, content-addressed, and identity scoped.
2. External attempts require a fresh target precondition.
3. Post-attempt evidence cannot predate the attempt or be stale at verification.
4. Attempted, observed, and verified are separate durable states.
5. Producers cannot verify their own outcomes.
6. Narration and simulation cannot produce a completion claim.
7. Expected facts must match authoritative read-back evidence.
8. Verification transitions and audits commit atomically.
9. The dashboard exposes attempts, receipts, and verification decisions.
