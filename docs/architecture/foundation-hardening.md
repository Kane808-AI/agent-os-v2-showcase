# Foundation Trust-Boundary Hardening

**Status:** binding

**Version:** 4

This contract records the independent review remediation completed after Goal
8 and before Goal 9. It strengthens existing goals; it does not activate a live
executor, external adapter, approval workflow, or financial capability.

## Independent review disposition

The first review of commit `c1c105a` moved validation into storage transitions.
A second review of `4524aa0` showed that mutable durable rows, ID-only parent
links, stale plan prerequisites, automatic migration, and ledger-only schema
claims still bypassed those transitions. A third adversarial pass against
`8e7e857` found unsigned terminal entry, parent-side scope movement, and
check/commit races in materialization and migration. A fourth review of
`2.0.0-alpha.10` found that signatures did not bind work semantics, terminal
inserts were not guarded, the SQLite DDL threat boundary was ambiguous, and
database/key backup publication was not paired. `2.0.0-alpha.11` implements
those remediations. Bounded acceptance of commit `c7a8bff` closes the Goal 8
gate and authorizes Goal 9 to begin; production-only residual risks are
deferred to Goal 14.

| Finding | Disposition |
| --- | --- |
| Forged verified completion | Deep verification rechecks every prerequisite; an external HMAC key authenticates each append-only completion attestation and terminal entry requires one |
| Rewritten completed-work semantics | Attestation payload version 2 binds immutable work semantics and objective/work scope; migration refuses to reinterpret version-1 terminal truth |
| Wrong-target or untrusted evidence | Exact target binding and enabled, versioned issuer registration are mandatory |
| Unsafe event replay | Canonical fingerprints and single-owner processing leases reject mismatches and concurrent planning; persisted events are append-only |
| Unledgered schema adoption | Non-empty unledgered state is refused and ledger claims are attested against actual tables, indexes, and triggers |
| Cross-tenant/business storage | Row scope, actor scope, identity lifecycle, and child- plus parent-side scope triggers enforce ownership |
| Rejected or forged plan materialization | Plan and evaluation hashes are recomputed and current evidence, capability, and authority are re-evaluated under one immediate write transaction |
| Actor-insensitive authority | Durable roles/capabilities are evaluated and the most restrictive matching rule wins |
| Orphaned attempted work | Bounded, leased, read-only reconciliation handles uncertain attempts |
| Mutating diagnostics | Every ordinary command refuses old state; only `migrate-state` performs an exclusive backup-first migration |
| Database/key backup mismatch | A single captured key and database are staged, validated, published as a pair, and revalidated before success |
| Knowledge purpose mismatch | Fact and procedure retrieval require compatible kind and lifecycle state |
| Global dashboard exposure | The unauthenticated dashboard rejects non-loopback binding |

## Deepest-enforced invariants

Security and truth invariants are enforced in the storage transaction that
changes durable state. Service-layer validation remains useful for clear error
messages, but cannot be the only control.

- Every scoped row must reference a business owned by the same tenant.
- A verified outcome requires an external, observed attempt; a separate
  in-scope assurance actor; current registered-issuer evidence for the exact
  attempted target; and matching expected facts.
- An accepted plan and its evaluation hashes, authority decisions, capability
  assignments, work items, and candidate memory must match before
  materialization.
- Authority rules without an actor, role, or capability entitlement fail
  closed. All matching rules are evaluated and the most restrictive decision
  wins.

Migration 3 creates database triggers for tenant/business ownership and
actor-linked business scope. Migration 4 adds exact parent/child scope,
append-only truth records, terminal-state immutability, and actual-schema
attestation. Migration 5 adds signed completion attestations, parent-side
lifecycle guards, and terminal-transition constraints. Migration 6 binds work
semantics and scope, covers terminal inserts, restricts runtime DDL, and
strengthens paired backup publication. Application validation still provides
earlier errors.

Public SHA-256 digests remain deterministic identity and corruption checks;
they are not treated as authentication against a storage writer. Completion
truth uses HMAC-SHA-256 with a 256-bit key stored outside SQLite. SQLite
append-only triggers, authenticated transitions, bidirectional relationship
guards, and schema/data attestation provide the local adapter's durable-write
boundary.

## SQLite authority boundary

The local adapter treats direct DML against its canonical schema as an
untrusted storage-writer boundary. Runtime connections deny DDL through the
SQLite authorizer; only initialization and explicit migration receive
schema-control connections. Database and truth-key files are owner-only.

An operating-system owner or independent process able to alter code, replace
the key, edit the database file, or open its own DDL-capable SQLite connection
is a trusted control-plane principal. It is outside the containment claim of
this local adapter because triggers stored in the database cannot prove they
were never temporarily removed. Completed truth remains tamper-evident against
restored-schema semantic or scope changes because its signature covers those
values. Production containment of a hostile database administrator requires a
separately administered persistence control plane.

## Event intake and idempotency

An event fingerprint covers tenant, business, source, actor, kind, occurrence
time, correlation, idempotency key, and canonical payload. The entire persisted
event row is append-only, so a writer cannot rewrite both the payload and its
public digest. A reused event ID or idempotency key must match exactly.

Event processing has one durable lease owner. A concurrent exact replay cannot
enter planning while the first delivery is processing. Completion records the
workflow run, audit, and processing disposition in one transaction. Expired
processing leases may be recovered without creating another event.

## Evidence issuer trust

Calling a payload `external_readback` or `machine_check` does not make it
authoritative. Trusted evidence requires an enabled registration binding:

- tenant and business;
- source system;
- evidence kind;
- capturing actor; and
- versioned adapter or check.

Post-attempt evidence must reference the exact attempted target. Verification
must consume the latest valid receipt from each applicable issuer and rejects
contradictory facts.

## Uncertain external attempts

An attempted write without read-back is uncertain, not complete and not safe to
retry. Reconciliation uses its own lease and bounded attempt count. A
reconciler may only inspect the target using the original target reference and
idempotency key, attach read-back evidence, or defer/fail reconciliation. It
does not re-execute the external write.

## Schema and diagnostics

Initialization refuses a non-empty database without a valid migration ledger.
It never stamps an arbitrary historical layout with the canonical checksum.
It also compares the ledger's declared version with the actual tables, indexes,
and triggers, then validates current relationship and completion-attestation
data. Migration 3 validates tenant/business ownership; migration 4 validates
parent/child identity scope; migration 5 refuses to bless pre-existing terminal
truth without independent re-verification. Migration 6 likewise refuses
version-1 terminal truth that was not bound to its work semantics and scope.

`doctor` is read-only. It reports integrity, migration gaps, unknown versions,
checksum/name drift, and actual-schema drift without initializing or upgrading
state. Initialization and ordinary runtime commands refuse an older existing
schema. Migration is exclusively an explicit `migrate-state` operation that
holds a write reservation across attestation, integrity-checked backup, and
schema changes.

## Local dashboard boundary

The dashboard remains unauthenticated and global only because the local adapter
is forced to loopback. Non-loopback binding is rejected. Authentication and
tenant-scoped projections remain required before any shared deployment.
