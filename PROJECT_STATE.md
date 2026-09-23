# NEXUS Project State

## Current Milestone

Phase 6 - Reproducible AegisOps Baseline Benchmarking and Regression History:
infrastructure complete, live baseline pending.

The memoryless Phase 5 investigator is frozen as `aegisops-memoryless-v1`. No prompt,
tool, model guidance, scoring, scenario, memory, multi-agent, remediation, Atlas, or
MCP capability was added or tuned.

## Repository

- Local path: `C:\Users\arman\source\repos\nexus-agent-platform`
- Remote: `https://github.com/gavinocvara/nexus-agent-platform.git`
- Branch: `main`
- Approved Phase 5 starting point: `67081f3f877311c2f199686b5d1e1d6287f19e53`
- Package version: `0.7.0`

## Phase 6 Baseline Identity

- Baseline name: `aegisops-memoryless-v1`
- Investigator instruction hash:
  `d858a63116e8579a4e19a19f01cea5441aa840213380455449528ca385dad456`
- Diagnostic tool-registry hash:
  `44292aa3ded19cf0e03f086be6716d51099efa78eb97e3517799b713175ff240`
- Diagnosis-schema hash:
  `f10168000946760b57a11fadf51efb86566f689e90a338050b0bc7d5c5c4e455`
- Scenario-catalog hash:
  `6b47e28ee3d5c13619903d9885212022c49940224e333188d177a40e46214e2c`
- Agents SDK: pinned and installed at `0.22.3`
- Benchmark, evaluation, and scenario schema versions: `1`
- Evaluator: `aegisops-evaluator-v1`
- Runtime identity also records Git SHA/dirty state, NEXUS/model/Python/platform
  versions, max turns, max tool calls, and timeout.

## What Exists

- Everything completed in Phases 1-5: the local service lab, deterministic failure
  catalog, metrics/logs/traces, typed read-only diagnostic boundary, and one bounded
  memoryless AegisOps investigator.
- `preflight`, `smoke`, `baseline`, `resume`, `compare`, and `lock` evaluator commands.
- Secret-safe preflight for live opt-in, key presence, model, Git state, scenario
  catalog, exact tool policy, application health, observability, and lab controls.
- Clean-tree enforcement with an explicit non-reproducible developer override.
- Atomic session manifests, per-attempt run files, incremental summaries, comparison
  reports, and immutable baseline lock manifests below ignored
  `.nexus/benchmarks/aegisops/`.
- Recovery-aware resume that skips recovered runs and quarantines persisted runs with
  unverified recovery.
- Catalog and recorded-seed shuffle ordering with the actual plan persisted.
- Clean Compose stack and volumes before every run, deterministic healthy warm-up,
  scenario reset/health/symptom checks, and mandatory reset/recovery verification.
- Explicit run statuses for completed, timeout, tool-budget, turn-limit, invalid
  output, model, backend, setup, and reset failures; failures are never hidden by an
  automatic model retry.
- Aggregate correctness, reliability, evidence, safety, tool-count, latency, token,
  and efficiency metrics; confidence buckets; per-scenario/failure analysis; and
  ordered tool-use analysis.
- Deterministic comparisons with configurable threshold crossings, incompatible
  schema handling, and warnings for model, Git, instruction, tool, diagnosis, and
  scenario identity changes. No winner or composite score is produced.
- SDK request/token detail persistence when exposed by the pinned SDK, without
  inventing absent values or calculating cost.
- Sequential contamination integration coverage and deterministic clean-stack-per-run
  orchestration coverage.
- ADR 0007 and the AegisOps benchmarking runbook.

## Storage And Security

- Every persisted benchmark record declares explicit schema versions.
- Atomic writes flush and replace a same-directory temporary file.
- Artifacts contain observable tool metadata and structured outputs, not prompts,
  transcripts, hidden reasoning, evidence payloads, environment mappings, keys,
  authorization headers, or database credentials.
- Dirty, incomplete, smoke, or recovery-unverified sessions cannot be locked as the
  official baseline. Existing lock manifests are not overwritten.
- Raw live output remains local and ignored by Git.

## Validation Commands

Run from the repository root:

```powershell
py -m pip install -e ".[dev]"
py -m pip check
py -m ruff format --check .
py -m ruff check .
py -m mypy
py -m pytest
py -m pytest tests/unit/test_aegisops_benchmark.py tests/unit/test_aegisops_comparison.py tests/unit/test_aegisops_preflight.py
py -m nexus.lab.scenarios validate
py -m nexus.evaluation.aegisops preflight
docker compose config --quiet
$env:RUN_INTEGRATION = "1"
py -m pytest -m integration tests/integration
```

## Latest Local Validation

- Editable installation and `py -m pip check` passed for version `0.7.0`.
- Ruff formatting and lint passed for 103 files.
- Strict mypy passed with no issues in 60 source files.
- `py -m pytest` passed 86 tests; 17 Compose integration tests were deselected.
- The focused Phase 6 suite passed 21 tests.
- All 5 version 1 scenario files passed typed catalog validation.
- Governing files are unchanged from approved Phase 5 commit `67081f3`.
- Whitespace and tracked-file secret-pattern scans passed.
- CLI command discovery and origin/`main` Git configuration passed.
- Local preflight safely exited `2` without a model call: model, scenario catalog, and
  the 11-tool policy boundary passed; live opt-in, key, clean-tree, and backend checks
  failed as expected in this development state.
- Docker and the optional Python `build` frontend are not installed on this host.
  Editable package construction succeeded locally.
- GitHub Actions run `35901974987` passed for Phase 6 implementation commit
  `0c75809a67980cc7b70cd7804759a9ee42705397`:
  - `validate` passed installation, Ruff, strict mypy, all 86 non-integration tests,
    the focused Phase 5 and Phase 6 suites, and scenario validation.
  - `compose-integration` validated and built Compose, started the complete service
    and observability stack, passed all 17 integration tests including sequential
    contamination coverage, exercised diagnostic and incident workflows, and tore
    the environment down.

## Live Evaluation State

- No `OPENAI_API_KEY` is available in the process environment.
- Docker is unavailable on the current host.
- Live smoke was not executed.
- Official repeated baseline was not executed.
- No baseline lock manifest or fabricated live result was created.
- Therefore there are no honest Phase 6 model, tool-use, evidence-quality, accuracy,
  latency, variance, token, or cost findings yet.

## Known Issues And Technical Debt

- A genuine clean-tree smoke and repeated baseline still require a machine with Docker
  and an explicitly supplied API key.
- Clean-stack-per-run isolation favors validity over speed and is intentionally costly.
- Version 1 schemas reject incompatible future data; an explicit migration layer is
  required when schema version 2 is introduced.
- Baseline lock acceptance is an operator decision. The software verifies baseline
  mode, completion, recovery, reproducibility, completeness, and summary integrity,
  but cannot independently attest that an external credential belonged to a specific
  person or billing account.
- Small calibration samples are labeled, not treated as statistical evidence.
- FastAPI's current `TestClient` emits an upstream Starlette deprecation warning;
  behavior is unaffected.

## Next Step

On a Docker-capable machine with genuine credentials, commit any remaining work so the
tree is clean, run `preflight`, then `smoke --confirm-live`. If smoke exposes only
model-quality weaknesses, run `baseline --runs 3 --confirm-live`, review every run,
and lock the accepted session as `aegisops-memoryless-v1`. Do not begin Brain v1
without that genuine baseline unless the project owner explicitly chooses to.
