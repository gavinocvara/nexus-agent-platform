# ADR 0004: Operational Observability

## Status

Accepted

## Context

The AegisOps lab needs enough ordinary production-style evidence to diagnose its
deterministic incidents before an investigator agent is introduced. Telemetry must
remain useful under failure, correlate service boundaries, avoid unbounded metric
cardinality, and reveal no evaluator ground truth.

## Decision

Use three complementary signals behind one provisioned Grafana interface.

Services expose Prometheus metrics from isolated registries. HTTP series are labeled
only by service, method, normalized route template, and status class. Dependency
series use service, a fixed dependency name, and status class. Histograms use fixed
buckets. Correlation IDs, resource IDs, raw paths, exception messages, evaluator
fields, and scenario identifiers are not labels.

Services continue to write structured JSON to stdout. Request logs retain the
application correlation ID and duration. When an OpenTelemetry span is active, the
formatter adds its real trace and span identifiers; it does not manufacture values.
Grafana Alloy discovers local Compose containers through a read-only Docker socket
and forwards their stdout to Loki with bounded service, container, and environment
labels.

Services create server spans with normalized routes, propagate W3C trace context,
and instrument HTTPX and SQLAlchemy. They export OTLP/gRPC to an OpenTelemetry
Collector, which is the only component that forwards spans to Tempo. Export is
asynchronous and collector failure cannot fail an application request.

Grafana provisions Prometheus, Loki, and Tempo plus one AegisOps overview dashboard.
The Compose topology deliberately enables telemetry; application defaults remain
disabled so importing or testing a service creates no network dependency.

Evaluator ground-truth files, scenario IDs, injected settings, expected root causes,
and evaluator output are excluded from ordinary responses and telemetry. Integration
tests audit metrics, Loki records, and Tempo traces for prohibited terms.

## Consequences

- A request can be followed across metrics, logs, Gateway and service spans, and
  PostgreSQL work using operational evidence.
- Incident symptoms remain visible when dependencies fail or become slow.
- Route normalization and bounded labels constrain Prometheus and Loki cardinality.
- The Collector decouples application instrumentation from the tracing backend.
- The local stack is intentionally not production-hardened: Grafana allows anonymous
  viewing, observability ports are exposed, retention is short, and Alloy can read
  the Docker socket. Production authentication, isolation, retention, and alerting
  remain future deployment concerns.
