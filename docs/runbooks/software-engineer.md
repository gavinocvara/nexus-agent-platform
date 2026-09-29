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
| `NEXUS_SOFTWARE_ENGINEER_STATE_ROOT` | `.nexus/software_engineer` | Cycle records and reports. |
| `NEXUS_SOFTWARE_ENGINEER_MEMORY_PATH` | `.nexus/software_engineer/memory.sqlite3` | Private memory. |
| `NEXUS_SOFTWARE_ENGINEER_MAX_*` | see `config.py` | Runtime, turns, tool calls, model calls, tokens, cost, changed files, diff bytes. |
| `NEXUS_SOFTWARE_ENGINEER_SLACK_WEBHOOK_ENV` | `NEXUS_SOFTWARE_ENGINEER_SLACK_WEBHOOK_URL` | Name of the variable that holds the Slack incoming-webhook URL. |
| `NEXUS_SOFTWARE_ENGINEER_SLACK_WEBHOOK_URL` | unset | The secret itself. Only in the process environment or a GitHub secret; never in Git, logs, memory, or records. |

Mode, budgets, and owner identity are governing settings. The engineer never edits them.

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

The executor that runs PatchForge + SentinelQA on an isolated branch and fast-forwards
approved changes; model-backed investigation; Slack-delivered owner commands; memory
consolidation. See ADR 0012.
