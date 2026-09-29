# Resident Software Engineer Runbook

## Boundary

`nexus.software_engineer` (ADR 0012) runs one bounded engineering cycle over this
repository: observe, understand, prioritize, investigate, plan, implement, test,
self-review, assess risk, decide, observe results, learn, report. It is disabled by
default, changes no code in `dry_run` mode, needs no model, and cannot approve, merge, or
push. Owner decisions come only from the configured human owner. The most it can ever do
with a change is open a draft pull request after the owner says SHIP; a human merges.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `NEXUS_SOFTWARE_ENGINEER_ENABLED` | `false` | Nothing but `preflight` and `inspect` runs while false. |
| `NEXUS_SOFTWARE_ENGINEER_MODE` | `dry_run` | `dry_run` (plan and report), `propose` (validated approval requests), `autonomous_low_risk` (ship low-risk validated changes; needs a real executor). |
| `NEXUS_SOFTWARE_ENGINEER_OWNER_ID` | `owner` | Human actor id whose decisions are accepted. |
| `NEXUS_SOFTWARE_ENGINEER_REPOSITORY_URL` | this repository | Operator identity used in PatchForge tasks and SentinelQA locks. |
| `NEXUS_SOFTWARE_ENGINEER_SANDBOX` | `none` | `local_process` lets recipes run as bounded local processes (ephemeral runners only). |
| `NEXUS_SOFTWARE_ENGINEER_MODEL`, `_CONFIRM_MODEL_SPEND`, `_MAX_MODEL_CALLS`, `_MAX_OUTPUT_TOKENS` | unset, `false`, `0`, `0` | All four, plus `OPENAI_API_KEY`, prices, and a cost budget, are needed before a model recipe can spend money. |
| `NEXUS_SOFTWARE_ENGINEER_MODEL_PRICE_INPUT_PER_MTOK`, `_MODEL_PRICE_OUTPUT_PER_MTOK`, `_MAX_COST_USD` | unset, unset, `0` | USD per million input / output tokens and the per-cycle cost ceiling. The engineer never guesses a price: without both prices and a positive ceiling, model recipes stay plans. |
| `NEXUS_SOFTWARE_ENGINEER_STATE_ROOT` | `.nexus/software_engineer` | Cycle records, reports, and recipe runs. |
| `NEXUS_SOFTWARE_ENGINEER_MEMORY_PATH` | `.nexus/software_engineer/memory.sqlite3` | Private memory. |
| `NEXUS_SOFTWARE_ENGINEER_MAX_*` | see `config.py` | Runtime, turns, tool calls, model calls, tokens, cost, changed files, diff bytes. |
| `NEXUS_SOFTWARE_ENGINEER_SLACK_WEBHOOK_ENV` | `NEXUS_SOFTWARE_ENGINEER_SLACK_WEBHOOK_URL` | Name of the variable that holds the Slack incoming-webhook URL. |
| `NEXUS_SOFTWARE_ENGINEER_SLACK_WEBHOOK_URL` | unset | The secret itself. Only in the process environment or a GitHub secret; never in Git, logs, memory, or records. |
| `NEXUS_SOFTWARE_ENGINEER_GITHUB_TOKEN_ENV` | `NEXUS_SOFTWARE_ENGINEER_GITHUB_TOKEN` | Name of the variable that holds the GitHub token used by `publish`. |
| `NEXUS_SOFTWARE_ENGINEER_GITHUB_TOKEN` | unset | Fine-grained token for this repository only: contents and pull requests write, nothing else. Read at publish time, sent as a header, never stored. The scheduled workflow never receives it. |
| `NEXUS_SOFTWARE_ENGINEER_PUBLISH_FROM_CYCLE` | `false` | Lets an `autonomous_low_risk` cycle open draft pull requests itself when the token is present. Keep it false unless the owner has decided otherwise in a reviewed commit. |
| `NEXUS_SOFTWARE_ENGINEER_SLACK_SIGNING_SECRET` | unset | Slack app signing secret that authenticates `/nexus` slash commands; read per request, never stored. |
| `NEXUS_SOFTWARE_ENGINEER_SLACK_OWNER_USER_ID` | unset | The one Slack member id whose commands count as the owner's. Unset means no Slack command is accepted. |
| `NEXUS_SOFTWARE_ENGINEER_SLACK_REPLAY_WINDOW_SECONDS` | `300` | Maximum age of a signed Slack request. |
| `NEXUS_SOFTWARE_ENGINEER_READ_ISSUES` | `false` | Read open GitHub issues (never pull requests) as untrusted signals; the engineer's only network read. |
| `NEXUS_SOFTWARE_ENGINEER_GITHUB_READ_TOKEN` | unset | Optional read-only token for issue intake (`issues: read`); the workflow passes its own token. Never stored. |
| `NEXUS_SOFTWARE_ENGINEER_MAX_ISSUES` | `20` | Most recently updated open issues read per cycle (1-100). |

