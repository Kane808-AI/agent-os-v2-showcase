# Pinterest Affiliate Delivery Validation

**Status:** Candidate. Research use only. This is not current Pinterest,
affiliate-network, or merchant policy and is not an approved posting procedure.

## Scope

This record supports Northwind affiliate-commerce research. It contains no live
account, affiliate tag, product identifier, credential, schedule, or legacy
runtime instruction.

## Incident-derived lessons

- A scheduler or worker reporting that it ran is not proof that content was
  authenticated, accepted, published, visible, linked correctly, or measured.
- Monitor the full delivery path and alert authentication failure promptly.
  Reconcile the internal queue with live platform state before claiming output.
- Verify that the approved product, source rights, visual asset, description,
  disclosure, call to action, and destination all refer to the same offer.
- Reject placeholders, broken or unapproved links, misleading product imagery,
  missing disclosures, and destination drift.
- Do not evaluate an organic or paid strategy until the delivery pipeline has
  actually operated long enough to produce trustworthy data.
- Measure downstream qualified traffic, conversion, and contribution rather
  than treating pin volume or impressions as business outcomes.

## Promotion checklist

Promotion requires current platform and affiliate-policy review, authenticated
end-to-end delivery evidence, destination health checks, rights and disclosure
approval, attribution validation, failure alerts, and an authorized reviewer.
