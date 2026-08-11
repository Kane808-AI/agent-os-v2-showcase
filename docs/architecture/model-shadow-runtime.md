# Real-model shadow runtime

**Status:** binding

**Version:** 1

**Scope:** Goal 11 proposal-only model calls

## Boundary

The shadow runtime may call a selected model to produce a proposal. It cannot
publish, message, purchase, mutate a business system, execute a provider tool,
or claim an external outcome. Model API usage and cost are real; business-side
effects are impossible because adapters expose no tools and validated output is
returned only as data for a later deterministic evaluation boundary.

The runtime accepts an immutable Goal 10 `selected` decision ID. It does not
accept a provider or model override and does not call the router. The selected
catalog entry supplies the exact provider/model reference. A failure records one
usage result against that decision; only a caller may subsequently ask the Goal
10 router for a new, linked fallback decision.

## Provider adapters

The platform includes non-streaming adapters for the OpenAI Responses API and
Anthropic Messages API. Both request strict JSON-schema output. The OpenAI
adapter sends `store: false` and an empty tool list; the Anthropic adapter omits
tools. Both reject refusals, truncation, tool calls, unknown content, incomplete
responses, negative usage, and malformed payloads as `invalid_response`.

HTTP authentication, rate-limit, server, timeout, and invalid-response states
map to the existing Goal 10 outcomes. Adapters receive only the exact catalog
model reference and one resolved credential value. They cannot see the catalog,
rank candidates, request fallback, or write durable state.

## Credential isolation

SQLite stores only the Goal 10 `env://` or `vault://` binding. The shadow
runtime loads the credential binding by joining the selected decision to the
same tenant, business, provider, and credential ID. A resolver receives that
immutable scoped binding outside SQLite.

The included environment resolver accepts only `env://UPPER_CASE_NAME`. The
resolver protocol permits a hosted vault implementation later without changing
the runtime contract. Secret values never enter prompt metadata, exceptions,
usage records, attempt evidence, or model-output evidence.

## Prompt and context controls

Every call binds an exact semantic prompt-template version, a strict output
schema, and explicit input/output token ceilings. `latest` aliases are not
accepted. Context blocks must carry a source reference, data class, tenant, and
business. A block is rejected if it crosses the selected route scope or exceeds
the route's data sensitivity.

The composed prompt plus schema must fit both the routed input estimate and the
selected model context window. The requested output limit cannot exceed the
routed output estimate. Context is encoded as data, separated from system
instructions, and the fixed system suffix states that the model is in
proposal-only shadow mode, has no tools, and must return only JSON.

Durable attempt evidence contains template identity, token limits, and hashes
of prompt, context, and schema. Prompt text, context text, schema content, output
content, and credentials are not stored. The validated output exists only in
the caller's process; its canonical hash is durable.

## Structured-output validation

Provider-side constrained decoding is treated as a convenience, not a trust
boundary. The local validator parses JSON and independently checks the supported
strict schema subset: nested objects and arrays, required properties, no extra
properties, scalar types, enums/constants, lengths, counts, and numeric bounds.
Remote references and unsupported schema keywords are rejected before a call.

An output that fails local validation is not returned. Its known provider token
usage is recorded with `invalid_response`, followed by a failed shadow outcome.

## One-shot claims and uncertain state

Migration 11 adds one immutable shadow attempt per route decision before
credential resolution or provider invocation. The unique decision claim
prevents concurrent, retried, or restarted processes from issuing a duplicate
call. A terminal shadow outcome must match the exact attempt and the already
recorded Goal 10 usage evidence.

Process loss can leave a claim without usage. Re-entry remains fail-closed and
does not call the provider again. An explicit operator/runtime recovery action
may isolate the claim, record zero-token `invalid_response` telemetry when no
usage is known, and append an `isolated` outcome. Only then is Goal 10 fallback
eligible. If usage exists but the shadow outcome was interrupted, isolation
binds the existing usage rather than inventing new usage.

## Canaries and evaluation replay

A canary is an ordinary one-shot shadow attempt with stronger controls: it must
use a route classified `public`, contains a named synthetic prompt, and cannot
include tenant context. It exercises the real adapter, validation, usage, and
circuit paths without business data or business actions.

Evaluation replay is fully offline. Versioned fixtures provide output text,
strict schemas, and expected validity. The deterministic local validator
replays them, hashes the complete fixture set, and appends pass counts and its
evaluator version. Identical replay is idempotent and performs no credential
resolution or provider call.

## Durable evidence and observability

Migration 11 adds append-only shadow attempts, shadow outcomes, and evaluation
replays. Database triggers bind attempts to selected routes, require public
routes for canaries, and require matching usage before terminal outcomes.
`doctor` independently rechecks route identity, outcome/usage identity, replay
counts, and terminal evidence. Verified backups preserve these records.

The dashboard exposes proposal/canary status and replay results alongside the
unchanged Goal 10 route, usage, cost, health, and circuit views.

The adapter wire shapes follow the official
[OpenAI Responses API](https://platform.openai.com/docs/api-reference/responses)
and
[Anthropic structured-output](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)
contracts. Provider policy and retention terms remain deployment inputs rather
than platform assumptions.

## Operational activation

No provider credential, endpoint invocation, canary schedule, or shadow worker
is activated by repository initialization, demos, tests, or migration. An
operator must configure a tenant policy, bind a credential reference, select a
route, install the matching resolver, and explicitly invoke the shadow runtime.
The repository test suite uses injected transports and resolvers and makes no
network provider calls.
