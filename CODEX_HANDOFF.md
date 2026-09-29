# Codex Handoff

Canonical transfer document for the next coding agent. Current state only; history is in
Git and `CHANGELOG.md`, rules in `AGENTS.md`, the long-form resume state in
`PROJECT_STATE.md`. Read this file, then only the source and test files named below.

## Checkpoint

| Item | Value |
| --- | --- |
| Version | `0.26.0` (`pyproject.toml`) |
| HEAD | the commit containing this file; `git rev-parse HEAD` must equal `git rev-parse origin/main` |
| Working tree | clean at the checkpoint (`git status --short` empty) |
| Branch | `main` (direct pushes by the owner's agent sessions; no PR flow for this repo) |

Commits created in this run, oldest first: `2632597` marker scanner fix, `6210fb4` 0.24.0
publisher, `6dea458` publication scenarios, `ebca518` Git-date CI fix, `5bdbfc2` issue
intake, `bcb04ae` Slack commands, `cd46730` 0.25.0, `5f79cfe` cost accounting, `8622b81`
docs, `9f4d70b` SentinelQA harness boundary, `2cfd87e` brain, `36873a1` run lease, and the
0.26.0 checkpoint commit containing this file. CI (`validate` + `compose-integration`) was
green for every one of them observed before the last push (`6210fb4` and `6dea458`
failed once on a Git 2.55 date format, fixed in `ebca518`).

## Subsystem status

| Subsystem | Status |
| --- | --- |
| SentinelQA-lite | Complete. Specification lock from Git objects; fresh pristine/candidate trees; verification tree with the pristine specification restored; count rules; cross-check; **harness boundary** (shadowing modules are evaluation config, `harness_tampering` scans added source lines); 32 adversarial scenarios replayed byte-identically; Benchmark v0 agreement; gates Atlas approval. Known blind spot: in-process test manipulation that leaves counts, outcomes, and all scanned markers intact. |
| Persistent brain | Complete for this scope. Private SQLite namespace `software_engineer.resident`; categories incl. root cause and recurring pattern; OBSERVATION / INFERENCE / VALIDATED_FACT / OWNER_DECISION / FAILED_HYPOTHESIS; provenance, validity, versions, supersession, dispute, dedup; trusted-only retrieval; typed `KnowledgeExport`; learns root causes (validated), recurrence and overconfidence (observations); never consulted by policy or risk. |
| Daily scheduler/runtime | Complete. Workflow `.github/workflows/software-engineer.yml` (cron + dispatch, gated by repository variable, read-only token, concurrency group, artifact upload); local `run.lock` lease with interrupted-run recovery; budgets (runtime, turns, tool calls, model calls, tokens, cost); `no_work` is a normal outcome; record + report persisted on every path. **Production scheduling is not enabled** (variable unset). |
| Slack | Notifier (webhook from env) and `serve-slack` owner commands (HMAC v0, replay window, owner member id; decisions only). Fake-backed tests only; no real Slack call ever made. |
| GitHub publisher | `decide` + `publish` open a **draft PR only** after an owner SHIP, tree re-derived from the validated patch, remote blob/tree SHAs verified, never merges. Fake-backed tests only; no real publication ever made. Read-only issue intake is opt-in. |
| Autonomous shipping | Possible only with `PUBLISH_FROM_CYCLE=true` + `autonomous_low_risk` + token, LOW risk, every gate passed, clean self-review; still a draft PR. Off by default; the workflow never receives the token. |
| Model spend | Off. Needs model, confirmation, positive call/token budgets, both prices, positive `MAX_COST_USD`, and the key; no live call ever made. |

## Validation (0.26.0 local gate, Linux, Python 3.12.3)

- `uv pip check` compatible; Ruff format/lint clean; strict mypy clean (125 files).
- pytest: 898 passed, 23 deselected (0.25.0: 876; 0.24.0: 864).
- E2E gate 23/23, Benchmark v0 gate passed, SentinelQA gate 32/32, engineer evaluation
  gate 30/30 (all byte-identical replays), `nexus.lab.scenarios validate` 5, Compose
  config valid, secret scan of tracked files clean (no credential shapes in tracked files; no .env, .nexus, sqlite, or patch files tracked), preflight all-off defaults.
- Not run locally: Compose integration (CI job `compose-integration` is the gate).

## Known risks and unfinished work

- Publisher, issue source, and Slack receiver are proven only against fakes; first real
  runs are owner steps and may surface API differences.
- SentinelQA cannot see in-process manipulation without the scanned markers; the Docker
  runner proof and out-of-process oracles are deferred.
- Test modules are not strict-mypy-checked in CI (pre-existing noise).
- Remote branch `maintenance/repo-hygiene-claude` is unmerged; owner decides.
- Deferred by design: test repair (SentinelQA treats tests as the specification), memory
  consolidation beyond the deterministic rules above, hosted Slack receiver.

## Exact next task

Owner steps, no code: (1) run one `propose` + `local_process` cycle, `decide --verdict
ship`, `publish` with a fine-grained token, verify the draft PR; (2) host `serve-slack`
if Slack decisions are wanted; (3) set `READ_ISSUES=true` if issues should feed the daily
report. Engineering next, only with owner authorization: SentinelQA review of
candidate-changed tests (an ADR change), then the first owner-authorized model-recipe
run. The live PatchForge run remains unexecuted and owner-authorized only.

## Files that matter

- SentinelQA: `src/nexus/sentinelqa/{models,lock,verifier,tamper,catalog,harness}.py`;
  tests `tests/unit/test_sentinelqa_*.py`.
- Engineer: `src/nexus/software_engineer/{config,models,cycle,runtime,memory,policy,risk,
  trust,inspect,issues,executor,recipes,sandbox,pricing,publish,approval,slack_commands,
  notify,report,evaluation,__main__}.py`; tests `tests/unit/test_software_engineer_*.py`.
- Docs: ADR 0011, ADR 0012, `docs/runbooks/{sentinelqa,software-engineer}.md`,
  `PROJECT_STATE.md`.

## Resume and validation commands

```bash
uv venv --python 3.12 <outside repo> && uv pip install -e ".[dev]"   # or pip install -e ".[dev]"
git rev-parse HEAD origin/main && git status --short
python -m ruff format --check . && python -m ruff check . && python -m mypy
python -m pytest -q
python -m nexus.patchforge.e2e_catalog && python -m nexus.patchforge.benchmark_corpus
python -m nexus.sentinelqa && python -m nexus.software_engineer.evaluation
python -m nexus.lab.scenarios validate && docker compose config --quiet
python -m nexus.software_engineer preflight
```

## Invariants that must not be violated

- Agents act only through narrow typed tools; no unrestricted shell, filesystem, network,
  Git, database, or credential access. Model output is narrative; evidence comes only
  from runtime code; never fabricate tests, metrics, or CI results.
- PatchForge proposes; SentinelQA verifies against the pristine specification; Atlas
  records; a human approves and merges. Draft PRs only; no auto-merge; silence is never
  approval; SHIP / REVISE / REJECT come only from the configured human owner.
- Memory carries knowledge, never authority: nothing in memory may change shipping
  permissions, approval thresholds, security policy, tool authorization, spending limits,
  autonomy rules, or destructive-action policy. Policy and risk never import memory.
- Each agent's memory namespace is private; knowledge leaves only as `KnowledgeExport`.
- Governing paths, the specification lock, the SentinelQA finding vocabulary, budgets,
  and publication checks change only through an ADR and owner review.
- Fail closed everywhere; secrets live only in the environment or CI secrets; never
  commit `.env`, `.nexus/`, model outputs, patches, or bundles. Frozen Phase 5/6/7
  evidence stays untouched.
