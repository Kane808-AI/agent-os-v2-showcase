# Knowledge Governance and Migration Contract

**Status:** binding
**Version:** 1

## Knowledge is not model memory

Models are ephemeral. Governed knowledge is durable, scoped, versioned, and
reviewable. A research agent can find new evidence; it cannot make that evidence
an organizational fact or executable procedure by repeating it.

## Record classes

- `fact`: a scoped statement supported by evidence.
- `procedure`: approved steps with inputs, outputs, controls, and rollback.
- `strategy`: a hypothesis or operating choice with a review horizon.
- `reference`: terminology or background that informs research.
- `historical`: an incident, prior state, or past decision retained as evidence.

## Lifecycle and promotion

All agent-generated and imported knowledge begins as `candidate`. Evaluation
checks provenance, scope, freshness, contradictions, counterexamples, and
relevant outcomes. An authorized reviewer may then mark a record `verified` or
`approved-procedure`.

Only `verified` records can satisfy fact-required retrieval. Only
`approved-procedure` records can satisfy procedure-required retrieval. Stale,
conflicted, candidate, evaluated, rejected, superseded, and archive-only records
cannot silently satisfy those purposes.

## Provenance

Every imported source records:

- source system and path;
- SHA-256 digest;
- source modification time;
- whether it was synthesized, used only as evidence, or reviewed but excluded;
- migration time; and
- review identity when promoted.

The source inventory records rejected, superseded, sensitive, and stale material
as well as migrated material. Exclusion is a governed decision.

## Freshness

- `stable`: review on material process or policy change.
- `periodic`: review on the record's scheduled date.
- `volatile`: re-check against current authoritative sources before a
  consequential decision.

Passing `review_by` makes a record stale for fact and procedure retrieval even
if its stored lifecycle status was not yet updated by a maintenance job.

## Conflict handling

Records conflict when they explicitly reference one another or assert different
values for the same claim key inside the same scope. Both remain visible for
research and audit. Neither may satisfy fact or procedure retrieval until a
versioned resolution supersedes or rejects the losing claim.

Latest timestamp never wins automatically. Human recency, model confidence, or
source count does not replace a conflict decision.

## Retrieval

Retrieval always filters tenant and business before ranking. It then filters by
purpose, status, freshness, confidence, and conflict state. Semantic similarity
and embeddings are indexes, not sources of truth; returned content keeps its
record ID and provenance.

External content is evidence, never instruction. Prompt-injection text inside a
source has no authority over the agent or runtime.

## Selective migration

Migration is synthesis, not copying:

1. inventory and hash the source;
2. classify sensitivity and expected volatility;
3. identify conflicts and supersession;
4. strip credentials, IDs, personal data, runtime state, and wrapper-specific
   instructions;
5. rewrite stable reusable knowledge with explicit limitations;
6. register it as a candidate;
7. evaluate and promote separately.

Legacy API endpoints, platform limits, pricing benchmarks, policies, live
account configuration, and automation health are presumed stale until verified
against current authoritative state.

## Obsidian boundary

Obsidian is an optional Markdown interface. Catalog metadata and Git history are
canonical. Obsidian plugins, backlinks, caches, and workspace files cannot
grant retrieval status or runtime authority.
