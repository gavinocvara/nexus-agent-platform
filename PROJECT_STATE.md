# NEXUS Project State

## Current Milestone

Phase 6 - Reproducible AegisOps Baseline Benchmarking and Regression History:
live-runtime correctness repair validated deterministically; targeted live verification pending.

The memoryless Phase 5 investigator is frozen as `aegisops-memoryless-v1`. No prompt,
tool, model guidance, scoring, scenario, memory, multi-agent, remediation, Atlas, or
MCP capability was added or tuned.

## Repository

- Local path: `C:\Users\arman\source\repos\nexus-agent-platform`
- Remote: `https://github.com/gavinocvara/nexus-agent-platform.git`
- Branch: `main`
- Approved Phase 5 starting point: `67081f3f877311c2f199686b5d1e1d6287f19e53`
- Invalid live smoke under investigation: `16e2dfa5-000f-45e7-b87a-5d275ae882a6`
  at `309caa7cb0f900b9c20948c07df79fa88c5597f9`; it is quarantined and cannot be
  accepted as baseline evidence.
- Package version: `0.7.2`

## Phase 6 Baseline Identity

- Baseline name: `aegisops-memoryless-v1`
- Investigator instruction hash:
  `d858a63116e8579a4e19a19f01cea5441aa840213380455449528ca385dad456`
- Diagnostic tool-registry hash:
  `44292aa3ded19cf0e03f086be6716d51099efa78eb97e3517799b713175ff240`
- Diagnosis-schema hash:
  `aea17a6803fe3c49f1d1e3268b5f58072e05b5edd5dce9fb72514b96a5b720ab`
- Scenario-catalog hash:
  `6b47e28ee3d5c13619903d9885212022c49940224e333188d177a40e46214e2c`
- Agents SDK: pinned and installed at `0.22.3`
- Benchmark schema version: `2`; evaluation and scenario schema versions: `1`
- Evaluator: `aegisops-evaluator-v1`
- Runtime identity also records Git SHA/dirty state, NEXUS/model/Python/platform
  versions, max turns, max tool calls, and timeout.
- The diagnosis hash now covers the unchanged Pydantic domain schema plus the strict
  provider-facing output schema. The unchanged Phase 5 domain-only schema hash is
  `f10168000946760b57a11fadf51efb86566f689e90a338050b0bc7d5c5c4e455`.

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
- A strict Agents SDK output adapter that replaces Pydantic's provider-incompatible
  empty `JsonValue` schema, preserves all diagnosis fields, supports compound JSON
  evidence values, and validates final JSON back into `Diagnosis`.
- Atomic diagnostic permits enforce completed plus reserved calls without serializing
  backend work; runtime waits for admitted sibling calls before diagnostic clients close.
- Sanitized failures retain stable category, origin, outer/cause types, and allowlisted
  provider status/code without exception messages, prompts, payloads, or evidence bodies.
- Failed-run turns are nullable when unknown, accounting completeness is explicit, and
  SDK lifecycle/run data preserves exact partial turns and usage when available.
- Diagnostic backend failures expose typed safe codes and HTTP status while legitimate
  empty Prometheus/Loki results remain successful evidence.
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

- Editable installation and `py -m pip check` passed for version `0.7.2`.
- Ruff formatting and lint passed for 85 Python files.
- Strict mypy passed with no issues in 61 source files.
- All 101 non-integration tests passed, including concurrent budget, output transport,
  failure classification, partial accounting, and in-flight drain regressions.
- All 17 Docker Compose integration tests passed.
- The structured-output regression suite passed with the exact no-tools Agents SDK
  response format and no API request.
- All 5 version 1 scenario files passed typed catalog validation.
- Direct diagnostic replay under `orders_unavailable` and `users_latency` returned
  successful Prometheus/Loki results; empty recent-error sets remained successful.
