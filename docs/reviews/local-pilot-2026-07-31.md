# Local Pilot 1 execution review — 2026-07-31

**Decision:** GO — first real normalized-export canary completed

**Production qualification:** HELD

## Result

The zero-cost local-first profile is running on the Mac. The pinned PostgreSQL
container is persistent, has no published port, and is connected only to an
internal Docker network. The one-shot worker completed the tracked zero-data
smoke canary and returned `inconclusive`, the correct safe result when outbound
clicks are zero.

The run created one aggregate snapshot, one independent verification, and two
append-only evidence records. It created no qualification or cutover record and
reported `external_side_effects_enabled: false` throughout.

A separately reviewed real aggregate was then read from signed-in Pinterest and
Amazon Associates reports for the same closed reporting window, normalized into
private ignored local data, and imported exactly once. The real canary returned
`verified`. The database now contains two snapshots, two independent
verifications, and four append-only evidence records. It still contains no
qualification or cutover record and still reports external side effects disabled.

## Storage and process evidence

- Host free space after the real canary: 64,997,949,440 bytes (about 60.5 GiB)
- PostgreSQL database size after the run: 8,723,479 bytes (about 8.3 MiB)
- Host stop line: 50 GiB free
- Database ceiling: 1 GiB
- PostgreSQL limit: one CPU and 512 MiB RAM
- Container logs: three files of at most 10 MiB each
- Restart policy: disabled
- Published host ports: none
- Docker network: internal

Private local credentials exist only under ignored `data/local-pilot/secrets`
with directory mode `0700` and file mode `0600`. Their values were not logged,
committed, or included in this review.

## Smoke evidence

- Decision: `inconclusive`
- Evidence class: `directional_aggregate`
- Snapshot ID: `aggregate-c9471777-6a25-458e-94ac-ac10dd10b915`
- Snapshot hash:
  `16131a26058a9dec7c574b8a2faa2f50dc1efd5c26fc90989df88f3e415dd6a6`
- Schema checksum:
  `516d05bc1533f465b4a79a7e912e84e1a1a6b9a9ff936659fc5b4247d5ab02b1`

One compressed logical backup was created through the separate read-only backup
login and verified with `pg_restore --list`. The current-scope dump SHA-256 is
`ca09615b2b85990db35da433739252dbf72d7b49b2b1975b1c806f703addb101`.
The backup itself is private ignored runtime data, not repository content.

## Real aggregate canary evidence

- Reporting window: 2026-07-01 through 2026-07-30
- Decision: `verified`
- Evidence class: `directional_aggregate`
- Snapshot ID: `aggregate-5e70ad2e-fc01-4bf9-be34-7ed097488f6f`
- Snapshot hash:
  `f809533bff5b39f40cdb2045486d59267094f52517503e647ac676351a461e0e`
- External side effects enabled: `false`
- Stored totals: two snapshots, two independent verifications, four evidence
  records, zero qualification records, and zero cutover records

The normalized aggregate file is mode `0600` under ignored local runtime data.
It contains only the exact numeric contract; it contains no credentials, report
URLs, customer rows, personal data, instructions, or content. Its business
figures are intentionally absent from this committed review.

After the real canary, a third compressed logical backup was created through the
separate backup login and verified as readable. Its SHA-256 is
`dfbdb65bce7a3e329e0a573a86b64fdcd44a831e52d2c28c9c0a67557a764922`.
The private dump is 61,417 bytes, mode `0600`, and is not repository content.

## Boundary

This proves local PostgreSQL schema attestation, login-bound forced RLS,
separated duties, atomic normalized import, append-only evidence, independent
verification, storage holding, a readable logical backup, an isolated restore
rehearsal, and one checksum-verified off-device iCloud copy. It does not prove
independent cloud availability, external KMS custody, hosted secret management,
or TLS/OIDC edge authentication. Production therefore remains held.

The GCP project remains a deferred empty shell with billing disabled. Read-only
aggregate report access occurred for this authorized canary. No Terraform apply,
cloud resource, account mutation, publishing, messaging, advertising, purchase,
link change, or money movement occurred.

## Verification

```text
Local/unit suite: 249 tests, OK (5 PostgreSQL tests skipped by default)
Independent PostgreSQL suite: 5 tests, OK
Normalized local smoke canary: inconclusive (expected), no side effects
Real normalized Pinterest/Amazon canary: verified, no side effects
Logical backup archive validation: OK
Isolated restore rehearsal: OK; live and restored metadata matched
Off-device iCloud copy: OK; permissions, bytes, checksum, and sync checked
GCP billing check: disabled
```

## Next gate

Schedule a small number of manual read-only canaries to measure value before any
paid-hosting or production-qualification decision. Keep each source immutable,
each run one-shot, and every backup and off-device copy checksum-verified.
