# Changelog

Meaningful changes to NEXUS, newest first. Versions are the `pyproject.toml` package
versions. The repository has no release tags; each heading names the commit that set
that version. Design rationale lives in `docs/adr/`, detail in Git history.

## Unreleased

Nothing yet.

## 0.14.0 — PatchForge Milestone E: Runtime (release commit)

- Closed deterministic lifecycle from `created` through `closed`, with explicit
  runtime-owned transition records and only the bounded
  `implement <-> targeted_validate` retry edge.
- Single-action typed engine protocol and coordinator over ToolGateway. Phase/report
  requests remain request-only until Runtime accepts them; no parallel action shape or
  live engine exists.
- Deterministic partial, cancellation, policy, sandbox, workspace, validation,
  attestation, engine, budget-exhaustion, finalization, and cleanup-failure paths.
- Runtime renews leases only for workspace-bound tools and immediately refreshes
  ToolGateway after every renewal. The gateway and Runtime expose cross-checked
  runtime-attested phase and total budget usage.
- Cleanup runs on success, typed failure, and unexpected-defect paths. Verified workspace
  removal safely handles read-only Git objects on Windows without relaxing managed-path
  checks.
- A real deterministic ToolGateway/WorkspaceManager/FakeSandbox path proves reproduction,
  editing, targeted/full validation, diff inspection, reserved finalization, report
  submission, evidence retention, lease refresh, and cleanup.
- Successful patch attestation remains deferred to Milestone F. Runtime produces no
  merge, approval, GitHub mutation, network, secret, memory, or live-model behavior.

## 0.13.1 — PatchForge Milestone D hardening (`16a58f3`)

### Security

Fixes seven gateway defects from an independent adversarial review of Milestone D:

- A budget overrun after a sandbox execution raised an unhandled `ValidationError`; it
  now returns a typed budget failure and keeps the execution evidence.
- Non-report tools in `finalize` could consume the reserve `submit_report` needs; the
  last call and result envelope are now reserved for the report.
- A new or changed `.gitignore`/`.gitattributes` could hide worktree changes from status
  and diff. Those files and Git-ignored paths are no longer writable, and status/diff
  fail closed when ignore or attribute rules differ from the source commit (tool-cache
  `.gitignore` files excepted).
- Non-portable filenames such as `a:b.py` crashed tree, search, and diff; reads now omit
  them (counted in the additive `TreeOutput.omitted_unrepresentable` field) and diffs
  fail closed.
- File and execution tools did not re-verify the workspace handle or lease; every
  workspace-touching call now does. `refresh_workspace` adopts a renewed handle.
- `submit_report` accepted unknown evidence IDs; it now requires existing runtime records.
- `conftest.py`, `pytest.ini`, `tox.ini`, `setup.cfg`, `sitecustomize.py`,
  `usercustomize.py`, and `*.pth` bypassed the test-change policy; they now follow it
  and are reported as test changes.

### Changed

- Slimmed default agent context: a short `AGENTS.md` router (imported by `CLAUDE.md`)
  and a current-state-only `CODEX_HANDOFF.md`. Retired `NEXUS_MASTER_BUILD_PROMPT.md`,
  `NEXUS_PROJECT_INSTRUCTIONS.md`, and `PROJECT_STATE.md`; their still-current content
  moved to `AGENTS.md`, `ROADMAP.md`, `BRAIN.md`, the ADRs, and the Brain runbook.
- Trimmed `README.md` to a concise entry point and reorganized this changelog by version.
- The PatchForge policy test fixture protects `AGENTS.md` instead of the retired file.

## 0.13.0 — PatchForge Milestone D: ToolGateway (`bbb093e`)

- `ToolGateway`: strict typed read, compare-and-swap write, runtime-owned Git status/diff,
  fixed operator-profile execution, and request-only `advance_phase`/`submit_report`,
  bound to the Atlas job, agent, task, source SHA, repository profile, and workspace.
- Enforces capability, phase, path/symlink, protected-file, test-change, call, duration,
  output, and finalization-reserve policy. Shell executables are rejected.
- Append-only runtime evidence with contiguous call IDs, canonical hashes, typed
  operational failures, and bounded-output truncation attestation.
- Reproduction and targeted validation select distinct immutable profile commands.

## 0.12.0 — PatchForge Milestone C: SandboxExecutor (`72c34c6`)

- `DockerSandbox` and deterministic `FakeSandbox` behind one typed executor protocol with
  runtime-attested command/policy hashes and typed outcomes.
- Docker isolation: content-addressed local image, no pull/network/host environment,
  non-root, read-only root, dropped capabilities, no-new-privileges, bounded
  CPU/memory/PIDs/time/output, one `.git`-free worktree mount, forced cleanup.
- Profile commands must fit sandbox timeout/output ceilings; images accept only
  content-addressed IDs or repository digests.

## 0.11.0 — PatchForge Milestone B: WorkspaceManager (`c0bcc9b`)

- Exact-SHA disposable worktrees with runtime-owned bare Git metadata outside the
  execution tree, deterministic bounded diffs, durable leases, idempotent cleanup, and
  marker-verified orphan reaping.
- No-shell Git runner with sanitized configuration, disabled hooks and line-ending
  conversion, and bounded time/output.

## 0.10.0 — PatchForge Milestone A: Contracts (`dd91aba`)

- Contracts for engineering tasks, run identity, phase budgets with a finalization
  reserve, runtime-attested evidence, narrative-only agent reports, typed patch outcomes,
  and canonical hashes.
