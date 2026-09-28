# Codex Handoff

Current state only. History lives in Git, `CHANGELOG.md`, and the ADRs. Rules live in
`AGENTS.md`.

## Checkpoint

- Branch `main`; local `HEAD` must equal `origin/main` at every checkpoint.
- NEXUS `0.15.0`: PatchForge Milestones A-F complete. Milestone F (Attestor) release
  `d6d39d3c1f2954921d03631dc7b1d4add5dca4b8`; GitHub Actions run `36491013537` passed
  `validate` and `compose-integration` on the first attempt.
- Local release gate: `pip check` passed; Ruff format (156 files) and lint clean; strict
  mypy clean (86 files); pytest 602 passed, 23 deselected; 5 scenarios validated; Compose
  config passed. Compose integration was not run locally (no Docker daemon); CI's
  `compose-integration` job is the integration gate.

## Milestone F Summary

- `WorkspaceManager.propose_commit`: runtime-owned proposal commit, byte-identical diff.
- Runtime workspace fingerprints and pre-cleanup `FinalWorkspaceCapture`.
- `PatchForgeAttestor` + `LocalArtifactStore`: reproduction, final-tree checks, diff,
  scope/protected/sensitive/test/size findings, and tamper checks; only a clean
  attestation is `patch_proposed` (ADR 0010, CHANGELOG 0.15.0).

## Milestone G Progress (in progress, unreleased)

Done:

1. `nexus.patchforge.e2e`: `PatchForgeE2EHarness` composes WorkspaceManager, ToolGateway,
   FakeSandbox, Runtime, and Attestor around a `ScriptedEngine`; `materialize_fixture`
   builds fixture repositories with a fixed Git identity and date. Fixed clocks and
   content-derived IDs make a scenario replay to a byte-identical `PatchResult`. Fault
   hooks: out-of-band worktree steps, in-sandbox hooks, and a refused cleanup.
2. `nexus.patchforge.e2e_catalog`: calculator fixture, profile, budgets, step helpers,
   and the `patch_proposed` scenario.

3. Catalog: success plus one scenario per `PatchForgeFailure` (validation, budget,
   policy, cancelled, sandbox, workspace, engine, attestation, cleanup), each asserting
   outcome and classification. `python -m nexus.patchforge.e2e_catalog` runs every
   scenario twice, requires the expected outcome and a byte-identical replay, and exits
   nonzero otherwise; CI runs it as "Validate PatchForge deterministic E2E".

## Exact Next Step

Release 0.16.0 (ADR 0010 note, ROADMAP, CHANGELOG, README status, full gate, green CI),
then hand off Milestone H - Benchmark v0. No live model calls.

## Active Issues

- No known failing tests. Focused PatchForge suite: 352 passed.
- During Milestone F, one focused-suite run reported a single failure that was not
  captured and did not recur in 19 later runs. Watch for PatchForge flakiness.
- Test modules are not type-checked in CI and carry pre-existing strict-mypy noise
  (`HttpUrl` literals, fake gateway locals).
- Remote branch `maintenance/repo-hygiene-claude` is unmerged and untouched; owner decides.

## Critical Constraints

- Atlas remains the outer policy, review, approval, persistence, and audit boundary.
- ToolGateway is the only engineering capability boundary; the model never gets Git.
- Runtime-attested evidence is authoritative; model output is narrative only.
- No unrestricted shell, network, secrets, memory/Brain, GitHub mutation, or live model
  calls. The owner's API key stays in ignored local state only.
- Preserve phase budgets, finalization reserve, disabled parallel calls, bounded loops,
  cleanup on every path, and Phase 5/6/7 frozen evidence.
