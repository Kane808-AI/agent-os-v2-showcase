# Approval and risk controls

**Version:** `2.0.0-alpha.14`

**Goal:** 9 — Approval, risk, and financial controls

## Implemented boundary

An authority decision of `approve` creates work in `awaiting_approval` and an
immutable approval request bound to the work's identity and canonical
semantics. The assigned agent is the requester and cannot approve its own
work. A separate, enabled, in-scope human with the `approver`,
`business-owner`, or `finance-approver` role may approve or reject it.

Approval decisions are append-only. Approvals expire after 24 hours by default,
may be revoked before execution, and are rechecked against current work
semantics, current authority policy, and expiry immediately before simulated or
external execution. Changing approval-bound work semantics or scope is refused.

An enabled, in-scope human with the `business-owner` or `emergency-admin` role
may activate or clear a business-scoped emergency stop. Activation releases
current queue leases. While active, the runtime rejects event planning,
objective discovery defers, workers cannot claim work, simulated completion is
refused, and external execution attempts cannot begin.

The read-only local dashboard shows the approval queue and emergency-stop
history. All request, decision, stop, expiry, and execution effects also emit
audit records.

## Storage invariants

Schema migration 7 adds:

- immutable `approval_requests`;
- append-only `approval_events`;
- append-only `emergency_stop_events`;
- tenant, business, actor, role, sequencing, and parent-scope triggers; and
- work guards preventing approval-bound scope, semantic, or identity changes.

Normal service methods use immediate transactions for decisions and stop-state
changes. Claiming and final execution checks consult durable state rather than
trusting a prior in-memory policy result.

## Spend boundary

External actions ending in `.spend` require exactly one active envelope bound
to the tenant, business, action, platform, account, base currency, and time
period. The per-action authority ceiling and cumulative envelope are separate
controls. A spend attempt atomically creates an append-only commitment before
the external adapter may act.

Commitments are conservative: uncertain, failed, or later-disproved attempts
continue consuming the envelope. Releasing budget requires a future,
independently verified adjustment design and is not inferred from executor
narration.

## Deliberate limits

This remains a local, side-effect-free adapter. The database writer is a
trusted control-plane principal and can assert an actor identity; actor
authentication, signed human decisions, and a writable production UI/API are
not claimed here. Hostile database-administrator containment remains Goal 14.

Goal 9 is complete for the local side-effect-free adapter. Production identity
authentication, hostile-administrator containment, and any separately
controlled payment product remain outside this gate.
