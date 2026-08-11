# 0022: Keep private GitHub controls zero-cost

**Status:** Accepted

**Date:** 2026-07-31

## Context

Agent OS v2 now has a separate canonical private repository at
`Kane808-AI/agent-os-v2`. Both push- and pull-request-triggered `release-gates`
runs passed their unit and PostgreSQL checks. Independent review found that the
action-based pull-request secret scan covered only the PR commit range, so it
was replaced with a checksum-pinned Gitleaks CLI invocation that explicitly
scans `--all` reachable history. GitHub rejected branch-protection configuration
for this private repository with HTTP 403 because that feature requires a paid
GitHub plan or a public repository.

The source must remain private, and the zero-cost local-first decision does not
authorize a GitHub subscription. Weakening privacy or silently adding a paid
service would violate the project boundary.

## Decision

Keep the repository private and do not purchase GitHub Pro. Use the strongest
available free controls:

- default GitHub Actions token permissions are read-only;
- workflows cannot approve pull requests;
- all workflow actions and the PostgreSQL image are immutable-digest pinned;
- pushes to `main` and `codex/**`, plus pull requests, run unit, real PostgreSQL,
  and full-history secret gates;
- merge commits are disabled and merged branches are deleted automatically; and
- the project manager never force-pushes or pushes feature work directly to
  `main`, verifies local/remote SHA parity, and keeps changes in a draft pull
  request until their checks pass.

## Consequences

Source backup and remote CI are verified. Repository-owner bypass prevention is
not technically enforceable on the current free private plan, so
`AOS-CI-001` remains `implemented`, not `verified`, and Goal 16 remains in
progress. The status board must report this limitation rather than describe the
branch as protected.

If a paid plan is explicitly authorized later, require the
`unit-postgresql-secrets` status check, pull requests, conversation resolution,
linear history, and administrator enforcement; continue to forbid force-pushes
and branch deletion. Making the repository public is not an acceptable
substitute.
