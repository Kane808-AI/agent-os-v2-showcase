# Memory Architecture

Obsidian and the workflow runtime are complementary, not alternatives.

## Memory classes

| Class | Purpose | Canonical storage |
| --- | --- | --- |
| Working state | Current workflow inputs, steps, interrupts, and outputs | Runtime checkpoints in PostgreSQL |
| Operational state | Goals, budgets, jobs, experiments, approvals, metrics | PostgreSQL |
| Episodic | What happened during an execution | Append-only event and outcome records |
| Semantic | Verified facts about a tenant, business, customer, or market | Structured memory store |
| Procedural | Approved ways to perform repeatable work | Version-controlled playbooks and skills |
| Human knowledge | Strategy, decisions, research, and explanations | Markdown, optionally viewed in Obsidian |
| Artifacts | Reports, exports, media, generated deliverables | Artifact storage plus immutable references |

## Required memory metadata

Every durable memory record includes:

- tenant and business namespace;
- memory type;
- statement;
- source type and source reference;
- confidence;
- verification status;
- creation and observation times;
- expiry or review date where applicable;
- evidence references;
- supersession relationship; and
- authoring agent or human.

## Retrieval

Retrieval combines:

- exact and lexical search for identifiers, error codes, names, and policies;
- semantic search for conceptually related knowledge;
- metadata filters for tenant, business, task, source, confidence, and freshness;
- reranking when justified by measured retrieval errors.

Unverified or inference-sourced records are visibly labeled and excluded from
fact-required tasks by default.

## Obsidian boundary

Obsidian is an optional interface over Markdown. The platform must continue to
operate when Obsidian is closed or uninstalled. Obsidian configuration, plugins,
metadata caches, and workspace layout are not part of runtime state.

## Migration rule

OpenClaw memory is not copied wholesale. Each imported record must be classified
as verified business knowledge, historical evidence, candidate lesson, obsolete
runtime detail, or archive-only material.
