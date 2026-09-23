# AegisOps Observability Runbook

## Start the Lab

From the repository root:

```powershell
Copy-Item .env.example .env
docker compose config
docker compose up --build --detach --wait
```

Open Grafana at `http://localhost:3000`. The local lab grants anonymous viewer
access. Prometheus is at `http://localhost:9090`, Loki at `http://localhost:3100`,
and Tempo at `http://localhost:3200`.

## Check Metrics

Application metrics are available at:

- Gateway: `http://localhost:8000/metrics`
- Users: `http://localhost:8001/metrics`
- Orders: `http://localhost:8002/metrics`

In Grafana Explore or Prometheus, useful starting queries are:

```promql
sum by (service, route, status_class) (rate(nexus_http_requests_total[1m]))
sum by (service, dependency) (rate(nexus_dependency_failures_total[1m]))
nexus_database_health{service="orders"}
```

## Search Logs

Select Loki in Grafana Explore and start with:

```logql
{service="gateway"}
```

Filter on a JSON field to follow one request:

```logql
{service=~"gateway|users|orders"} | json | correlation_id="<correlation-id>"
```

Request logs include duration and, when tracing is active, real `trace_id` and
`span_id` values. Copy a `trace_id` to the Tempo data source to inspect the trace.

## Inspect Traces

Select Tempo in Grafana Explore and search by trace ID. A successful order request
should contain a Gateway server span, the outgoing HTTPX client span, an Orders
server span, and SQLAlchemy database spans. Route attributes are templates rather
than concrete user or order paths.

## Demonstrate Incidents

Run the evaluator scenarios after the stack is healthy:

```powershell
py -m nexus.lab.scenarios run users_unavailable
py -m nexus.lab.scenarios run users_latency
py -m nexus.lab.scenarios run orders_database_unavailable
```

For unavailable Users, inspect Gateway dependency failures, 503 request logs, and
the failed downstream spans. For Users latency, inspect the dependency-duration
histogram, request `duration_ms`, and the long client/server spans. For database
unavailability, inspect `nexus_database_health`, Orders health logs, and error spans;
the gauge should return to `1` after reset.

Scenario names and expected causes belong to the evaluator. Diagnose from the
ordinary signals above; do not use the scenario catalog as operational evidence.

## Validate and Stop

Run the Compose integration gate with the stack active:

```powershell
$env:RUN_INTEGRATION = "1"
py -m pytest -m integration tests/integration
Remove-Item Env:RUN_INTEGRATION
```

Stop the lab and remove its data volumes:

```powershell
docker compose down --volumes
```
