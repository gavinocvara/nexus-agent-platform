# Changelog

All meaningful changes to NEXUS are recorded here.

## Unreleased

### Added

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

- Advanced the package to version 0.3.0 for the Phase 2 controlled incident lab.
- Structured request logs now include method, path, and status code fields already
  supplied by the request middleware.
