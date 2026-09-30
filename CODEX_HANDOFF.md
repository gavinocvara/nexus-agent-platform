# Codex Handoff

Canonical transfer document for the next coding agent. Current state only; history is in
Git and `CHANGELOG.md`, rules in `AGENTS.md`, the long-form resume state in
`PROJECT_STATE.md`. Read this file, then only the source and test files named below.

## Checkpoint

| Item | Value |
| --- | --- |
| Version | `0.27.0` (`pyproject.toml`) |
| Branch | `main` (0.27.0 landed via PR #1 and PR #2; `main` push rules: `AGENTS.md` Invariants) |
| origin/main | `6ea5a92` (publisher relative-`work_root` fix) plus the docs checkpoint containing this file |
| Working tree | clean at the checkpoint (`git status --short` empty) |
| CI | green on `main` at `6ea5a92`: run `36641561380` (`validate`, `compose-integration`). |

Landed on `main`: PR #1 (0.27.0 production hardening, merge `7fa0f29`) and PR #2 (README
reflects 0.27.0, portable Makefile, AegisOps runbook corrections, merge `b382922`).

Commits in this run, oldest first: `d2d55ec` SentinelQA runner-integrity canary,
`004d43c` autonomy requires SentinelQA + governance docs and eval config governing,
`7358ff1` owner rejections and failed hypotheses never age out of memory view, `af69cfa`
Slack replay ledger, `0f4b97a` publisher verifies commit/ref/PR head, `08986c6`
self-review fixes, `89c3370` race-safe run lease, `b4afb04` `exercise-github` + 204 fix,
`55ea114` blockers cite memory ids, and the 0.27.0 checkpoint commit containing this file.

## What changed in 0.27.0 (details: `CHANGELOG.md`; each fix has a test that failed first)

- SentinelQA runner-integrity canary: real-pytest attacks (forged summary at exit, patched
  `_pytest` reports, swallowed failures) passed 0.26.0; now `runner_integrity_violated` /
  `runner_integrity_unproven`, and the verdict contract refuses a pass without the probe.
- Autonomy requires `sentinel_review`; governance docs and evaluation config are governing.
- Owner rejections and failed hypotheses no longer age out of the memory window.
- Self-review: deleted-file attribution, unittest/raises/skip detection, any concern needs
  the owner. Run lease: atomic creation, race-safe recovery. Slack: replay ledger.
- Publisher: commit/ref/PR-head verification, bodiless-204 fix, `exercise-github`.

## Validation (0.27.0 local gate, Linux, Python 3.12.3)

- `uv pip check` compatible; Ruff format and lint clean; strict mypy clean (127 files).
- pytest: **934 passed, 23 deselected** in 4m28s (0.26.0: 898).
- E2E gate passed; Benchmark v0 gate passed (`reference` 5/5 passed, `fix_and_edit_tests`
  5/5 failed, zero inconclusive, zero disagreements); SentinelQA gate **35/35**; engineer
  evaluation gate 30/30 (all byte-identical replays); `nexus.lab.scenarios validate` 5;
  `docker compose config --quiet` valid; preflight all-off defaults.
- Secret scan of tracked files: no file has a credential-shaped line the 0.26.0 base did
  not already have (deliberate test fixtures and env-variable references only); no
  `.env`, `.nexus/`, sqlite, patch, or bundle file tracked.
- Compose integration was not run locally; CI job `compose-integration` passed on `main`.

## Subsystem status

