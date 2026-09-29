# Codex Handoff

Current state only. History lives in Git, `CHANGELOG.md`, and the ADRs. Rules live in
`AGENTS.md`. The complete resume state (architecture status, credentials, rules) is in
`PROJECT_STATE.md`.

## Checkpoint

- Branch `main`; local `HEAD` must equal `origin/main` at every checkpoint
  (`git rev-parse HEAD origin/main`, `git diff HEAD`, `git diff origin/main...HEAD`).
- NEXUS `0.21.0` (resident Software Engineer executor v1) is the commit containing this
  file (`git rev-parse HEAD`). Record its CI run in `PROJECT_STATE.md` once green.
- Previous: 0.20.0 foundation `c045de8` (run `36549244409` green); 0.19.0 Milestone J
  SentinelQA-lite `09b8c99` (run `36546322678` green); 0.18.0 `b613072` (run
  `36540394002` green).

## Completed Unit: Resident Software Engineer executor v1 (0.21.0)

- `PatchForgeExecutor` (mechanical recipes `formatting` and `dead_code_removal`) runs the
  real PatchForge path from the operator checkout, has SentinelQA verify the attested
  patch, maps checks and verdict to gates with evidence hashes, and materializes a local
  `nexus/software-engineer/<cycle>` branch. `can_ship` is False: validated changes become
  approval requests. `LocalProcessSandbox` (explicit enablement, scrubbed environment,
  bounds, no shell, no `.git`) serves ephemeral runners without Docker.
- Policy: plan-only outcomes and unshippable ship decisions become approval requests.
  SentinelQA: reproduction commands need no pytest summary.
- 0.20.0 (foundation) shipped in `c045de8`: contracts, risk, policy, trust, memory,
  inspection, self-review, notifier, cycle, CLI, scheduled workflow, ADR 0012, runbook.

## Validation (0.21.0 local release gate, Linux, Python 3.12.3)

- Ruff format and lint clean; strict mypy clean (117 files).
- pytest: 818 passed, 23 deselected (0.20.0: 807; 0.19.0: 744).
- `python -m nexus.patchforge.e2e_catalog`: 23 scenarios passed, byte-identical replay.
- `python -m nexus.patchforge.benchmark_corpus`: passed (SentinelQA agreement included).
- `python -m nexus.sentinelqa`: 29 scenarios passed, byte-identical replay.
- `python -m nexus.lab.scenarios validate`: 5 scenarios. `docker compose config`: valid.
- `python -m nexus.software_engineer preflight`: `enabled=False mode=dry_run sandbox=none`.
- Not run locally: Compose integration (no Docker daemon). CI's `compose-integration`
  job is the integration gate. Frozen `docs/experiments/` unchanged.

## Exact Next Step

1. Publisher for approved candidate branches (push + draft PR, never merge) with a
   deliberately supplied write token, only after an `OwnerDecision(ship)`. Then
   model-backed recipes behind `ModelClient` with zero default budgets, extended
   adversarial evaluation, and Slack-delivered `OwnerCommand`s. See `PROJECT_STATE.md`.
2. The live PatchForge run remains unexecuted and owner-authorized only; it is
   SentinelQA-reviewed end to end.

## Active Issues

- The engineer produces verified branches only for mechanical recipes and cannot publish
  them; other categories are approval-only plans.
- SentinelQA's runner has no Docker integration proof yet.
- Test modules are not type-checked in CI and carry pre-existing strict-mypy noise.
- Remote branch `maintenance/repo-hygiene-claude` is unmerged and untouched; owner decides.

## Critical Constraints

- Atlas remains the outer policy, review, approval, persistence, and audit boundary.
  SentinelQA reviews; the resident engineer proposes; humans approve.
- ToolGateway is the only engineering capability boundary; the model never gets Git.
- Runtime-attested evidence is authoritative; model output is narrative only.
- Governing paths (`GOVERNING_PATH_PREFIXES` in `nexus.software_engineer.risk`), the
  specification lock, and finding vocabularies are safety controls: changes need an ADR
  and owner review, never a silent edit by an agent.
- No unrestricted shell, network, secrets, memory/Brain, GitHub mutation, or live model
  calls without explicit owner authorization. The owner's API key and Slack webhook stay
  in ignored local state or CI secrets only.
- Preserve phase budgets, the finalization reserve, disabled parallel calls, bounded
  loops, cleanup on every path, and Phase 5/6/7 frozen evidence (do not clean `.nexus/`).
