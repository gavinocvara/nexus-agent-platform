# Resident Software Engineer Runbook

## Boundary

`nexus.software_engineer` (ADR 0012) runs one bounded engineering cycle over this
repository: observe, understand, prioritize, investigate, plan, implement, test,
self-review, assess risk, decide, observe results, learn, report. It is disabled by
default, changes no code in `dry_run` mode, needs no model, and cannot approve, merge, or
push. Owner decisions come only from the configured human owner.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `NEXUS_SOFTWARE_ENGINEER_ENABLED` | `false` | Nothing but `preflight` and `inspect` runs while false. |
| `NEXUS_SOFTWARE_ENGINEER_MODE` | `dry_run` | `dry_run` (plan and report), `propose` (validated approval requests), `autonomous_low_risk` (ship low-risk validated changes; needs a real executor). |
| `NEXUS_SOFTWARE_ENGINEER_OWNER_ID` | `owner` | Human actor id whose decisions are accepted. |
| `NEXUS_SOFTWARE_ENGINEER_REPOSITORY_URL` | this repository | Operator identity used in PatchForge tasks and SentinelQA locks. |
| `NEXUS_SOFTWARE_ENGINEER_SANDBOX` | `none` | `local_process` lets recipes run as bounded local processes (ephemeral runners only). |
| `NEXUS_SOFTWARE_ENGINEER_MODEL`, `_CONFIRM_MODEL_SPEND`, `_MAX_MODEL_CALLS`, `_MAX_OUTPUT_TOKENS` | unset, `false`, `0`, `0` | All four, plus `OPENAI_API_KEY`, are needed before a model recipe can spend money. |
| `NEXUS_SOFTWARE_ENGINEER_STATE_ROOT` | `.nexus/software_engineer` | Cycle records, reports, and recipe runs. |
| `NEXUS_SOFTWARE_ENGINEER_MEMORY_PATH` | `.nexus/software_engineer/memory.sqlite3` | Private memory. |
| `NEXUS_SOFTWARE_ENGINEER_MAX_*` | see `config.py` | Runtime, turns, tool calls, model calls, tokens, cost, changed files, diff bytes. |
| `NEXUS_SOFTWARE_ENGINEER_SLACK_WEBHOOK_ENV` | `NEXUS_SOFTWARE_ENGINEER_SLACK_WEBHOOK_URL` | Name of the variable that holds the Slack incoming-webhook URL. |
| `NEXUS_SOFTWARE_ENGINEER_SLACK_WEBHOOK_URL` | unset | The secret itself. Only in the process environment or a GitHub secret; never in Git, logs, memory, or records. |

Mode, budgets, and owner identity are governing settings. The engineer never edits them.

## What the engineer can change today

Only mechanical recipes run without a model: `formatting` (`ruff format src`) and
`dead_code_removal` (`ruff check --fix src`). Each recipe runs through PatchForge (real
ToolGateway, Runtime, Attestor), is verified by SentinelQA against the pristine tests, and
ends as a commit on a local branch `nexus/software-engineer/<cycle>` under
`.nexus/software_engineer/runs/<cycle>/<candidate>/branch`, next to `candidate.patch`,
`patch_result.json`, and `sentinel_verdict.json`. Nothing is pushed. In the scheduled
workflow these files are in the uploaded artifact; apply the patch or fetch the branch
from the artifact after deciding SHIP.

Executing a recipe needs `NEXUS_SOFTWARE_ENGINEER_MODE=propose` (or
`autonomous_low_risk`) and `NEXUS_SOFTWARE_ENGINEER_SANDBOX=local_process`. The local
sandbox is for ephemeral, credential-free runners only: it scrubs the environment and
bounds each command but has no container isolation.

Model recipes (`type_annotation`, `micro_bug_fix`, `defensive_check`) additionally need
`NEXUS_SOFTWARE_ENGINEER_MODEL`, `NEXUS_SOFTWARE_ENGINEER_CONFIRM_MODEL_SPEND=true`,
positive `NEXUS_SOFTWARE_ENGINEER_MAX_MODEL_CALLS` and `NEXUS_SOFTWARE_ENGINEER_MAX_OUTPUT_TOKENS`,
and `OPENAI_API_KEY` in the environment (in the workflow: the
`NEXUS_SOFTWARE_ENGINEER_OPENAI_API_KEY` secret). Missing any of them leaves the candidate
as an approval-only plan. `preflight` prints `model_recipes_allowed`.

