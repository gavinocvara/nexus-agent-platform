# ADR 0012: Resident Software Engineer (bounded daily engineering cycle)

## Status

Accepted (foundation). Autonomous shipping stays disabled until the executor described
under "Deferred" exists and the owner enables it.

## Context

NEXUS should have a resident engineer that inspects the repository every day, notices
small problems before the owner does, fixes safe micro-issues, validates its own work,
explains itself, learns from validated outcomes, and asks the owner through Slack when
judgment is needed. It must do this without gaining authority it was not given: no
unrestricted shell, no self-approval, no silent policy changes, no spending on a fresh
clone.

The repository already has the pieces that make such an agent trustworthy: PatchForge
(bounded implementation through a typed tool gateway), SentinelQA (independent verification
against the pristine specification), Atlas (review, human approval, audit), Brain v1's
private-memory pattern, and deterministic gates. The engineer should compose them rather
than duplicate them.

## Decision

Build `nexus.software_engineer` as a governed cycle whose every phase, budget, decision,
gate result, memory write, and notification is runtime-attested.

### Defaults

`SoftwareEngineerSettings` (`NEXUS_SOFTWARE_ENGINEER_*`): `enabled=False`, `mode=dry_run`,
no model, `max_model_calls=0`, `max_cost_usd=0`. Nothing runs, spends, or sends until the
owner enables it. Mode, budgets, and the owner identity are governing settings the
engineer reads and never writes.

### Cycle

`EngineeringCycle.run` walks a closed phase machine: `created -> observe -> understand ->
prioritize -> investigate -> plan -> implement -> test -> self_review -> assess_risk ->
decide -> observe_results -> learn -> report -> closed`, never backwards, with budgets on
runtime, turns, tool calls, model calls, tokens, cost, changed files, and diff size. It
persists a canonical `CycleRecord` (signals, candidates, ranking, selection, risk,
self-review, gates, change, decision and reasons, approval request, usage, memory reads
and writes, notification records, rollback, failure) and a `CycleReport` under
`.nexus/software_engineer/cycles/`. "No sufficiently valuable safe work found today" is a
successful outcome.

### Observation and candidates

`RepositoryInspector` reads only what it can attest: Git history and status through the
runtime-owned `GitRunner` (read-only commands), TODO markers, and validation artifacts
produced by the repository's own checks (pytest JUnit XML, Ruff JSON, mypy output, gate
transcripts, benchmark reports). Commit messages and markers are `UntrustedText`:
bounded, hashed, scanned for instruction-shaped content, flagged, never followed.
`CandidateGenerator` is rule-based: every candidate cites signal identifiers and carries
runtime estimates (value, urgency, confidence, cost); ranking is a deterministic score.
Candidates derived from untrusted text are marked and classified as more risky.

### Risk and ship policy

`classify_change` takes the highest of the category floor, the most sensitive path, and
the change size, raises the level once when anything is uncertain, and forces HIGH for any
governing path (the engineer's own package, PatchForge/SentinelQA/Atlas policy and
boundary modules, CI workflows, `AGENTS.md`, frozen evidence, infrastructure, migrations,
sensitive files). Unknown categories, unclassified paths, and empty path lists are HIGH.

`ShipPolicy.decide` is a pure function: over budget -> `blocked`; dry run -> a plan-only
`request_approval`; any failed or missing required gate (`ruff_format`, `ruff_lint`,
`mypy`, `pytest_full`) -> `abandon`; governing paths, medium or high risk, a missing or
human-requiring self-review, or any non-autonomous mode -> `request_approval`; only a
low-risk, fully validated, cleanly reviewed change in `autonomous_low_risk` mode ->
`ship`. Owner decisions (`ship` / `revise` / `reject`) come only as typed `OwnerDecision`s
from a human actor and are re-checked against validation; silence never decides. Since
0.24.0 `ship` means "open a draft pull request" and nothing more.

### Self-review

`SelfReviewer` answers fourteen fixed questions (root cause, other paths, public behavior,
invariants, weakened tests, races, nondeterminism, complexity, hidden state, sensitive
data, prompt injection, test coverage, smaller solution, human review) from
`DiffFacts` derived from the diff alone. It shares no state with the implementer. A concern
or an unknown on a critical question blocks autonomous shipping.

