# ADR 0005: Typed Read-Only Diagnostic Boundary

## Status

Accepted

## Context

A future AegisOps investigator needs operational evidence but must not inherit the
ambient capabilities of the developer or evaluator. Direct access to URLs, query
languages, files, environment variables, SQL, Docker, Grafana, lab controls, or the
scenario catalog would make least privilege and benchmark isolation unverifiable.
Telemetry itself is untrusted and may contain instruction-shaped text.

## Decision

Expose a framework-neutral `DiagnosticServiceLayer` whose public operations accept
closed Pydantic schemas. Service names, dependency edges, time windows, log filters,
limits, correlation IDs, and trace IDs are validated before a backend call. Unknown
fields are rejected. No public schema contains a URL, path, PromQL, LogQL, SQL, shell,
filesystem, Docker, Grafana, or general trace-search field.

Use separate adapters for ordinary service health, Prometheus, Loki, and Tempo.
Operator-owned settings define fixed base URLs. Adapter methods own every endpoint
path and construct backend queries from enums and allowlisted fields. Calls have
bounded timeouts and response sizes; log and trace result counts are bounded.
Backend outages and malformed responses become typed unsuccessful results rather
than exceptions escaping the diagnostic runtime.

Prometheus operations return only predefined service and dependency aggregates over
`1m`, `5m`, `15m`, or `30m`. Loki operations expose approved structured fields and
exact filters. Tempo retrieval requires an exact trace ID and normalizes spans into
safe fields. Span attributes use an explicit allowlist, and database span names are
reduced to an operation class so SQL statements and connection details cannot escape.

Define a deterministic tool registry with descriptions, input JSON schemas,
read-only declarations, backend sources, and low-risk classification. Define the
`aegisops.investigator` precursor policy with the registry tools as its allowlist and
an explicit deny list for evaluator, ambient, write, and raw-query capabilities.
Atlas will later enforce this contract; Phase 4 does not implement Atlas.

Every tool call appends an audit record containing its session, tool, timestamp,
normalized arguments, outcome, duration, backend, and result count. Returned evidence
is not duplicated into audit events. A session tracks call count without yet imposing
an agent or token budget.

Logs, span names, and selected attributes remain untrusted data. The diagnostic layer
does not execute commands, follow URLs, run queries, interpret instructions, or alter
policy based on telemetry content.

Evaluator code remains separate. Production diagnostics do not import the scenario
catalog, failure controller, evaluator runner, or ground-truth schemas. Evaluator-side
tests may activate incidents and assess whether permitted evidence is sufficient, but
the diagnostic layer receives no scenario identity or answer key.

Grafana is excluded from the tool layer. It remains a human interface over the same
underlying data sources and adds credentials and API coupling without improving the
typed investigator contract.

## Consequences

- The future investigator receives a small, testable capability set rather than
  ambient developer access.
- Query cost, context size, and cardinality remain predictable.
- Correlation-to-trace workflows are possible without unrestricted search.
- Backend-specific payloads cannot become the long-term agent contract.
- The in-memory audit/session foundation is not yet durable policy enforcement;
  Atlas, authenticated identities, persistent audit storage, and budgets remain later
  milestones.

Phase 5 subsequently adds per-call IDs, a separate in-memory evidence ledger, and a
session call ceiling as described by ADR 0006. Durable policy enforcement remains an
Atlas concern.