## Judgment evaluation

`python -m nexus.software_engineer.evaluation` runs the controlled judgment catalog
(ADR 0012): every scenario in the owner's brief, from an obvious micro bug to a request to
weaken safety controls, with scripted executors and transports, replayed twice. It prints
one line per scenario (decision, failure, risk, events, record hash) and fails on any
unexpected judgment, invariant violation, or non-identical replay. CI runs it.

## Manual operation

```bash
python -m nexus.software_engineer preflight                 # no side effects
python -m nexus.software_engineer --artifacts artifacts inspect   # read-only signals
NEXUS_SOFTWARE_ENGINEER_ENABLED=true python -m nexus.software_engineer --artifacts artifacts cycle
```

Produce the artifacts the inspector reads (each command may fail; the engineer treats a
failure as a signal, not an error):

```bash
mkdir -p artifacts/benchmark
python -m ruff check . --output-format json > artifacts/ruff.json || true
python -m mypy > artifacts/mypy.txt || true
python -m pytest -q --junitxml=artifacts/pytest.xml || true
python -m nexus.patchforge.e2e_catalog > artifacts/e2e.txt 2>&1 || true
python -m nexus.patchforge.benchmark_corpus --output artifacts/benchmark > artifacts/benchmark.txt 2>&1 || true
python -m nexus.sentinelqa > artifacts/sentinelqa.txt 2>&1 || true
```

Outputs: `.nexus/software_engineer/cycles/<cycle_id>.json` (the `CycleRecord`),
`<cycle_id>.report.md`, and `latest.*`. The CLI prints the report and a one-line summary
and exits 1 when the cycle recorded a failure.

## Daily operation

`.github/workflows/software-engineer.yml` runs daily and on demand only when the
repository variable `NEXUS_SOFTWARE_ENGINEER_ENABLED` is `true`. Set
`NEXUS_SOFTWARE_ENGINEER_MODE` (variable) and `NEXUS_SOFTWARE_ENGINEER_SLACK_WEBHOOK_URL`
(secret) to change mode or enable Slack. The workflow token is read-only; the record and
report are uploaded as a workflow artifact.

## Reading a cycle

- `decision`: `no_work`, `request_approval`, `ship`, `abandon`, or `blocked`, with
  `decision_reasons`.
- `risk`: level plus category, path, and size components, `uncertain`, `governing_paths`.
- `self_review`: fourteen answers; `blocking` or `requires_human` explain escalation.
- `gates`: per-gate status with evidence hashes. A `ship` never has a failed gate.
- `approval_request`: the READY FOR REVIEW block the owner receives, with `dry_run` set
  when nothing was executed.
- `memory_writes`: ids of new memories; `notifications`: delivery evidence (hash only).
- `failure`: `budget_exhausted`, `credentials_missing`, `executor_error`,
  `validation_failed`, `policy_denied`, or `internal_error`.

## Answering an approval request

Owner decisions are typed. Until Slack commands exist, record one from code or a small
script:

```python
from nexus.software_engineer.cycle import record_owner_decision
from nexus.software_engineer.trust import OwnerCommand

decision = record_owner_decision(
    store, request=request, command=OwnerCommand(...), owner_id="owner", now=now
)
```

`record_owner_decision` refuses non-human actors and any actor other than the configured
owner, and remembers the decision as an owner preference. Silence is never a decision.

## Failure recovery

- A failed cycle still writes its record and report and sends
  `engineering_cycle_failed` when a transport is configured. Read `failure` and
  `decision_reasons`, fix the cause, and re-run; nothing needs cleanup.
- A dirty working tree blocks execution outside `dry_run`; commit or clean it.
- Memory problems: the store is a single SQLite file. Inspect with `EngineerMemoryStore(path).load_all()`; correct a wrong record with `correct`, retire one with `invalidate`. Never edit rows by hand.
- Notification problems are recorded per attempt (`error_code`); they never change a
  decision.

## Rollback

Every `ChangeSummary` carries `rollback_reference`. When a post-ship gate regresses, the
cycle calls the executor's `rollback`, records a `RollbackRecord`, and sends
`rollback_occurred`. Until a real executor exists nothing ships, so nothing can need a
rollback.

## Not yet built

A publisher that pushes approved branches and fast-forwards `main`; test repair (blocked
by SentinelQA-lite's byte-level specification rule); Slack-delivered owner commands;
memory consolidation; price tables for cost accounting. See ADR 0012.
