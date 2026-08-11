# Runtime Slice 1

**Goal:** prove the Agent OS control path before adding model, channel, or live
business integrations.

## Flow

```text
normalized event
  -> durable receipt
  -> immutable fingerprint + processing lease
  -> tenant/business/inbound-actor validation
  -> Atlas planning boundary
  -> downstream-agent identity validation
  -> deterministic authority decision
  -> simulated execution or approval hold/rejection
  -> atomic workflow state + append-only audit
  -> read-only dashboard
```

## Deliberate limitations

- Events are simulated through the local CLI.
- Atlas uses a deterministic planner so tests do not depend on a model.
- Authorized work is marked `simulated`; no tool or external side effect exists.
- SQLite is the local adapter. PostgreSQL remains the production target.
- The dashboard is read-only and binds to loopback by default.
- No channel, credential, bank, advertising, or legacy-system adapter is loaded.

## Why this comes before LangGraph

The slice proves contracts that must survive any orchestration choice:

- tenancy and identity;
- event idempotency;
- Atlas plan shape;
- authority evaluation;
- workflow lifecycle;
- audit semantics; and
- dashboard projection.

A LangGraph adapter can later own checkpointed multi-step execution without
changing these domain contracts.

## Acceptance tests

1. An authorized simulated event produces exactly one workflow run.
2. Re-delivery is idempotent.
3. An interrupted event receipt can resume without creating a second event.
4. Concurrent exact replays have one processing owner.
5. Mismatched reuse of an event ID or idempotency key is rejected.
6. Event identity and idempotency cannot cross a tenant or business boundary.
7. Both the inbound actor and downstream action actor are authorized.
8. Unknown actions default to forbidden.
9. Approval-required actions enter a hold instead of executing.
10. A workflow disposition and its audit record commit atomically.
11. Audit records explain every terminal disposition.
12. Dashboard output reflects durable database state.
13. Kernel source contains no client, legacy, or machine-specific identifier.
