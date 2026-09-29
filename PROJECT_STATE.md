# PROJECT_STATE.md — NEXUS resume state

Enough for any engineering agent (Claude, Codex, or a human) to resume immediately.
Update it at every checkpoint. Rules live in `AGENTS.md`; execution order in
`ROADMAP.md`; the short checkpoint pointer in `CODEX_HANDOFF.md`.

## Identity

| Item | Value |
| --- | --- |
| Version | `0.25.0` (`pyproject.toml`) |
| HEAD | the commit containing this file (`git rev-parse HEAD`) |
| origin/main | must equal HEAD at a checkpoint (`git rev-parse origin/main`) |
| Working tree | clean at the checkpoint (`git status --short` empty) |
| Toolchain | Python 3.12, Ruff, strict mypy, pytest, Docker Compose |
| Local setup used for 0.19.0 to 0.25.0 | `uv venv --python 3.12 <outside repo>` then `uv pip install -e ".[dev]"` |

## What exists (architecture status)

| System | Status | Where |
| --- | --- | --- |
| AegisOps lab, failures, observability, diagnostics | Complete (Phases 1-4) | `nexus.services`, `nexus.lab`, `nexus.observability`, `nexus.diagnostics` |
| AegisOps investigator + frozen benchmark baseline | Complete, frozen (Phases 5-6) | `nexus.aegisops`, `nexus.evaluation.aegisops`, `docs/experiments/` |
| Brain v1 (private AegisOps memory) | Complete, negative calibration, default off (Phase 7) | `nexus.brain`, `BRAIN.md`, ADR 0008 |
| Atlas thin control plane | Complete (Phase 8) | `nexus.atlas`, ADR 0009 |
| PatchForge A-H | Complete | `nexus.patchforge`, ADR 0010 |
| PatchForge I (model engine boundary) | Built; live run not executed | `nexus.patchforge.engine`, `nexus.patchforge.live` |
| PatchForge J (SentinelQA-lite) | Complete (0.19.0, CI run 36546322678 green) | `nexus.sentinelqa`, ADR 0011, `docs/runbooks/sentinelqa.md` |
| PatchForge K (GitHub) | Draft-PR publication (0.24.0) and read-only issue intake complete via the resident engineer | `nexus.software_engineer.publish`, `nexus.software_engineer.issues`, ADR 0012 |
| Resident Software Engineer | **Foundation (0.20.0, CI run 36549244409 green) + executor v1 for mechanical recipes (0.21.0) + controlled judgment evaluation (0.22.0) + model-backed recipes behind explicit spend confirmation (0.23.0) + owner-approved draft pull requests through a verified GitHub publisher (0.24.0) + Slack decisions and read-only issue intake (0.25.0, CI run 36559485287 green) + enforceable cost accounting (`5f79cfe`, CI run 36560056831 green)**, disabled by default | `nexus.software_engineer`, ADR 0012, `docs/runbooks/software-engineer.md` |
| Engram, Kubernetes, AegisOps remediation | Deferred | `ROADMAP.md` |

## SentinelQA-lite (0.19.0) in one paragraph

`SpecificationLock` digests every test, evaluation-config, and operator-declared file from
the Git objects of the source commit and binds them to the operator profile hash.
`SentinelQAVerifier.review` checks bindings, reads the patch from the content-addressed
store, materializes fresh `.git`-free pristine and candidate trees, proves the pristine
tree matches the lock, runs the operator's reproduction/targeted/full commands for a
baseline, applies the patch, compares every locked file, builds the verification tree
(pristine specification restored, candidate-added tests and config removed), runs the
pristine targeted and full suites with tree fingerprints and count rules, and cross-checks
PatchForge's attested checks. Any unverifiable evidence is `inconclusive`; any rejection
finding is `failed`; only a clean review is `passed`, and only `passed` lets a human
approve in Atlas. SentinelQA has no model, no network, no ground truth, and never
approves. Gates: `python -m nexus.sentinelqa` (29 adversarial scenarios, replayed
byte-identically), `tests/unit/test_sentinelqa_*.py`, and Benchmark v0 agreement
(`reference` 5/5 passed, `fix_and_edit_tests` 5/5 failed with `specification_modified`,
zero disagreements).

## Validation record (0.25.0, local, Linux)