- Historical run timestamps show the failed Prometheus/Loki calls completed after the
  failed run timestamp while the next clean-stack teardown began. The repaired runtime
  drains sibling SDK tool work before the shared clients can close.
- Governing files are unchanged from approved Phase 5 commit `67081f3`.
- Whitespace and tracked-file secret-pattern scans passed.
- CLI command discovery and origin/`main` Git configuration passed.
- GitHub Actions run `35915953170` passed for structured-output repair commit
  `e014a878b16a6c59212dbc708ba2e0e1975dde5c`; both `validate` and
  `compose-integration` completed successfully.
- A clean local Compose rebuild started all ten application, database, telemetry, and
  dashboard containers. Gateway, Users, Orders, Prometheus, Loki, Tempo, Grafana, and
  both evaluator controls returned healthy responses.
- Clean-tree preflight on the repaired commit passed live opt-in, model
  `gpt-5.6-sol`, Git identity, all five scenarios, the 11-tool policy boundary, all
  application services, all observability backends, and both evaluator controls. It
  exited `2` solely because `OPENAI_API_KEY` is absent from the current process.
- GitHub Actions run `35901974987` passed for Phase 6 implementation commit
  `0c75809a67980cc7b70cd7804759a9ee42705397`:
  - `validate` passed installation, Ruff, strict mypy, all 86 non-integration tests,
    the focused Phase 5 and Phase 6 suites, and scenario validation.
  - `compose-integration` validated and built Compose, started the complete service
    and observability stack, passed all 17 integration tests including sequential
    contamination coverage, exercised diagnostic and incident workflows, and tore
    the environment down.

## Live Evaluation State

- Docker Engine and Compose are now available on the current host.
- The intended live baseline model is `gpt-5.6-sol`.
- The current Codex process does not contain `OPENAI_API_KEY`; live verification
  remains gated until it is supplied locally without entering source control.
- Smoke `16e2dfa5-000f-45e7-b87a-5d275ae882a6` is invalid framework evidence: four
  generic model errors, hard-budget overshoot to 15/16 calls, and incomplete accounting.
- The current process still has no `OPENAI_API_KEY`, so the required targeted replay of
  `orders_database_unavailable` cannot yet make a paid model request.
- No repaired targeted live run or valid live smoke has completed.
- Official repeated baseline was not executed.
- No baseline lock manifest or fabricated live result was created.
- Therefore there are no honest Phase 6 model, tool-use, evidence-quality, accuracy,
  latency, variance, token, or cost findings yet.
- Smoke sessions `405a7230-22d3-4ffe-be5f-3e688bb86843` and
  `9bd4dfd4-3a7d-45cd-98dd-d3388c0083d3` failed before generation because the old
  structured-output transport contained an empty `JsonValue` schema. Both had zero
  completed runs, tool calls, and token usage and are invalid infrastructure attempts,
  not benchmark evidence.

## Known Issues And Technical Debt

- A genuine clean-tree smoke and repeated baseline still require an explicitly
  supplied API key.
- Clean-stack-per-run isolation favors validity over speed and is intentionally costly.
- Version 1 benchmark artifacts remain raw historical evidence and are intentionally
  incompatible with version 2 comparison/locking without an explicit migration.
- Baseline lock acceptance is an operator decision. The software verifies baseline
  mode, completion, recovery, reproducibility, completeness, and summary integrity,
  but cannot independently attest that an external credential belonged to a specific
  person or billing account.
- Small calibration samples are labeled, not treated as statistical evidence.
- FastAPI's current `TestClient` emits an upstream Starlette deprecation warning;
  behavior is unaffected.

## Next Step

Commit and push the runtime repair, obtain green CI, clean the Compose stack, and run
preflight. Once `OPENAI_API_KEY` is present locally, run only the targeted
`orders_database_unavailable` investigation and review its typed outcome, hard budget,
accounting, and evidence. Stop before the five-scenario smoke and do not begin Phase 7.
