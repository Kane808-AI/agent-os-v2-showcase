# Finance Department Module

This module implements the contracts in
`docs/architecture/finance-and-accounting.md`.

The Goal 13 accepted capability contract is
[`capability-pack.json`](capability-pack.json). It covers read-only cash-position
review, forecast proposals, and simulated scenario analysis while keeping
finance separate from Accounting and globally forbidding money movement.

Later production components may include:

```text
adapters/
  quicken_export/
  quickbooks/
  xero/
controller/
cfo/
reconciliation/
reports/
evals/
```

Accounting reconciliation is defined separately under `departments/accounting/`.
No bank credentials or financial records are stored in Git.
