# Bounded Intelligence and Learning

**Goal:** allow planning models to propose useful work without allowing model
output to become authority, verified truth, or an external side effect.

## Pipeline

```text
measurable objective + tenant-scoped evidence
  -> assigned agent capability
  -> structured planner contract
  -> deterministic evaluation gate
  -> accepted plan or durable rejection
  -> authorized work items
  -> simulated leased execution
  -> candidate memory
```

The planner is replaceable. The evaluation and authority boundaries are not.

## Capability boundary

A capability defines:

- the role an agent must hold;
- the action types the capability may propose; and
- a stable identifier that can be assigned per tenant and business.

Holding a role alone does not grant a capability. The actor must be explicitly
assigned that capability inside the same tenant and business boundary.

## Evidence boundary

Every plan cites durable evidence records. Evaluation rejects a plan if:

- evidence is missing;
- evidence belongs to another tenant or business;
- confidence is below the configured floor;
- the hypothesis is incomplete;
- an agent lacks the required capability;
- a step is outside the capability action set; or
- authority forbids any step.

Accepted and rejected plans, reasons, scores, hashes, and evaluator versions are
persisted for replay.

For an accepted plan, all derived work items, discovery audits, objective
rescheduling, candidate memory, and the plan's `materialized` status commit in
one transaction. An interruption cannot expose a partially materialized plan.

## Learning boundary

An accepted plan may create a `candidate` memory linked to its evidence. This is
not verified knowledge. The runtime insertion path refuses `verified` status, so
an agent cannot promote its own hypothesis.

Future promotion requires separately measured outcomes, minimum sample size,
repeatability, and an evaluator or human gate. Memory never changes permissions,
authority rules, evaluation criteria, or safety policy.

## Current provider boundary

The current planner is deterministic and loads a structured playbook. It proves
the exact interface a model provider must satisfy and gives the evaluation suite
a stable replay baseline. No network model call is enabled in this milestone.

## Reference pilot

The first executable playbook lives in the Northwind reference pack and plans a
qualified-lead funnel diagnosis plus one acquisition experiment. The kernel
contains no Northwind-specific logic.