### Memory

`EngineerMemoryStore` is a private SQLite namespace `software_engineer.resident` with
typed categories (repository knowledge, engineering lessons, owner preferences,
decisions, incidents, backlog, self-evaluation), an epistemic status (observation,
inference, owner decision, validated fact, failed hypothesis), provenance bound to a
cycle and evidence hashes, confidence, validity intervals, versions, supersession,
invalidation, deduplication by claim, and retrieval ordered by trust. Validated facts
require evidence or an owner decision; inferences cannot exceed 80% confidence;
secret-shaped or instruction-shaped content is refused. The store refuses foreign
namespaces; nothing reads AegisOps memory.

### Notifications

`Notifier` sends urgent events (approval required, blocked, security concern, rollback,
cycle failed, regressions) immediately with bounded retries and a per-cycle cap, and folds
minor events into one daily report. `SlackWebhookTransport` reads the webhook URL from the
configured environment variable at send time and never stores it; records keep only a
body hash. Delivery failure is evidence, never an exception in the cycle.

### Scheduling

`.github/workflows/software-engineer.yml` runs on `workflow_dispatch` and a daily cron,
only when the repository variable `NEXUS_SOFTWARE_ENGINEER_ENABLED` is `true`, with a
read-only token, producing the inspection artifacts and running one cycle. It uploads the
record and report; it cannot push.

### Gates

`tests/unit/test_software_engineer_*.py` cover: default-off configuration; category and
path risk tables; escalation on uncertainty, size, and governing paths; every ship-policy
refusal; owner decisions and impostors; instruction-like text detection; memory dedup,
correction, invalidation, trust ordering, claim rules, and namespace isolation; notifier
urgency, aggregation, retries, secret blocking, rate limits, and the Slack transport over a
mock HTTP client; diff facts and the reviewer; inspection signals and candidate ranking;
and the cycle itself: no-work, dry-run approval requests without touching code,
autonomous shipping with a validated-fact memory, propose mode never shipping, failed
gates producing a failed hypothesis that blocks a retry, governing paths and higher risk
escalating, weakened tests needing a human, budget exhaustion, notifier outage, a dirty
tree, prompt injection in history, and the disabled-by-default CLI.

### Executor v1: mechanical recipes (0.21.0)

`PatchForgeExecutor` implements `CandidateExecutor` for categories with a mechanical
recipe: `formatting` (`ruff format`) and `dead_code_removal` (`ruff check --fix`). A recipe
is an operator-owned `RepositoryProfile` (reproduction = the matching check; formatter =
the fixing tool; linter, type checker, targeted and full tests) plus a fixed PatchForge
script. The engine never edits files: the operator's tool makes the change inside the
sandbox as the profile's formatter command, the rest of the profile validates it, and
PatchForge's Runtime and Attestor own all evidence. SentinelQA then verifies the attested
patch against the pristine specification lock captured from the operator checkout's HEAD.
Gate results are mapped from the attested checks (`ruff_format`, `ruff_lint`, `mypy`,
`pytest_targeted`, `pytest_full`) and the SentinelQA verdict (`sentinel_review`), each
with an evidence hash. The candidate is materialized as a commit on a local branch
`nexus/software-engineer/<cycle>` in a separate clone; the operator checkout is never
modified. `can_ship` is `False`: without a publisher every validated change becomes an
approval request, and the owner applies the branch or patch.

`LocalProcessSandbox` exists because ephemeral runners such as GitHub Actions have no
Docker daemon. It runs only immutable operator-profile commands, without a shell, in a
`.git`-free worktree, with a scrubbed environment (no secrets; `PYTHONPATH` and `MYPYPATH`
point at the worktree so the checkout under test, not an installed copy, is exercised),
bounded by timeouts and output limits. It provides no kernel isolation, so it must be
enabled explicitly (`NEXUS_SOFTWARE_ENGINEER_SANDBOX=local_process`) and is meant only for
this repository in a disposable, credential-free job. Docker remains the sandbox for
untrusted repositories.

### Controlled evaluation (0.22.0)

