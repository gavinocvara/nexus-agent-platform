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

## Phase 4 — Typed Diagnostic Tool Layer (Complete)

- Typed high-level health, metrics, log, correlation, and trace operations
- Fixed backend adapters with bounded timeouts, payloads, windows, and results
- Deterministic read-only tool registry and investigator precursor policy
- Per-session call accounting and evidence-free audit metadata
- Hard evaluator, raw-query, ambient-access, and prompt-injection boundaries
- Five-scenario evidence sufficiency evaluation against the real stack

## Phase 5 — First AegisOps Investigator (Complete)

- One OpenAI Agents SDK investigator behind a framework-neutral runtime adapter
- Exact read-only registry/policy tool intersection with hard call, turn, and time limits
- Structured diagnosis, machine-checkable evidence provenance, and typed failure states
- Ground-truth-isolated five-scenario harness with deterministic scoring and recovery
- Scripted no-key safety, policy, provenance, injection, and lifecycle validation

## Phase 6 — Reproducible AegisOps Baseline Benchmarking (Complete)

- Frozen `aegisops-memoryless-v1` identity with behavior, environment, and version hashes
- Secret-safe live preflight and explicit smoke/repeated live execution gates
- Atomic per-run benchmark persistence, recovery-aware resume, and baseline locking
- Aggregate, calibration, scenario, tool-use, evidence, and efficiency analysis
- Deterministic comparison reports with compatibility warnings and configurable thresholds
- Clean-stack-per-run contamination control and deterministic non-scenario warm-up
- Immutable 15-run `aegisops-memoryless-v1` baseline accepted at `52bd5f4`

## Phase 7 — Agent-Private Brain v1 (Deterministic Review Gate)

- Private `aegisops.investigator` episodic and procedural memory namespace
- Durable typed SQLite persistence with provenance, lifecycle state, and frozen snapshots
- Deterministic bounded lexical retrieval with untrusted-context prompt framing
- Default-off memoryless compatibility and frozen read-only Brain evaluation mode
- Brain retrieval/write audit metadata and benchmark comparison metrics
- Structural evaluator isolation, explicit learn/frozen modes, fail-closed execution,
  pre-registered experiment identity, and cross-process deterministic replay
- Full local/Compose/CI validation pending; no Brain-enabled live call has run

## Later Phases

The full sequence is governed by `NEXUS_MASTER_BUILD_PROMPT.md`: Brain live calibration,
Atlas v1, AegisOps remediation, Kubernetes, PatchForge,
SentinelQA, Engram evolution, and integrated NEXUS workflows. Brain v1 should not
begin without a genuine baseline unless the project owner explicitly chooses to.
