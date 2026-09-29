# Codex Handoff

Current state only. History lives in Git, `CHANGELOG.md`, and the ADRs. Rules live in
`AGENTS.md`.

## Checkpoint

- Branch `main`; local `HEAD` must equal `origin/main` at every checkpoint
  (`git rev-parse HEAD origin/main`, `git diff HEAD`, `git diff origin/main...HEAD`).
- NEXUS `0.16.2` (finalization-reserve hardening), release
  `1984b8e09fe1d4766b55271ef88844e3b8647eaf`; GitHub Actions run `36537351468` passed
  both jobs. HEAD is the commit containing this file (`git rev-parse HEAD`).
- Verified releases: 0.16.1 Milestone G `9b05cdc` (GitHub Actions run `36494052048`
  green); 0.15.0 Milestone F `d6d39d3` (run `36491013537` green).

## Completed Unit: Finalization-Reserve Hardening (0.16.2)

- `GatewayReserveRefusal` is raised only when a non-report finalize call would take the
  report's reserved call or output. Runtime records a single `finalize -> finalize`
  failure transition (`budget_exhausted`, or an earlier primary failure) and a
  `ReserveRefusalRecord`, then accepts only `submit_report`. Anything else ends
  finalization. The outcome stays `partial`; the budget ledger is unchanged.
- Proven by lifecycle, gateway, coordinator, and E2E tests (scenarios
  `finalization_reserve_protected`, `reserve_refusal_then_other_action`,
  `second_report_attempt`; unrelated failures `finalization_failed`,
  `unknown_report_evidence`).

## Validation (0.16.2 local release gate)

- `pip check` passed; Ruff format (159 files) and lint clean; strict mypy clean (88).
- pytest: 644 passed, 23 deselected, in two consecutive runs. PatchForge focused suite:
  378 passed.
- `python -m nexus.lab.scenarios validate`: 5 scenarios. `python -m
  nexus.patchforge.e2e_catalog`: 23 scenarios passed, byte-identical replay.
- Compose config passed. Secret-pattern scan of tracked files: no matches. Frozen
  `docs/experiments/` unchanged; `test_atlas_isolation.py` hash pins pass.
- Not run locally: Compose integration (no Docker daemon here). CI's
  `compose-integration` job is the integration gate.

## Milestone H Progress (in progress, unreleased)

- `nexus.patchforge.benchmark`: `GroundTruth` (evaluator-only accepted contents plus
  must-not-change files), `ContentOracleSandbox` (scores worktree contents without
  executing code; generic output reveals no truth; rejects non-profile commands; symlinked
  targets read as absent), an independent `evaluate` that re-applies the attested patch
  artifact to a clean clone, and deterministic `BenchmarkReport`s named by corpus hash.
- `nexus.patchforge.benchmark_corpus`: five synthetic defects; engines `reference`,
  `noop`, `test_editor`, and `fix_and_edit_tests`. `python -m
  nexus.patchforge.benchmark_corpus [--output DIR]` is a CI gate: expected
  resolved/false-proposal counts, zero invariant violations, byte-identical replay.
- The E2E harness accepts a `sandbox_factory`, exposes `E2ERun.root`, and splits
  `invariant_problems()` from scenario expectations.

## Exact Next Step

Release Milestone H as 0.17.0 (ADR 0010 note, ROADMAP, CHANGELOG, README, full gate,
green CI). Then Milestone I - live engine integration boundary: build a model-backed
`RuntimeEngine` adapter deterministically (typed action parsing, malformed-output and
injection handling, budgets) behind an explicit opt-in. Stop before any live call and
record the exact command for owner authorization.

## Active Issues

- Benchmark finding: with `allow_test_file_changes=True`, PatchForge proposes patches
  that also weaken the specification tests (engine `fix_and_edit_tests`: 5/5 proposals,
  all rejected by the independent evaluator). The attestor lists the test changes but
  does not block them; SentinelQA (Milestone J) must re-validate against the original
  tests.
- None failing. Test modules are not type-checked in CI and carry pre-existing
  strict-mypy noise (`HttpUrl` literals, fake gateway locals).
- Remote branch `maintenance/repo-hygiene-claude` is unmerged and untouched; owner decides.

## Critical Constraints

- Atlas remains the outer policy, review, approval, persistence, and audit boundary.
- ToolGateway is the only engineering capability boundary; the model never gets Git.
- Runtime-attested evidence is authoritative; model output is narrative only.
- No unrestricted shell, network, secrets, memory/Brain, GitHub mutation, or live model
  calls without explicit owner authorization. The owner's API key stays in ignored local
  state only.
- Preserve phase budgets, the finalization reserve, disabled parallel calls, bounded
  loops, cleanup on every path, and Phase 5/6/7 frozen evidence (do not clean `.nexus/`).
