# Codex Handoff

Current state only. History lives in Git, `CHANGELOG.md`, and the ADRs. Rules live in
`AGENTS.md`.

## Checkpoint

- Branch `main`; local `HEAD` must equal `origin/main` at every checkpoint.
- NEXUS `0.15.0`: PatchForge Milestones A-F complete. Milestone F (Attestor) release is
  the commit that set 0.15.0; its SHA and GitHub Actions run are recorded here once CI is
  green.
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

## Exact Next Step

After CI is green for 0.15.0, begin PatchForge Milestone G - Deterministic E2E: a
scripted happy path plus every required failure/partial path through Runtime and
Attestor end to end (see `ROADMAP.md` and ADR 0010). Reuse the real-path fixtures in
`tests/unit/test_patchforge_attestor.py`. No live model calls.

## Active Issues

- No known failing tests.
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
