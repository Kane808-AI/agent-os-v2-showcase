# Product Boundaries

## Kernel

The kernel answers questions that every installation has:

- Who is acting?
- Which tenant and business does the action concern?
- What happened?
- What objective does this serve?
- Is the action permitted?
- What state must survive a failure?
- What evidence proves completion?
- What should be audited?

The kernel never contains an offer, campaign, chart of accounts, brand voice, or
industry procedure.

## Departments

Departments are optional shared capabilities with explicit inputs, outputs,
tools, metrics, and authority requirements:

- Accounting Controller
- CFO / Finance
- Sales
- Marketing and Paid Acquisition
- Operations
- Customer Success
- Product / Venture Studio
- Research and Intelligence
- Engineering
- QA and Compliance

A department can serve multiple businesses inside one tenant, but every task and
record remains bound to exactly one business unless an explicitly authorized
portfolio report aggregates them.

## Industry packs

Industry packs provide reusable domain behavior. Examples:

- digital agency;
- professional services;
- ecommerce and affiliate;
- local service contractor;
- legal marketing; and
- software venture studio.

Packs may add procedures, metrics, schemas, review rules, and tool adapters.
They may not weaken kernel security or tenant isolation.

## Client configuration

Client configuration supplies:

- business entities;
- objectives and targets;
- chart of accounts and accounting adapter;
- offers and pricing;
- brand voice;
- users and roles;
- connected platforms;
- authority envelopes;
- budget ceilings;
- quiet hours;
- escalation contacts; and
- enabled departments and packs.

Secrets are injected through a credential provider and are never stored in the
configuration repository.

## Reference implementations

Reference implementations prove the product with real operating data. Northwind
is the first reference implementation. A reference pack may depend on core
contracts; core contracts may never depend on a reference pack.
