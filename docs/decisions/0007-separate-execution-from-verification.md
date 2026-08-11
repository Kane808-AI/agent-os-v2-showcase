# ADR 0007: Separate execution from outcome verification

**Status:** accepted
**Date:** 2026-07-28

## Context

The runtime already distinguishes simulated work from rejected and
approval-held work, but executor success could still be mistaken for business
completion by a future adapter. Agent constitutions prohibit self-verification;
the durable runtime did not yet enforce that boundary.

External systems also change between planning, writing, and reporting. Without
explicit precondition freshness and post-write read-back evidence, a plausible
executor summary could overwrite newer state or create a false completion.

## Decision

Agent OS will persist execution attempts, evidence receipts, and verification
decisions as separate records and states.

- An external attempt requires a fresh receipt for the exact target state.
- External read-back or a versioned machine check is required before
  verification.
- The producer and verifier must be different in-scope actors, and the verifier
  must hold an assurance role.
- Verified claims name explicit expected facts and immutable evidence receipts.
- Generic work resolution and executor narration cannot assert `verified`.
- Only a verified terminal record can be rendered as a completion claim.

The schema is introduced through append-only migration 2. No external executor
is activated by this decision.

## Consequences

Execution adapters need additional read-before-write and read-after-write calls,
and some fast operations may remain observed while awaiting QA. In exchange,
status reports can distinguish intent, attempt, observation, and verified
outcome; stale evidence and self-attestation fail closed.
