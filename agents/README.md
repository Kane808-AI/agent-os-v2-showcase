# Agent Organization

This directory defines reusable Agent OS role archetypes. It does not contain
live credentials, model names, client account IDs, or runtime session state.

Each role has two complementary files:

- `CONSTITUTION.json` is the machine-readable authority and capability contract.
- `SOUL.md` is the role-specific judgment, temperament, and communication
  guidance presented to a model.

`AGENTS.md` contains the non-negotiable operating constitution shared by every
role. `registry.json` is the authoritative role inventory. The runtime composes
the shared constitution, one role constitution, and one role SOUL for each
ephemeral invocation.

## Product versus tenant configuration

Role names are stable product identifiers such as `business-owner` and
`accounting-controller`. A tenant assignment supplies the actor ID, display
name, business scope, enabled state, and specific capabilities. For example,
several businesses can each instantiate a different actor from the same
`business-owner` constitution.

Northwind assignments live in `packs/northwind/agent-assignments.json`. They are a
reference deployment, not kernel defaults.

## Activation

The registry labels roles as:

- `core`: required for any autonomous deployment;
- `department`: enabled when that department is installed; or
- `specialist`: enabled only when a business capability needs it.

Defining a role does not activate an agent or grant a tool. Runtime identity,
tenant scope, capability assignment, tool adapters, authority envelopes, and
model routing are independent gates.

## Protected changes

Agents may propose lessons about how they work. They cannot edit or promote
changes to `AGENTS.md`, their constitution, SOUL, evaluations, tool grants,
authority, or model policy. Those are version-controlled product changes.
