# Goal 11 alpha.16 completion

**Decision:** GO to begin Goal 12

**Date:** 2026-07-31

**Release:** `2.0.0-alpha.16`

## Exit-criteria result

Goal 11 is complete. The real-model shadow runtime now provides:

- exact consumption of an immutable Goal 10 selected decision with no provider
  or model override;
- tool-free OpenAI Responses and Anthropic Messages adapters using strict
  provider-side structured-output requests;
- scoped credential binding resolution outside SQLite, with no secret material
  in durable data or error evidence;
- exact semantic prompt versions, tenant/business context isolation,
  sensitivity enforcement, and routed input/output/context ceilings;
- independent local JSON parsing and strict schema validation before any model
  proposal is returned;
- one immutable claim per route decision, one Goal 10 usage result, and an
  append-only terminal shadow outcome;
- explicit isolation for interrupted or uncertain provider state without an
  automatic retry;
- public, synthetic, context-free real-adapter canaries;
- versioned, fixture-hashed, deterministic offline evaluation replay; and
- dashboard, `doctor`, schema-attestation, durable-data-attestation, and backup
  coverage for all Goal 11 evidence.

No provider credential, shadow worker, canary schedule, or endpoint invocation
is activated by initialization, migration, demos, or tests. The provider
adapters can incur real model usage only when an operator explicitly supplies a
selected route, resolver, credential binding, and invocation. They cannot
perform business-system actions because their request contracts expose no
tools and their only result is validated proposal data.

## Goal 10 preservation

The runtime does not call `route` or `route_fallback`. It joins the supplied
decision to its exact catalog entry and same-scope credential binding. Every
normal terminal path records exactly one existing Goal 10 `ProviderOutcome`
with actual known token counts before shadow outcome evidence. Malformed output
uses `invalid_response`; credential failure uses `auth_error`; HTTP and
transport failures retain the Goal 10 mapping.

Fallback remains explicit and external to the adapter. A caller can request it
only after the first decision has a non-success usage record; the Goal 10 router
then creates a new linked decision, excludes the failed model, and rechecks
current policy, health, sensitivity, compatibility, and budget. Existing
tenant-scoped circuit and cost telemetry therefore remain authoritative.

## Verification

The complete repository suite passes:

```text
Ran 183 tests
OK
```

Goal 11 contributes 13 focused tests covering exact route and credential
binding, secret/prompt/context/output non-retention, independent structured
validation, actual usage and explicit fallback, authentication isolation,
scope/sensitivity/token controls, duplicate-call refusal, abandoned-call
isolation, public canaries, deterministic offline replay, current OpenAI and
Anthropic tool-free request shapes, explicit HTTP failure mapping, schema/data
attestation, dashboard visibility, and backup preservation.

The unchanged Goal 10 tests continue to cover deterministic selection,
credential isolation, explicit fallback, cost telemetry, circuit isolation,
concurrency, and routing incident regressions.

## Bounded risk accepted

This is a shadow runtime, not production deployment or external execution.
Provider calls are synchronous and local-process scoped. A process loss after a
provider accepts a request remains intentionally fail-closed and requires
explicit isolation; the platform will not risk a duplicate automatic call.

Production vault implementation, provider-specific enterprise retention
configuration, distributed claim/lease semantics, hosted observability, and
multi-process deployment remain Goal 14 work. Goal 12 may use this runtime only
for read-only affiliate research and proposal generation. It may not publish,
alter links, contact partners, purchase ads, or spend money.
