# 0021: Select the zero-cost local-first pilot

**Status:** Accepted

**Date:** 2026-07-31

## Context

The first reviewed GCP foundation plan used a regional, dedicated-core Cloud
SQL instance and projected approximately USD 110 per month. That configuration
is appropriate for a later production qualification but is disproportionate to
the first one-shot, read-only aggregate canary. The authoritative build plan
requires PostgreSQL behavior and safety evidence; it does not require Cloud SQL
or an always-on HA database for this learning step.

The local Mac has about 61 GiB free. Agent OS source is under 10 MiB, its image
layers add only hundreds of MiB, and the normalized pilot stores small numeric
and text records rather than media.

## Decision

Run Production Pilot 1 locally before paying for hosting. PostgreSQL uses a
persistent Docker volume on an internal network with no published host port.
Migration, runtime, and backup logins remain distinct. Runtime scope continues
to derive from the database login and forced RLS; evidence remains append-only.

The pilot stops before a canary when host free space is below 50 GiB or the
database exceeds 1 GiB. PostgreSQL is limited to one CPU and 512 MiB memory,
container logs rotate at 10 MiB with three files, and seven verified compressed
logical backups are retained. Secrets live only under ignored `data/` runtime
storage with private permissions.

The local worker consumes one human-reviewed normalized Pinterest/Amazon file.
It contains no external connector, browser, publisher, scheduler, advertising,
messaging, or financial client and has no outbound Docker network path.

## Consequences

The current pilot costs no hosting fee and is suitable for functional evidence
and learning. It does not qualify as production: local secrets, Docker-internal
unencrypted database transport, absence of external KMS, lack of hosted
identity edge, Mac availability, and same-device backups keep the relevant Goal
14 gates held. A later production decision must review fresh costs and supply
off-device recovery evidence.

The isolated GCP project remains empty and unbilled. Its Terraform is retained
as a deferred option and must never be applied from the old saved plan.
