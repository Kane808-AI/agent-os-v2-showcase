# Agent OS v2 project status

**Updated:** 2026-08-03

**Project manager:** Codex primary agent

**Owner:** Chris Kaneshiro

**Current goal:** Goal 16 — Pilot operations and project control

**Overall status:** IN PROGRESS — GOAL 17 LIVE TELEGRAM CHANNEL OPERATING
WITH APPROVAL-GATED OUTBOUND; WEEKLY CANARY 1 DATE-GATED

## Current control board

| Control | State | Evidence | Owner / next action |
| --- | --- | --- | --- |
| Local source | green | PRs 5 through 9 each merged an exact approved head with president approval; local `main` is `adbb33d` with a clean tree and exact remote parity | Branch new work from merged `main` only |
| GitHub source backup | green for merged main | Private canonical repository `Kane808-AI/agent-os-v2` has `main` at exact local commit `adbb33d`; automatic head-branch deletion was disabled in repository settings on 2026-08-01 and every merged branch since has survived at its exact head | Preserve fetch-before-push and exact-head merges |
| Off-device source recovery | green | Latest bundle `agent-os-v2-source-...-adbb33d.bundle` in iCloud holds all branches including merged `main` `adbb33d`; a scratch clone restore-verified `main` at the exact commit | Refresh and restore-verify after each merged milestone |
| Live Telegram channel (Goal 17) | green/operating | PRs 7 through 13 shipped inbound, transport, approval-gated outbound, model-drafted replies through the Goal 10/11 routed shadow runtime (budget-capped), the continuous chat loop, and transport retry hardening; each was live-verified. The channel now runs as the `com.agentos.atlas-telegram` LaunchAgent from a committed template | Ground replies in `state.read` context, then build the read-only email triage adapter to complete Goal 17 |
| Standing owner approval | accepted by owner decision | The owner explicitly granted standing approval for model replies into their own chat, first per-session and then as a persistent service on 2026-08-03. Replies remain hash-verified one-shot proposals to the bound owner chat only; installing the service is the grant and uninstalling it withdraws it | Any wider autonomy (other recipients, actions beyond words) requires a new explicit decision |
| Secret and private-data safety | green | Working-tree and complete reachable-history Gitleaks scans passed locally and in remote CI at exact head `5e0b979`; AGE-2 tools operated only on tracked source or disposable exact-package targets and did not scan `data/`, live images, volumes, credentials, or private runtime inputs | Rerun in CI on every push |
| Terraform artifact safety | green | State, variables, plans, overrides, provider cache, and crash files are ignored; automated ignore checks pass | Codex reruns before future Terraform work |
| Default test suite | green | The merged AGE-2 diff discovered 255: 250 passed and 5 PostgreSQL cases skipped by design; targeted project-control tests passed 6 of 6, and the same suite passed in exact-head CI | Rerun in CI on every push |
| PostgreSQL integration | green | Independent release-test review passed 5 of 5 against a disposable database using pinned `psycopg==3.3.4`; temporary environment and container were removed; live pilot was untouched | Remote CI remains the exact-branch integration gate |
| Remote CI | green | Push run `30733885192` passed at exact AGE-2 head `5e0b979`; PR run `30733898268` passed on GitHub's synthetic merge `96d9779` whose parents are exactly base `c185fd1` and head `5e0b979` | Require exact-head push CI and PR CI before every merge gate |
| Build assurance | green/bounded gate merged | The checksum-pinned Hadolint gate is merged into `main` with measured Hadolint, Ruff, TFLint, and pip-audit baselines recorded. Trivy, Hypothesis, broad Ruff, pip-audit CI, TFLint CI, and Superpowers remain frozen or held | Upgrade track closed; no further assurance upgrades without a new approved scope |
| Private branch enforcement | amber/held | GitHub returned HTTP 403 because private branch protection requires GitHub Pro; repository remains private and zero-cost | PR-only/no-force operational control applies; do not pay or make public without explicit authorization |
| Local database backup | green | Three private local dumps; latest restore rehearsal passed | Codex creates and verifies a new dump after each canary |
| Off-device database backup | green | Latest dump has a byte-identical, checksum-verified iCloud copy and caught-up sync | Codex verifies copy and sync after each canary |
| External side effects | green/held | Pilot reports external side effects disabled; production qualification and legacy cutover remain held | No change without a separate explicit approval gate |

## Active risks

1. **Canonical-source risk — controlled.** Private GitHub contains current
   committed `main` `3cead3f`; the restore-verified iCloud Git bundle protects
   complete history through the same commit. Remote SHA parity remains a
   mandatory gate, and the bundle must be refreshed after each merged milestone.
2. **Owner-bypass risk — accepted zero-cost limitation.** Remote CI is proven,
   but GitHub cannot technically prevent the repository owner from bypassing it
   on the current free private plan. Decision 0022 requires PR-only operation,
   no force-pushes, and explicit SHA/check verification.
3. **Terraform artifact risk — controlled locally.** Generated state, plans,
   local variables, overrides, and crash output are ignored and tested; the gate
   must still run before every push.
4. **Value uncertainty — expected.** One verified aggregate canary proves the
   pipeline, not commercial performance.
5. **Branch auto-deletion risk — closed.** The repository setting was
   disabled on 2026-08-01 after it overrode the PR 4 preserve-branch
   approval; the affected branches were restored at their exact heads, and
   every merge since (PRs 5 through 9) preserved its head branch.
6. **First external side effect — bounded and accepted.** Goal 17 outbound
   Telegram sending is the platform's first authorized external action. It is
   confined to one send client, one bound owner chat, hash-verified bodies,
   an explicit human decision per message, and one-shot execution. Any
   broadening of this surface requires a new approved scope.

## Ordered next gates

1. Prepare weekly canary 1 for the closed 2026-07-31 through 2026-08-06
   reporting window. Run it no earlier than 2026-08-07 and only after the
   normalized Pinterest and Amazon exports for that window are available.
   The canary track preempts Goal 17 work on its gate dates.
2. Complete Goal 17 with the read-only email triage adapter: summarize and
   categorize only, no send, move, label, or delete capability, behind the
   same proposal-only contract as the Telegram channel.
3. Run four weekly, closed, non-overlapping, read-only canaries. Each run gets an
   immutable source reference, independent verification, local backup, iCloud
   copy, checksum proof, and metadata-only review.
4. Make a written value decision after four runs. Paid hosting remains deferred
   unless measured value and an explicit budget justify a fresh plan.

## Weekly canary schedule

| Run | Closed reporting window | Earliest run date | State |
| --- | --- | --- | --- |
| 1 | 2026-07-31 through 2026-08-06 | 2026-08-07 | scheduled |
| 2 | 2026-08-07 through 2026-08-13 | 2026-08-14 | pending |
| 3 | 2026-08-14 through 2026-08-20 | 2026-08-21 | pending |
| 4 | 2026-08-21 through 2026-08-27 | 2026-08-28 | pending |

Each run remains manual, zero-retry, read-only, and side-effect-free. The
project manager must hold if either source window is incomplete or if its
normalized export is unavailable.

## Reporting contract

At the end of every milestone, the project manager reports: outcome, tests,
security findings, commit, remote push and SHA parity, PR/CI state, backup state,
risks or holds, and the next recommended action. A local commit is never called
backed up until remote parity is verified.
