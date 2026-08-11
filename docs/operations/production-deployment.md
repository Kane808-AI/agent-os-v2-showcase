# Production deployment runbook

This runbook packages and qualifies one isolated tenant. It does not activate
external side effects.

## 1. Prepare the manifest

Copy `deployment/reference/tenant-package.example.json` outside the repository
and replace identifiers, immutable release/image values, dashboard origin, and
opaque `secretref://` references. Never paste a database URL, password, token,
private key, or resolved secret into the manifest.

The runtime, migration, and backup database roles must be different. The
attestation key must be held by external KMS and administratively separate from
all database roles.

## 2. Build the private package

```bash
PYTHONPATH=src python3 -m agent_os.cli build-tenant-package \
  --manifest /secure/input/tenant-package.json \
  --output /secure/output/tenant-package
```

The output directory must not already exist. Confirm directory mode `0700`,
file mode `0600`, the returned manifest hash, and
`external_side_effects_enabled: false`.

## 3. Provision without activation

Provision a dedicated PostgreSQL database, the three least-privilege roles,
hosted secret references, external KMS signing, authenticated TLS dashboard,
and metadata-only telemetry. Do not add a publisher, sender, advertising buyer,
financial executor, schedule, or legacy-disable permission.

## 4. Record qualification evidence

Operations supplies immutable hashes for the package, persistence test,
threat model, telemetry profile, recovery/fuzz report, cost model, and upgrade
rehearsal. A separately scoped QA actor verifies every gate. Failed checks must
remain `held`; never replace a failed observation with an assertion.

Read the durable decision:

```bash
PYTHONPATH=src python3 -m agent_os.cli production-readiness \
  --db /secure/state/agent-os.db \
  --tenant-id TENANT \
  --business-id BUSINESS \
  --release-version 2.0.0-alpha.19
```

Only `passed` with no missing gates permits a read-only canary. It is not
authority for writes.

## 5. Read-only canary and rollback

Create one capability cutover plan. Preserve the legacy runtime unchanged.
Record inventory, shadow comparison, verified recovery, human approval, and
canary observation in order. Stop and record rollback if scope, freshness,
cost, error rate, recovery, or output validation differs from its accepted
evidence.

## Support handoff

Give operators the manifest hash, release digest, qualification IDs, dashboard
URL, alert route reference, RPO/RTO, backup evidence hash, upgrade and rollback
records, and the still-active legacy owner. Do not include credential values or
prompt/output content.
