# NEXUS Project State

## Current Milestone

Phase 3 - Operational Observability complete.
Phase 4 has not started.

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
  method, request path, status code, duration, and active trace/span identifiers.
- Per-service Prometheus registries with bounded HTTP, dependency, latency,
  in-flight request, and PostgreSQL health metrics.
- W3C distributed tracing across service requests, HTTPX dependencies, and
  SQLAlchemy through an OpenTelemetry Collector into Tempo.
- Grafana Alloy stdout collection into Loki.
- Provisioned Prometheus, Loki, and Tempo Grafana data sources with an AegisOps
  overview dashboard.
- Telemetry tests for bounded cardinality, normalized routes, graceful exporter
  failure, cross-signal request correlation, and evaluator ground-truth isolation.
- Operational observability architecture record and local runbook.

## Validation Commands

Run from the repository root:

```powershell
py -m pip install -e ".[dev]"
py -m pip check
py -m ruff format --check .
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

- Editable installation and `py -m pip check` passed for version 0.4.0.
- `py -m ruff format --check .` and `py -m ruff check .` passed.
- `py -m mypy` passed with no issues in 24 source files.
- `py -m pytest` passed 30 tests; 11 Compose integration tests were deselected.
- All 5 version 1 scenario files passed typed catalog validation.
- Observability YAML/JSON parsing and `git diff --check` passed.
- GitHub Actions run `35809978006` passed for commit `c101f07`:
  - Python install, Ruff, mypy, 30 unit/service tests, and scenario validation passed.
  - Compose validation, image builds, startup, and all service health checks passed.
  - Prometheus, Loki, Tempo, and Grafana readiness checks passed.
  - All 11 PostgreSQL, incident, telemetry, Grafana, and isolation integration tests
    passed.
  - Three representative incident lifecycles produced operational evidence, reset,
    and recovered.
  - Compose teardown and volume cleanup passed.

## Safety Boundary

- `NEXUS_LAB_FAILURES_ENABLED` defaults to false.
- Gateway has no lab routes and never proxies the control plane.
- Compose enables controls only because it is explicitly the local incident lab.
- Operational errors contain no scenario IDs, fault configuration, or expected cause.
- Faults cannot execute commands, evaluate expressions, mutate schema, delete files,
  corrupt PostgreSQL, or choose arbitrary delays.
- Metrics and tracing default to disabled and are enabled explicitly by Compose.
- Metrics use normalized route templates and bounded labels; request/resource IDs and
  evaluator fields are excluded.
- Ordinary responses, Prometheus samples, Loki logs, and Tempo traces are audited for
  scenario identifiers, expected causes, and evaluator-only fields.
- Trace export is asynchronous and an unavailable collector cannot fail requests.

## Known Issues

- Docker is not installed on the current Windows host; the complete Docker gate runs
  on GitHub's Linux runner.
- FastAPI's current `TestClient` emits an upstream Starlette deprecation warning;
  behavior is unaffected and tests pass.
- Ground-truth isolation still depends on future diagnostic tools not receiving
  repository filesystem or lab-control access; Atlas policy enforcement comes later.
- One active fault per service is intentional for Phase 2; general chaos composition
  is deferred.
- Grafana anonymous access, exposed observability ports, short retention, and Alloy's
  read-only Docker socket mount are local-lab choices, not production hardening.
- Alerting, production retention, authentication, and observability high availability
  are deferred.

## Next Step

Begin Phase 4 with narrow, typed, read-only diagnostic tools over operational signals.
Do not introduce an autonomous investigator agent before the tool boundary, access
policy, and evaluator-isolation controls are explicitly defined and validated.