- Operator-owned repository profiles with argument-vector commands and a mandatory
  digest-pinned, networkless, secretless, non-root, resource-bounded sandbox policy.
- ADR 0010 records the accelerated PatchForge sequence.

## 0.9.0 — Phase 8: Atlas thin control plane (`c28fe5c`)

- Typed jobs, source revisions, budgets, capabilities, results, reviews, human approval,
  failures, leases, and runtime dispatch adapters (ADR 0009).
- Closed deterministic lifecycle, explicit agent policy registry, transactional SQLite
  persistence, optimistic revisions, idempotent command replay, explicit expired-lease
  recovery, and append-only audit events protected by database triggers.
- Local library only: no service, model call, scheduler, shell, deployment, or remediation.

## 0.8.1 — Phase 7 post-calibration hardening (`15eff37`)

- Legacy analysis-version-1 summaries report unavailable Phase 7 metrics as `null`, so
  comparisons cannot manufacture historical zeros or deltas.
- Brain configuration is explicit: constructors default to disabled, the legacy
  evaluation harness rejects writable mode, and the ad-hoc investigator refuses ambient
  writable Brain settings.
- Evaluator score/canary invariance covered end to end; lock-schema-2 artifact digests
  must match `run_count`.

## 0.8.0 — Phase 7: Brain v1 (`a7b6f5c`)

- Private episodic and procedural memory for `aegisops.investigator` with provenance,
  namespace isolation, lifecycle state, deterministic retrieval, untrusted-context
  framing, secret rejection, and SQLite storage (ADR 0008).
- Default-off `disabled`/`learn`/`frozen_eval` modes, fail-closed `brain_failure`,
  per-run audit metadata, frozen snapshots, a targeted benchmark command, and a committed
  pre-registration protocol.
- Portable baseline lock schema 2 (storage-relative paths, per-artifact digests); legacy
  locks resolve by session ID. Benchmark schema 3 is unchanged and backward compatible.
- SQLAlchemy restricted to `<2.1` for OpenTelemetry instrumentor support.

## 0.7.4 — Evaluator portability repair (`52bd5f4`)

- Clean-stack subprocess capture is byte-based and locale-independent; failures
  distinguish timeout, launch failure, and nonzero exit without persisting output.

## 0.7.3 — SDK tool validation repair (`5a2939e`)

- SDK-facing tool schemas match inner Pydantic constraints; Agents SDK pinned to
  `0.22.3`; the tool identity hash covers SDK parameter/output schemas.
- Payload-free SDK validation diagnostics. Benchmark schema advanced to 3.

## 0.7.2 — Live runtime accounting repair (`2c2603a`)

- Concurrency-safe diagnostic call permits, in-flight drain tracking, sanitized failure
  classification, and partial failed-run accounting.
- Benchmark schema 2: failed-run turns distinguish unknown from zero; runs carry
  accounting completeness and typed failures.

## 0.7.1 — Structured output repair (`e014a87`)

- Strict `DiagnosisOutputSchema` Responses API adapter; diagnosis identity hashes both
  the domain and provider-facing schemas.

## 0.7.0 — Phase 6: Reproducible benchmarking (`0c75809`)

- Versioned `aegisops-memoryless-v1` identity; secret-safe preflight; explicit smoke,
  baseline, resume, compare, and immutable lock commands (ADR 0007).
- Atomic per-run history with recovery verification, clean-stack isolation, and warm-up;
  aggregate, calibration, scenario, tool-use, evidence, usage, and latency analysis.
- Normalized model usage includes cached, cache-write, and reasoning-token detail.

## 0.6.0 — Phase 5: AegisOps investigator (`d6132bd`)

- One `aegisops.investigator` (OpenAI Agents SDK) with exactly the eleven read-only
  diagnostic tools, structured diagnoses, evidence provenance, hard tool/turn/time
  limits, and a ground-truth-isolated five-scenario evaluation harness (ADR 0006).
- Diagnostic results and audit events share a stable `tool_call_id`.

## 0.5.0 — Phase 4: Read-only diagnostics (`6d99c3d`)

- Typed diagnostics over fixed health, Prometheus, Loki, and Tempo adapters with strict
  inputs and no raw-query or arbitrary-URL fields (ADR 0005).
- Deterministic tool registry, investigator allow/deny policy, per-session call counting,
  evidence-free audit records, and a diagnostic CLI.
- Trace normalization redacts SQL-shaped operation names and non-allowlisted attributes.

## 0.4.0 — Phase 3: Operational observability (`88425d4`)

- Bounded-label Prometheus metrics, OpenTelemetry tracing via the Collector to Tempo,
  Loki logs via Grafana Alloy, and a provisioned Grafana dashboard (ADR 0004).
- Request logs include duration and real trace/span IDs inside active spans.

## 0.3.0 — Phase 2: Deterministic incident lab (`1e23b3a`)

- Five versioned scenarios with typed, isolated ground truth; disabled-by-default,
  concurrency-safe failure controls with idempotent reset; evaluator lifecycle CLI
  (ADR 0003).

## 0.2.0 — Phase 1: AegisOps lab (`f377535`)

- Gateway, Users, and Orders FastAPI services; PostgreSQL orders with Alembic; Docker
  Compose with health checks; correlation IDs, structured JSON logs, and error envelopes
  (ADR 0002).
- CI for static checks, unit tests, Docker builds, Compose startup, and integration tests.

## 0.1.0 — Phase 0: Foundation (`66ef536`)

- Repository bootstrap, packaging, and quality tooling.
