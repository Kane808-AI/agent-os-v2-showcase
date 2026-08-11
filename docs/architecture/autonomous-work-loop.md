# Autonomous Work Loop

**Goal:** make the durable Agent OS runtime look for bounded work without
requiring an inbound message.

## Lifecycle

```text
active objective becomes due
  -> Atlas verifies its own tenant-scoped identity
  -> Atlas selects an eligible department agent
  -> deterministic discovery proposes one bounded action
  -> authority engine evaluates the proposal
     -> auto/notify: ready queue
     -> approve: approval hold
     -> forbidden: rejected record
  -> worker atomically claims ready work with a lease
  -> agent identity and authority are evaluated again
  -> local executor simulates the action
  -> outcome and audit record commit atomically
```

The second authority check is intentional. Permissions, budgets, accounts, or
envelope expiry may change while work waits in the queue.

## Durable state

An objective records a measurable target, current value, priority, lifecycle
status, review cadence, and next review time. A work item records:

- the source objective and deterministic discovery key;
- the proposed action and assigned agent;
- authority mode and queue status;
- priority, availability, attempt count, and maximum attempts;
- lease owner and expiry; and
- the last execution error.

Discovery insertion and objective rescheduling are one transaction. Work
resolution and its audit record are also one transaction.

## Recovery semantics

- Only one worker can claim a live lease.
- An expired lease may be reclaimed by another worker.
- Reclamation is written to the audit log.
- Failures use exponential backoff.
- Work becomes terminally failed after its maximum attempts.
- If a worker dies during the final attempt, lease expiry moves the item to
  `failed`; it cannot remain claimed forever.
- The continuous worker catches cycle-level faults, reports them, and resumes
  polling instead of terminating.

## Current safety boundary

The only executor is simulated. There is no model call, browser, shell, channel,
bank, ad account, CRM, or other external capability in this loop. A future
executor adapter must preserve leases, authority revalidation, idempotency,
audit, and tenant isolation.

The process is operationally always-on only when a supervisor keeps
`run-worker` alive. Production packaging and supervision are a later deployment
goal; an LLM conversation is never the scheduler.

## Acceptance criteria

1. An active due objective creates work without an inbound event.
2. Duplicate polling does not duplicate the same discovery window.
3. Missing orchestrators or department agents defer safely.
4. Work is prioritized deterministically.
5. Approval and forbidden policies never reach the executor.
6. Policy and assigned-agent identity are rechecked after claim.
7. A live lease has only one owner.
8. Expired leases recover and final-attempt crashes fail terminally.
9. Retries back off and stop at the configured maximum.
10. Objectives, work state, leases, errors, and audits appear in the dashboard.
