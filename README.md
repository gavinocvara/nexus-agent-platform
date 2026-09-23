# NEXUS Agent Platform

NEXUS is a local-first engineering platform for auditable agent workflows. Phase 1
provides the AegisOps distributed-systems lab that later diagnostic agents will
observe, disrupt, repair, and evaluate.

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
