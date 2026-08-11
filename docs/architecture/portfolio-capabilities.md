# Portfolio capability expansion

**Status:** binding

**Version:** 1

**Scope:** Goal 13 reusable revenue-stream and department modules

## Common boundary

Goal 13 expands the Northwind proof into reusable capability packs without
activating a production integration. Every pack is business-neutral, versioned,
and limited to `read_only`, `proposal`, or `simulated` modes. The common policy
globally forbids production writes, publishing, external contact, advertising
spend, affiliate-link or payout mutation, and money movement.

The policy declares an exact required portfolio. A pack cannot shrink that
coverage, add an execution mode, reference an unknown role, grant a forbidden
action, omit evidence and output contracts, make its producer its verifier, or
claim acceptance without both happy-path and safe-hold evaluation cases.

## Accepted portfolio

The platform accepts 13 packs:

| Pack | Principal scope |
| --- | --- |
| Digital marketing and consulting | SEO evidence, GEO citation readiness, consulting diagnostics |
| YouTube | Aggregate channel review and proposal-only content |
| Applications | Opportunity evaluation and simulated release readiness |
| Physical products | Demand/unit-economics review and listing proposals |
| Commerce | Offer performance and merchandising proposals |
| Finance | Cash position, forecasts, and simulated scenarios |
| Accounting | Reconciliation and close-readiness review |
| Sales | Pipeline analysis and consent-aware outreach drafts |
| Operations | Bottleneck review and runbook simulation |
| Customer success | Health review and response drafts |
| Research | Provenance-bound evidence synthesis |
| Engineering | Change proposals, tests, and incident review |
| QA | Independent claim and regression verification |

Finance and Accounting remain separate. Finance cannot move money or approve a
commitment; Accounting cannot post entries or certify books. Sales and Customer
Success can draft communication but cannot contact a person. Engineering and
Product can simulate change but cannot deploy, merge, or submit to an app store.

`packs/northwind/portfolio-capabilities.json` maps these reusable packs to the
services, commerce, media, and ventures businesses. That mapping is
`accepted-not-activated` and enables no side effect.

## Deterministic acceptance

`CapabilityPackCatalog` validates the shared policy, agent registry references,
pack structure, global capability identity, exact portfolio coverage, source
read modes, execution boundary, and client neutrality. Each fixture submits a
capability, mode, and action. The evaluator accepts only exact declared
non-executing combinations; unknown, executing, or forbidden combinations hold.

Migration 13 stores the exact pack ID, semantic version, canonical pack hash,
evaluator version, case counts, verdict, and time. Replaying the same exact pack
and evaluator is idempotent. A changed pack receives a different hash and must
be accepted independently.

## Aggregate platform evidence

Pinterest and affiliate-network reporting commonly exposes totals rather than
person-level click and conversion events. Goal 13 therefore adds a separate
`directional_aggregate` evidence path instead of fabricating subject identity or
weakening Goal 12.

An aggregate snapshot binds a tenant, business, producer, channel, offer,
explicit `-readonly` source identity, completed window, ordered funnel counts,
economics, sample floor, same-business evidence references, canonical hash,
and an explicit non-incrementality limitation. It stores no subject key,
recipient, cookie, device, or raw analytics payload.

Independent scoped QA recomputes arithmetic and the canonical hash. Matching
reports above the sample floor verify; small reports are inconclusive; hash
mismatches reject. “Verified” means the aggregate fields and evidence binding
are internally consistent. It does not mean causal lift, event-level
attribution, or execution success. Aggregate tables have no memory or affiliate
learning link.

Goal 12 remains unchanged: a conversion in that lifecycle still requires a
prior same-subject click in the same experiment. Aggregate evidence cannot
satisfy that trigger or create candidate learning.

## Communications

The dashboard remains the canonical control plane. Slack, Telegram, Discord,
Teams, and email have replaceable descriptor contracts with only
`inbound.read` and `outbound.propose`; the local dashboard additionally exposes
`state.read`. A proposed outbound message stores only its opaque target,
adapter identity, payload hash, proposed state, and human-approval requirement
in process. There is no `send`, `publish`, `delete`, or credential method.

These descriptors prove interchangeability and control-plane precedence. They
are not installed third-party adapters and make no network call.

## Integrity and activation

Capability acceptance and aggregate evidence are append-only. Database checks
enforce funnel ordering, completed windows, directional classification, exact
limitation text, and independent verifier scope. `doctor` recomputes aggregate
hashes, source/evidence scope, producer eligibility, sample verdicts, and pack
acceptance counts. Dashboard and verified backups preserve both evidence sets.

No pack, Northwind mapping, aggregate source, channel descriptor, model provider,
credential, worker, schedule, or external executor is activated by Goal 13.
Tenant packaging, real connectors, operational observability, production
secrets, deployment, and controlled cutover remain Goal 14.
