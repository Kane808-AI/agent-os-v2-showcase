# 0023: Grant the research executor read-only web retrieval

**Status:** Accepted

**Date:** 2026-08-04

## Context

Goal 18 slice 2 proved the owner-filed work loop live: a chat request became a
ready work item, the research executor ran one routed CLI model call, and a
real shortlist arrived in the owner chat. That call ran fully sealed, so the
research grounded itself in model knowledge alone and said so in its caveats.
The owner asked for current web data. The Goal 18 gate pattern requires each
external connection to arrive one at a time, read-only before write, with its
own explicit owner approval.

## Decision

The owner approved read-only web access for the offer-research executor on
2026-08-04, in chat, after reviewing the tradeoffs. Scope of the grant:

- `WebSearch` and `WebFetch` only, and only for calls whose routed template
  carries `web_access=True`. Today that is `owner-work-offer-research` 1.2.0
  alone.
- Shell, filesystem, and delegation tools remain denied unconditionally; no
  request flag can enable them.
- Drafting and chat-reply templates stay sealed. Each future web-consuming
  executor requires its own explicit owner enable.
- Adapters that cannot honor the grant (the tool-free HTTP adapters) refuse
  with `web_access_unsupported` instead of silently running sealed, so a
  catalog rollback holds research work visibly.
- Synthetic canaries refuse web-granted templates outright.
- Every research finding carries a `source` field: the URL actually
  retrieved, or the literal `unverified`, so grounding is auditable rather
  than prompt-trusted.

## Accepted risk

Fetched web content is untrusted and may attempt prompt injection. The blast
radius is bounded to the schema-validated, size-truncated proposal text that
waits for owner approval; nothing else on the machine is reachable from the
subprocess. The subprocess inherits the Claude CLI's own WebFetch protections;
this codebase does not add a network-level destination allowlist. Independent
security review rated the residual destination-restriction gap MEDIUM with a
bounded blast radius. Acceptance of that residual risk is part of the owner's
PR 24 merge approval; this record must not merge without it. Revisit before
any wider web grant.
