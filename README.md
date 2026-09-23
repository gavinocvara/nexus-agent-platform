# NEXUS Agent Platform

NEXUS is a local-first engineering platform for auditable agent workflows. Phase 1
provides the AegisOps distributed-systems lab that later diagnostic agents will
observe, disrupt, repair, and evaluate. Phase 2 turns it into a deterministic
incident laboratory with isolated evaluator ground truth. Phase 3 adds operational
metrics, logs, and distributed traces without exposing those answers.
Phase 4 adds bounded read-only diagnostics, and Phase 5 adds one evidence-grounded
AegisOps investigator plus an isolated evaluation harness.

The governing specifications are `NEXUS_PROJECT_INSTRUCTIONS.md`,
`NEXUS_MASTER_BUILD_PROMPT.md`, and `BRAIN.md`.

## AegisOps Lab

The current request path is:

```text
Client -> Gateway -> Users
                  -> Orders -> PostgreSQL
```

The Gateway exposes the external API and uses typed HTTP calls for all downstream
work. Users owns deterministic user data. Orders exclusively owns the PostgreSQL
schema and applies Alembic migrations before it starts. Shared code is limited to
HTTP contracts, typed configuration, correlation IDs, error envelopes, and JSON
logging conventions.

## Install and Validate

Python 3.12 or newer is required.

```powershell
py -m pip install -e ".[dev]"
py -m ruff check .
py -m mypy
py -m pytest
```

The default pytest command runs fast unit and service tests. Compose integration
tests are selected explicitly because they require the running environment.

## Run with Docker

Create an untracked local environment file, choose a local-only PostgreSQL
password, and start the complete stack:

```powershell
Copy-Item .env.example .env
docker compose config
docker compose up --build --detach --wait
```

The endpoints are available at:

- Gateway: `http://localhost:8000`
- Users: `http://localhost:8001`
- Orders: `http://localhost:8002`
- Grafana: `http://localhost:3000`
- Prometheus: `http://localhost:9090`
- Loki: `http://localhost:3100`
- Tempo: `http://localhost:3200`

Run the genuine PostgreSQL integration paths after startup:

```powershell
$env:RUN_INTEGRATION = "1"
py -m pytest -m integration tests/integration
Remove-Item Env:RUN_INTEGRATION
```

Stop the environment while preserving order data with `docker compose down`.
Stop it and remove the PostgreSQL volume with `docker compose down --volumes`.

## Configuration

All runtime configuration is environment driven through the shared typed settings
models. Compose uses Docker service names for internal HTTP and database traffic.
`.env` is ignored and must never contain committed credentials.

Important variables are:

- `NEXUS_LOG_LEVEL`
- `NEXUS_USERS_SERVICE_URL`
- `NEXUS_ORDERS_SERVICE_URL`
- `NEXUS_DATABASE_URL`
- `NEXUS_METRICS_ENABLED`
- `NEXUS_OTEL_ENABLED`
- `NEXUS_OTEL_EXPORTER_ENDPOINT`
- `POSTGRES_DB`, `POSTGRES_USER`, and `POSTGRES_PASSWORD` for Compose substitution

## Operations Foundation

Every service exposes `GET /health`. Orders reports PostgreSQL health separately
and returns HTTP 503 when the database is unavailable. Requests accept or generate
`X-Correlation-ID`; the Gateway forwards it downstream and every response returns
it. Logs are JSON objects containing timestamp, level, service, message, and
correlation ID.

## Deterministic Incident Lab

Phase 2 adds five versioned scenarios under `lab/scenarios/v1`. Each scenario has
typed evaluator ground truth, deterministic symptoms, and a mandatory reset. The
service runtime receives only its operational fault type; ordinary APIs never
return scenario IDs, injected configuration, or expected root cause.

Lab controls are disabled by default. The Compose lab explicitly enables them on
Users and Orders with `NEXUS_LAB_FAILURES_ENABLED=true`. The Gateway never exposes
or proxies control routes. Do not enable this setting in a normal deployment: the
control plane is intentionally privileged and has no production authentication.

With Compose running, evaluator commands are:

```powershell
py -m nexus.lab.scenarios validate
py -m nexus.lab.scenarios list
py -m nexus.lab.scenarios status
py -m nexus.lab.scenarios activate users_latency
py -m nexus.lab.scenarios reset
py -m nexus.lab.scenarios run orders_database_unavailable
```

`run` verifies a healthy baseline, activates and checks expected symptoms, resets
in a `finally` path, and verifies recovery. Ground-truth files and evaluator output
must not be exposed to future investigator agents or diagnostic tools.

