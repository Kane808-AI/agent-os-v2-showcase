# Affiliate-marketing shadow loop

**Status:** binding

**Version:** 1

**Scope:** Goal 12 Northwind reference pilot

## Boundary

The affiliate loop evaluates one fast-feedback revenue stream without acting
on a live business system. It can persist read-only evidence, proposals,
historical observations, derived measurements, verification, and candidate
learning. It cannot publish content, create or modify an affiliate link,
contact a merchant or audience, change payout settings, purchase advertising,
or spend.

No publisher, messaging, partner-management, link-writer, ad-buying, payout, or
payment adapter is accepted by the loop. Repository initialization, migration,
tests, demos, and dashboard rendering perform no provider or business-system
network call. Goal 11 model usage remains possible only through its separately
configured shadow runtime and remains subject to Goal 10 routing and telemetry.

## Evidence chain

Each isolated run is bound to one active `affiliate_sales` objective, tenant,
business, and in-scope commerce, marketing, or growth producer:

```text
objective-bound run
  -> read-only offer snapshots
  -> deterministic recommendation or safe hold
  -> Goal 11-attested content proposal
  -> historical replay experiment
  -> read-only impression/click/conversion observations
  -> deterministic measurement
  -> independent QA verification
  -> candidate-only episodic learning
```

An objective may have repeated runs, but every run gets a new immutable chain.
Each run permits one recommendation, one content proposal, one experiment, one
measurement, one verification, and at most one learning record. A held
recommendation has no selected offer and cannot progress to content.

## Offer research and recommendation

Offer inputs are caller-supplied observations from source identities ending in
`-readonly`; the loop has no fetch or mutation client. Every snapshot carries a
source reference, content hash, observation time, same-business evidence
references, merchant, channel, credential-free HTTPS destination, base
currency economics, audience fit, confidence, destination health, verified
terms, required disclosure, and approved claims.

Research freezes when the recommendation is appended. The versioned
deterministic evaluator rejects stale, unhealthy, unverified, low-confidence,
low-fit, or zero-commission offers. Eligible offers are scored from audience
fit, evidence confidence, bounded commission rate, and bounded order value,
with offer key as a stable tie-break. No eligible offer produces a durable safe
hold rather than speculative content.

## Content and Goal 10/11 preservation

Content is proposal-only. Its canonical hash must equal the output hash of a
successful same-tenant, same-business Goal 11 attempt. The destination,
channel, required disclosure, and approved claim set must also equal the
selected offer evidence. These bindings are checked in the API, at database
insert, and by `doctor`.

Goal 12 does not select providers, resolve credentials, invoke fallback, or
write model usage. The Goal 11 runtime still consumes the exact immutable Goal
10 decision, uses its scoped credential binding, records usage and circuit
telemetry, and leaves fallback as a new explicit Goal 10 decision. Prompt,
context, output, and credentials retain the Goal 11 non-retention rules.

## Attribution, measurement, and verification

Experiments have only `historical_replay` mode and `shadow` status. Their
window must end before definition. Observations accept only read-only source
identities and hashes; raw analytics payloads are not stored. A conversion must
point to an earlier click for the same subject and experiment. Imports freeze
once measurement begins, preventing evidence from changing underneath a
result.

Measurement deterministically counts impressions, clicks, conversions, gross
revenue, and commission, computes integer conversion-rate basis points, and
records whether the configured click floor was met. A verifier must be a
different enabled actor with scoped QA or verifier authority. The verifier
recomputes the measurement: mismatches reject, insufficient samples are
inconclusive, and only matching sufficient samples verify.

## Learning and failure isolation

Only an independently verified result can create learning. The output is an
append-only candidate episodic memory, not approved knowledge or policy. Its
statement describes the historical correlation and explicitly says it is not
proof of incrementality or authority to publish or spend. Memory and its
affiliate-learning link commit atomically.

Every lifecycle table is append-only. Database triggers protect the content,
historical replay, conversion attribution, observation freeze, and verifier
boundaries. `doctor` recomputes measurements and content/learning bindings;
verified backup includes the complete chain. Invalid evidence raises a scoped
failure and cannot silently advance the run.

## Activation

The pack file `packs/northwind/affiliate-shadow-pilot.json` supplies pilot
thresholds, strict content schema, producer/verifier constitutions, and the
forbidden-side-effect list. It is inert configuration. An operator or future
orchestrator must explicitly create an objective, provide read-only evidence,
obtain a Goal 10 route and Goal 11 proposal, and invoke each durable lifecycle
step. Production credentials, schedules, data connectors, publishers, and
external executors are not activated.
