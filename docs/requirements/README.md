# Requirements and Traceability

`registry.json` is the machine-readable master requirements registry for Agent
OS v2. It prevents broad ideas, architecture decisions, implementation, and
verification from being mistaken for the same thing.

## Requirement status

- `captured`: accepted requirement with no binding design yet.
- `designed`: binding architecture or decision exists.
- `implemented`: implementation exists but verification is incomplete.
- `verified`: acceptance evidence passes.
- `deferred`: accepted but intentionally outside the active release.
- `retired`: no longer applicable, with a superseding requirement recorded.

Each requirement must have one owner goal. `evidence` contains repository paths
to tests or binding documents. A document alone can establish `designed`; only
executable evidence can establish `verified` for runtime behavior.

The product status and requirement status are intentionally distinct. A build
goal is not complete until all of its required exit criteria are verified.