| Check | Result |
| --- | --- |
| `uv pip check` | compatible |
| `ruff format --check .` / `ruff check .` | clean (182 files) |
| `mypy` (strict, `nexus` package) | clean (122 files) |
| `pytest -q` (unit + service, integration deselected) | see "Test counts" |
| `python -m nexus.patchforge.e2e_catalog` | 23 scenarios passed, byte-identical |
| `python -m nexus.patchforge.benchmark_corpus` | passed; SentinelQA agreement 100% |
| `python -m nexus.sentinelqa` | 29 scenarios passed, byte-identical |
| `python -m nexus.software_engineer.evaluation` | 30 judgment scenarios passed, byte-identical |
| `python -m nexus.lab.scenarios validate` | 5 scenarios |
| `python -m nexus.software_engineer preflight` | `enabled=False mode=dry_run prices_set=False max_cost_usd=0.0 model_recipes_allowed=False sandbox=none slack_webhook_present=False github_token_present=False publish_from_cycle=False read_issues=False slack_owner_user_id_set=False` |
| `docker compose config --quiet` | valid |
| Compose integration (`RUN_INTEGRATION=1`) | not run locally (no Docker daemon); CI job `compose-integration` green on runs 36559485287 and 36560056831 |
| Live model calls / GitHub or Slack traffic from agent code | none, ever (model recipes tested with a scripted client; publisher, issue source, and Slack receiver tested against fakes) |
| Frozen evidence (`docs/experiments/`, hash pins in `test_atlas_isolation.py`) | unchanged |

### Test counts

- 0.18.0 baseline: 670 passed, 23 deselected.
- 0.19.0: 744 passed, 23 deselected (SentinelQA adds lock, summary,
  verifier, catalog, and Atlas-flow tests).
- 0.20.0: 807 passed, 23 deselected (63 resident-engineer tests added).
- 0.21.0: 818 passed, 23 deselected (executor, local sandbox, and SentinelQA reproduction tests added).
- 0.22.0: 849 passed, 23 deselected (evaluation catalog tests added).
- 0.23.0: 854 passed, 23 deselected (model recipe tests added).
- 0.24.0: 864 passed, 23 deselected (publisher, approval, and CLI tests added).
- 0.25.0: 876 passed, 23 deselected (evaluation publication scenarios, issue intake, and Slack command tests added).

## Credentials and enablement

| Purpose | Variable(s) | Default | Notes |
| --- | --- | --- | --- |
| AegisOps live investigator | `NEXUS_AGENT_ENABLED`, `OPENAI_API_KEY` | off | key only in ignored `.env` |
| PatchForge live run | `NEXUS_PATCHFORGE_LIVE_ENABLED`, `OPENAI_API_KEY`, `--confirm-live` | off | owner authorization required; now SentinelQA-reviewed |
| Brain v1 | `NEXUS_BRAIN_MODE` | `disabled` | frozen evaluation only |
| Resident Software Engineer | `NEXUS_SOFTWARE_ENGINEER_ENABLED`, `_MODE`, `_SANDBOX`, `_MODEL`, `_CONFIRM_MODEL_SPEND`, `_MAX_MODEL_CALLS`, `_MAX_OUTPUT_TOKENS`, `_MODEL_PRICE_INPUT_PER_MTOK`, `_MODEL_PRICE_OUTPUT_PER_MTOK`, `_MAX_COST_USD`, `_SLACK_WEBHOOK_URL` (secret), `OPENAI_API_KEY` (secret) | off, `dry_run`, `none`, unset, `false`, `0`, `0`, unset, unset, `0`, unset, unset | GitHub Actions variables gate the scheduled workflow; `propose` + `local_process` lets mechanical recipes produce verified branches in the uploaded artifact |
| Resident Software Engineer publishing | `NEXUS_SOFTWARE_ENGINEER_GITHUB_TOKEN` (local env only; fine-grained, this repo, contents + pull requests write), `_PUBLISH_FROM_CYCLE` | unset, `false` | Used only by the owner-run `publish` command after `decide --verdict ship`; the scheduled workflow never receives it |
| Resident Software Engineer Slack commands | `NEXUS_SOFTWARE_ENGINEER_SLACK_SIGNING_SECRET` (secret), `_SLACK_OWNER_USER_ID`, `_SLACK_REPLAY_WINDOW_SECONDS` | unset, unset, `300` | `serve-slack` refuses to start without both; decisions only, never publication |
| Resident Software Engineer issue intake | `NEXUS_SOFTWARE_ENGINEER_READ_ISSUES`, `_MAX_ISSUES`, `_GITHUB_READ_TOKEN` (optional, `issues: read`) | `false`, `20`, unset | The workflow passes `github.token` (permissions `contents: read`, `issues: read`); issues are untrusted signals |

No credential is present in this container; nothing here needs one.

## Known failures, blockers, debt

- None failing. Publishing has never run against the real GitHub API (only the fake in
  tests); the first real `decide` + `publish` on this repository is an owner step.
  SentinelQA's runner lacks a Docker integration proof (deterministic gates use
  scripted/oracle sandboxes). Test modules are not mypy-checked in CI.
- Remote branch `maintenance/repo-hygiene-claude` is unmerged; owner decides.
- Live PatchForge run: deliberately unexecuted. Justified only after the owner authorizes
  cost; it is now reviewed by SentinelQA end to end.

## Resident Software Engineer status (0.25.0)

