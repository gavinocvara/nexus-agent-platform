# NEXUS Project State

## Current Milestone

Phase 2 - Deterministic Failure Injection and Scenario Ground Truth complete.
Phase 3 has not started.

## Repository

- Local path: `C:\Users\arman\source\repos\nexus-agent-platform`
- Remote: `https://github.com/gavinocvara/nexus-agent-platform.git`
- Branch: `main`

## What Exists

- Phase 1 Gateway, Users, Orders, PostgreSQL, Alembic, and Compose lab.
- Five versioned Pydantic-validated scenarios under `lab/scenarios/v1`.
- Ephemeral, lock-protected failure controllers for Users and Orders.
- Disabled-by-default lab routes for list, activate, state, reset, and reset-all.
- Deterministic service unavailable, 1500 ms latency, and database unavailable effects.
- Evaluator CLI and runner with baseline, activation, symptom, reset, and recovery states.
- Strict separation between evaluator ground truth and ordinary operational responses.
- Structured request logs with timestamp, level, service, message, correlation ID,
  method, path, and status code.

## Validation Commands

Run from the repository root:

```powershell
py -m pip install -e ".[dev]"
py -m pip check
py -m ruff check .
py -m mypy
py -m pytest
py -m nexus.lab.scenarios validate
docker compose config
docker compose up --build --detach --wait
$env:RUN_INTEGRATION = "1"
py -m pytest -m integration tests/integration
py -m nexus.lab.scenarios run users_unavailable
py -m nexus.lab.scenarios run users_latency
py -m nexus.lab.scenarios run orders_database_unavailable
docker compose down --volumes
```

## Latest Validation

- Editable installation and `py -m pip check` passed for version 0.3.0.
- `py -m ruff check .` passed.
- `py -m mypy` passed with no issues in 20 source files.
- `py -m pytest` passed 22 tests; 5 Compose integration tests were deselected.
- All 5 version 1 scenario files passed typed catalog validation.
- YAML parsing and `git diff --check` passed.
- GitHub Actions run `35807406416` passed for implementation commit `1e23b3a`:
  - Python install, Ruff, mypy, unit/service tests, and scenario validation passed.
  - Compose configuration, all image builds, startup, and health checks passed.
  - Two normal PostgreSQL paths and three complete incident lifecycles passed.
  - Unavailable, latency, and database symptoms were observed and reset.
  - Correlation preservation and post-reset recovery passed.
  - Compose teardown and volume cleanup passed.

## Safety Boundary

- `NEXUS_LAB_FAILURES_ENABLED` defaults to false.
- Gateway has no lab routes and never proxies the control plane.
- Compose enables controls only because it is explicitly the local incident lab.
- Operational errors contain no scenario IDs, fault configuration, or expected cause.
- Faults cannot execute commands, evaluate expressions, mutate schema, delete files,
  corrupt PostgreSQL, or choose arbitrary delays.

## Known Issues

- Docker is not installed on the current Windows host; the complete Docker gate runs
  on GitHub's Linux runner.
- FastAPI's current `TestClient` emits an upstream Starlette deprecation warning;
  behavior is unaffected and tests pass.
- Ground-truth isolation currently depends on future diagnostic tools not receiving
  repository filesystem or lab-control access; Atlas policy enforcement comes later.
- One active fault per service is intentional for Phase 2; general chaos composition
  is deferred.

## Next Step

Begin Phase 3 with operational observability: retain the current JSON logs, then add
bounded service metrics and distributed traces while preserving evaluator isolation.
