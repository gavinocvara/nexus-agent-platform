# Codex Handoff

Current state only. History lives in Git, `CHANGELOG.md`, and the ADRs. Rules live in
`AGENTS.md`.

## Checkpoint

- Branch `main`; local `HEAD` must equal `origin/main` at every checkpoint.
- Released: NEXUS `0.16.0` (Milestone G first cut) `915ea12`; GitHub Actions run
  `36493180253` passed both jobs, including the E2E gate step. Milestone F release
  `d6d39d3`, run `36491013537` green.
- Milestone G completion release: NEXUS `0.16.1`
  `9b05cdce16a25b1ae86c3e580951728e017b7df1`; GitHub Actions run `36494052048` passed
  `validate` and `compose-integration` on the first attempt.

## Milestone G Progress

- Harness: `PatchForgeE2EHarness` (real Runtime + ToolGateway + WorkspaceManager +
  FakeSandbox + Attestor), deterministic replay, fault injection (worktree, in-sandbox,
  refused cleanup, refused Nth lease renewal).
- `E2ERun.problems()` checks every run for expected outcome, classification, phase,
  loop count, and findings; a closed transcript through cleanup; workspace removal;
  runtime-owned evidence linkage; an untouched source repository (no push, merge, or
  approval); sandbox requests without network or secrets; memory off; scripted engine.
- Catalog: 21 scenarios covering success, the reproduction variants, targeted failure,
  successful bounded retry, full-suite failure, budget, cancellation, policy,
  workspace and lease failure, sandbox, engine, finalization failure, cleanup failure,
  in-run and post-final tamper, stale validation, unknown report evidence, and
  finalization-reserve use and refusal. The gate runs each twice (byte-identical).

## Exact Next Step

Milestone G is released and verified. Next is Milestone H - Benchmark v0 (small
synthetic defect corpus and reproducible harness), built on the Milestone G harness,
once the owner has weighed the finalization-reserve question below. No live model calls; Milestone I needs explicit owner authorization.

## Active Issues

- Design question for the owner: in `finalize`, the gateway refuses a non-report tool
  that would take the report's reserved call, but Runtime (per Milestone E) treats any
  failure during finalization as final, so the report is never submitted (scenario
  `finalization_reserve_protected`). This fails closed; making the reserve usable after
  a refusal would change Runtime lifecycle semantics.
- No known failing tests; the F/G intermittent failure was a test bug, fixed in 0.16.0.
- Remote branch `maintenance/repo-hygiene-claude` is unmerged and untouched; owner decides.

## Critical Constraints

- Atlas remains the outer policy, review, approval, persistence, and audit boundary.
- ToolGateway is the only engineering capability boundary; the model never gets Git.
- Runtime-attested evidence is authoritative; model output is narrative only.
- No unrestricted shell, network, secrets, memory/Brain, GitHub mutation, or live model
  calls. The owner's API key stays in ignored local state only.
- Preserve phase budgets, finalization reserve, disabled parallel calls, bounded loops,
  cleanup on every path, and Phase 5/6/7 frozen evidence.
