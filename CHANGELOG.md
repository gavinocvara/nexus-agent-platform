# Changelog

All meaningful changes to NEXUS are recorded here.

## Unreleased

### Added

- Three independently runnable FastAPI services for Gateway, Users, and Orders.
- PostgreSQL order persistence with SQLAlchemy and an explicit Alembic migration.
- Docker Compose runtime with service and dependency health checks.
- Typed shared HTTP contracts, correlation ID propagation, structured JSON logging,
  and consistent error envelopes.
- Unit/service tests and genuine PostgreSQL integration paths through the Gateway.
- CI validation for static checks, unit tests, Docker builds, healthy Compose startup,
  and integration tests.

### Changed

- Advanced the package to version 0.2.0 for the Phase 1 AegisOps lab.