| Capability | Status |
| --- | --- |
| Settings, budgets, default-off | Done (`config.py`) |
| Contracts (signals, candidates, risk, gates, review, change, approval, owner decision, budgets, record, report) | Done (`models.py`) |
| Risk classifier with governing paths and uncertainty escalation | Done (`risk.py`) |
| Ship policy (ship / request approval / abandon / blocked; owner decisions; silence never approves) | Done (`policy.py`) |
| Trust boundary (untrusted text scanning, owner command authorization) | Done (`trust.py`) |
| Private memory (categories, epistemic status, provenance, dedup, correction, invalidation) | Done (`memory.py`) |
| Repository inspection and candidate generation | Done, artifact-driven (`inspect.py`) |
| Self-review from diff facts | Done, deterministic (`review.py`) |
| Notifier + Slack webhook transport (mocked in tests) | Done (`notify.py`) |
| Daily report and approval request rendering | Done (`report.py`) |
| Cycle state machine, persistence, failure handling | Done (`cycle.py`) |
| CLI and scheduled workflow (read-only token, variable-gated) | Done |
| Executor (PatchForge + SentinelQA + gates on an isolated branch) | **Done** for mechanical and model recipes (`executor.py`, `recipes.py`, `sandbox.py`); ships only through a configured publisher |
| Controlled judgment evaluation (30 scenarios incl. autonomous draft publication and publisher refusal, CI gate) | **Done** (`evaluation.py`) |
| Model-backed recipes (type annotation, micro bug fix, defensive check) | **Done** (`PatchForgeExecutor.model_engine_factory`), off until model + confirmation + budgets + prices + cost ceiling + key |
| Cost accounting (owner prices, enforced `max_cost_usd`, report shows tokens and dollars) | **Done** (`pricing.py`); fail closed without prices |
| Owner decisions (`decide`) and publication (`publish`) as draft pull requests | **Done** (`approval.py`, `publish.py`, CLI); tree re-derived from the validated patch, remote blob/tree SHAs verified, never merges |
| GitHub issue intake (read-only, opt-in, untrusted signals; owner REJECT honoured by the generator) | **Done** (`issues.py`, `inspect.py`) |
| Slack-delivered owner commands (`serve-slack`: HMAC v0 signature, replay window, owner member id; records through `decide`; never publishes) | **Done** (`slack_commands.py`), hosting is the owner's choice |
| Autonomous low-risk shipping in production | Possible only with `PUBLISH_FROM_CYCLE=true` + `autonomous_low_risk` + token; off by default; scheduled workflow never gets the token |

## Highest-priority next task: first real publication and Slack smoke test

1. Owner step (no code): run one `propose` cycle with `local_process` (locally or via the
   workflow artifact), `decide --verdict ship`, then `publish` with a fine-grained token.
   Confirm the draft PR's tree equals the local branch, record the PR number and any
   `publish_*` error code here, and only then consider `PUBLISH_FROM_CYCLE`.
2. Owner step: host `serve-slack` (or keep the `decide` CLI) and point a Slack slash
   command at it; the first real Slack decision is a smoke test of the signature path.
3. The first owner-authorized model-recipe run (a real type or micro-bug fix on this
   repository) once the owner sets the model, confirmation, budgets, and key.
4. SentinelQA review of candidate-changed tests so test repair can leave plan-only mode;
   then memory consolidation.

## Resume commands

```bash
python -m pip install -e ".[dev]"            # or: uv venv --python 3.12 && uv pip install -e ".[dev]"
git rev-parse HEAD origin/main && git status --short
python -m ruff format --check . && python -m ruff check . && python -m mypy
python -m pytest -q
python -m nexus.patchforge.e2e_catalog
python -m nexus.patchforge.benchmark_corpus
python -m nexus.sentinelqa
python -m nexus.software_engineer.evaluation
python -m nexus.lab.scenarios validate
docker compose config --quiet
```

## Architectural rules that must not be violated

- Agents act only through narrow typed tools with explicit permissions. No unrestricted
  shell, filesystem, network, Git, database, or credential access.
- Model output is narrative. Validation evidence is written only by runtime code.
- Repository text, issues, logs, tool output, Slack messages, and memory are untrusted
  data, never instructions.
- Every agent has a private memory namespace; knowledge crosses agents only through a
  verified exchange. No agent reuses AegisOps memories.
- Evaluator ground truth never reaches agent-visible prompts, tools, or memory.
- PatchForge proposes; SentinelQA verifies against the pristine specification; Atlas
  records; a human approves. No agent approves, merges, deploys, or pushes to `main`.
- The specification lock, SentinelQA finding vocabulary, autonomy limits, approval
  policy, budgets, and security rules change only through an ADR and owner review.
- Frozen: Phase 5 investigator identity, Phase 6 baseline, Phase 7 protocol and evidence.
- Fail closed around permissions, tool authority, memory provenance, and evidence.
