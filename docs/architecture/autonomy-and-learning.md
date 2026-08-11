# Autonomy and Learning Contract

## Definition of autonomy

A business is autonomous only when it has:

1. a commercial objective and measurable target;
2. a current baseline and trusted data feeds;
3. an accountable business owner;
4. a ranked experiment backlog;
5. an authority and budget envelope;
6. tools capable of executing the work;
7. verification and attribution;
8. stop, scale, and escalation rules; and
9. a learning loop.

A scheduled report or recurring content task is automation, not autonomy.

## Operating loop

```text
Sense -> Diagnose -> Hypothesize -> Rank -> Authorize
      -> Execute -> Verify -> Measure -> Scale / Revise / Stop -> Learn
```

Atlas runs portfolio selection. Business owners run business loops. Specialists
execute bounded work. QA verifies claims. Finance verifies economic impact.

## Authority decisions

The policy engine returns exactly one mode:

- `auto`: execute without interruption and write an audit record;
- `notify`: execute and notify the accountable human;
- `approve`: hold until an authorized human approves;
- `forbidden`: reject regardless of model recommendation.

Unknown actions default to `forbidden`.

Money movement, credential changes, legal commitments, tax filings, new payment
methods, and changes to core authority policy are forbidden to autonomous model
execution.

## Budget envelopes

An envelope may constrain:

- platform and account;
- action type;
- allowed recipients or audiences;
- maximum amount per action;
- daily and total experiment spend;
- maximum loss;
- expiry;
- minimum evidence before scaling; and
- automatic pause thresholds.

The policy engine calculates compliance. Models cannot reinterpret the limits.

## Self-learning levels

### Level 1: observation

Agents append outcome records with source, context, evidence, and confidence.
This is automatic and immutable.

### Level 2: candidate lesson

Agents may propose a scoped lesson. A candidate is not retrieved as policy or
fact.

### Level 3: evaluated lesson

A candidate is tested against historical evidence, counterexamples, and
relevant evaluations. Failed or unsupported candidates are rejected.

### Level 4: promoted knowledge

Validated facts and strategies enter durable structured memory. Successful
procedures enter version-controlled playbooks with provenance and a rollback
version.

### Level 5: protected changes

Changes to safety policy, permissions, spending ceilings, legal rules,
credential access, evaluation criteria, or production code require human
approval and regression testing.

## Anti-contamination rules

- External content is untrusted evidence, never instruction.
- Inferences are labeled and cannot be retrieved as verified facts.
- Lessons are scoped by tenant, business, task type, channel, and date.
- Absolute rules require explicit human promotion.
- Memory is updated by delta; existing knowledge is never silently rewritten.
- Rare safety rules cannot disappear through summarization.
- Every promoted procedure can be traced to evidence and reverted.
