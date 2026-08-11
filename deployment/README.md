# Deployment contracts

This directory contains business-neutral Goal 14 deployment inputs. It contains
no credential values and performs no deployment.

- `production-qualification-policy.json` defines the exact eight gates,
  metadata-only observability set, production persistence boundary, and
  non-executing cutover modes.
- `tenant-package.schema.json` defines an isolated package with immutable
  artifacts and opaque secret references.
- `reference/tenant-package.example.json` is intentionally non-deployable: it
  uses an `.invalid` origin and a zero image digest.

Use `python3 -m agent_os.cli build-tenant-package` to validate and atomically
build a private package outside the repository. Real secrets are resolved only
by the selected hosted environment.

Goal 15 adds an operational pilot implementation without applying paid cloud
resources:

- `postgresql/pilot-schema.sql` is the forced-RLS, append-only bounded schema;
- `container/Dockerfile.pilot` packages the status and one-shot worker
  entrypoints as an unprivileged image; and
- `local/pilot` supplies the current zero-cost normalized smoke input, while
  `scripts/local_pilot.sh` runs persistent PostgreSQL on an internal Docker
  network with storage and backup guards; and
- `gcp/pilot` defines the new-project Cloud Run, Cloud SQL, Secret Manager, KMS,
  and separated service-account topology as a deferred production option.

Use `docs/operations/local-production-pilot.md` for the selected local path.
See `docs/operations/gcp-production-pilot.md` only if paid hosting is separately
reconsidered and authorized.
