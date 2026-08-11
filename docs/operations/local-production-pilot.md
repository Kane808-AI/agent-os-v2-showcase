# Local Production Pilot 1 runbook

This is the selected zero-cost execution path. Despite the historical pilot
name, it is a local read-only canary and **not** a production qualification.

## Safety boundary

- PostgreSQL and the worker share an internal Docker network with no published
  host port and no outbound route.
- Only a manually supplied, exact normalized JSON aggregate is accepted.
- The worker has no Pinterest, Amazon, Etsy, browser, publishing, messaging,
  advertising, spending, or scheduling client.
- Runtime, migration, and backup PostgreSQL logins are different.
- Private runtime files are stored under ignored `data/local-pilot` with modes
  `0700` for directories and `0600` for credentials and backups.
- A canary is refused below 50 GiB host free space or above a 1 GiB database.
- PostgreSQL is limited to one CPU and 512 MiB RAM; logs retain at most three
  10 MiB files.

## Start and verify

From the repository root:

```bash
scripts/local_pilot.sh up
scripts/local_pilot.sh status
```

`up` builds the pinned image, creates private credentials once, starts the
pinned PostgreSQL image with a persistent named volume, creates the separated
roles, applies the attested schema, binds the business-neutral local runtime
login, and prints metadata-only status. Repeating `up` does not silently rotate
secrets.

## Run the safe smoke canary once

The tracked smoke file contains zero values and proves the complete importer
without claiming real Pinterest/Amazon observations:

```bash
scripts/local_pilot.sh canary \
  deployment/local/pilot/normalized-smoke-canary.json
```

Its expected decision is `inconclusive`, because zero outbound clicks cannot
support a performance conclusion. The immutable source reference makes an
accidental second import fail rather than duplicate evidence.

## Run a real normalized-export canary

Copy `deployment/reference/pilot-canary.example.json` outside tracked source,
use the exact local IDs from the smoke file, give the export a unique immutable
`source_ref`, and enter aggregate counts only. Do not include names, account
tokens, URLs carrying credentials, instructions, images, or row-level customer
data. Review the file before running the same `canary` command.

The command is manual, one-shot, and has no automatic retry. Accept `verified`
or the safe `inconclusive` result. Any scope, schema, arithmetic, freshness,
database, or verifier error is a stop.

## Backup and stop

```bash
scripts/local_pilot.sh backup
scripts/local_pilot.sh down
```

`backup` creates a compressed logical dump through the read-only backup login,
verifies that the archive is readable, and retains the seven newest dumps.
`down` stops the database but preserves both its Docker volume and backups.
There is deliberately no reset or delete command.

These same-device backups protect against an application mistake, not loss of
the Mac. Before production qualification, copy verified backups off-device and
perform a separate restore rehearsal.

## Restore rehearsal checklist

Use a separately named temporary PostgreSQL container with the pinned database
image, no published host port, the internal pilot network, resource caps, and a
memory-only data directory. Mount one verified dump read-only, restore with
`--no-owner`, `--no-acl`, and `--exit-on-error`, then compare the restored and
live metadata before deleting the temporary container:

- latest migration version and schema checksum;
- tenant, business, runtime-binding, actor, snapshot, verification, and evidence
  counts;
- zero qualification and cutover records;
- forced-RLS coverage on all nine scoped tables; and
- the independently verified real-canary snapshot ID and hash.

Do not reuse the live container name, volume, or host port. A rehearsal passes
only when every comparison matches and the original database and backup remain
unchanged. The 2026-07-31 evidence record is
`docs/reviews/local-pilot-restore-rehearsal-2026-07-31.md`.

## Off-device copy check

Copy only a verified `0600` dump to an explicitly approved private destination,
never overwrite an existing file, and compare both SHA-256 and bytes after the
copy. For a sync folder, also confirm the provider reports no pending upload.
The backup contains database evidence but no local DSN, role password, browser
credential, or normalized-source file. Keep those local secrets excluded.
