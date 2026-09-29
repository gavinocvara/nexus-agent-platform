# Codex Handoff

Current state only. History lives in Git, `CHANGELOG.md`, and the ADRs. Rules live in
`AGENTS.md`. The complete resume state (architecture status, credentials, rules) is in
`PROJECT_STATE.md`.

## Checkpoint

- Branch `main`; local `HEAD` must equal `origin/main` at every checkpoint
  (`git rev-parse HEAD origin/main`, `git diff HEAD`, `git diff origin/main...HEAD`).
- NEXUS `0.24.0` (resident Software Engineer owner-approved draft pull requests) is the
  commit containing this file (`git rev-parse HEAD`). Record its CI run in
  `PROJECT_STATE.md` once green.
- Previous: `2632597` marker-scanner fix (run `36554057081` green); 0.23.0 model
  recipes `035e0f5` (run `36553508853` green); 0.22.0 evaluation `f10a5ef` (run `36552400237`); 0.21.0
  executor v1 `aad015d` + `fddcf16`; 0.20.0 foundation `c045de8`; 0.19.0 SentinelQA-lite
  `09b8c99`; 0.18.0 `b613072`. All green.

## Completed Unit: owner-approved draft pull requests (0.24.0)

- `nexus.software_engineer.publish`: `Publisher` protocol, `GitHubDraftPullRequestPublisher`
  (REST: blobs, tree, commit, ref, draft PR; blob and tree SHAs verified against local
  objects; moved base, existing branch, rejected drafts, bad credentials fail closed;
  self-created branch deleted on failure; never merges), `bundle_from_branch` (re-applies
  the validated patch on the base in a scratch clone and compares `git write-tree`),
  `render_pull_request`, `RecordingPublisher` for tests.
- `nexus.software_engineer.approval` + CLI `decide` / `publish`: typed `OwnerDecision`
  once per request; publication only for SHIP by the configured owner with every gate
  passed, once; `PublishedChange` persisted under `publications/` and remembered as a
  validated fact.
- `PatchForgeExecutor(publisher=...)`: `can_ship`, `ship` (only the change it produced),
  `rollback` withdraws a published change. CLI wires a cycle publisher only with
  `PUBLISH_FROM_CYCLE=true` + `autonomous_low_risk` + token; the workflow never gets the
  token and now uploads the branch clone and evidence (hidden paths included).
- `contains_credential` (trust module) adds GitHub/Slack/cloud secret shapes to every
  memory, notification, report, and pull-request text check.

## Validation (0.24.0 local release gate, Linux, Python 3.12.3)

- Ruff format and lint clean; strict mypy clean (120 files).
- pytest: 864 passed, 23 deselected (0.23.0: 854 passed, 23 deselected).
- `python -m nexus.patchforge.e2e_catalog`: 23 scenarios passed, byte-identical replay.
- `python -m nexus.patchforge.benchmark_corpus`: passed (SentinelQA agreement included).
- `python -m nexus.sentinelqa`: 29 scenarios passed, byte-identical replay.
- `python -m nexus.software_engineer.evaluation`: 28 scenarios passed, byte-identical.
- `python -m nexus.lab.scenarios validate`: 5 scenarios. `docker compose config`: valid.
- `python -m nexus.software_engineer preflight`: `enabled=False mode=dry_run sandbox=none
  github_token_present=False publish_from_cycle=False`.
- Not run locally: Compose integration (no Docker daemon); CI's `compose-integration`
  job is the integration gate. No real GitHub or model call was made. Frozen
  `docs/experiments/` unchanged.

## Exact Next Step

1. Owner step, no code: one `propose` + `local_process` cycle, `decide --verdict ship`,
   `publish` with a fine-grained token; verify the draft PR and record the outcome in
   `PROJECT_STATE.md`. Do not enable `PUBLISH_FROM_CYCLE` before that.
2. Then Slack-delivered `OwnerCommand`s (signature-verified) replacing the `decide` CLI;
   the first owner-authorized model-recipe run; SentinelQA review of candidate-changed
   tests; GitHub issue intake. The live PatchForge run stays owner-authorized only.

## Active Issues

- Publishing is untested against the real GitHub API (fake API only); the first real run
  may surface API-shape differences (draft support on the plan, date normalization).
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