Mode, budgets, and owner identity are governing settings. The engineer never edits them.

## What the engineer can change today

Only mechanical recipes run without a model: `formatting` (`ruff format src`) and
`dead_code_removal` (`ruff check --fix src`). Each recipe runs through PatchForge (real
ToolGateway, Runtime, Attestor), is verified by SentinelQA against the pristine tests, and
ends as a commit on a local branch `nexus/software-engineer/<cycle>` under
`.nexus/software_engineer/runs/<cycle>/<candidate>/branch`, next to `candidate.patch`,
`patch_result.json`, and `sentinel_verdict.json`. The cycle pushes nothing. After the
owner records SHIP, `publish` turns that branch into a draft pull request (see
"Publishing an approved change"). In the scheduled workflow these files are in the
uploaded artifact; unpack it at the repository root of a local checkout to publish.

Executing a recipe needs `NEXUS_SOFTWARE_ENGINEER_MODE=propose` (or
`autonomous_low_risk`) and `NEXUS_SOFTWARE_ENGINEER_SANDBOX=local_process`. The local
sandbox is for ephemeral, credential-free runners only: it scrubs the environment and
bounds each command but has no container isolation.

Model recipes (`type_annotation`, `micro_bug_fix`, `defensive_check`) additionally need
`NEXUS_SOFTWARE_ENGINEER_MODEL`, `NEXUS_SOFTWARE_ENGINEER_CONFIRM_MODEL_SPEND=true`,
positive `NEXUS_SOFTWARE_ENGINEER_MAX_MODEL_CALLS` and `NEXUS_SOFTWARE_ENGINEER_MAX_OUTPUT_TOKENS`,
both `NEXUS_SOFTWARE_ENGINEER_MODEL_PRICE_INPUT_PER_MTOK` and `_OUTPUT_PER_MTOK`, a positive
`NEXUS_SOFTWARE_ENGINEER_MAX_COST_USD`, and `OPENAI_API_KEY` in the environment (in the
workflow: the `NEXUS_SOFTWARE_ENGINEER_OPENAI_API_KEY` secret). Missing any of them leaves
the candidate as an approval-only plan. Every model run's cost (tokens times the owner's
prices) flows into the cycle's usage; a cycle that crosses `MAX_COST_USD` stops as
`budget_exhausted`, and the daily report shows tokens and dollars. `preflight` prints
`prices_set`, `max_cost_usd`, and `model_recipes_allowed`.

## Issues as signals

With `NEXUS_SOFTWARE_ENGINEER_READ_ISSUES=true` the inspector lists the most recently
updated open issues through the GitHub REST API (pull requests excluded, bodies bounded to
4000 characters) and records each as an `issue` signal: untrusted text, hashed, scanned
for instruction-shaped content, `warning` severity when labelled `bug`, `defect`, or
`regression`, `info` otherwise. Up to three issues per cycle become candidates titled
`Investigate issue #N`: a bug-labelled issue is worth an approval-only plan (category
`unknown`, derived from untrusted text, so never autonomous); anything else is report-only
context. Once the owner answers such a request with REJECT, the remembered preference
keeps the same ask at zero value on later days. A GitHub error or rate limit becomes one
`warning` signal and never fails the cycle.

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
and exits 1 when the cycle recorded a failure. Owner steps:

```bash
python -m nexus.software_engineer decide --cycle latest --verdict ship --reason "why"
NEXUS_SOFTWARE_ENGINEER_ENABLED=true NEXUS_SOFTWARE_ENGINEER_GITHUB_TOKEN=... \
  python -m nexus.software_engineer publish --cycle latest
```

