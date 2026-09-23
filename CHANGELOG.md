# Changelog

All meaningful changes to NEXUS are recorded here.

## Unreleased

### Added

- A single `aegisops.investigator` using the OpenAI Agents SDK with exactly the
  eleven Phase 4 read-only diagnostic tools and no handoffs, memory, or write access.
- Structured diagnoses, stable tool-call provenance, a separate evidence ledger,
  hard tool/turn/time limits, typed run failures, and auditable run records.
- A five-scenario evaluation harness with generic agent input, deterministic scoring,
  mandatory reset/recovery, reproducibility metadata, and ignored local reports.
- Scripted no-key tests for policy intersection, schemas, provenance, limits,
  evaluator isolation, prompt injection, unsafe requests, scoring, and recovery.
- A single-investigator ADR and operator/evaluation runbook.

- A typed read-only diagnostics package with fixed health, Prometheus, Loki, and
  Tempo adapters.
- High-level service inventory, health, request/dependency summaries, database
  health, structured log search, correlation evidence, trace lookup, and recent
  error tools.
- A deterministic tool registry, explicit investigator allow/deny policy,
  diagnostic session call counting, and evidence-free audit records.
- Strict service, dependency, time-window, result-limit, correlation-ID, and trace-ID
  input contracts with no raw-query or arbitrary-URL fields.
- Diagnostic CLI, adapter/security unit tests, real-stack integration tests, and a
  five-scenario evidence sufficiency evaluation.
- A typed diagnostic boundary ADR and operator runbook.
- Prometheus request, dependency, latency, in-flight, and database-health metrics
  with bounded labels and normalized route templates.
- OpenTelemetry request, HTTPX, and SQLAlchemy tracing through an OpenTelemetry
  Collector to Tempo.
- Loki log aggregation through Grafana Alloy and a provisioned Grafana overview
  dashboard with Prometheus, Loki, and Tempo data sources.
- Telemetry isolation, cardinality, correlation, graceful-failure, and incident
  integration tests.
- An operational observability ADR and local runbook.
- Versioned, typed ground-truth definitions for five deterministic failure scenarios.
- Ephemeral concurrency-safe failure controllers for Users and Orders.
- Disabled-by-default lab control APIs with bounded activation and idempotent reset.
- Evaluator CLI and lifecycle runner for baseline, activation, symptom, reset, and
  recovery verification.
- Unit, service, and Compose incident-lifecycle tests.
- Three independently runnable FastAPI services for Gateway, Users, and Orders.
- PostgreSQL order persistence with SQLAlchemy and an explicit Alembic migration.
- Docker Compose runtime with service and dependency health checks.
- Typed shared HTTP contracts, correlation ID propagation, structured JSON logging,
  and consistent error envelopes.
- Unit/service tests and genuine PostgreSQL integration paths through the Gateway.
- CI validation for static checks, unit tests, Docker builds, healthy Compose startup,
  and integration tests.

### Changed

- Advanced the package to version 0.6.0 for the Phase 5 investigator baseline.
- Diagnostic results and audit events now share a stable `tool_call_id`; complete
  evidence remains separate from audit metadata.

- Advanced the package to version 0.5.0 for Phase 4 read-only diagnostics.
- Service error codes are recorded as ordinary `error.type` trace evidence; trace
  normalization redacts SQL-shaped operation names and non-allowlisted attributes.
- Advanced the package to version 0.4.0 for Phase 3 operational observability.
- Structured request logs include request duration and real OpenTelemetry trace and
  span identifiers when an active span exists.
- Advanced the package to version 0.3.0 for the Phase 2 controlled incident lab.
- Structured request logs now include method, path, and status code fields already
  supplied by the request middleware.
