# Production recovery qualification

Production recovery is qualified per tenant and immutable release. The local
SQLite procedure in `docs/operations/recovery.md` remains the development
adapter procedure; it is not production evidence.

Required production evidence:

- a PostgreSQL backup with immutable SHA-256 identity;
- point-in-time recovery into a fresh isolated target;
- row-level tenant-isolation checks after restore;
- verification that database roles cannot access the external KMS attestation
  key;
- at least eight named crash-point rehearsals;
- at least 128 deterministic state-machine fuzz cases with the recorded seed
  and zero failures;
- observed RPO and RTO within the tenant's declared ceilings; and
- independent QA verification.

Never restore over the authoritative target. Preserve incident evidence,
restore into a new target, run schema/data attestation, exercise a loopback or
read-only canary, then change the supervised target through a separately
approved operator procedure. Keep the old target and legacy capability
available until the observation window closes.

A missing hash, corrupt restore, missed RPO/RTO, fuzz failure, shared database
and attestation authority, or unverifiable tenant isolation is a hard hold.
