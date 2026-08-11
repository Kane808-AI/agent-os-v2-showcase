# ADR 0017: Bind the affiliate pilot to historical shadow replay

**Status:** accepted

**Date:** 2026-07-31

## Context

Goal 12 needs a fast-feedback reference loop that proves objective-driven
offer selection, content proposals, attribution, measurement, verification,
and learning. A live affiliate pilot would also introduce publishing, tracking
link mutation, partner communication, advertising spend, payout access, consent,
and uncertain causal attribution before the platform has production hardening.

Goal 11 already provides a proposal-only model boundary and Goal 10 already
owns routing, credential isolation, explicit fallback, usage, cost, health, and
circuit state. The new loop must consume those controls without creating a
second provider path.

## Decision

The Northwind pilot runs only as append-only historical replay. It accepts
explicit read-only offer and analytics evidence, deterministically recommends
or holds, binds content to a successful Goal 11 output and the selected offer,
attributes conversions only to prior same-subject clicks, freezes observations
before measurement, requires independent QA, and emits candidate-only learning.

The lifecycle exposes no transition or adapter for publishing, link or payout
mutation, partner or audience contact, ad buying, or spend. Goal 12 neither
routes a model nor invokes fallback; it consumes Goal 11 evidence, leaving all
provider controls and telemetry authoritative in Goal 10 and Goal 11.

## Consequences

The pilot can prove deterministic control flow, evidence provenance, failure
isolation, and learning discipline without external business side effects. It
can measure attributed historical correlation but cannot claim incrementality,
future performance, a live conversion lift, or production readiness.

Offer and analytics acquisition remain outside this runtime and must be
read-only. Live experimentation, consent and disclosure review, publisher and
affiliate-network integration, causal experiment design, production secrets,
scheduling, and operational monitoring require later explicit acceptance. No
part of this decision grants authority to activate those capabilities.
