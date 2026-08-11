# ADR 0013: Forbid external financial execution below configurable policy

**Status:** accepted

**Date:** 2026-07-30

## Context

Finance and accounting role constitutions prohibit money movement, but prose is
not an execution boundary. A mistaken or hostile authority rule could otherwise
label a transfer, payment, ledger adjustment, tax filing, contract signature,
or payment-destination change as allowed.

## Decision

The kernel maintains a non-configurable set of prohibited financial actions.
`AuthorityEnvelope.decide` returns `forbidden` for those actions before
evaluating tenant rules. The external execution-attempt service repeats the
check, and migration 8 rejects direct insertion of an external attempt for the
same actions.

Read-only finance, reconciliation, analysis, recommendations, classifications,
and staged proposals remain eligible for normal capability and authority
evaluation. Simulation does not become evidence that a financial action
occurred.

## Consequences

No tenant envelope or agent role can grant Agent OS authority to move money or
create an irreversible financial commitment. Adding a future payment capability
would require an explicit architectural decision and a separately controlled
execution principal; it cannot emerge from configuration.

This does not implement cumulative budgets. Spend-envelope accounting remains
the final active Goal 9 control slice.
