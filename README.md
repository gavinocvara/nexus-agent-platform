# NEXUS Agent Platform

NEXUS is a local-first engineering platform for auditable agent workflows. Phase 1
provides the AegisOps distributed-systems lab that later diagnostic agents will
observe, disrupt, repair, and evaluate. Phase 2 turns it into a deterministic
incident laboratory with isolated evaluator ground truth.

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
