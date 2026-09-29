# Codex Handoff

Current state only. History lives in Git, `CHANGELOG.md`, and the ADRs. Rules live in
`AGENTS.md`. The complete resume state (architecture status, credentials, rules) is in
`PROJECT_STATE.md`.

## Checkpoint

- Branch `main`; local `HEAD` must equal `origin/main` at every checkpoint
  (`git rev-parse HEAD origin/main`, `git diff HEAD`, `git diff origin/main...HEAD`).
- NEXUS `0.19.0` (Milestone J, SentinelQA-lite) is the commit containing this file
  (`git rev-parse HEAD`). Its CI run must be recorded in `PROJECT_STATE.md` once green.
- Previous: 0.18.0 Milestone I `b613072` (run `36540394002` green); 0.17.0 Milestone H
  `693047c`; 0.16.2 `1984b8e`; 0.16.1 `9b05cdc`; 0.15.0 `d6d39d3`.

## Completed Unit: Milestone J, SentinelQA-lite (0.19.0)

- `nexus.sentinelqa` (ADR 0011): `SpecificationLock` from Git objects of the source
  commit; `SentinelQAVerifier` verifies a `patch_proposed` result on fresh `.git`-free
  trees against the pristine specification only (restored tests, candidate-added tests
  excluded), with baseline and verification runs through any `SandboxExecutor`, count
  rules from pytest's own summary, tree fingerprints, and a cross-check of PatchForge's
  attested checks. Verdicts fail closed (`inconclusive` > `failed` > `passed`) and
  convert to Atlas `ReviewResult`s for `sentinelqa.reviewer`.
- Gates: `python -m nexus.sentinelqa` (29 adversarial scenarios, byte-identical replay),
  `tests/unit/test_sentinelqa_*.py`, and Benchmark v0 now requiring SentinelQA to pass
  `reference` 5/5 and fail `fix_and_edit_tests` 5/5 with `specification_modified`, with
  zero disagreements against the hidden ground truth. Both are CI steps.
- The Milestone H finding (specification-weakening proposals exposed only by ground
  truth) is closed.

## Validation (0.19.0 local release gate, Linux, Python 3.12.3)

- `uv pip check` compatible; Ruff format and lint clean (182 files); strict mypy clean
  (101 source files).
- pytest: 744 passed, 23 deselected (0.18.0: 670 passed).
- `python -m nexus.patchforge.e2e_catalog`: 23 scenarios passed, byte-identical replay.
- `python -m nexus.patchforge.benchmark_corpus`: passed (SentinelQA agreement included).
- `python -m nexus.sentinelqa`: 29 scenarios passed, byte-identical replay.
- `python -m nexus.lab.scenarios validate`: 5 scenarios. `docker compose config`: valid.
- Not run locally: Compose integration (no Docker daemon). CI's `compose-integration`
  job is the integration gate. Frozen `docs/experiments/` unchanged.

## Exact Next Step

1. Resident Software Engineer foundation (`nexus.software_engineer`): typed settings
   (disabled by default), budgets, engineering task and candidate model, risk classifier,
   ship policy, trust boundary for untrusted text, cycle state machine, report model,
   deterministic tests. See `PROJECT_STATE.md` for the full plan and rules.
2. The live PatchForge run remains unexecuted and owner-authorized only. It is now
   SentinelQA-reviewed end to end (`python -m nexus.patchforge.live run ...` prints the
   verdict). Run it only when the owner explicitly authorizes the cost.

## Active Issues

- SentinelQA's runner has no Docker integration proof yet; deterministic gates use the
  scripted or oracle sandbox. Add an integration test when the Compose job can build a
  Python image with pytest.
- Test modules are not type-checked in CI and carry pre-existing strict-mypy noise.
- Remote branch `maintenance/repo-hygiene-claude` is unmerged and untouched; owner decides.

## Critical Constraints

- Atlas remains the outer policy, review, approval, persistence, and audit boundary.
  SentinelQA reviews; it never approves. Humans approve.
- ToolGateway is the only engineering capability boundary; the model never gets Git.
- Runtime-attested evidence is authoritative; model output is narrative only.
- The specification lock and SentinelQA finding vocabulary are safety controls: changes
  need an ADR and owner review, never a silent edit by an agent.
- No unrestricted shell, network, secrets, memory/Brain, GitHub mutation, or live model
  calls without explicit owner authorization. The owner's API key stays in ignored local
  state only.
- Preserve phase budgets, the finalization reserve, disabled parallel calls, bounded
  loops, cleanup on every path, and Phase 5/6/7 frozen evidence (do not clean `.nexus/`).
