# NEXUS Project State

## Current Milestone

Phase 5 - Single AegisOps Investigator and Evaluation Baseline complete. No Phase 6
implementation has started.

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
- One `aegisops.investigator` implemented through a narrow OpenAI Agents SDK adapter.
- Exact eleven-tool SDK surface validated against the Phase 4 registry and policy;
  construction fails closed on drift, writable metadata, or unexpected risk.
- Structured diagnosis contracts for diagnosed, insufficient-evidence, and diagnostic-
  backend-failure outcomes, with bounded hypotheses and exact evidence references.
- Stable tool-call IDs shared by results and audit metadata, with full evidence retained
  separately in a defensive in-memory session ledger.
- Typed agent settings, opt-in live execution, hard diagnostic-call/turn/time limits,
  explicit run failure states, and run records with nullable model usage.
- Five-scenario evaluator that exposes only a generic prompt to the agent, verifies
  evidence values, scores safety and efficiency, and resets in a mandatory `finally`.
- Reproducibility metadata and ignored local evaluation output under `.nexus/`.
- Scripted no-key validation for policy, schema, provenance, limits, evaluator
  isolation, prompt injection, unsafe verbal actions, scoring, and recovery.
- Single-investigator architecture record and operator/evaluation runbook.

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
py -m nexus.aegisops investigate
py -m nexus.evaluation.aegisops --runs 1
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

- Editable installation and `py -m pip check` passed for version 0.6.0.
- `py -m ruff format --check .` and `py -m ruff check .` passed for 89 files.
- `py -m mypy` passed with no issues in 51 source files.
- `py -m pytest` passed 65 tests; 16 Compose integration tests were deselected.
- All 5 version 1 scenario files passed typed catalog validation.
- Deterministic Phase 5 tests exercised all five scenarios with scripted engines and
  no API key; these are wiring/safety results, not a live model benchmark.
- Whitespace, tracked-file secret-pattern, package metadata, CLI-disabled-state, and
  governing-file integrity checks passed.
- GitHub Actions run `35833758753` passed for Phase 5 commit `d6132bd`:
  - The `validate` job installed version 0.6.0, passed Ruff, strict mypy, all 65
    unit/service tests, the focused deterministic investigator suite, and catalog
    validation.
  - The `compose-integration` job validated and built Compose, started the complete
    service and observability stack, and passed all 16 PostgreSQL, incident,
    observability, diagnostic, sufficiency, and isolation integration tests.
  - Diagnostic CLI checks, three representative incident/recovery demonstrations,
    service-state inspection, and unconditional Compose teardown passed.

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
- Agent construction exposes only the exact registry/policy intersection and rejects
  capability drift before model execution.
- The agent context contains only diagnostics, its session, and a run ID; it has no
  evaluator, scenario, expected answer, credentials, or ambient access object.
- Telemetry instructions are untrusted data. The agent is instructed to gather and
  challenge evidence, abstain when needed, omit hidden reasoning, and never remediate.
- Evidence claims are checked against exact values in recorded diagnostic results.
- Model execution is disabled by default; API credentials are read only from the
  process environment and are never placed in agent context or run records.
- Agents SDK tracing is off by default and, if enabled, excludes sensitive content.

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
- No live model evaluation has been run in this phase on the current host, so NEXUS
  makes no investigator accuracy, latency, token, or cost benchmark claim yet.
- Deterministic unsafe-output detection is lexical and supplements, rather than
  replaces, future model-based or policy-based safety evaluation.
- The Phase 5 evaluator writes one complete report after all runs; durable incremental
  run history and trend comparisons remain Phase 6 work.

## Next Step

Begin Phase 6 by running and reviewing an explicit live-model baseline, then add
versioned benchmark history and regression comparison around the existing evaluator.
Keep the single-agent, read-only boundary; do not add remediation, multiple agents,
Atlas runtime, MCP, or memory until evidence from the baseline justifies the next
architectural step.
