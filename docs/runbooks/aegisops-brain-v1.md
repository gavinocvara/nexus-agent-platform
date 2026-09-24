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

Use a new ignored database. Do not point learn mode at a frozen snapshot.

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

## Freeze State

```powershell
py -m nexus.brain snapshot .nexus/brain/snapshots/aegisops-brain-v1.sqlite3
```

The command writes a read-only SQLite candidate plus a canonical JSONL evidence stream.
Record the SQLite file hash, canonical logical hash, and memory-type counts.

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

## Interpretation

The one generic incident prompt gives retrieval no incident-specific live observation.
Brain v1 therefore tests reusable procedural context, much like a learned prompt prefix,
not true situation-specific episodic recall. The five-run smoke validates mechanism and
security only. It overlaps the targeted learning scenario, has no placebo arm, and must
not support a causal improvement claim or an official Brain baseline lock.

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
