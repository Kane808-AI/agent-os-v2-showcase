# GHL and WhiteLabel Operating Guardrails

**Status:** Candidate and stale for current-state use. Research use only. This
is neither current API truth nor an approved CRM, messaging, or workflow
procedure.

## Scope

These generic controls support Northwind research involving GoHighLevel through a
WhiteLabel-branded service context. They contain no live account identifiers,
client records, credentials, endpoints, or asserted workflow state.

## Durable controls

- Isolate every tenant, sub-account, legal entity, credential set, and data
  export. Never infer the target from the most recent session.
- Resolve object and workflow identifiers from authorized live state; do not
  hard-code identifiers carried over from examples or another tenant.
- Search and deduplicate before contact creation. Preserve consent, suppression,
  do-not-disturb, opt-out, retention, and purpose restrictions.
- For workflows, inspect trigger filters, re-entry behavior, timing, branches,
  business hours, stop conditions, consent handling, and draft versus published
  state.
- Stage changes, test with non-customer data where safe, read back the stored
  result, and keep an audit record. A successful request is not proof that the
  intended outcome occurred.
- Store secrets in an approved credential service, authenticate inbound events,
  use idempotency keys, handle null and duplicate payloads, acknowledge safely,
  retry with bounded backoff, retain replay or dead-letter evidence, and alert
  on terminal failure.

## High-risk exclusions

Email deliverability, telephony, text-message registration and consent,
provider behavior, rate limits, endpoints, scopes, and compliance rules require
current official and live-account verification.

## Promotion checklist

Promotion requires current official documentation, tenant-specific live-state
inspection, privacy and messaging-compliance review, sandbox or test-contact
evidence, rollback and failure-alert tests, and an authorized reviewer.
