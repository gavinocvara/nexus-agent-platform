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

## Phase 7 — Agent-Private Brain v1 (Complete: Negative Calibration)

- Private `aegisops.investigator` episodic and procedural memory namespace
- Durable typed SQLite persistence with provenance, lifecycle state, and frozen snapshots
- Deterministic bounded lexical retrieval with untrusted-context prompt framing
- Default-off memoryless compatibility and frozen read-only Brain evaluation mode
- Brain retrieval/write audit metadata and benchmark comparison metrics
- Structural evaluator isolation, explicit learn/frozen modes, fail-closed execution,
  pre-registered experiment identity, and cross-process deterministic replay
- Single targeted learn calibration and immutable five-scenario frozen smoke complete
- Frozen smoke recorded 0/5 completion and 5/5 tool-budget exhaustion with the same
  unverified procedural record retrieved; no causal improvement claim is supported

## Phase 8 — Atlas Thin Control Plane Foundation (Complete)

- Strict typed jobs with source identity, bounded budgets, capabilities, results,
  failures, approval state, timestamps, revisions, and audit history
- Closed deterministic lifecycle with explicit review, human decision, terminal
  closeout, and expired-lease recovery
- Local transactional SQLite persistence with idempotent commands, optimistic
  concurrency, canonical snapshots, and append-only audit guards
- Explicit agent/reviewer policy registry with no ambient tools or unrestricted shell
- Typed `AgentRuntime`, `JobDispatch`, `PatchResult`, and `ReviewResult` boundaries for
  future PatchForge and SentinelQA registration
- No scheduler, network service, deployment, remediation, merge, model call, shared
  memory, Engram, Kubernetes, or PatchForge business logic

## Accelerated PatchForge v1

- **A. Contracts (Complete):** strict tasks, identities, phase budgets, runtime evidence,
  patch results, operator policy, sandbox requirements, and canonical hashes
- **B. WorkspaceManager (Complete):** disposable source-SHA workspaces, runtime-owned Git,
  hardened commands, diff, cleanup, and orphan reaping over synthetic repositories
- **C. SandboxExecutor (Complete):** Docker isolation plus deterministic `FakeSandbox`
- **D. ToolGateway (Complete):** narrow typed read, compare-and-swap write, runtime-owned
  Git inspection, fixed-profile execution, and explicit control-request tools
- **E. Runtime (Next):** closed phased workflow with bounded implementation loops and reserved
  finalization capacity
- **F. Attestor:** reproduction, regression, diff, validation, scope, and basic tamper checks
- **G. Deterministic E2E:** scripted happy path and all required failure/partial paths
- **H. Benchmark v0:** small synthetic defect corpus and reproducible harness
- **I. Live engine:** explicit opt-in, one targeted task, artifact inspection, then stop
- **J. SentinelQA-lite:** independent fresh-sandbox patch and validation verification
- **K. GitHub:** issue intake, reviewed branch push, and draft PR only; never merge

PatchForge memory, large benchmarks, automatic improvement adoption, Kubernetes, Engram,
and free-form multi-agent planning remain deferred.

## Later Phases

Execution order after PatchForge v1 (this file is the authoritative sequence):

1. AegisOps specialist agents, verifier, and approval-gated remediation with recovery
   verification, once the single-investigator baseline justifies decomposition
2. Kubernetes/kind lab, Kubernetes tools, and Kubernetes failure benchmark
3. SentinelQA: requirements-to-tests, browser/API evidence, bug reports, and a selector/
   DOM mutation resilience benchmark
4. Engram v2: temporal validity, supersession, contradiction handling, and validated
   cross-agent knowledge exchange (see `BRAIN.md`)
5. Integrated NEXUS loop: AegisOps detects and verifies an incident -> engineering issue
   -> PatchForge tested fix -> SentinelQA validation -> CI -> approved deployment ->
   AegisOps verifies recovery -> Engram records the validated lesson
6. Portfolio polish based only on measured results

No unrelated phase begins while PatchForge v1 is being brought online. Evidence may
reorder later phases; record such changes here and in an ADR.

## Known Limitations

- Brain v1 is SQLite, single-agent, episodic/procedural only; semantic, reflective,
  shared, vector, and graph memory are absent. Five scenarios cannot estimate learning
  effects; the official memoryless/placebo study remains deferred (ADR 0008).
- Atlas v1 is local single-node: no remote API, scheduler, heartbeat, distributed claim,
  credential broker, or PostgreSQL backend (ADR 0009).
- PatchForge has no Runtime or model adapter yet. `DockerSandbox` trusts the
  operator-selected content-addressed image; image provenance/signing is outside v1.
