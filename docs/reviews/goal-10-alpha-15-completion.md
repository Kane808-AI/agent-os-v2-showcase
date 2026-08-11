# Goal 10 alpha.15 completion

**Decision:** GO to begin Goal 11

**Date:** 2026-07-30

**Release:** `2.0.0-alpha.15`

## Exit-criteria result

Goal 10 is complete. The routing control plane now provides:

- a centrally registered, content-attested, immutable model catalog with
  explicit version activation and no `auto` or `latest` aliases;
- deterministic capability-, policy-, health-, sensitivity-, independence-,
  and budget-aware route selection;
- immutable selected or held decisions with request hashes, exact policy and
  catalog bindings, candidate order, rejection reasons, and estimated cost;
- explicit compatible fallback only after a recorded provider failure, with a
  new decision linked to and excluding the failed route;
- tenant/business-scoped credential references and append-only provider policy
  revisions without stored secret material;
- durable scoped circuit breakers, immediate authentication isolation, and one
  serialized half-open probe after cooldown;
- append-only actual token, outcome, latency, health, and integer cost evidence;
- dashboard views for decisions, usage, provider cost totals, and circuits; and
- a source-hashed legacy incident inventory mapped to executable regressions.

Migration 10 and `doctor` attest the routing schema and durable route evidence.
Backup tests prove accepted backups preserve the catalog, decisions, and usage
records.

## Verification

The complete repository suite passes:

```text
Ran 170 tests
OK
```

Goal 10 contributes 23 focused tests covering deterministic ranking,
constitution translation, silent-downgrade refusal, data and budget policy,
actual monthly cost exhaustion, concurrent budget reservation, explicit
fallback, catalog activation, concurrent idempotency, circuit opening and probe
serialization, authentication isolation, credential-scope and direct-SQL
attacks, immutable cost evidence, temporal validation, schema/data and circuit
attestation, backup preservation, dashboard visibility, and all five legacy
incident mappings.

## Bounded risk accepted

This is a routing control plane, not a live model runtime. It contains no
provider calls, provider SDKs, secret resolver, prompt transmission, or model
output parser. Goal 11 must implement those adapters in shadow mode and must:

1. consume a selected Goal 10 decision without choosing another model;
2. resolve only that decision's credential reference outside SQLite;
3. record exactly one usage outcome before requesting fallback;
4. preserve prompt/context sensitivity controls and structured-output checks;
5. run canaries and evaluation replay without external side effects; and
6. remain fail-closed when provider response state is uncertain.

Production PostgreSQL, hosted secret management, hostile-administrator
hardening, and multi-process operational deployment remain Goal 14 concerns.
They do not reopen this local SQLite gate without a concrete regression.
