# Governed Model Routing

**Status:** binding  
**Version:** 1  
**Scope:** Goal 10 control plane; no provider calls

## Boundary

Model routing is a deterministic platform service. An agent constitution
declares reasoning tier, tool use, structured output, modality, context class,
data classes, and whether evaluator independence is required. It cannot name a
provider or model. Work supplies its actual sensitivity, token estimate, cost
ceiling, and—for independent evaluation—the producer provider.

The router selects and records a route. It does not resolve credentials, call a
provider, retry a model request, or interpret model output. Those adapter and
shadow-runtime responsibilities begin in Goal 11.

## Catalog and activation

The central catalog is an immutable semantic version plus immutable entries.
Each entry has:

- a stable product model ID and exact provider model reference;
- capability, modality, context, and data-class limits;
- integer input and output prices per million tokens;
- evaluated quality and a required evaluation version; and
- an enabled flag.

The catalog content is canonically encoded and hashed. `auto`, `/auto`, and
`latest` references are rejected. Registering a catalog does not activate it.
An append-only activation event controls which version is used by future
decisions; existing decisions remain bound to their original version.

## Tenant provider policy and credentials

A provider is unavailable until the tenant/business has:

1. an immutable credential binding containing only a `vault://` or `env://`
   reference; and
2. an append-only policy revision defining enabled state, data classes,
   optional model allowlist, and monthly micro-unit budget.

Credential references are globally unique so one scoped binding cannot be
reused by another tenant. The router never accepts or returns secret material.
Authentication failures affect only the tenant/business/provider/model circuit
that used that binding.

## Deterministic selection

Every enabled catalog entry is checked against:

- requested reasoning tier, tools, structured output, modalities, and context;
- model and tenant sensitivity policy;
- explicit exclusion and independent-provider requirements;
- current circuit state;
- request cost ceiling; and
- actual month-to-date provider cost plus estimated request cost.

Compatible candidates are ordered by preferred-provider position, minimum
sufficient reasoning tier, estimated cost, evaluated quality descending, then
stable model ID. The immutable decision records the catalog, exact policy and
credential bindings, candidate order, rejection reasons, estimated cost,
request payload and hash, and decision time.

If no candidate is compatible, the decision is `held`. The router never lowers
a requirement to make a route available.

## Fallback and failure isolation

There is no in-call or silent failover. A provider adapter must first record one
actual usage outcome against the selected decision. A fallback is allowed only
after a non-success outcome and creates a new decision that:

- links to the prior decision;
- excludes the failed model explicitly; and
- re-runs all current compatibility, policy, health, and budget checks.

Three consecutive timeouts, rate limits, server errors, or invalid responses
open a five-minute circuit. Authentication errors open it immediately. After
cooldown, a serialized state transition admits one half-open probe. Success
closes the circuit; probe failure reopens it. No failure can open another
tenant's circuit.

## Cost and integrity evidence

Usage is append-only and limited to one outcome per decision. Cost is derived
with integer arithmetic from the decision's catalog prices and actual input and
output tokens. The dashboard exposes decisions, usage, aggregate provider cost,
and circuit state.

Schema attestation covers all catalog, activation, policy, credential,
decision, usage, health, and circuit objects. Durable-data attestation
recomputes catalog hashes, request hashes, selected-route compatibility, bound
cost estimates, route identity, and actual usage cost. Backups are refused when
these checks fail and preserve routing evidence when accepted.

## Legacy incident controls

`migrations/model-routing-incidents.json` selectively records five legacy
lessons: opaque automatic routes, rate-limit cascades, revoked subscription or
OAuth access, silent mid-task failover, and unevaluated model rollout. The
artifact stores source hashes and regression mappings, not legacy model
configuration or credentials.

