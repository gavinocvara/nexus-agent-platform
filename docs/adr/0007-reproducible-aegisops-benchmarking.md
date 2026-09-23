# ADR 0007: Reproducible AegisOps Benchmarking

## Status

Accepted

## Context

Phase 5 produced one bounded, memoryless AegisOps investigator and a deterministic
evaluation harness. Before prompts, tools, models, scoring, scenarios, or memory are
changed, NEXUS needs a durable account of how that investigator behaves in genuine
live executions. A useful baseline must survive interruption, preserve individual
runs, distinguish answer correctness from evidence quality, and make unlike sessions
visibly unlike.

## Decision

Freeze the Phase 5 behavior as `aegisops-memoryless-v1`. Its identity records the Git
SHA and working-tree state; NEXUS, model, Agents SDK, Python, evaluator, benchmark,
evaluation, and scenario versions; hashes of instructions, tools, diagnosis schema,
and scenario catalog; and execution limits. The pinned Agents SDK remains `0.22.3`.
Official runs require a clean tree. An explicit dirty-tree override is available for
experiments, but marks the identity non-reproducible and prevents baseline locking.

Persist each benchmark session below ignored `.nexus/benchmarks/aegisops/`. A session
contains an atomic `manifest.json`, one atomic JSON record per attempted run, and an
incrementally rebuilt `summary.json`. Comparison reports and accepted baseline
manifests occupy separate directories. Persist observable tool order, normalized
arguments, timing, result metadata, structured diagnoses, scores, and normalized SDK
usage. Do not persist prompts, transcripts, hidden reasoning, evidence payloads,
credentials, authorization headers, or environment mappings.

Use explicit version-1 benchmark and evaluation schemas. Parsing fails on unknown
schema shapes, and comparison reports incompatible schema versions instead of
silently reinterpreting them. A locked baseline is a small immutable pointer carrying
the accepted session identity and a SHA-256 digest of its aggregate summary.

Before each planned investigation, destroy the Compose stack and volumes, rebuild and
wait for health, generate only deterministic healthy traffic, and wait for telemetry
scrapes. Then reset the target service, verify health, activate one scenario, and
verify its symptoms. After every model outcome or setup failure, reset and verify
recovery. Failed model runs are retained and are not silently retried. Failed recovery
quarantines and stops the session. Resume skips recovered run records and refuses any
session containing a persisted run without verified recovery.

Keep live and deterministic evaluation distinct. `preflight` checks credentials and
all system boundaries without making a model call or exposing a key. `smoke` performs
one investigation per scenario. `baseline` defaults to three repetitions and both
commands require `--confirm-live`. CI uses scripted engines and fixtures only.

Aggregate factual quality, reliability, latency, usage, calibration, scenario, tool,
and efficiency measures. Comparisons report per-metric deltas and configurable
threshold crossings without a composite score or automatic winner. Different models
are labeled cross-model; differing Git or behavior hashes produce explicit warnings.
Tiny calibration buckets are marked as small samples.

Do not tune instructions, tools, model, evaluator, scoring, or scenarios during an
official baseline session. A correctness or security fix invalidates the interrupted
session and requires a new identity and session.

## Consequences

- Partial live work survives process failure without hiding failed attempts.
- Clean-stack-per-run isolation is expensive but removes prior application, metrics,
  log, trace, and database state from official measurements.
- Repeated baselines measure observed variance while retaining every source run.
- Baselines cannot be accepted from dirty or incomplete sessions.
- Deterministic CI proves machinery and boundaries, not live model quality.
- Brain v1 remains blocked on a genuine accepted baseline unless the owner explicitly
  chooses otherwise.

## Pre-Baseline Structured-Output Repair

Before any valid live run completed, two smoke sessions
`405a7230-22d3-4ffe-be5f-3e688bb86843` and
`9bd4dfd4-3a7d-45cd-98dd-d3388c0083d3` failed before model generation. Their run
counts, tool calls, and token usage were all zero, so they are invalid infrastructure
attempts rather than benchmark evidence.

The automatic Agents SDK schema retained a valid root object but represented
Pydantic's `JsonValue` definition as an empty schema. Strict Structured Outputs rejects
that referenced node because it has no type. NEXUS now supplies a narrow strict SDK
output adapter. It preserves the existing `Diagnosis` model and every diagnosis field,
uses natural JSON scalar and recursive array values, represents arbitrary object-valued
evidence through an explicit JSON-encoded object branch, then decodes and validates the
complete result back into `Diagnosis`. Invalid JSON, invalid object encoding, or any
domain-validation failure raises a model behavior error.

The baseline identity now hashes the unchanged domain schema together with this
provider-facing transport schema. The original Phase 5 domain-schema hash remains
recorded in tests to prove that diagnosis semantics did not change.