## Daily operation

`.github/workflows/software-engineer.yml` runs daily and on demand only when the
repository variable `NEXUS_SOFTWARE_ENGINEER_ENABLED` is `true`. Set
`NEXUS_SOFTWARE_ENGINEER_MODE` (variable) and `NEXUS_SOFTWARE_ENGINEER_SLACK_WEBHOOK_URL`
(secret) to change mode or enable Slack. The workflow token is read-only and no GitHub
write token is passed in, so a scheduled cycle can never publish. The record, report,
evidence files, and candidate branch clone are uploaded as the `resident-engineer-cycle`
artifact; unpack it at the root of a local checkout (it restores
`.nexus/software_engineer/...`) before running `decide` and `publish`.

## Reading a cycle

- `decision`: `no_work`, `request_approval`, `ship`, `abandon`, or `blocked`, with
  `decision_reasons`.
- `risk`: level plus category, path, and size components, `uncertain`, `governing_paths`.
- `self_review`: fourteen answers; `blocking` or `requires_human` explain escalation.
- `gates`: per-gate status with evidence hashes. A `ship` never has a failed gate.
- `approval_request`: the READY FOR REVIEW block the owner receives, with `dry_run` set
  when nothing was executed.
- `change.publication`: present only after a draft pull request was opened: repository,
  branch, base and remote commit SHAs, tree SHA, pull request number and URL, and who
  authorized it (`owner_decision` or `autonomous_low_risk`).
- `memory_writes`: ids of new memories; `notifications`: delivery evidence (hash only).
- `failure`: `budget_exhausted`, `credentials_missing`, `executor_error`,
  `validation_failed`, `policy_denied`, or `internal_error`.

## Answering an approval request

Owner decisions are typed and recorded once per request:

```bash
python -m nexus.software_engineer decide --cycle <cycle-id|latest> \
  --verdict ship|revise|reject --reason "one sentence"
```

`decide` reads the cycle record, builds an `OwnerCommand` for the configured owner over
the `cli` channel, records an `OwnerDecision` in
`.nexus/software_engineer/decisions/<request_id>.json`, and remembers the verdict as an
owner preference. It refuses a cycle without an approval request and a request that was
already decided. Silence is never a decision; `revise` and `reject` never publish.

### From Slack

The same decision can arrive as a slash command. Create a Slack app with a slash command
(for example `/nexus`) pointing at `https://<your host>/slack/commands`, then run the
receiver next to the state root that holds the cycle records:

```bash
export NEXUS_SOFTWARE_ENGINEER_SLACK_SIGNING_SECRET=...   # from the Slack app
export NEXUS_SOFTWARE_ENGINEER_SLACK_OWNER_USER_ID=U0...   # your Slack member id
python -m nexus.software_engineer serve-slack --host 127.0.0.1 --port 8787
```

Grammar: `/nexus ship|revise|reject [<cycle-id>|latest] <reason>`. Every request must
carry a valid Slack `v0` HMAC signature (checked with the signing secret from the
environment) inside the replay window, must not repeat a signed request already seen in
that window, and must come from the configured owner member id; anything else is refused
with a stable code (`signature_invalid`, `timestamp_stale`, `replayed`, `not_owner`,
`already_decided`, `body_too_large`, ...) and records nothing. Seen signatures are kept as
digests in `<state_root>/slack/replay_ledger.json`, so a restart does not reopen the
window; if that file is unreadable every command is refused (`replay_ledger_unreadable`)
until the owner removes it. Prefer an explicit cycle id over `latest` when a new cycle may
have finished since the notification you are answering: `latest` is resolved when the
command arrives, and the reply names the request that was decided. A SHIP from Slack records the
decision only: publishing remains the owner-run `publish` step, so a compromised Slack
account can at most say "ship" about an already-validated draft, never open or merge one.
The receiver never reads secrets from Slack messages and never echoes them.

## Publishing an approved change

```bash
export NEXUS_SOFTWARE_ENGINEER_GITHUB_TOKEN=...   # fine-grained, this repository only
NEXUS_SOFTWARE_ENGINEER_ENABLED=true python -m nexus.software_engineer publish --cycle <id>
```

