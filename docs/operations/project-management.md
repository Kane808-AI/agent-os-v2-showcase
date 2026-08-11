# Project management and release-control runbook

This runbook makes the Codex primary agent responsible for moving the build
forward without waiting for the owner to ask what comes next. It does not expand
authority over publishing, spending, account mutation, production activation,
legacy shutdown, or private-data transmission.

## Roles

- **Codex primary agent:** project manager and integrator; maintains the plan,
  requirements, status board, risk register, milestone scope, and final gate.
- **Build-status reviewer:** independently checks the authoritative plan,
  requirement evidence, completion claims, holds, and next gates.
- **Security reviewer:** independently checks secrets, private data, generated
  artifacts, isolation controls, and backup boundaries.
- **Release-test reviewer:** independently runs the default and PostgreSQL test
  gates and reports environment caveats.
- **Owner:** supplies only decisions or authority that cannot be inferred safely,
  including authentication, repository selection, spending, and activation.

## Executive operating mode

The owner is the product president, not the daily implementation coordinator.
The Codex primary agent acts as Technical Lead: it converts accepted goals into
testable tasks, maintains the board and evidence, sequences and delegates the
next reversible work, and replans after each verified result without waiting to
be prompted. It escalates only a material product/scope tradeoff or a decision
requiring new authority (including accounts, spend, publishing, activation,
irreversible change, or private-data transmission).

Every work cycle closes with the verified outcome, remaining risks or holds,
the next one to three recommended tasks, and the minimum decision needed from
the president. The root `AGENTS.md` makes this same contract available to all
interactive coding sessions, including Nimbalyst-managed sessions.

A progress report is not a stopping condition. While safe, reversible,
in-scope work remains, the primary agent reports progress in commentary and
continues the ordered sequence without waiting for another prompt. An approved
intermediate gate resumes that action and every following safe step immediately.
A turn ends only at a president-only decision or authority gate, a
genuine unresolved blocker, or completion of the current work cycle.

The primary agent also manages session boundaries. It proactively recommends a
fresh Nimbalyst session when context pressure could reduce accuracy, after a
coherent milestone checkpoint, when parallel work needs its own worktree, or
when a capability reconnect requires a restart. Before a handoff it updates the
authoritative board and tracker with the current branch/worktree, test and
review evidence, unresolved risks or decisions, and the next concrete task.
Sibling sessions are the default for related work and context escape; isolated
top-level sessions separate unrelated or independently tracked session records.
Worktree choice is independent: use a new worktree when concurrent file or
branch conflicts are possible, because an isolated session otherwise inherits
the caller's working directory. Every replacement session begins from a durable
repository-and-tracker checkpoint rather than chat history alone.

Major goals and production-adjacent milestones use separate build, security, and
test reviewers. The primary agent integrates their evidence and resolves release
blockers; it does not substitute its own confidence for independent results.

## Milestone gate

A milestone is not complete until all applicable items pass:

1. Scope has a requirement ID and the build plan/status board are current.
2. The working tree contains only intended files and `git diff --check` passes.
3. Generated data, credentials, normalized real inputs, backups, Terraform state,
   local variables, and plan files are ignored.
4. A redacted reachable-history secret scan and targeted private-data scan pass.
5. The default unit suite passes; PostgreSQL integration passes for persistence,
   evidence, recovery, or deployment work.
6. The change is committed intentionally on a non-default branch.
7. The canonical private remote is fetched and compared before a non-force push.
8. The pushed remote branch SHA equals local `HEAD`.
9. A draft PR and CI status are recorded when the remote supports them.
10. Database-changing or canary work creates a readable local backup and an
    approved checksum-verified off-device copy.
11. The final report states remaining risks, holds, and the next action.

If remote authentication, repository identity, tests, secret scans, backup, or
SHA parity cannot be proven, the milestone is held and the user is told
immediately. A local commit is recoverable history, but it is not an off-device
source backup.

## Source-backup policy

- Push each completed milestone and before ending a substantive build session.
- Never force-push the canonical backup branch.
- Never guess a missing remote. Identify and verify the private repository first.
- Fetch before push and inspect divergence; unrelated histories require owner
  review instead of overwrite.
- Record branch, local SHA, remote SHA, PR, and CI result in
  `docs/PROJECT_STATUS.md`.
- Keep the iCloud PostgreSQL dump separate from Git source backup; each protects
  a different failure mode.

## Test and security policy

The default release commands are:

```text
python3 -m unittest discover -s tests -v
scripts/test_postgresql_pilot.sh
```

The PostgreSQL driver may be installed only at the pinned project version in a
disposable test environment when the host Python lacks it. Test containers and
temporary environments are removed after use; the live pilot is not a test
target.

Terraform must remain plan-only until separately authorized. Before any future
Terraform command, verify that `.terraform/`, state, local variable files, plan
files, overrides, and crash logs are ignored and absent from Git.

## Weekly pilot cadence

Goal 16 calls for four closed, non-overlapping weekly windows. Runs remain
manual, read-only, zero-retry, and side-effect-free. The project manager prepares
the next window, validates the exact aggregate contract, dispatches independent
verification, runs the canary once, backs up, copies off-device, updates evidence,
commits, pushes, and reports the following window without waiting to be asked.
