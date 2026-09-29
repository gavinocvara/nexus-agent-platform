# Codex Handoff

Current state only. History lives in Git, `CHANGELOG.md`, and the ADRs. Rules live in
`AGENTS.md`. The complete resume state (architecture status, credentials, rules) is in
`PROJECT_STATE.md`.

## Checkpoint

- Branch `main`; local `HEAD` must equal `origin/main` at every checkpoint
  (`git rev-parse HEAD origin/main`, `git diff HEAD`, `git diff origin/main...HEAD`).
- NEXUS `0.20.0` (resident Software Engineer foundation) is the commit containing this
  file (`git rev-parse HEAD`). Record its CI run in `PROJECT_STATE.md` once green.
- Previous: 0.19.0 Milestone J SentinelQA-lite `09b8c99` (GitHub Actions run
  `36546322678` green); 0.18.0 Milestone I `b613072` (run `36540394002` green).

## Completed Unit: Resident Software Engineer foundation (0.20.0)

- `nexus.software_engineer` (ADR 0012), disabled by default, `dry_run` mode, no model:
  contracts, `classify_change`, `ShipPolicy`, `UntrustedText`/`OwnerCommand` trust
  boundary, `EngineerMemoryStore` (private namespace `software_engineer.resident`),
  `RepositoryInspector` + `CandidateGenerator`, `SelfReviewer`, `Notifier` +
  `SlackWebhookTransport`, `EngineeringCycle` with persisted canonical records and daily
  reports, `python -m nexus.software_engineer {preflight,inspect,cycle}`, and a
  disabled-by-default scheduled workflow with a read-only token.
- 63 tests in `tests/unit/test_software_engineer_*.py`; CI runs them plus `preflight`.

## Validation (0.20.0 local release gate, Linux, Python 3.12.3)

- `uv pip check` compatible; Ruff format and lint clean; strict mypy clean (114 files).
- pytest: 807 passed, 23 deselected (0.19.0: 744).
- `python -m nexus.patchforge.e2e_catalog`: 23 scenarios passed, byte-identical replay.
- `python -m nexus.patchforge.benchmark_corpus`: passed (SentinelQA agreement included).
- `python -m nexus.sentinelqa`: 29 scenarios passed, byte-identical replay.
- `python -m nexus.lab.scenarios validate`: 5 scenarios. `docker compose config`: valid.
- `python -m nexus.software_engineer preflight`: `enabled=False mode=dry_run`.
- Not run locally: Compose integration (no Docker daemon). CI's `compose-integration`
  job is the integration gate. Frozen `docs/experiments/` unchanged.

## Exact Next Step

1. Resident engineer executor: a `CandidateExecutor` that provisions an isolated branch,
   runs PatchForge with this repository as the operator profile, has SentinelQA verify the
   candidate, runs the required gates in a sandboxed runner, and ships only by
   fast-forwarding an approved branch. Keep `autonomous_low_risk` behind the owner's
   explicit mode change. Then model-backed investigation behind `ModelClient`, and
   Slack-delivered `OwnerCommand`s.
2. The live PatchForge run remains unexecuted and owner-authorized only; it is
   SentinelQA-reviewed end to end.

## Active Issues

- The engineer can plan and propose; it cannot yet produce code changes (no executor).
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
