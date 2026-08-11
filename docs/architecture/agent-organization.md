# Agent Organization and Constitution Contract

**Status:** binding  
**Version:** 1

## Organization

```text
Human owner / authorized governors
                 |
               Atlas
                 |
      +----------+-----------+
      |                      |
Business owner(s)      Platform services
      |                      |
Department / specialist agents
      |
Independent QA verifier

Accounting Controller <-> Finance Lead
        separate duties; both report through business governance
```

Atlas owns portfolio orchestration, not specialist execution. A business owner
owns one business's commercial loop. Department and specialist agents accept
bounded work. QA independently verifies consequential claims. Finance validates
economic impact. Platform agents maintain the operating substrate and cannot
invent business priorities.

## Composition

An invocation is assembled from:

1. kernel identity and tenant scope;
2. deterministic authority and budget state;
3. `agents/AGENTS.md`;
4. one versioned role `CONSTITUTION.json`;
5. the matching `SOUL.md`;
6. an approved objective and scoped work envelope;
7. only the knowledge and tools required for that work.

Tenant assignments can change display names and business scopes but cannot
weaken a constitution. A pack can add narrower procedures and evaluations but
cannot grant tools or authority.

## Agent versus service

Use deterministic code for identity, policy, scheduling, queueing, accounting
math, limits, schema validation, idempotency, and audit. Invoke an agent for
judgment under uncertainty: diagnosis, prioritization, research synthesis,
creative development, exception analysis, and recommendations.

No agent is continuously conscious. Always-on services wake ephemeral agents
from durable work.

## Work discovery

Atlas and business owners may discover work against active goals. Other roles
may discover bounded gaps within their assigned capability—for example QA may
discover an unverified claim and Accounting may discover an unreconciled
transaction. Discovery creates a proposed work item; it does not bypass policy,
budget, assignment, or verification.

## Handoffs and accountability

The delegating role remains accountable until the receiving role accepts the
handoff and a verifier records the required outcome. A handoff cannot transfer
tenant scope, authority, or credentials. The runtime must reject missing
identity, objective, evidence, authority, or verification fields.

Agents cannot mark their own consequential output independently verified.
Low-risk deterministic checks can be recorded as machine verification when the
check and expected result are versioned.

## Model routing boundary

Role constitutions declare model capabilities, not provider names. Model
routing uses the role profile, work envelope, data class, tenant provider
policy, current health, evaluated quality, and budget. QA should use an
independent route from the producer when feasible. The constitution adapter in
`src/agent_os/routing.py` translates those requirements into a route request;
it cannot grant a provider or model. The binding routing behavior is defined in
`docs/architecture/model-routing.md`.

## Learning boundary

Every role may record observations and most may propose candidate lessons.
No role can directly modify constitutions, SOUL files, tool grants, authority,
evaluation fixtures, or verified knowledge. Promotion requires evaluation and
the configured human or governance approval.

## Activation

A role definition is inert. Activation requires:

- an actor identity scoped to a tenant and business;
- an enabled role assignment;
- explicit capabilities;
- tool adapters with runtime allowlists;
- an authority envelope;
- objectives and measurable success criteria;
- evaluations passing in shadow mode; and
- a kill switch and accountable escalation target.
