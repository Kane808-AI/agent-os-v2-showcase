# ADR 0014: Commit cumulative budget before external spend

**Status:** accepted

**Date:** 2026-07-30

## Context

Per-action authority ceilings prevent one oversized action but do not prevent a
sequence of individually allowed actions from exceeding a period budget.
Checking a total in memory before execution would also permit concurrent
workers to overcommit the same budget.

## Decision

Migration 9 adds immutable spend envelopes and append-only spend commitments.
An envelope is bound to one tenant, business, spend action, platform, account,
base currency, and non-overlapping period. Only an authorized in-scope human
business owner or finance approver can create it, and that principal cannot be
the assigned spending actor.

Immediately before an external spend attempt, the store obtains a serialized
write transaction and rechecks current authority, emergency-stop state,
approval when required, and cumulative committed amount. The commitment and
attempt are committed together. Database triggers reject mismatched,
unbudgeted, or over-budget direct writes, and data attestation detects invalid
totals even if a trigger was temporarily removed and restored.

## Consequences

Concurrent workers cannot legitimately exceed one envelope. Per-action limits
and cumulative limits remain independent. The dashboard exposes envelope,
committed, and remaining minor-unit amounts.

A commitment is conservative and append-only. Failure, uncertainty, or
disproof does not automatically return capacity; introducing releases requires
independent evidence and a new decision record. This favors false budget
exhaustion over overspend.
