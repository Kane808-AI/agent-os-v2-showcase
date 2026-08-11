# ADR 0016: Isolate real-model calls behind one-shot shadow claims

**Status:** accepted

**Date:** 2026-07-31

## Context

Goal 10 selects and attributes a compatible model but deliberately cannot
resolve credentials, transmit prompts, or interpret model output. Adding those
behaviors creates new risks: prompt/context leakage, duplicate calls after
process loss, invalid structured output, silent adapter failover, accidental
tool execution, and usage evidence that diverges from the selected route.

## Decision

Migration 11 adds an immutable one-shot shadow claim per selected decision plus
append-only terminal outcome and evaluation-replay evidence. The runtime:

- joins the selected decision to its exact catalog and scoped credential
  binding;
- resolves that binding outside SQLite through a narrow resolver protocol;
- composes only versioned, sensitivity- and token-bounded prompts;
- invokes an exact provider adapter with tools disabled;
- validates JSON locally against a strict schema subset;
- records exactly one Goal 10 usage result before terminal shadow evidence;
- never selects or invokes fallback; and
- refuses a second call when provider state is uncertain.

Canaries require public synthetic data and no tenant context. Evaluation replay
is offline, versioned, fixture-hashed, deterministic, and provider-free.

## Consequences

Real provider cost is possible when an operator explicitly configures and
invokes a shadow call, but external business actions remain impossible at this
boundary. A failed call affects the same tenant/business/provider/model circuit
defined by Goal 10. Prompt, context, output, and secret contents do not become
durable runtime data.

A process loss after the provider accepted a request cannot be retried
automatically. The abandoned claim must be explicitly isolated; the system
prefers a held or failed proposal over duplicate uncertain provider activity.
Fallback remains a new Goal 10 decision and therefore rechecks current policy,
health, compatibility, sensitivity, and budget.

Provider-hosted retention and production secret management remain deployment
concerns. Tenant policy must account for provider data handling before allowing
sensitive classes. Hosted vault implementation and distributed claim semantics
remain Goal 14 hardening work.
