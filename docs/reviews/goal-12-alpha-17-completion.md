# Goal 12 alpha.17 completion

**Decision:** GO to begin Goal 13

**Date:** 2026-07-31

**Release:** `2.0.0-alpha.17`

## Exit-criteria result

Goal 12 is complete. The Northwind affiliate reference loop now provides:

- repeated objective-bound, tenant/business-isolated shadow runs;
- explicit read-only offer snapshots with evidence, freshness, destination,
  terms, disclosure, claims, audience-fit, confidence, and economic controls;
- versioned deterministic scoring, stable selection, and safe holds;
- proposal-only content bound to the selected offer and a successful Goal 11
  same-scope output hash;
- immutable historical replay experiments and hashed read-only observations;
- prior same-subject click evidence for every attributed conversion;
- deterministic measurement after the replay window and evidence freeze;
- independent scoped QA verification with rejected and inconclusive outcomes;
- atomic candidate-only learning that disclaims causality and execution
  authority; and
- append-only storage, dashboard visibility, `doctor` recomputation, schema
  attestation, and verified-backup coverage.

The runtime has no publisher, link writer, messaging/contact client,
ad-purchasing adapter, payout mutation, or external executor. Pack configuration
is inert and tests use only local fake model transports and historical fixtures.
No external provider or business-system call occurs during verification.

## Goal 10 and Goal 11 preservation

Affiliate content accepts evidence from an already successful Goal 11 attempt;
it cannot choose or override its provider/model. Goal 11 still resolves only
the exact same-scope credential binding outside SQLite, locally validates the
strict output, retains content only in process, and records its terminal usage
against the immutable Goal 10 decision.

Goal 12 does not call routing or fallback and does not write model usage,
health, cost, or circuit state. A provider failure remains isolated by Goal 11,
and fallback remains a separate explicit Goal 10 decision that rechecks policy,
compatibility, sensitivity, health, and budget.

## Verification

The complete repository suite passes:

```text
Ran 194 tests
OK
```

Goal 12 contributes 11 focused tests covering a complete verified replay and
candidate learning, weak-offer holds, destination/claim drift, Goal 11 output
binding, click-before-conversion attribution, insufficient samples,
segregation of duties, repeated isolated runs, explicit read-only sources,
forged-measurement detection, side-effect absence, dashboard visibility,
schema integrity, and backup preservation.

## Bounded risk accepted

This result proves a shadow evidence and learning loop, not a live affiliate
campaign or causal lift. Historical attribution can contain source bias,
selection bias, duplicates upstream of the supplied identity, and correlations
that do not generalize. The candidate-memory label and explicit statement keep
those observations from becoming approved knowledge or execution authority.

Goal 13 may reuse the business-neutral lifecycle concepts while adding other
revenue streams and departments. It may not silently activate live publishing,
contact, link, spend, payout, or financial execution. Production integration
and operational hardening remain subject to later goals and explicit review.
