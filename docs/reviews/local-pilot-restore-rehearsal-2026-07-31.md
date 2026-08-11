# Local Pilot 1 restore rehearsal — 2026-07-31

**Decision:** PASS

**Off-device copy:** COMPLETE — ICLOUD DRIVE

## Scope

The post-canary backup
`agent-os-local-pilot-20260731T220236Z.dump` was rehearsed without changing the
live database. Its SHA-256 remained
`dfbdb65bce7a3e329e0a573a86b64fdcd44a831e52d2c28c9c0a67557a764922`.

The restore target was a separately named temporary container using the pinned
PostgreSQL image, the internal pilot network, no published host port, one CPU,
512 MiB RAM, and a memory-only data directory. The backup directory was mounted
read-only. `pg_restore` completed with `--no-owner`, `--no-acl`, and
`--exit-on-error`.

## Exact comparison

The live and restored databases matched on every reviewed field:

| Check | Live | Restored |
| --- | ---: | ---: |
| Schema version | 1 | 1 |
| Schema checksum | `516d05bc1533f465b4a79a7e912e84e1a1a6b9a9ff936659fc5b4247d5ab02b1` | same |
| Tenants | 2 | 2 |
| Businesses | 2 | 2 |
| Runtime bindings | 1 | 1 |
| Actors | 6 | 6 |
| Aggregate snapshots | 3 | 3 |
| Independent verifications | 3 | 3 |
| Evidence records | 6 | 6 |
| Production qualifications | 0 | 0 |
| Legacy cutover plans | 0 | 0 |
| Forced-RLS tables | 9 | 9 |

The real-canary snapshot
`aggregate-5e70ad2e-fc01-4bf9-be34-7ed097488f6f` was present with snapshot hash
`f809533bff5b39f40cdb2045486d59267094f52517503e647ac676351a461e0e`, a
matching independently recomputed hash, and decision `verified`.

The full-database backup also contains one earlier local synthetic Northwind smoke
scope. That row already existed in the live source and was reproduced exactly;
it did not appear in the current local pilot runtime view because the runtime
login remains bound to `tenant-local-pilot` and `business-local-pilot` through
forced RLS.

## Cleanup and boundary

After verification, the temporary container and its memory-only restored data
were deleted. The persistent live container, its named volume, the original
backup, local credentials, and the private normalized report were untouched.
No external account call, cloud resource, publishing, purchase, message, link
change, or money movement occurred.

The connected external drive was visible but inaccessible to the process under
macOS privacy controls. The user explicitly selected iCloud Drive instead. A
new private `Agent OS Backups/Local Pilot` folder was created, and the verified
61,417-byte dump was copied without overwrite as mode `0600`.

The source and iCloud copies were byte-identical and both produced SHA-256
`dfbdb65bce7a3e329e0a573a86b64fdcd44a831e52d2c28c9c0a67557a764922`.
The focused iCloud Drive status reported `client:idle` and `caught-up` after the
copy, with no pending item listed. Local DSNs, role passwords, browser
credentials, and the private normalized-source file were not copied.
