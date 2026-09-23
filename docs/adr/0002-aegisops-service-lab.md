# ADR 0002: AegisOps Service Lab Boundaries and Persistence

## Status

Accepted

## Context

Phase 1 needs the smallest real distributed application that future AegisOps
agents can observe and diagnose. It requires independently runnable Gateway,
Users, and Orders services, with durable Orders data in PostgreSQL.

## Decision

Keep all three FastAPI services in the existing typed `nexus` package while giving
each service its own app factory and process. This avoids premature independently
versioned packages while preserving runtime boundaries.

The Gateway communicates with downstream services only through HTTP using
`httpx`. It does not import service repositories or access PostgreSQL. Users keeps
deterministic in-process data because Phase 1 does not require another database.
Orders owns a SQLAlchemy 2.0 repository and PostgreSQL schema.

Use Alembic for explicit, forward-versioned schema changes. The Orders Compose
command applies migrations after PostgreSQL becomes healthy and before Uvicorn
starts. Orders also verifies connectivity during application startup and includes
database state in its health response. A lost database changes Orders health to
HTTP 503 without conflating process health with dependency health.

Use a shared HTTP foundation for typed contracts, JSON error envelopes, structured
logging, and `X-Correlation-ID` propagation. Full tracing is deliberately deferred.

## Consequences

- Services are independently runnable but share one build artifact for now.
- HTTP boundaries are real and testable without duplicating business logic.
- PostgreSQL migrations are explicit and reversible.
- Unit tests remain fast with temporary SQLite databases; Compose integration
  tests verify the same SQLAlchemy repository against real PostgreSQL.
- A future split into separately versioned packages remains possible if deployment
  or ownership boundaries justify it.
