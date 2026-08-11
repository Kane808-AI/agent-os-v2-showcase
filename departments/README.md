# Departments

Departments are reusable capability modules. Each department must define:

- charter;
- inputs and outputs;
- supported tools and adapters;
- business metrics;
- authority requirements;
- prohibited actions;
- verification procedure;
- evaluation cases; and
- activation and shutdown criteria.

The accepted Goal 13 department and revenue-stream set is:

- `marketing/`: digital marketing and consulting, including SEO/GEO;
- `media/`: YouTube performance and content proposals;
- `product/`: application opportunities and release readiness;
- `physical-products/`: demand validation and listing proposals;
- `commerce/`: broader offer, affiliate, and merchandising analysis;
- `finance/` and `accounting/`: separate read-only financial functions;
- `sales/`, `operations/`, and `customer-success/`;
- `research/`, `engineering/`, and `qa/`.

`capability-pack-policy.json` defines the shared versioned shadow boundary and
the exact required portfolio. Each module has a `capability-pack.json` with
owners, independent verifier, objective metrics, read-only sources, evidence,
outputs, allowed non-executing modes, prohibited actions, and happy/boundary
fixtures. Deterministic acceptance is recorded by migration 13. No pack
activation, credentials, schedule, publisher, contact adapter, spend, or
production write is included.
