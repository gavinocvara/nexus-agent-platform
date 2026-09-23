# NEXUS Project State

## Current Milestone

Phase 4 - Typed Read-Only Diagnostic Tool Layer and Investigator Access Boundary
complete. Phase 5 has not started.

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
- Typed diagnostic service with fixed health, Prometheus, Loki, and Tempo adapters.
- Eleven high-level operations for topology, health, request/dependency metrics,
  database health, structured logs, correlation evidence, and exact trace retrieval.
- Closed Pydantic input/result contracts with registered services, dependency edges,
  four bounded windows, validated identifiers, and bounded result limits.
- Deterministic read-only tool registry and `aegisops.investigator` precursor policy.
- In-memory diagnostic sessions with tool-call counts and evidence-free audit records.
- Developer CLI exercising exactly the same service layer intended for a future agent.
- Unit and real-stack tests for adapters, raw-query prohibition, ambient-access
  isolation, prompt-injection data handling, incident evidence, and recovery.
- Evaluator-side proof that permitted evidence yields five distinct fingerprints for
  the five existing scenarios without scenario knowledge in production diagnostics.
- Typed diagnostic boundary architecture record and operator runbook.

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
py -m nexus.diagnostics services
py -m nexus.diagnostics system-health
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

- Editable installation and `py -m pip check` passed for version 0.5.0.
- `py -m ruff format --check .` and `py -m ruff check .` passed for 70 files.
- `py -m mypy` passed with no issues in 37 source files.
- `py -m pytest` passed 50 tests; 16 Compose integration tests were deselected.
- All 5 version 1 scenario files passed typed catalog validation.
- CLI inventory serialization, whitespace checks, secret scan, and governing-file
  comparison passed.
- GitHub Actions run `35830240997` passed for commit `6158657`:
  - Python install, Ruff, mypy, 50 unit/service tests, and scenario validation passed.
  - Compose validation, builds, startup, health, and observability readiness passed.
  - All 16 PostgreSQL, incident, observability, diagnostic, sufficiency, and isolation
    integration tests passed.
  - Diagnostic CLI topology, health, metrics, dependency, and log commands passed.
  - Five scenario evidence fingerprints were distinct using only permitted results.
  - Three representative incidents exposed required evidence, reset, and recovered.
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
- Diagnostic callers cannot supply URLs, paths, PromQL, LogQL, trace searches, SQL,
  shell commands, filesystem paths, Docker operations, or Grafana credentials.
- Production diagnostics have no imports or capability path to lab controls, scenario
  files, evaluator schemas, or expected answers.
- Adapter timeouts, response sizes, windows, identifiers, and result counts are
  bounded; backend failures become typed unsuccessful results.
- Trace attributes are allowlisted and SQL-shaped span names are normalized. Log and
  trace text is explicitly untrusted data and is never executed or interpreted.
- Audit events contain normalized arguments and execution metadata, not evidence or
  backend payloads.

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
- Prometheus rates and percentiles can be `null` for a newly created series until
  enough scrapes exist; cumulative counts remain available for immediate evidence.
- Diagnostic sessions and audit records are in-memory and single-process. Persistent
  identity, policy enforcement, durable audit storage, and budgets belong to Atlas.
- The diagnostic CLI is a developer interface, not an authenticated network service.

## Next Step

Begin Phase 5 with one AegisOps investigator agent behind the existing diagnostic
service and policy. Give it no ambient tools, require structured hypotheses with
supporting and conflicting evidence, retain diagnostic audit records, and evaluate it
against the five scenarios with accuracy, tool-call count, latency, and unsafe-output
measures. Do not add remediation, multiple agents, Atlas runtime, MCP, or memory yet.
