# ADR 0006: Governed Selective Knowledge Migration

**Status:** accepted
**Date:** 2026-07-28

## Context

OpenClaw Legacy contains valuable domain knowledge, but it also mixes verified
observations, hypotheses, outdated platform details, client identifiers,
credentials references, runtime incidents, duplicated prompts, and explicitly
superseded procedures. Copying directories would preserve contradictions and
give stale text accidental authority.

## Decision

Agent OS v2 will use a machine-readable knowledge catalog with versioned
Markdown content. Imported records retain source hashes, scope, lifecycle,
freshness, confidence, conflicts, and retrieval permissions.

Migration is source-by-source synthesis. Imported material defaults to
`candidate` and research-only retrieval. Fact and procedure retrieval require
separate evaluation and authorized promotion.

Obsidian remains an optional view. Research agents create evidence and
candidates; they do not replace governed knowledge.

## Consequences

- Valuable Etsy, TikTok, GHL/WhiteLabel, Pinterest, and business lessons can be
  retained without importing OpenClaw state.
- Stale or contradictory material remains auditable but cannot silently drive
  execution.
- Migration requires deliberate review and produces less volume than copying.
- Current platform facts must be revalidated before promotion or live use.