`nexus.software_engineer.evaluation` is the engineer's judgment gate: 28 scripted
scenarios over fixture repositories (see the CHANGELOG list), each checked for the
expected decision, failure, risk, notifications, memory statuses, and decision reasons,
plus universal invariants (untouched source repository, no shipping outside policy, no
secrets in records or reports, no instruction-shaped memory, provenance on every write)
and byte-identical replay. Only the obvious micro bug ships, and only in
`autonomous_low_risk` mode with a scripted executor that can publish. Two behaviors came
out of building it: candidates whose cost outweighs their value are never selected, and
regressions visible in the evidence are notified even when nothing is actionable.

### Model-backed recipes (0.23.0)

Categories that need judgment (`type_annotation`, `micro_bug_fix`, `defensive_check`) run
PatchForge's `ModelBackedEngine` inside the same executor: the model chooses one typed
tool call per turn, the gateway enforces phase and path policy, reproduction is the check
that reported the defect (mypy or the targeted tests), the formatter only checks so the
diff is the model's alone, SentinelQA verifies the attested patch, and the result is a
local branch plus an approval request. Test repair is excluded because SentinelQA-lite
treats tests as the specification. Spending needs a model, an explicit
`confirm_model_spend`, positive call and token budgets, and the API key in the
environment; without any one of them the candidate stays an approval-only plan. Model
calls and tokens are accounted in the cycle's usage; engine call records are hash-only.

### Publication as draft pull requests (0.24.0)

"Ship" never means merge. The only publisher is `GitHubDraftPullRequestPublisher`: it
creates a branch and a draft pull request through the GitHub REST API and a human merges.
Two paths lead to it. The owner path is two explicit CLI steps: `decide` records a typed
`OwnerDecision` (SHIP, REVISE, or REJECT) for a cycle's approval request, once; `publish`
opens the draft pull request only for a SHIP by the configured owner, only when every
recorded gate (including `sentinel_review`) passed, and only once per request. The
autonomous path exists but is off: `PatchForgeExecutor.ship` publishes the change it
produced in that cycle when a publisher is configured, which the CLI does only when
`publish_from_cycle` is true, the mode is `autonomous_low_risk`, and the token is present;
the scheduled workflow never receives the token.

Before any request reaches GitHub, `bundle_from_branch` proves the candidate commit is
exactly the validated patch applied to the validated base (re-apply in a scratch clone,
compare `git write-tree`). The publisher then verifies every uploaded blob's SHA and the
created tree's SHA against the local objects before the branch reference exists, refuses
a moved default branch unless the owner allows it, refuses an existing branch, deletes
its own branch if the repository rejects drafts, and never touches the base branch. The
token is read from the environment at publish time, sent only as a header, and absent
from records, memories, reports, and errors; `contains_credential` extends the shared
secret detector with GitHub and Slack shapes for everything the engineer writes. A
`PublishedChange` (repository, branch, base and remote SHAs, tree SHA, pull request
number and URL, authority, publisher identity) is persisted, attached to the change, and
remembered as a validated fact with the decision as provenance. Rollback of a published
change is `withdraw`: close the pull request, delete the branch.

## Deferred

- GitHub issue intake (the remainder of PatchForge Milestone K) and any publisher that
  fast-forwards `main`; the engineer will not merge.
- Test repair and any recipe that must change tests wait for SentinelQA to review
  candidate-added or changed tests; today they remain approval-only plans.
- Price tables for cost accounting; until then token and call budgets bound spending.
- A model-backed investigator for candidate refinement behind the existing `ModelClient`
  boundary, with the same one-action-per-turn parsing PatchForge uses.
- Owner commands arriving through Slack (today: typed `OwnerCommand` via code/CLI).
- Consolidation of memories into durable repository knowledge with pre-registered
  evaluation of whether memory improves outcomes (BRAIN.md rules apply).

## Consequences

- The engineer can run daily from day one without risk: it observes, ranks, plans,
  reports, and asks. Its authority grows only through owner-set mode and the executor.
- Every decision is reconstructible from the persisted record; every memory says how it
  is known; every notification leaves delivery evidence without content.
- Governing files cannot be changed autonomously even if the classifier is wrong,
  because the policy checks governing paths independently.
