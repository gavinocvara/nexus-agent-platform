# PROJECT_STATE.md — NEXUS resume state

Enough for any engineering agent (Claude, Codex, or a human) to resume immediately.
Update it at every checkpoint. Rules live in `AGENTS.md`; execution order in
`ROADMAP.md`; the short checkpoint pointer in `CODEX_HANDOFF.md`.

## Identity

| Item | Value |
| --- | --- |
| Version | `0.21.0` (`pyproject.toml`) |
| HEAD | the commit containing this file (`git rev-parse HEAD`) |
| origin/main | must equal HEAD at a checkpoint (`git rev-parse origin/main`) |
| Working tree | clean at the checkpoint (`git status --short` empty) |
| Toolchain | Python 3.12, Ruff, strict mypy, pytest, Docker Compose |
| Local setup used for 0.19.0 to 0.21.0 | `uv venv --python 3.12 <outside repo>` then `uv pip install -e ".[dev]"` |

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
| PatchForge K (GitHub intake, branch push, draft PR) | Not started | — |
| Resident Software Engineer | **Foundation (0.20.0, CI run 36549244409 green) + executor v1 for mechanical recipes (0.21.0)**, disabled by default | `nexus.software_engineer`, ADR 0012, `docs/runbooks/software-engineer.md` |
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

## Validation record (0.21.0, local, Linux)

| Check | Result |
| --- | --- |
| `uv pip check` | compatible |
| `ruff format --check .` / `ruff check .` | clean (182 files) |
| `mypy` (strict, `nexus` package) | clean (117 files) |
| `pytest -q` (unit + service, integration deselected) | see "Test counts" |
| `python -m nexus.patchforge.e2e_catalog` | 23 scenarios passed, byte-identical |
| `python -m nexus.patchforge.benchmark_corpus` | passed; SentinelQA agreement 100% |
| `python -m nexus.sentinelqa` | 29 scenarios passed, byte-identical |
| `python -m nexus.lab.scenarios validate` | 5 scenarios |
| `python -m nexus.software_engineer preflight` | `enabled=False mode=dry_run sandbox=none slack_webhook_present=False` |
| `docker compose config --quiet` | valid |
| Compose integration (`RUN_INTEGRATION=1`) | not run locally (no Docker daemon); CI job `compose-integration` |
| Live model calls | none, ever, in this repository's history |
| Frozen evidence (`docs/experiments/`, hash pins in `test_atlas_isolation.py`) | unchanged |

### Test counts

- 0.18.0 baseline: 670 passed, 23 deselected.
- 0.19.0: 744 passed, 23 deselected (SentinelQA adds lock, summary,
  verifier, catalog, and Atlas-flow tests).
- 0.20.0: 807 passed, 23 deselected (63 resident-engineer tests added).
- 0.21.0: 818 passed, 23 deselected (executor, local sandbox, and SentinelQA reproduction tests added).

## Credentials and enablement

| Purpose | Variable(s) | Default | Notes |
| --- | --- | --- | --- |
| AegisOps live investigator | `NEXUS_AGENT_ENABLED`, `OPENAI_API_KEY` | off | key only in ignored `.env` |
| PatchForge live run | `NEXUS_PATCHFORGE_LIVE_ENABLED`, `OPENAI_API_KEY`, `--confirm-live` | off | owner authorization required; now SentinelQA-reviewed |
| Brain v1 | `NEXUS_BRAIN_MODE` | `disabled` | frozen evaluation only |
| Resident Software Engineer | `NEXUS_SOFTWARE_ENGINEER_ENABLED`, `NEXUS_SOFTWARE_ENGINEER_MODE`, `NEXUS_SOFTWARE_ENGINEER_SANDBOX`, `NEXUS_SOFTWARE_ENGINEER_SLACK_WEBHOOK_URL` (secret) | off, `dry_run`, `none`, unset | GitHub Actions variables gate the scheduled workflow; `propose` + `local_process` lets mechanical recipes produce verified branches in the uploaded artifact |

No credential is present in this container; nothing here needs one.

## Known failures, blockers, debt

- None failing. The resident engineer produces verified branches only for mechanical
  recipes (formatting, lint fixes) and cannot publish them (no publisher). SentinelQA's
  runner lacks a Docker integration proof (deterministic gates use scripted/oracle
  sandboxes). Test modules are not mypy-checked in CI.
- Remote branch `maintenance/repo-hygiene-claude` is unmerged; owner decides.
- Live PatchForge run: deliberately unexecuted. Justified only after the owner authorizes
  cost; it is now reviewed by SentinelQA end to end.

## Resident Software Engineer status (0.20.0)

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
| Executor (PatchForge + SentinelQA + gates on an isolated branch) | **Done for mechanical recipes** (`executor.py`, `recipes.py`, `sandbox.py`); no publisher, so it never ships |
| Model-backed investigation | Not built (no model configured; budgets zero) |
| Slack-delivered owner commands | Not built (typed `OwnerCommand` via code) |
| Autonomous low-risk shipping in production | Disabled; needs a publisher with a deliberately supplied write token; exercised only with scripted executors in tests |

## Highest-priority next task: publisher and model-backed recipes

1. Publisher: push an approved candidate branch and open a draft PR (never merge) with a
   deliberately supplied write token, only after an `OwnerDecision(ship)`; record the
   PR reference in the change summary and memory. Keep the workflow token read-only by
   default; a separate, owner-enabled workflow would hold the write token.
2. Model-backed recipes behind `ModelClient` (PatchForge's `ModelBackedEngine`) for
   type-annotation repair and test repair, with zero budgets by default, then extend
   the adversarial evaluation (injection in issue text, safety-weakening requests, flaky
   and unrelated failing tests, merge conflicts).
3. Slack-delivered `OwnerCommand`s with signature verification, replacing the code/CLI
   path for approvals.

## Resume commands

```bash
python -m pip install -e ".[dev]"            # or: uv venv --python 3.12 && uv pip install -e ".[dev]"
git rev-parse HEAD origin/main && git status --short
python -m ruff format --check . && python -m ruff check . && python -m mypy
python -m pytest -q
python -m nexus.patchforge.e2e_catalog
python -m nexus.patchforge.benchmark_corpus
python -m nexus.sentinelqa
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
