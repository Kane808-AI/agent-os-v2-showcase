# Goal 8 alpha.11 bounded acceptance

**Decision:** GO to begin Goal 9

**Accepted candidate:** `c7a8bffa18799079865694871224bfae88940612`

**Date:** 2026-07-30

**Release authority:** Product-owner acceptance after independent NO-GO review
and bounded remediation

## Decision basis

The independent review of `2.0.0-alpha.10` identified four release blockers:
completion signatures did not bind work semantics, terminal inserts were not
guarded, the SQLite DDL authority boundary was ambiguous, and database/key
backup publication could select mismatched state.

Commit `c7a8bff` addresses only those named blockers and records the resulting
architecture in ADR 0011. The acceptance run against that exact clean commit
completed all 128 tests successfully. Focused regressions cover:

- semantic work mutation after completion;
- restored-schema objective/work scope movement;
- direct terminal inserts without attestations;
- source-key interleaving during backup;
- runtime schema-control denial; and
- refusal to migrate version-1 terminal attestations.

This bounded acceptance closes the independent Goal 8 remediation gate. It is
not an approval of Goal 9 functionality and is not a production security
certification.

## Deferred production risks

The following are intentionally deferred to Goal 14:

- containment of an operating-system owner or hostile database administrator;
- crash and power-loss consistency between filesystem operations;
- exhaustive state-machine fuzzing;
- production PostgreSQL role separation and audit controls; and
- production backup durability and disaster-recovery qualification.

New Goal 8 review scope requires a concrete regression in the current local
adapter. Speculative production-hardening concerns are recorded for Goal 14
without reopening this gate.
