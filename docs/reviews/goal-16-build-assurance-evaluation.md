# Goal 16 build-assurance evaluation

**Date:** 2026-08-01

**Tracker:** AGE-2

**Scope:** Read-only evaluation of a minimal, pinned developer and CI assurance
stack. No production service, live pilot container, private runtime data,
credential, cloud project, or external account was scanned or changed.

## Baseline

- Branch/worktree: `codex/goal-16-build-assurance` in the isolated AGE-2
  worktree, created from canonical `origin/main` at `c185fd1`.
- Default suite: 255 discovered, 250 passed, and the 5 PostgreSQL cases skipped
  by design because no isolated test DSN was supplied.
- `git diff --check`: clean before implementation.
- Existing assurance: checksum-pinned Gitleaks over complete reachable history,
  digest-pinned PostgreSQL CI service, and full-SHA GitHub Actions.

## Candidate decisions

| Candidate | Reviewed release and license | Measured baseline | Decision |
| --- | --- | --- | --- |
| `pip-audit` | [2.10.1](https://github.com/pypa/pip-audit/releases/tag/v2.10.1), Apache-2.0; direct wheel SHA-256 `99ef3f600a317c1945f1e89e227ef26e1c2d618429b8bd3fa6f4f7c440c4611a` | A disposable, offline-installed target containing only `psycopg==3.3.4` and `psycopg-binary==3.3.4` returned no known vulnerabilities from the PyPI vulnerability service | Adopt for the next CI slice after every assurance dependency is exact-version and hash locked. Never use `--fix`; audit an already installed disposable target so project resolution cannot execute during the scan. |
| Ruff | [0.16.1](https://github.com/astral-sh/ruff/releases/tag/0.16.1), MIT; macOS ARM64 archive SHA-256 `a8df4e8e9f22e3b0ae0b9f165ddaafb7e34df692197a6c1a361e7426f90681d5` | Expanded defaults reported 261 findings; explicit `E9,F` reported 6 `F401` findings; format check reported 37 of 40 files would change | Adopt only as a staged, explicit-rule, non-blocking linter after the six core findings are triaged. Defer formatting and all autofix to avoid unrelated churn. Never rely on the expanded default rule set. |
| Hypothesis | 6.164.0 cutoff, MPL-2.0; CPython 3.12 manylinux wheel SHA-256 `7fca6632933fc506dd96926d9383483e4c0066c7ff62c748d059a3276da761e7` | No current test imports Hypothesis. The existing deterministic suite already covers 255 examples but not generated invariants | Defer broad adoption. A later test-only pilot may cover authority-rule ordering, aggregate funnel bounds, and exact money conversion with deterministic settings, no example database, and no database or live-service targets. |
| Hadolint | [2.15.1](https://github.com/hadolint/hadolint/releases/tag/v2.15.1), GPL-3.0; Linux x86-64 binary SHA-256 `c7187db94eeeeca956519a6af171adc31453941a1e777961f6e680f697c8c507` | Checksum-verified macOS ARM64 binary returned zero findings for `deployment/container/Dockerfile.pilot` | Adopt now as a checksum-pinned blocking CI gate at warning severity. The binary is an ephemeral CI tool and is not copied into or distributed with the product image. |
| Trivy | [0.72.0](https://github.com/aquasecurity/trivy/releases/tag/v0.72.0), Apache-2.0; Linux x86-64 archive SHA-256 `bbb64b9695866ce4a7a8f5c9592002c5961cab378577fa3f8a040df362b9b2ea` | Not executed: the sandbox could not access the Docker daemon, and local filesystem scanning could traverse ignored private runtime paths | Defer to a clean-checkout, ephemeral CI image experiment. The March 2026 [upstream compromise](https://github.com/aquasecurity/trivy/security/advisories/GHSA-69fq-xp46-6x23) makes checksum plus Sigstore/attestation verification mandatory. Never scan the live local image, volumes, `data/`, or private runtime paths. |
| TFLint | [0.64.0](https://github.com/terraform-linters/tflint/releases/tag/v0.64.0), MPL-2.0 with BUSL-1.1-covered Terraform-derived code; Linux x86-64 archive SHA-256 `cca9d13e2e1d7a2c627af60ff899a3c9b74212899416aeb96ec764d2ef954537` | Checksum-verified base linter returned zero findings with no provider plugin initialization | Adopt only when Terraform work resumes. Pair it with pinned native `terraform fmt -check` and an isolated `terraform init -backend=false`/`validate` gate; no plan or apply is authorized. |

All executable candidates were confined to a disposable directory. The local
baseline used exact release artifacts whose computed SHA-256 values matched
the upstream release metadata. Future Python assurance tooling must use a
binary-only lock with hashes for every direct and transitive wheel; a direct
tool pin by itself is insufficient.

## Superpowers upgrade hold

Superpowers remains held for execution. Upstream
[v6.2.0](https://github.com/obra/superpowers/releases/tag/v6.2.0) has no release
assets or checksums, and both its annotated tag and target commit are unsigned.
The curated-plugin install prompt was accepted during AGE-2, but the current
runtime immediately still reported `superpowers@openai-curated` as not
installed. A fresh Nimbalyst session must verify the installed version, hash
the complete installed cache, compare it with the curated source, and review
all executable hooks and scripts before any Superpowers skill is invoked.

The reviewed upstream release includes plan-workspace deletion after a clean
review and executable packaging/synchronization helpers. Those paths must not
receive repository, environment, keychain, or cleanup authority merely because
the plugin appears in a curated marketplace.

## Adopted gate and rollback

The only gate added in this slice is Hadolint. CI downloads the exact standalone
Linux binary, verifies its hard-coded SHA-256, marks it owner-executable, and
lints the one tracked pilot Dockerfile at warning severity. It does not use a
third-party GitHub Action, mutable tag, container registry, or private input.

Rollback removes the two Hadolint workflow steps and their corresponding
project-control assertions in one reviewed change. The same change must also
reconcile `AOS-CI-002` status and evidence, this evaluation's adoption decision,
and the current project board and next action so no authoritative source keeps
claiming a removed gate. A rollback requires an evidence-backed tool failure or
unacceptable false-positive rate; it must not silently weaken the existing
unit, PostgreSQL, or secret gates.
