# Codex Handoff

Current state only. History lives in Git, `CHANGELOG.md`, and the ADRs. Rules live in
`AGENTS.md`. The complete resume state (architecture status, credentials, rules) is in
`PROJECT_STATE.md`.

## Checkpoint

- Branch `main`; local `HEAD` must equal `origin/main` at every checkpoint
  (`git rev-parse HEAD origin/main`, `git diff HEAD`, `git diff origin/main...HEAD`).
- NEXUS `0.25.0` (resident Software Engineer Slack decisions and issue intake) is the
  commit containing this file (`git rev-parse HEAD`). Record its CI run in
  `PROJECT_STATE.md` once green.
- Previous: `bcb04ae` Slack commands, `5bdbfc2` issue intake, `ebca518` Git-date CI fix,
  `6dea458` evaluation scenarios, 0.24.0 publisher `6210fb4` (its CI run `36557239380`
  failed on the Git 2.55 date format fixed in `ebca518`); `2632597` marker-scanner fix
  (run `36554057081` green); 0.23.0 model recipes `035e0f5` (run `36553508853` green); 0.22.0 evaluation `f10a5ef` (run `36552400237`); 0.21.0
  executor v1 `aad015d` + `fddcf16`; 0.20.0 foundation `c045de8`; 0.19.0 SentinelQA-lite
  `09b8c99`; 0.18.0 `b613072`. All green.

## Completed Units since 0.23.0 (0.24.0 and 0.25.0)

- 0.25.0: `slack_commands.py` + `serve-slack` (Slack `v0` HMAC, replay window, owner
  member id; records through `decide`; never publishes); `issues.py` read-only issue
  intake as untrusted signals (opt-in `READ_ISSUES`, workflow `issues: read`); owner
  REJECT honoured by the candidate generator; evaluation catalog at 30 scenarios with
  publication cases; Git author dates normalized across Git versions.
- 0.24.0: `publish.py` (`GitHubDraftPullRequestPublisher`: blobs, tree, commit, ref,
  draft PR; blob and tree SHAs verified against local objects; moved base, existing
  branch, rejected drafts, bad credentials fail closed; never merges),
  `bundle_from_branch` (re-applies the validated patch and compares `git write-tree`),
  `approval.py` + CLI `decide` / `publish` (typed `OwnerDecision` once per request;
  publication only for SHIP with every gate passed, once), `PatchForgeExecutor(publisher)`
  (`ship` only its own change, `rollback` withdraws), `contains_credential`, workflow
  artifact with branch clone. Details in `CHANGELOG.md` and ADR 0012.

## Validation (0.25.0 local release gate, Linux, Python 3.12.3)

- Ruff format and lint clean; strict mypy clean (122 files); `uv pip check` compatible.
- pytest: 876 passed, 23 deselected (0.24.0: 864; 0.23.0: 854).
- `python -m nexus.patchforge.e2e_catalog`: 23 scenarios passed, byte-identical replay.
- `python -m nexus.patchforge.benchmark_corpus`: passed (SentinelQA agreement included).
- `python -m nexus.sentinelqa`: 29 scenarios passed, byte-identical replay.
- `python -m nexus.software_engineer.evaluation`: 30 scenarios passed, byte-identical.
- `python -m nexus.lab.scenarios validate`: 5 scenarios. `docker compose config`: valid.
- `python -m nexus.software_engineer preflight`: `enabled=False mode=dry_run sandbox=none
  github_token_present=False publish_from_cycle=False read_issues=False
  slack_owner_user_id_set=False`.
- Real dry-run cycle over this repository (scratch state): `decision=no_work`, 56 signals,
  2 report-only candidates, no memory writes.
- Not run locally: Compose integration (no Docker daemon); CI's `compose-integration`
  job is the integration gate. No real GitHub, Slack, or model call was made. Frozen
  `docs/experiments/` unchanged.

## Exact Next Step

1. Owner step, no code: one `propose` + `local_process` cycle, `decide --verdict ship`,
   `publish` with a fine-grained token; verify the draft PR and record the outcome in
   `PROJECT_STATE.md`. Do not enable `PUBLISH_FROM_CYCLE` before that.
2. Owner steps: host `serve-slack` for Slack decisions; set `READ_ISSUES=true` when
   wanted. Then the first owner-authorized model-recipe run and SentinelQA review of
   candidate-changed tests. The live PatchForge run stays owner-authorized only.

## Active Issues

- Publishing, issue intake, and the Slack receiver are untested against the real
  services (fakes only); the first real runs are owner steps and may surface API-shape
  differences (draft support on the plan, rate limits, Slack retries).
- SentinelQA's runner has no Docker integration proof yet.
- Test modules are not type-checked in CI and carry pre-existing strict-mypy noise.
- Remote branch `maintenance/repo-hygiene-claude` is unmerged and untouched; owner decides.

## Critical Constraints

- Atlas remains the outer policy, review, approval, persistence, and audit boundary.
  SentinelQA reviews; the resident engineer proposes; humans approve and merge. A draft
  pull request is the engineer's maximum reach.
- ToolGateway is the only engineering capability boundary; the model never gets Git.
- Runtime-attested evidence is authoritative; model output is narrative only.
- Governing paths (`GOVERNING_PATH_PREFIXES` in `nexus.software_engineer.risk`), the
  specification lock, finding vocabularies, and the publication checks are safety
  controls: changes need an ADR and owner review, never a silent edit by an agent.
- No unrestricted shell, network, secrets, memory/Brain, GitHub mutation, or live model
  calls without explicit owner authorization. The owner's API key, Slack webhook, and
  GitHub token stay in ignored local state or CI secrets only; the scheduled workflow's
  token is read-only.
- Preserve phase budgets, the finalization reserve, disabled parallel calls, bounded
  loops, cleanup on every path, and Phase 5/6/7 frozen evidence (do not clean `.nexus/`).
