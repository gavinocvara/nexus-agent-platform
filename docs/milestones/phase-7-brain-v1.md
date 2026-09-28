# Phase 7 Brain v1 Milestone Record

Historical record moved out of `PROJECT_STATE.md` during the 2026-09 repository-hygiene
pass. The content is unchanged except for heading levels, removal of one developer's local
worktree path, and replacement of self-referential "the commit containing this document"
entries with their actual commit SHAs. Authoritative experiment evidence remains in
`docs/experiments/brain-v1-calibration-report.md`, `docs/experiments/brain-v1-protocol.md`,
and `docs/experiments/phase6-memoryless-baseline.md`. The protocol JSON and every recorded
hash are unchanged.

## Phase 7 Repository Checkpoints

- Remote: `https://github.com/gavinocvara/nexus-agent-platform.git`
- Branch: `main`
- Phase 7 starting point: `52bd5f47dfbf5591e5263911b1c64dab66158d90`
- Accepted Phase 6 baseline-record checkpoint: `5c0200d`
- Phase 7 implementation checkpoint: `a7b6f5c1fbfc1e218cfabb1d78ad8d830e2bc17c`
- Phase 7 validation handoff: `b72c922f894dd731e3ce13b12ac038cfd8f5c8f1`
- Post-calibration hardening: `15eff373cd92bcfd37611c3c7d9dd59f4c9e5efc`
- Frozen-smoke implementation identity: `f77313e7e624b68750578b2b25b90ba8123fab62`
- Phase 7 postmortem checkpoint: `71de1de99985a52c9cbb59a0b6e1950c83229500`
- Phase 8 Atlas checkpoint: `c28fe5ca5ef393014fa6194944f91b1973097154`

## Targeted Brain Calibration

- Session: `c6832bae-feb7-4216-933e-139c734c2173`
- Completed with recovery verified, 11/12 diagnostic calls, three turns, complete
  accounting, zero backend failures, and zero Brain failures.
- Initial retrieval was empty: zero retrieved records and zero Brain-attributable input
  tokens. The run wrote one episodic and one procedural memory.
- Empty pre-snapshot advanced to logical SHA-256
  `9e7d36376c6b0c8c77d5a3f94cafcea22cb2a53a313986a2a3fe6aed4f0fd3b6`; inspection
  reported two active memories.
- Diagnosis component was correct and failure class was wrong at 0.97 confidence. This
  remains legitimate `self_reported/unverified` history and was not corrected, promoted,
  removed, or used to tune retrieval.
- The live store remains only in the ignored canonical checkout and has not been opened
  writable during post-calibration implementation.

## Frozen Brain Smoke

- Session: `4776dc1c-6f7a-41d1-b75b-e6c70856923c`
- Repository: `f77313e7e624b68750578b2b25b90ba8123fab62`; NEXUS `0.8.1`;
  model `gpt-5.6-sol`
- Five planned runs completed at the benchmark-session level, but all five investigations
  ended `tool_budget_exceeded`: zero investigation completions, 12 successful diagnostic
  calls and two turns in every run, zero backend failures, and zero Brain failures.
- Every run retrieved the same active, unverified candidate procedural memory,
  `8eef2c33-9acb-52b3-8329-11345784890c`. The active episodic memory was not retrieved.
  Retrieval was 5/5, memory hit rate was 100%, and each run added 420 estimated
  Brain-attributable input tokens.
- Every run wrote zero memories and recorded identical pre/post logical SHA-256
  `9e7d36376c6b0c8c77d5a3f94cafcea22cb2a53a313986a2a3fe6aed4f0fd3b6`.
  The frozen SQLite file SHA-256 is
  `eeb6e6604f032ac925a981ab249f189418bd92a0005801f513faeb3fd5636bb5`.
- Frozen calibration observed 0/5 completion with the same unverified procedural memory
  retrieved in all five scenarios and 100% diagnostic tool-budget exhaustion. This is
  consistent with procedural-memory anchoring but is not a causal comparison because no
  contemporaneous memoryless or placebo arm was run.
- The full safe memory payload, deterministic lexical scoring, sequence-overlap analysis,
  evidentiary classification, and artifact hashes are recorded in
  `docs/experiments/brain-v1-calibration-report.md`.

- Read-only Phase 7 postmortem on 2026-09-27 parsed the frozen canonical snapshot and all
  five durable smoke run records. The procedural record scored 22 from two lexical
  overlaps plus its type bonus; the episodic record had zero overlap and was excluded.
  Snapshot and smoke artifacts retained their recorded hashes after inspection.
- Post-calibration 0.8.1 validation on 2026-09-25: `pip check` passes; all 121 files
  pass Ruff formatting; Ruff lint passes; strict mypy reports no issues in 69 source
  files; all 149 non-integration tests pass; the 68 focused Brain/runtime/benchmark/
  evaluator/preflight tests pass; all 20 Compose integration tests pass.
- The five scenario documents and Compose configuration validate. GitHub Actions run
  `36104819422` for `15eff37` passed both `validate` and `compose-integration` on the
  first attempt.
- The immutable Phase 6 analysis-version-1 summary parses `genuine_abstention_rate`,
  `confident_wrong_rate`, and `brain_failure_rate` as unavailable rather than zero. Its
  historical `abstention_rate=0.533333...` remains distinct from the recorded
  `tool_budget_failure_rate=0.333333...`.
