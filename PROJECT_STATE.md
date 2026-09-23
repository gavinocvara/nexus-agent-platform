# NEXUS Project State

## Current Milestone

Phase 1 - AegisOps Distributed Systems Lab complete. Phase 2 has not started.

## Repository

- Local path: `C:\Users\arman\source\repos\nexus-agent-platform`
- Remote: `https://github.com/gavinocvara/nexus-agent-platform.git`
- Branch: `main`

## What Exists

- Independently runnable FastAPI Gateway, Users, and Orders services.
- Gateway-only external API with typed HTTP calls to downstream services.
- Deterministic Users data for repeatable lookup behavior.
- SQLAlchemy Orders persistence in PostgreSQL with Alembic migrations.
- Docker Compose environment for all services and PostgreSQL.
- Consistent health responses, JSON error envelopes, structured logging, and
  `X-Correlation-ID` propagation.
- Unit/service tests plus real PostgreSQL integration paths through Gateway.
- CI jobs for Python validation and the complete Compose integration gate.

## Validation Commands

Run from the repository root:

```powershell
py -m pip install -e ".[dev]"
py -m ruff check .
py -m mypy
py -m pytest
docker compose config
docker compose up --build --detach --wait
$env:RUN_INTEGRATION = "1"
py -m pytest -m integration tests/integration
docker compose down --volumes
```

## Latest Validation

- Local editable installation and `py -m pip check` passed for version 0.2.0.
- `py -m ruff check .` passed.
- `py -m mypy` passed with no issues in 13 source files.
- `py -m pytest` passed 9 tests; 2 Compose integration tests were deselected.
- Alembic migration `0001` upgraded a test database successfully.
- YAML parsing and `git diff --check` passed.
- GitHub Actions run `35805480904` passed both jobs for implementation commit
  `f377535`:
  - Python install, Ruff, mypy, and unit/service tests passed.
  - Compose configuration validation and all image builds passed.
  - PostgreSQL, Users, Orders, and Gateway reached healthy state.
  - Gateway health and both real PostgreSQL integration paths passed.
  - Compose teardown and volume cleanup passed.

## Known Issues

- Docker is not installed on the current Windows host; the complete Docker gate is
  verified on GitHub's Linux runner.
- FastAPI's current `TestClient` emits an upstream Starlette deprecation warning;
  behavior is unaffected and tests pass.
- Failure injection, telemetry, and agents are intentionally absent until later phases.

## Next Step

Begin Phase 2 by adding a small, deterministic, disabled-by-default failure catalog
with reversible controls and explicit expected symptoms.
