# ADR 0004: Evaluate structured model output before creating work

**Status:** accepted  
**Date:** 2026-07-28

## Context

Model-generated plans can be useful but are nondeterministic, may cite weak
evidence, and must not inherit execution authority merely because an agent
produced them.

## Decision

Every intelligent plan must:

- conform to a structured plan contract;
- cite tenant-scoped durable evidence;
- use an explicitly assigned agent capability;
- pass a versioned deterministic evaluator;
- pass authority for every proposed step; and
- persist both the plan and evaluation before creating work.

Accepted hypotheses enter memory only as candidates. Runtime code cannot mark
them verified.

## Consequences

- A future model adapter cannot bypass identity, capability, evaluation, or
  authority.
- Rejections become inspectable training and product data.
- Evaluation replay can detect behavioral drift before provider or prompt
  changes are promoted.
- Live external execution remains a separate, later decision.
