# Control and Recovery Runbook

This runbook covers the local SQLite development adapter. Production recovery
will use the same invariants with PostgreSQL-native backup and point-in-time
recovery.

Goal 14 production qualification requirements are defined separately in
`docs/operations/production-recovery.md`. A successful local SQLite rehearsal
does not qualify a production PostgreSQL environment.

## Invariants

- Git contains code, contracts, migrations, packs, and approved documentation.
- Runtime databases, credentials, logs, and generated artifacts are never
  committed.
- `schema_migrations` is append-only. Never edit a migration already applied to
  any environment.
- A backup is not accepted until SQLite reports `PRAGMA integrity_check = ok`.
- Restores are performed into a new path and verified before any process is
  pointed at them.
- OpenClaw Legacy and Agent OS v1 are not recovery sources for v2 state.

## Inspect local state

```bash
PYTHONPATH=src python3 -m agent_os.cli doctor
```

Healthy output reports an existing database, `integrity` equal to `ok`, an
attested schema with no missing or altered tables, indexes, or triggers, and a
current schema version equal to the expected version. It also validates
parent/child data and every completion signature against the external
durable-truth key.

`doctor` is strictly read-only. It does not initialize or migrate the inspected
database.

## Migrate local state

```bash
PYTHONPATH=src python3 -m agent_os.cli migrate-state --db <database>
```

Migration is explicit and creates an integrity-checked pre-migration backup
before applying append-only migrations. A non-empty database without a valid
ledger is refused; import its data into newly initialized state instead of
stamping it as canonical.

No other CLI command migrates existing state. `init`, demos, rendering, cycles,
workers, and dashboard serving refuse an older schema and direct the operator
to `migrate-state`.

The migration command holds a SQLite write reservation from validation through
backup and schema commit. A concurrent writer cannot create state that is
migrated but absent from the pre-migration backup. Migration to schema version
5 refuses existing terminal completion truth because the new signing key cannot
retroactively prove how that state was created; re-verify or import that truth
through an explicitly reviewed procedure. Migration to version 6 likewise
refuses version-1 terminal truth because it cannot retroactively prove the
original work semantics and scope.

## Create a backup

```bash
PYTHONPATH=src python3 -m agent_os.cli backup-state
```

The command uses SQLite's online backup API and refuses to overwrite an
existing file. For schema version 5 and later, it captures the validated
external key once, stages the database and adjacent `.truth-key` under private
temporary names, validates that staged pair, publishes the key before the
database path, and validates the published pair before returning. A failed
publication removes the incomplete destination. Database and key backups are a
required pair. Backups are written below the ignored `state/backups/`
directory unless an explicit destination is supplied.

## Restore rehearsal

1. Stop every worker that writes to the affected database.
2. Keep the damaged database unchanged as incident evidence.
3. Copy the selected database and its `.truth-key` companion to matching,
   uniquely named restore paths.
4. Run `doctor --db <restore-path>`.
5. Compare schema version, tenant counts, work state, and recent audit records
   with the incident record.
6. Start one loopback-only canary against the restored path.
7. Change the supervised runtime database path only after the canary passes.
8. Record the operator, source backup, integrity result, timestamps, and reason.

This repository intentionally has no one-command destructive restore. Replacing
an authoritative database requires an explicit operator decision and a verified
new target.

## Recovery checkpoints

- Before every schema migration
- Before enabling a live provider or external executor
- Before a tenant configuration change with broad authority impact
- Before a legacy capability cutover
- Before upgrading the runtime or orchestration adapter

## Git recovery

The protected recovery point is a tested commit, not an untracked working
directory. Commits must exclude secrets and runtime state. A build goal is not
complete until its acceptance tests pass from the committed tree.