- Read-only compatibility inspection reports the calibration store at logical SHA-256
  `9e7d36376c6b0c8c77d5a3f94cafcea22cb2a53a313986a2a3fe6aed4f0fd3b6`, with one
  episodic and one procedural memory. Its physical file SHA-256 remains
  `41cee12dd35c38c62fcecaf38e8b2aad3f19231029d356142621c3d5254de38c`.
- Frozen behavior, memory-record schema, writer allowlist, retrieval algorithm, renderer,
  and protocol identities are unchanged by the post-calibration fixes.
- Post-final-edit validation on 2026-09-24: `pip check` passes; all 121 files pass Ruff
  formatting; Ruff lint passes; strict mypy reports no issues in 69 source files; all
  142 non-integration tests pass; the 51 focused Brain/runtime/benchmark/comparison
  tests pass.
- With the documented local-only PostgreSQL values supplied to Compose, a fresh
  volume-removing rebuild plus standard warm-up passes all 20 integration tests. This
  includes Brain survival across `down --volumes`, retrieval during the orders database
  fault, no Brain data in diagnostic telemetry, and PostgreSQL spans.
- The first fresh-stack integration pass was 19/20 because one Loki correlation test saw
  only one of two logs during startup ingestion. It passed immediately in isolation;
  adding the evaluator's standard warm-up after the deliberate reset produced the clean
  20/20 full pass.
- All five scenario documents validate. Compose configuration, diff whitespace, ignored
  `.env`/`.nexus`, and package dependency checks pass.
- The real locked schema-3 summary parses under 0.8.1 as 15 memoryless runs. Historical
  lock SHA-256 remains
  `b6a46c51bf235f4bbe4465ee060f5d92cf0b0571a3835ce0c67ff062c0209d74`.
- Governing specification files are unchanged. GitHub Actions run `36063663628` for
  `a7b6f5c` passed both `validate` and `compose-integration` on the first attempt.
- Disabled-mode preflight at `a7b6f5c` passed the frozen contract, five scenarios,
  eleven-tool policy, Brain identity, clean reproducible Git identity, all services,
  observability APIs, and lab controls. It correctly remained not ready because live
  execution was not opted in, no `OPENAI_API_KEY` was present, and the local default
  model was `gpt-5-mini` rather than frozen `gpt-5.6-sol`.
- One local integration attempt could not reach Docker from the filesystem sandbox. An
  unrestricted attempt then exposed the absent ignored `.env`: empty PostgreSQL values
  prevented startup. No code was changed for either environmental issue; supplying the
  documented process-local values produced the clean 20/20 result above.

## Preserved Phase 7 Evidence

Complete in this checkpoint:

- Brain modes, private schema/storage, strict runtime projection, bounded deterministic
  retrieval, escaped untrusted rendering, lifecycle/dispute handling, frozen read-only
  verification, explicit failures, and audit identity are implemented.
- Benchmark compatibility, targeted calibration mode, frozen-run enforcement, portable
  locks, split Brain/agent/end-to-end measurements, evaluator separation, analytics,
  protocol pre-registration, ADR, runbook, and deterministic adversarial tests are
  implemented.
- All independent review constraints applicable before a Brain-enabled live run have
  an implementation and deterministic test path. No requirement was discarded, and
  no large or official evaluation was run.
- The authorized frozen snapshot and five-scenario smoke are complete. The result is
  negative: 0/5 investigation completion with 5/5 tool-budget exhaustion. Frozen-store
  integrity, read-only behavior, retrieval audit, and zero-write guarantees held.
- The postmortem documents the exact record, retrieval mechanism, tool-sequence overlap,
  and the boundary between observation, deterministic inference, and causal hypothesis.

Deferred and still requiring separate authorization:

- No further Phase 7 live investigation or smoke is authorized. Do not tune retrieval,
  rewrite memory, change budgets/prompts/tools, or delete the historical 0.97-confidence
  unverified self-report from this result.
- Any causal test of procedural anchoring requires the already designed, pre-registered
  contemporaneous memoryless and length-matched placebo arms. The official 100-plus-run
  study remains deferred.
Phase 7 is accepted complete. Do not alter the Phase 6 lock, frozen Brain snapshot,
protocol JSON, historical self-report, or frozen behavior while continuing the platform.

## Independent Review Disposition

Satisfied and enforced: structural evaluator isolation, the exact observable writer
projection, no score-conditioned behavior, unverified self-diagnoses, explicit modes,
frozen read-only hashes, fail-closed Brain errors, complete identity/audit metadata,
canonical replayable snapshots, isolated SQLite storage/telemetry, separate retrieval
budgets, escaped untrusted context, current-run-only evidence, contamination guards,
portable future locks, derived metric hygiene, and deterministic T1-T28-equivalent
coverage.

Already satisfied before review-driven hardening: the unchanged eleven read-only tools,
12-call/10-turn/120-second limits, stable investigator instruction and Diagnosis hashes,
runtime-owned current evidence validation, and local ignored storage boundary.

Changed because of review: exact field-by-field projection and import tests; explicit
`disabled`/`learn`/`frozen_eval`; self-report trust labels and dispute lifecycle; escaped
renderer and persistent-injection tests; Brain identity and per-run hashes; split
retrieval/agent/end-to-end latency and token accounting; evaluator-only fold ledger;
pre-registration; Brain calibration lock prohibition; and portable lock schema 2 with
legacy resolution.

Deferred by design: placebo snapshot generation, interleaved leave-one-scenario-out
orchestration, and the 100-plus-run official study require later explicit authorization.
Semantic/reflective memory, supervised confirmed outcomes, vectors, Engram, cross-agent
sharing, and off-machine evidence publication are outside Brain v1. No review
requirement is incompatible with the architecture.