## Operational Observability

Phase 3 preserves JSON stdout logs and adds Prometheus metrics plus OpenTelemetry
traces. Grafana Alloy discovers Compose containers and forwards their logs to Loki.
Services send OTLP/gRPC spans to the OpenTelemetry Collector, which forwards them
to Tempo. Grafana provisions all three data sources and the **AegisOps Overview**
dashboard automatically.

Each service exposes `/metrics` when `NEXUS_METRICS_ENABLED=true`. HTTP metrics use
route templates such as `/users/{user_id}`, status classes, and fixed service names;
request IDs, correlation IDs, user IDs, order IDs, and scenario data are never metric
labels. Active request logs contain real `trace_id` and `span_id` fields, while logs
outside a span omit them. HTTPX and SQLAlchemy spans connect the Gateway, services,
and PostgreSQL work into one trace.

Metrics and tracing are disabled by default and enabled explicitly by Compose. An
unavailable trace collector does not make request handling fail. The local lab uses
anonymous Grafana access and mounts the Docker socket read-only into Alloy; these
choices are convenient for local development and are not production defaults.

See `docs/runbooks/observability.md` for queries, incident demonstrations, and
cross-signal correlation steps. The detailed architecture decision is recorded in
`docs/adr/0004-operational-observability.md`.

## Read-Only Diagnostics

Phase 4 exposes operational evidence through a typed Python service and developer
CLI. It is intentionally not a generic HTTP, PromQL, LogQL, SQL, filesystem, shell,
Docker, Grafana, or Tempo-search interface. Backend URLs are operator configuration;
tool callers choose only registered services, dependency edges, structured filters,
exact correlation/trace identifiers, and `1m`, `5m`, `15m`, or `30m` windows.

The tool catalog is:

| Tool | Evidence | Backend |
| --- | --- | --- |
| `list_services` | Registered topology and dependencies | Static topology |
| `get_service_health` | One service/dependency health snapshot | `/health` |
| `get_system_health` | Complete bounded health snapshot | `/health` |
| `get_request_summary` | Counts, rates, percentiles, in-flight requests | Prometheus |
| `get_dependency_summary` | Counts, failures, rates, percentiles | Prometheus |
| `get_database_health` | Orders PostgreSQL health gauge | Prometheus |
| `search_logs` | Approved structured fields and filters | Loki |
| `get_request_evidence` | Chronological correlation-scoped events | Loki |
| `find_traces` | Bounded trace IDs from correlation-scoped logs | Loki |
| `get_trace` | Normalized spans and allowlisted attributes | Tempo |
| `get_recent_errors` | Bounded structured 5xx events | Loki |

Every call produces safe audit metadata and increments a diagnostic-session tool
count. Log messages and trace content are returned as untrusted data, never executed
or treated as policy. The investigator policy explicitly denies lab controls,
scenario ground truth, arbitrary URLs and queries, environment secrets, direct SQL,
write actions, and ambient platform access.

Start with:

```powershell
py -m nexus.diagnostics services
py -m nexus.diagnostics system-health
py -m nexus.diagnostics requests gateway --window 5m
py -m nexus.diagnostics dependency gateway users --window 5m
py -m nexus.diagnostics logs orders --window 5m --limit 20
```

See `docs/runbooks/diagnostics.md` for every command and
`docs/adr/0005-typed-diagnostic-boundary.md` for the security architecture.

## AegisOps Investigator

Phase 5 implements exactly one `aegisops.investigator` through the OpenAI Agents SDK.
It receives only the eleven registered diagnostic tools and returns a strict diagnosis
covering status, component, failure class, hypotheses, evidence references,
alternatives, confidence, and the next read-only diagnostic action. Every evidence
claim names a recorded tool call and an exact result value. There are no handoffs,
sessions, memory, write/remediation tools, or ambient machine capabilities.

Live execution is opt-in and requires `NEXUS_AGENT_ENABLED=true` plus an
`OPENAI_API_KEY` in the untracked environment. Run one generic investigation with:

```powershell
py -m nexus.aegisops investigate
```

With the Compose lab running, the explicit live evaluator command is:

```powershell
py -m nexus.evaluation.aegisops --runs 1
```

It runs every scenario, passes only the generic prompt to the agent, scores against
ground truth outside agent context, and guarantees reset and recovery verification.
Local reports are written below ignored `.nexus/evaluations/aegisops/`. No live model
benchmark is claimed by the deterministic test suite. See
`docs/runbooks/aegisops-investigator.md` and
`docs/adr/0006-single-aegisops-investigator.md`.
