# ADR 0012: Make approvals and emergency stops durable execution gates

**Status:** accepted

**Date:** 2026-07-30

## Context

The authority engine could classify work as approval-required, but the hold had
no durable decision lifecycle. There was no supported approve, reject, expire,
or revoke transition, and no independent emergency control that stopped all
business work. Treating an earlier policy result as permission at execution
time would also create a time-of-check/time-of-use gap.

## Decision

Migration 7 records immutable approval requests and append-only approval
events. A request fingerprints the exact work semantics. Approval requires a
different authorized human from the requesting and assigned actor. Decisions
are ordered, time-bounded, audited, and checked again with current policy
immediately before execution.

Migration 7 also records append-only, business-scoped emergency-stop events.
Only an authorized in-scope human can activate or clear the stop. The latest
event is authoritative. The event runtime, autonomous discovery, queue claim,
simulated resolution, and external-attempt boundary all fail closed while the
stop is active.

## Consequences

Approval-held work can now progress without weakening deterministic authority
policy, and an owner can stop autonomous activity through one durable control.
The local dashboard exposes both histories.

This decision does not complete Goal 9. Budget and finance controls remain.
The SQLite adapter also does not authenticate the human operating the database;
production identity authentication and hostile-administrator containment are
separate deployment controls.
