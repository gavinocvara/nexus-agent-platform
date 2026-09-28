# Codex Handoff

Current state only. History lives in Git, `CHANGELOG.md`, and the ADRs. Rules live in
`AGENTS.md`.

## Checkpoint

- Branch `main`; local `HEAD` must equal `origin/main` at every checkpoint.
- NEXUS `0.16.0`: PatchForge Milestones A-G complete. The Milestone G release is the
  commit that set 0.16.0; its SHA and GitHub Actions run are recorded here once CI is
  green.
- Previous release: 0.15.0 (Attestor) `d6d39d3`, GitHub Actions run `36491013537` green.
- Local release gate: `pip check` passed; Ruff format (159 files) and lint clean; strict
  mypy clean (88 files); pytest 618 passed, 23 deselected (six consecutive clean runs);
  5 scenarios validated; E2E gate passed; Compose config passed. Compose integration was
  not run locally (no Docker daemon); CI's `compose-integration` job is the gate.

## Milestone G Summary

- `nexus.patchforge.e2e`: `PatchForgeE2EHarness`, `ScriptedEngine`, and
  `materialize_fixture` run the production path deterministically (byte-identical
  replay).
- `nexus.patchforge.e2e_catalog`: calculator fixture; success plus one scenario per
  `PatchForgeFailure`; `python -m nexus.patchforge.e2e_catalog` is a CI gate.

## Exact Next Step

After CI is green for 0.16.0, begin PatchForge Milestone H - Benchmark v0: a small
synthetic defect corpus and a reproducible harness (see `ROADMAP.md`). Build it on the
Milestone G harness and fixtures. No live model calls; Milestone I needs explicit owner
authorization.

## Active Issues

- No known failing tests. The intermittent failure seen in Milestones F and G was a test
  that compared proposal commits on two separately created fixture repositories; fixed.
- Test modules are not type-checked in CI and carry pre-existing strict-mypy noise.
- Remote branch `maintenance/repo-hygiene-claude` is unmerged and untouched; owner decides.

## Critical Constraints

- Atlas remains the outer policy, review, approval, persistence, and audit boundary.
- ToolGateway is the only engineering capability boundary; the model never gets Git.
- Runtime-attested evidence is authoritative; model output is narrative only.
- No unrestricted shell, network, secrets, memory/Brain, GitHub mutation, or live model
  calls. The owner's API key stays in ignored local state only.
- Preserve phase budgets, finalization reserve, disabled parallel calls, bounded loops,
  cleanup on every path, and Phase 5/6/7 frozen evidence.
