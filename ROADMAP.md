# NEXUS Roadmap

## Phase 0 — Foundation (Complete)

- Repository bootstrap
- Python packaging and quality tooling
- CI validation
- Baseline docs and project state
- Minimal package boundary

## Phase 1 — AegisOps Lab (Complete)

- Gateway, users, and orders service slice
- Health endpoints
- PostgreSQL order persistence with Alembic migrations
- Docker Compose for local reproducibility
- Tests around service behavior

## Phase 2 — Deterministic Failure Injection (Complete)

- Add explicit, reversible failure controls to the Phase 1 services
- Define a small failure catalog with stable identifiers and expected symptoms
- Keep failure activation disabled by default and observable through service signals

## Phase 3 — Operational Observability (Complete)

- Bounded Prometheus service and dependency metrics
- Structured JSON logs collected by Grafana Alloy into Loki
- Distributed OpenTelemetry traces collected through the Collector into Tempo
- Provisioned Grafana data sources and AegisOps overview dashboard
- Cross-signal incident evidence with evaluator ground truth kept isolated

## Later Phases

The full sequence is governed by `NEXUS_MASTER_BUILD_PROMPT.md`:
diagnostic tools, first investigator agent,
evaluation, Brain v1, Atlas v1, AegisOps remediation, Kubernetes, PatchForge,
SentinelQA, Engram evolution, and integrated NEXUS workflows.