| Subsystem | Status |
| --- | --- |
| SentinelQA-lite | Complete + runner-integrity canary. Residual risk (recorded in ADR 0011): canary-aware code, test-detecting behavior, tests fed by production data. Candidate-changed tests stay rejected; test repair stays an owner decision. |
| Resident engineer | Ship policy, risk, self-review, memory guards, lease hardened as above. Effective autonomous class is **empty in production**: every recipe (ruff format, ruff `--fix`, model recipes) edits `src/`, which is MEDIUM, so every real change needs the owner; only non-governing Markdown outside `src/`/`lab/` is LOW, and no recipe produces it. Widening that is an owner decision, not built. Autonomous path is still draft-PR-only and off by default. |
| Persistent brain | Unchanged architecture; guard memories read in full; knowledge never authority. |
| Scheduler | Unchanged, disabled (repository variable unset); read-only token; reversible by unsetting. |
| Slack | Live owner decision works over signed HTTPS (REVISE recorded, repeat `already_decided`, nothing published; owner-reported). Text refusals now HTTP 200 so Slack shows them. Live re-check of `/nexus bogus` and the non-owner step pending. |
| GitHub publisher | Validated against the real GitHub API: the first `exercise-github --confirm-live` (after fix `6ea5a92`) passed all 8 checks, `cleaned_up=True`; exercise PR #4 was draft only, closed, never merged; branch `nexus/integration-exercise/c4b393f1d868` deleted. No real `decide` + `publish` yet. |
| Model spend | Off; no live call ever made. |

## Known operational risks

- A real `propose` cycle on this repository runs the full test suite about seven times
  (PatchForge targeted, which is `tests/`, and full; SentinelQA pristine and
  verification targeted and full; the runner-integrity probe). With the default
  `NEXUS_SOFTWARE_ENGINEER_MAX_RUNTIME_SECONDS=1800` and the workflow's 45-minute timeout
  it may end `budget_exhausted` (blocked, recorded, nothing shipped). Raise both, or
  narrow the targeted test paths, before the first real propose cycle.
- SentinelQA residual risk: canary-aware or test-detecting candidate code (ADR 0011).

## Owner decisions required (nothing below has been done)

1. Finish the Slack exercise live: `/nexus bogus` (expect `Refused: command_unparsable.`)
   and a non-owner command (expect `Refused: not_owner.`).
2. Authorize the first real `decide` + `publish` (still before any `PUBLISH_FROM_CYCLE`).
3. Remote branches: `maintenance/repo-hygiene-claude` is unmerged;
   `maintenance/context-slimming` and `claude/confident-faraday-1drq98` are fully merged. Owner decides
   whether to delete them.

Done: the live GitHub publisher exercise, and the live Slack owner decision (above). Not
yet run: live model-backed cycle, real `publish`, unattended scheduling.

## Exact next task

Owner steps 1-2 above. Engineering next, only if the owner wants it: out-of-process
execution for SentinelQA (the only full answer to canary-aware code; see ADR 0011
"alternatives"), then a Docker proof of the runner. Files: `src/nexus/sentinelqa/
{verifier,executor,canary}.py`, `tests/unit/test_sentinelqa_runner_integrity.py`.

## Files that matter

- SentinelQA: `src/nexus/sentinelqa/{models,lock,verifier,executor,canary,tamper,catalog,
  harness}.py`; tests `tests/unit/test_sentinelqa_*.py`.
- Engineer: `src/nexus/software_engineer/{policy,risk,review,memory,cycle,runtime,inspect,
  executor,publish,exercise,slack_commands,approval,__main__}.py`; tests
  `tests/unit/test_software_engineer_*.py`.
- Docs: ADR 0011, ADR 0012, `docs/runbooks/{sentinelqa,software-engineer}.md`.

## Resume and validation commands

```bash
uv venv --python 3.12 <outside repo> && uv pip install -e ".[dev]"
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
  Git, database, or credential access. Evidence comes only from runtime code.
- The candidate never controls what correctness means: the pristine specification is
  locked by Git objects; candidate test changes are rejected; the runner must report the
  planted canary before any pass.
- PatchForge proposes; SentinelQA verifies; a human approves and merges. Draft PRs only;
  no auto-merge; silence is never approval; autonomy requires `sentinel_review`.
- Memory carries knowledge, never authority; policy and risk never import memory.
- Governing paths, the lock, the SentinelQA finding vocabulary, budgets, and publication
  checks change only through an ADR and owner review.
- Fail closed; secrets only in the environment; never commit `.env`, `.nexus/`, model
  outputs, patches, bundles. Frozen Phase 5/6/7 evidence untouched.