`publish` fails closed at every step:

1. The cycle must have an approval request, a change with a local branch, a recorded SHIP
   decision by the configured owner, and every recorded gate passed (including
   `sentinel_review`); a request is published at most once.
2. `candidate.patch` must hash to the change's `diff_sha256`.
3. `bundle_from_branch` proves the branch commit is exactly that patch applied to the
   validated base: it re-applies the patch on the base in a scratch clone and compares
   `git write-tree` with the commit's tree. Symlinks, submodules, type changes, and
   oversized files are refused.
4. `GitHubDraftPullRequestPublisher` reads the token from the environment, checks that the
   default branch still points at the validated base (`--allow-moved-base` overrides,
   and the record keeps both SHAs), that the base exists remotely, and that the branch
   does not; uploads each blob and checks its SHA against the local one; creates the
   tree and checks it against the local tree SHA; creates the commit and checks what it
   contains (exactly the verified tree, the validated base as its only parent, the message,
   author, committer, and date that were sent; the SHA itself may differ if GitHub signs
   it); creates the branch reference and checks it points at that commit; opens a
   **draft** pull request whose body carries the approval request, the decision, and the
   evidence hashes, and checks that its head is that commit and its base the default
   branch. If the repository rejects drafts, or the pull request shows anything else, the
   pull request is closed and the branch deleted again. It never merges and never touches
   the base branch.
5. The `PublishedChange` is written to `.nexus/software_engineer/publications/<request_id>.json`
   and remembered as a validated fact with the decision id as provenance.

Error codes are stable (`publish_credentials_missing`, `publish_base_moved`,
`publish_branch_exists`, `publish_tree_mismatch`, `publish_commit_mismatch`,
`publish_ref_mismatch`, `publish_pull_request_mismatch`, `gates_not_passed`, `patch_mismatch`,
`already_published`, ...) and never include response bodies or the token.

## Single run and interrupted runs

One cycle runs per state root. A cycle takes `.nexus/software_engineer/run.lock` before
it observes anything and releases it on every exit path; a second `cycle` against the
same state root refuses with `concurrent_run` (exit 2) and writes nothing. In GitHub
Actions the `resident-software-engineer` concurrency group gives the same guarantee. A
lease left by a cycle that died expires after `MAX_RUNTIME_SECONDS` plus five minutes, or
as soon as its process is gone; the next cycle then writes
`cycles/<cycle_id>.interrupted.json`, remembers the interruption as an incident, and
proceeds normally. Nothing else needs cleanup: an interrupted cycle wrote no record, and
its run directories are inert.

## Failure recovery

- A failed cycle still writes its record and report and sends
  `engineering_cycle_failed` when a transport is configured. Read `failure` and
  `decision_reasons`, fix the cause, and re-run; nothing needs cleanup.
- A dirty working tree blocks execution outside `dry_run`; commit or clean it.
- Memory problems: the store is a single SQLite file. Inspect with `EngineerMemoryStore(path).load_all()`; correct a wrong record with `correct`, retire one with `invalidate`. Never edit rows by hand.
- Sharing knowledge: `export_validated_knowledge(store, now=...)` is the only export; it carries active validated facts and owner decisions with provenance and a digest, never inferences or observations. Memory never changes mode, budgets, gates, or policy; those come from settings and reviewed code only.
- Notification problems are recorded per attempt (`error_code`); they never change a
  decision.

## Rollback

Every `ChangeSummary` carries `rollback_reference`. A published change is a draft pull
request on its own branch: closing the pull request and deleting the branch is the whole
rollback, and `main` was never touched. When a cycle that shipped autonomously observes a
gate regression it calls the executor's `rollback`, which asks the publisher to
`withdraw` (close the pull request, delete the branch), records a `RollbackRecord`, and
sends `rollback_occurred`. An owner can do the same by hand at any time.

## Not yet built

Test repair (blocked by SentinelQA-lite's byte-level specification rule); memory
consolidation; a hosted deployment of the Slack receiver (today it runs wherever the
owner starts it). No real GitHub or Slack call has
been made yet: the publisher, issue source, and Slack receiver are exercised only against
fakes in tests. See ADR 0012.
