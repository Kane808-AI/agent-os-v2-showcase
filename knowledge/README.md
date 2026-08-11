# Governed Knowledge

This directory contains version-controlled human knowledge and migration
metadata. It is not runtime working memory and it is not a dump of OpenClaw
files.

## Canonical components

- `catalog.json`: machine-readable knowledge records and retrieval policy.
- `record.schema.json`: record format.
- `northwind/`: selectively synthesized reference-pack knowledge.

Every record identifies its scope, lifecycle state, source hashes, confidence,
freshness, conflicts, and permitted retrieval purposes. Markdown content is
useful to people and models; the catalog decides whether it may be treated as
research, fact, or procedure.

## Lifecycle

```text
candidate -> evaluated -> verified fact
                       -> approved procedure
                       -> rejected

verified/approved -> stale -> re-evaluated
verified/approved -> superseded
```

Imported material starts as `candidate` unless an authorized human review and
the required evaluation evidence are recorded. Candidate records can inform
research but cannot drive fact-required or procedure-required work.

## Research does not replace knowledge

Research produces evidence and candidate records. Governed knowledge preserves
what was accepted, where it applies, when it must be reviewed, and what
contradicts or supersedes it. Agents still research changing platforms; they do
not repeatedly rediscover stable approved business context.

## Obsidian

Obsidian may display and edit Markdown through the normal reviewed Git workflow.
Its indexes, plugins, links, and workspace state are not canonical. Agent OS
must operate when Obsidian is absent.
