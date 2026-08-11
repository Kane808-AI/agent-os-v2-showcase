# ADR 0001: Build v2 in a clean workspace

**Status:** accepted  
**Date:** 2026-07-28

## Context

OpenClaw Legacy and Agent OS v1 contain valuable rules and live operations, but
they also contain mixed business data, nested repositories, machine-specific
paths, duplicated runtime generations, and remaining cross-system dependencies.
Copying either repository would carry those boundaries into the product.

## Decision

Agent OS v2 is built from first principles in
`/Users/operator/Documents/Agent OS`.

Legacy systems are read-only sources. Capabilities migrate through an explicit
inventory, specification, test, shadow run, cutover, and rollback process.

## Consequences

- No legacy directory is renamed during the parallel period.
- No legacy automation is copied without classification.
- V2 receives dependency management, tests, schemas, installation, upgrades,
  and recovery as product requirements.
- Northwind is implemented as a reference pack rather than hard-coded behavior.
- V1 and OpenClaw remain live until each replacement is independently proven.
