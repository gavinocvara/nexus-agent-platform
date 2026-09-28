# AegisOps Brain v1 Runbook

## Boundary

Brain v1 belongs only to `aegisops.investigator`. Its writer accepts only the strict
`AgentObservableRun` projection constructed in the investigator runtime. Scenario,
evaluator, benchmark-plan, harness-lifecycle, score, correctness, and recovery fields
never enter that projection. Keep the Phase 6 baseline lock unchanged.

The committed protocol is `docs/experiments/brain-v1-protocol.json`. Its byte hash is
part of every new benchmark identity. Any protocol or Brain configuration change starts
a new identity and session.

## Memoryless Reproduction

```powershell
$env:NEXUS_BRAIN_MODE = "disabled"
py -m nexus.evaluation.aegisops preflight
```

The identity must report `brain_enabled=false` and `brain_identity.mode=disabled`.
Instruction, tool, Diagnosis, scenario, model, and execution-limit hashes remain frozen.

## Learn Calibration

Use a new ignored database. Do not point learn mode at a frozen snapshot. Writable Brain
is accepted only by the guarded benchmark `targeted` command. The ad-hoc
`python -m nexus.aegisops` entry point refuses ambient enabled Brain settings, and the
legacy multi-scenario evaluation harness refuses explicit `learn` mode.

```powershell
$env:NEXUS_AGENT_ENABLED = "true"
$env:NEXUS_AGENT_MODEL = "gpt-5.6-sol"
$env:NEXUS_BRAIN_MODE = "learn"
$env:NEXUS_BRAIN_PATH = ".nexus/brain/aegisops-investigator.sqlite3"
py -m nexus.evaluation.aegisops preflight
py -m nexus.evaluation.aegisops targeted orders_database_unavailable --confirm-live
py -m nexus.brain inspect
```

Inspect the targeted run for recovery, at most 12 diagnostic calls, complete turn/usage
accounting, retrieval status and latency, context hash/size, retrieved IDs/content
hashes, write count/latency, and pre/post snapshot hashes. An empty initial Brain has
`retrieval_status=empty`; that is valid.

The authorized targeted calibration is complete as session
`c6832bae-feb7-4216-933e-139c734c2173`. It completed with verified recovery, 11/12 tool
calls, three turns, complete accounting, no backend or Brain failure, empty initial
retrieval, and two writes. The resulting active store contains one episodic and one
procedural memory at logical SHA-256
`9e7d36376c6b0c8c77d5a3f94cafcea22cb2a53a313986a2a3fe6aed4f0fd3b6`.
The live calibration store's physical SQLite SHA-256 is
`41cee12dd35c38c62fcecaf38e8b2aad3f19231029d356142621c3d5254de38c`.
Its component was correct but failure class was wrong at 0.97 confidence. Preserve that
diagnosis as unverified historical self-report; do not tune retrieval or rewrite memory.

## Freeze State

```powershell
py -m nexus.brain snapshot .nexus/brain/snapshots/aegisops-brain-v1.sqlite3
```

The command writes a read-only SQLite candidate plus a canonical JSONL evidence stream.
Record the SQLite file hash, canonical logical hash, and memory-type counts.

The authorized snapshot was frozen with logical SHA-256
`9e7d36376c6b0c8c77d5a3f94cafcea22cb2a53a313986a2a3fe6aed4f0fd3b6` and physical
SQLite SHA-256
`eeb6e6604f032ac925a981ab249f189418bd92a0005801f513faeb3fd5636bb5`. It contains one
active episodic and one active procedural memory.

## Frozen Smoke

```powershell
$env:NEXUS_BRAIN_MODE = "frozen_eval"
$env:NEXUS_BRAIN_PATH = ".nexus/brain/snapshots/aegisops-brain-v1.sqlite3"
$env:NEXUS_BRAIN_EXPECTED_SNAPSHOT_SHA256 = "<printed-logical-sha256>"
py -m nexus.evaluation.aegisops preflight
py -m nexus.evaluation.aegisops smoke --confirm-live
```

Every frozen run must have identical pre/post snapshot hashes and zero writes. Any write
attempt, unavailable/corrupt store, timeout, or hash change produces `brain_failure`; it
never silently continues as memoryless behavior.

The one authorized smoke completed as session
`4776dc1c-6f7a-41d1-b75b-e6c70856923c` at repository
`f77313e7e624b68750578b2b25b90ba8123fab62`, NEXUS `0.8.1`. Do not rerun it. It produced
0/5 completions and 5/5 `tool_budget_exceeded` outcomes, with 12 diagnostic calls and
two turns per run. Every run retrieved procedural memory
`8eef2c33-9acb-52b3-8329-11345784890c`, added 420 estimated Brain-attributable input
tokens, wrote no memory, and preserved the expected pre/post logical hash. No backend or
Brain failure occurred.

## Interpretation

The one generic incident prompt gives retrieval no incident-specific live observation.
Brain v1 therefore tests reusable procedural context, much like a learned prompt prefix,
not true situation-specific episodic recall. The five-run smoke validates mechanism and
security only. It overlaps the targeted learning scenario, has no placebo arm, and must
not support a causal improvement claim or an official Brain baseline lock.

With identical lexical overlap, ranking favors newer records before stable record-ID
tie-breaking. The generic prompt may retrieve a procedural record while failing to match
an episodic record's incident-specific terms. Because unverified self-reports may anchor
later output, future fold learning order must use its fixed pre-registered seed.

Frozen calibration observed 0/5 completion with the same unverified procedural memory
retrieved in all five scenarios and 100% diagnostic tool-budget exhaustion. This is
consistent with procedural-memory anchoring but is not a causal comparison because no
contemporaneous memoryless or placebo arm was run. The read-only forensic measurements
and evidentiary classification are in
`docs/experiments/brain-v1-calibration-report.md`.

The deferred official protocol uses a contemporaneous memoryless control,
length-matched placebo, five leave-one-scenario-out frozen snapshots, an evaluator-only
contamination ledger, interleaved arms, and about 35 evaluation runs per arm. It is not
authorized during calibration.

## Failure Handling

- Never copy evaluator summaries, scenario files, hidden labels, prompts, transcripts,
  raw tool payloads, environment mappings, headers, or credentials into Brain storage.
- Self-diagnoses remain `self_reported/unverified`, regardless of confidence.
- Retrieved content is delimiter-escaped canonical JSON inside an untrusted historical-data
  block. It cannot be current-run evidence or alter tools, policy, identity, model,
  timeout, permissions, turns, or diagnostic-call budget.
- Keep `.nexus/brain/` ignored, local, and outside Compose, lab PostgreSQL, and lab
  telemetry. Brain emits no data into investigator-visible Prometheus, Loki, or Tempo.
