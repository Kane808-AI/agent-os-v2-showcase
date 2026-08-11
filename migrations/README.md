# Legacy Migration

Migration is capability-by-capability, not folder-by-folder.

## Required lifecycle

1. Inventory the existing capability and its owner.
2. Identify code, data, credentials, schedules, and external side effects.
3. Decide: rebuild, temporary adapter, archive, or retire.
4. Specify expected behavior and acceptance tests.
5. Build in v2 without writing to the legacy system.
6. Run a shadow comparison where safe.
7. Verify recovery and rollback.
8. Approve cutover.
9. Disable the old capability.
10. Observe the replacement before deleting anything.

The machine-readable starting system inventory is `legacy-systems.json`.

`model-routing-incidents.json` records the small set of legacy routing lessons
that become Goal 10 regression controls. It preserves source hashes and control
intent without copying legacy model IDs, credentials, OAuth assumptions, or
wrapper configuration into the v2 product catalog.

Schema migration 11 adds the real-model shadow evidence boundary: immutable
one-shot claims, terminal outcomes bound to Goal 10 usage, and offline
evaluation replay. It activates no provider, credential, schedule, or network
call and stores only content hashes and control metadata.

Schema migration 12 adds the Northwind affiliate shadow evidence chain. It stores
read-only offer snapshots, proposal-only content, historical replay events,
attributed measurements, independent verification, and candidate learning. It
adds no publisher, affiliate-link writer, partner-contact adapter, payout
mutation, or spend path.

Schema migration 13 records deterministic capability-pack acceptance and
privacy-safe aggregate performance snapshots. Aggregate reports remain
directional, contain no subject identity, require scoped read-only evidence and
independent QA, and cannot feed Goal 12 event attribution or candidate learning.
The migration activates no department, communication channel, credential, or
external executor.

Schema migration 14 records eight append-only production qualification kinds
and capability-by-capability legacy cutover rehearsals. Qualification requires
separate scoped operations and QA identities; `doctor` recomputes its check
hashes and sequence. Cutover plans accept only read-only, proposal, or shadow
modes, require QA for comparison/recovery and a scoped human for approval, and
cannot enable external side effects or legacy disablement. The migration does
not connect to, modify, stop, rename, or delete either legacy system.

## Knowledge-source migration

`knowledge-source-inventory.json` records each reviewed legacy document, its
SHA-256 digest, sensitivity, volatility, disposition, and reason. It includes
excluded and superseded sources so absence from the v2 catalog is an auditable
decision rather than an accidental omission.

The governed results live in `knowledge/catalog.json`. Imported content begins
as research-only candidate knowledge; source inventory does not grant fact or
procedure status.
