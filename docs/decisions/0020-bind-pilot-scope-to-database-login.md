# 0020: Bind production-pilot scope to the database login

**Status:** Accepted

**Date:** 2026-07-31

## Context

PostgreSQL row-level security based only on application-set session variables
is not an isolation boundary. A compromised runtime connection could set those
variables to another tenant or business before issuing a query.

## Decision

An administrator-owned `pilot_runtime_bindings` row binds each PostgreSQL
login to one tenant and business. SECURITY DEFINER functions derive scope from
`session_user`. The runtime role cannot read or modify the binding table and
receives only SELECT/INSERT privileges on the bounded pilot tables. The adapter
also verifies that its requested scope equals the database-derived binding on
every transaction. All scoped tables use forced RLS and database triggers
enforce actor scope, independent verification, ordered cutover state, and
append-only records.

Schema migration and onboarding remain separate administrative operations.
The application cannot select an arbitrary scope, disable RLS, mutate committed
evidence, or obtain schema-control privileges.

## Consequences

Each isolated tenant/business runtime needs a dedicated login. Changing scope
is a controlled re-onboarding operation, not an application configuration
change. The tradeoff is additional role administration in exchange for a scope
boundary that does not trust application-controlled connection state.
