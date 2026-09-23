# AegisOps Benchmarking Runbook

## Boundary

Phase 6 measures the frozen `aegisops-memoryless-v1` investigator. It does not add
memory, agents, tools, remediation, or hidden-reasoning collection. Live output is
local and ignored by Git under `.nexus/benchmarks/aegisops/`.

## Configure Safely

Put local settings in the ignored `.env` or in the process environment:

```text
NEXUS_AGENT_ENABLED=true
NEXUS_AGENT_MODEL=gpt-5-mini
OPENAI_API_KEY=<local secret>
```

Never put the key on a command line, in a committed file, or in a benchmark artifact.
Optional benchmark settings are `NEXUS_BENCHMARK_ROOT`,
`NEXUS_BENCHMARK_WARMUP_SECONDS`, and `NEXUS_BENCHMARK_STACK_TIMEOUT_SECONDS`.

## Preflight

From a clean committed checkout, run:

```powershell
py -m nexus.evaluation.aegisops preflight
```

Preflight makes no model request. It checks opt-in execution, key presence, model,
Git state, scenario validity, the tool-policy boundary, application health,
Prometheus, Loki, Tempo, and evaluator controls. A failed preflight blocks paid work.
`--allow-dirty` is for experiments only and produces a non-reproducible identity that
cannot be locked.

## Live Smoke

The smoke command performs exactly one investigation for each catalog scenario:

```powershell
py -m nexus.evaluation.aegisops smoke --confirm-live
```

Review all run statuses, schema/runtime failures, usage, and evidence validity. Do not
tune the investigator after seeing smoke output. Classify framework defects separately
from model-quality weaknesses.

The pre-baseline sessions `405a7230-22d3-4ffe-be5f-3e688bb86843` and
`9bd4dfd4-3a7d-45cd-98dd-d3388c0083d3` are invalid infrastructure attempts: the
provider rejected the old structured-output schema before generation, so neither is
benchmark evidence and neither may be locked.

## Repeated Baseline

Run the default three repetitions per scenario only after smoke succeeds:

```powershell
py -m nexus.evaluation.aegisops baseline --runs 3 --confirm-live
```

Use `--ordering seeded_shuffle --seed 42` when a recorded deterministic shuffle is
desired. The command prints model, scenario count, repetitions, total investigations,
limits, Git SHA, and clean/dirty state before execution. It performs a full
`docker compose down --volumes --remove-orphans` and healthy rebuild before every
investigation, then warms health/readiness, one healthy Users request, one ordinary
missing-order request, and telemetry for the configured interval.

## Resume

Recovered runs are never repeated:

```powershell
py -m nexus.evaluation.aegisops resume <session-id> --confirm-live
```

Resume requires the current identity to equal the persisted identity. A run lacking
verified recovery quarantines the session; begin a new session after fixing the
environment rather than trusting potentially contaminated state.

## Results

Session files are:

```text
.nexus/benchmarks/aegisops/<session-id>/manifest.json
.nexus/benchmarks/aegisops/<session-id>/runs/run-001.json
.nexus/benchmarks/aegisops/<session-id>/summary.json
.nexus/benchmarks/aegisops/comparisons/*.json
.nexus/benchmarks/aegisops/baselines/*.json
```

Individual files are atomically replaced. Summaries include aggregate, calibration,
per-scenario, and tool-use analysis. Token fields stay null when the SDK does not
provide complete usage. A p95 is omitted below 20 observations. Calibration buckets
below 30 observations are explicitly marked `small_sample`; do not call such results
statistically significant.

## Compare and Lock

Compare two session IDs or session directories:

```powershell
py -m nexus.evaluation.aegisops compare <baseline> <candidate>
```

Optional flags configure accuracy, unsupported-claim, unsafe-attempt, tool-call, and
latency thresholds. The report gives factual deltas and compatibility warnings, not a
winner or composite score.

After reviewing a genuine, complete, clean-tree live baseline, lock it explicitly:

```powershell
py -m nexus.evaluation.aegisops lock <session-id> --name aegisops-memoryless-v1
```

The lock contains identifiers, schema versions, run count, summary path, and summary
hash only. Existing lock files are never overwritten.

## Cleanup

Each official investigation starts from a new stack and volumes. After the session:

```powershell
docker compose down --volumes --remove-orphans
```

Raw live artifacts remain local. Share only intentionally reviewed, secret-free
summaries or manifests.
