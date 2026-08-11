# Finance and Accounting Department

Finance is a first-class reusable department with two separated functions.

## Accounting Controller

Owns:

- transaction ingestion;
- bank and credit-card reconciliation;
- ledger matching;
- categorization proposals;
- duplicate and missing-transaction detection;
- accounts receivable and payable visibility;
- exception queues;
- proposed journal entries;
- month-end close packets; and
- source-backed reconciliation reports.

The accounting platform remains the ledger of record. The controller never
silently estimates missing records.

## CFO / Finance

Owns:

- budgets;
- entity-level P&Ls;
- cash-flow forecasts;
- unit economics;
- vendor and subscription audits;
- pricing analysis;
- experiment budgets;
- capital-allocation recommendations; and
- cost-versus-value monitoring.

## Separation of duties

The department may read authorized financial data and propose ledger changes.
It may not:

- initiate a transfer;
- pay a bill;
- create or modify a vendor payment destination;
- change bank credentials;
- open or close an account;
- file a tax return;
- sign a contract; or
- conceal an unreconciled discrepancy.

At the kernel boundary, these prohibitions are not merely role instructions.
`2.0.0-alpha.13` globally forbids external money movement, bill payment,
payment-destination changes, account opening or closing, tax filing, contract
signature, and direct ledger adjustment. Authority configuration cannot
convert those actions to `auto` or `approve`, and SQLite migration 8 refuses a
directly inserted external attempt.

## Spend envelopes

`2.0.0-alpha.14` adds cumulative spend enforcement. An authorized in-scope
human business owner or finance approver creates an immutable envelope for one
business, action, platform, account, base currency, and non-overlapping time
period. The actor performing spend cannot be the envelope creator.

Before external spend, the kernel serially rechecks current authority,
emergency-stop state, required approval, and remaining envelope capacity. It
then records an append-only commitment in the same transaction as the external
attempt. Direct SQL cannot insert an uncommitted attempt or exceed the
envelope. Schema diagnostics independently attest durable commitments and
totals.

Commitments are not automatically released after failure or uncertainty. This
conservative treatment prevents retry ambiguity from becoming overspend.

Proposed ledger adjustments follow the configured approval envelope and are
independently verified before a period is marked closed.

## Adapter contract

The department consumes a vendor-neutral accounting interface:

- list accounts and reporting periods;
- read balances;
- read transactions;
- read ledger entries;
- read invoices and bills when authorized;
- propose classifications and matches;
- stage adjustments for approval;
- read back approved changes; and
- produce a reconciliation snapshot.

Northwind begins with a Quicken export adapter. QuickBooks, Xero, and CSV adapters
can implement the same interface without changing the department charter.

## Evidence requirements

Every reported figure includes:

- tenant and legal entity;
- ledger or source adapter;
- account;
- reporting period;
- source record identifiers;
- currency;
- freshness;
- reconciliation status; and
- unresolved exceptions.

Stale or incomplete data produces a degraded report, never an invented number.
