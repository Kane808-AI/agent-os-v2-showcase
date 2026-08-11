# ADR 0005: Structured Agent Constitutions

**Status:** accepted  
**Date:** 2026-07-28

## Context

OpenClaw Legacy mixed shared rules, client incidents, tool instructions, role
guidance, and personality across duplicated Markdown files. Several SOUL files
remained generic templates. Prompt text described permissions that the runtime
did not necessarily enforce. That made drift, stale instructions, and false
assumptions about authority likely.

Agent OS v2 must be resellable, testable, model-independent, and safe to run
autonomously.

## Decision

Define each reusable role with:

- a shared, versioned `AGENTS.md` constitution;
- one machine-readable `CONSTITUTION.json`;
- one role-specific `SOUL.md`;
- evaluation scenarios;
- a tenant assignment separate from the product role; and
- runtime identity, capability, tool, policy, and model-routing gates.

The structured constitution is authoritative for capabilities and boundaries.
SOUL influences judgment and communication but cannot grant authority. Model
provider names are forbidden from constitutions.

## Consequences

- Agent contracts can be linted and regression-tested before activation.
- Tenants can rename and instantiate roles without forking core prompts.
- Tool and policy enforcement remains deterministic and external to the model.
- More files must be versioned, reviewed, and migrated deliberately.
- Legacy agent memory and prompts are sources for selective migration, not
  product configuration.
