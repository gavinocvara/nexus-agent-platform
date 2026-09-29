# Changelog

Meaningful changes to NEXUS, newest first. Versions are the `pyproject.toml` package
versions. The repository has no release tags; each heading names the commit that set
that version. Design rationale lives in `docs/adr/`, detail in Git history.

## Unreleased

- Resident Software Engineer brain: new memory categories `root_cause` and
  `recurring_pattern`; the learn stage records root causes as validated facts (with
  evidence), recurring work as observations when an identical decision was already
  remembered by an earlier cycle, and overconfident estimates as self-evaluations.
  `MemoryQuery(trusted_only=True)` and `KnowledgeExport` /
  `export_validated_knowledge` are the typed boundary out of the namespace (validated
  facts and owner decisions only, with provenance and a digest). New tests prove stale
  memory expires from retrieval but stays in history, conflicts resolve by trust and
  dispute, owner corrections supersede inferences, a false inference can never become
  trusted knowledge, poisoning is refused at the contract, other agents' tables are
  never read, and poisoned preferences change no decision, budget, mode, or gate.
- SentinelQA-lite: the harness is inside the specification boundary. Modules or packages
  that would shadow the runner (`pytest.py`, `_pytest/`, `pluggy/`, `unittest.py`,
  `site-packages/`, ...) are evaluation configuration; a new rejection finding
  `harness_tampering` catches added source lines that import the harness, edit
  `sys.modules`, replace `__import__`, register pytest plugins or hooks, read `PYTEST_*`,
  hook assertion rewriting, install tracers, or rebind `sys.excepthook`. Three adversarial
  scenarios join the gate (`pytest_module_shadowed`, `harness_patched_from_source`,
  `test_marked_skip`), for 32 in total, plus unit tests for the scanner.
- Resident Software Engineer: enforceable cost accounting. `PriceTable` (owner-supplied
  USD per million input / output tokens, `NEXUS_SOFTWARE_ENGINEER_MODEL_PRICE_INPUT_PER_MTOK`
  and `_OUTPUT_PER_MTOK`) prices every model run from the client's token counts; the cost
  flows into the cycle's usage and `MAX_COST_USD` is enforced as `budget_exhausted`.
  Model recipes are allowed only with both prices and a positive cost ceiling (settings,
  CLI, and executor all fail closed), the daily report shows tokens and dollars, and the
  workflow passes the price and ceiling variables through (all default to off).

## 0.25.0 — Resident Software Engineer owner channels: Slack decisions and issue intake

- Resident Software Engineer: owner decisions through Slack slash commands
  (`nexus.software_engineer.slack_commands`, `serve-slack`). `/nexus ship|revise|reject
  [cycle|latest] <reason>` is accepted only with a valid Slack `v0` HMAC signature (secret
  from the environment, per request), inside the replay window, and from the configured
  owner member id; it records the decision through the same path as the `decide` CLI, once
  per request, over the `slack` channel. Slack never publishes. Tests sign real HMACs and
  drive the FastAPI receiver with a test client.
- Resident Software Engineer: read-only GitHub issue intake (`nexus.software_engineer.issues`),
  opt-in through `NEXUS_SOFTWARE_ENGINEER_READ_ISSUES`. Open issues (never pull requests)
  become bounded, hashed, instruction-scanned `issue` signals; at most three per cycle
  become `Investigate issue #N` candidates of category `unknown` derived from untrusted
  text (a plan for the owner when bug-labelled, report-only otherwise). Owner REJECT
  decisions are now honoured by the candidate generator: the same ask is zeroed and
  blocked on later days. The scheduled workflow gains `issues: read` and passes its own
  token for the read. Any GitHub failure is one warning signal, never a failed cycle.
- Resident Software Engineer: `bundle_from_branch` normalizes Git author dates so Git 2.55
  (`Z`) and older Git (`+00:00`) produce the same bundle (CI fix, `ebca518`).
- Resident Software Engineer evaluation: two publication scenarios join the judgment gate.
  `autonomous_draft_publication` (a LOW-risk change in `autonomous_low_risk` mode with a
  publisher ships as a draft pull request recorded on the change) and
  `publisher_refuses_at_ship` (a publisher refusal is a typed executor failure: the cycle
  is `blocked`, nothing is published, `engineering_cycle_failed` is sent). New invariant:
  a publication appears only in a shipped, unfailed cycle and is always a draft.

## 0.24.0 — Resident Software Engineer owner-approved draft pull requests

- `nexus.software_engineer.publish`: a `Publisher` protocol and
  `GitHubDraftPullRequestPublisher` that creates a branch and a **draft** pull request
  through the GitHub REST API (blobs, tree, commit, reference, pull request), never a
  merge and never the base branch. Every uploaded blob and the created tree are checked
  against the local object SHAs before the reference exists; a moved default branch, an
  existing branch, rejected drafts, and rejected credentials fail closed, and a branch the
  publisher created is deleted if the pull request cannot be opened. The token comes from
  the environment at publish time and never appears in records, memories, errors, or
  reports.
- `bundle_from_branch` proves a candidate commit is exactly the validated patch applied to
  the validated base by re-applying the patch in a scratch clone and comparing
  `git write-tree` with the commit's tree; symlinks, submodules, type changes, and
  oversized files are refused.
- `nexus.software_engineer.approval` and two CLI commands: `decide` records the owner's
  SHIP / REVISE / REJECT for a cycle's approval request once, as a typed `OwnerDecision`
  and an owner-preference memory; `publish` opens the draft pull request only for a SHIP
  by the configured owner, only when every recorded gate passed, only once per request,
  and remembers the `PublishedChange` as a validated fact with the decision as provenance.
- `PatchForgeExecutor` accepts a publisher: `can_ship` becomes true, `ship` publishes
  exactly the change it produced in that cycle, and `rollback` withdraws a published
  change (close the pull request, delete the branch). The CLI wires a publisher into a
  cycle only when `NEXUS_SOFTWARE_ENGINEER_PUBLISH_FROM_CYCLE=true`, the mode is
  `autonomous_low_risk`, and the token is present; the scheduled workflow never receives
  the token, so scheduled cycles cannot publish.
- `ChangeSummary.publication` and the `PublishedChange` contract; `contains_credential`
  in the trust module extends the shared secret detector with GitHub token, Slack webhook
  and token, and cloud key shapes for everything the engineer notifies, remembers, or
  renders.
- The scheduled workflow uploads the candidate branch clone and evidence files (with
  hidden paths) so the owner can `decide` and `publish` from a local checkout.
- Marker scanner fix (from `2632597`): only comment markers count, and marker candidates
  are report-only rather than daily approval requests.
- Tests drive the publisher against a fake GitHub API (`httpx.MockTransport`) that
  computes real blob SHAs: the happy path end to end from a real propose-mode cycle,
  tampering, wrong owner, moved base, existing branch, tree mismatch, unsupported drafts,
  missing and rejected credentials, autonomous ship and withdraw, and the CLI. No real
  GitHub call was made.

## 0.23.0 — Resident Software Engineer model-backed recipes (no live calls)

- Model recipes for `type_annotation`, `micro_bug_fix`, and `defensive_check` candidates
  run PatchForge's existing `ModelBackedEngine` under exactly the same governance as the
  mechanical recipes: real ToolGateway, Runtime, and Attestor; reproduction by the check
  that reported the defect; check-only formatter so the diff is the model's alone;
  SentinelQA verification; gates with evidence hashes; a local candidate branch; no
  publisher. Test repair is deliberately excluded because SentinelQA-lite treats tests as
  the specification.
- Spending is opt-in four times over: a model, `confirm_model_spend=true`, positive
  `max_model_calls` and `max_output_tokens`, and `OPENAI_API_KEY` in the environment.
  Any missing condition keeps model recipes as approval-only plans. Model calls and
  tokens flow into the cycle's usage and budgets; hash-only engine call records are
  persisted next to the run.
- The scheduled workflow passes the model variables and an optional
  `NEXUS_SOFTWARE_ENGINEER_OPENAI_API_KEY` secret through; all default to off.
- Tests drive the model path with a scripted client (a real type-error fix verified end
  to end, a plan when no model is configured, garbage output as a typed engine failure,
  and the CLI refusing to build a model engine without every condition). No live model
  call was made.

## 0.22.0 — Resident Software Engineer controlled evaluation

- `nexus.software_engineer.evaluation`: a 28-scenario judgment catalog run as a CI gate
  (`python -m nexus.software_engineer.evaluation`), replayed byte for byte. Scenarios:
  nothing to do, obvious micro bug (the only one that ships, and only autonomously),
  failing unit test, misleading bug report, unrelated failing test, repeated failed patch
  (blocked on the second cycle by memory), risky dependency upgrade, security-sensitive
  change, ambiguous architectural change, unnecessary refactor (never selected),
  malformed repository, merge conflict, dirty working tree, missing Slack credentials,
  Slack outage, model outage, timeout, insufficient evidence, conflicting evidence, stale
  memory, prompt injection in history, a request to weaken safety controls, benchmark
  regression, cost budget exhaustion, owner rejection / approval / revision, and a
  validated change without a publisher. Every run also checks that the source repository
  is untouched, nothing ships outside policy, records and reports carry no secrets, and
  memory carries no instructions and full provenance.
- Cycle: candidates whose cost outweighs their value are never selected ("no candidate
  is worth its cost"); test, gate, and benchmark regressions observed in the evidence are
  notified as urgent events even when no candidate is actionable.
- Budgets: changed files and diff bytes are autonomous-shipping limits handled by the risk
  classifier (a larger change is proposed, not blocked); hard budgets remain runtime,
  turns, tool calls, model calls, tokens, and cost.

## 0.21.0 — Resident Software Engineer executor v1 (mechanical recipes)

- `PatchForgeExecutor`: for candidates whose category has a mechanical recipe
  (`formatting` -> `ruff format`, `dead_code_removal` -> `ruff check --fix`) it provisions a
  disposable PatchForge workspace from the operator checkout, runs the recipe's fixed
  script through the real ToolGateway, Runtime, and Attestor (reproduce with the matching
  check, let the operator's tool make the change, validate with formatter, linter, type
  checker, targeted and full tests, report), has SentinelQA verify the attested patch
  against the pristine specification lock, maps the attested checks and the verdict to
  gate results with evidence hashes, and materializes the candidate as a commit on a
  local `nexus/software-engineer/<cycle>` branch in a separate clone. It cannot ship: no
  publisher exists, so the policy turns every validated change into an approval request.
- `LocalProcessSandbox`: a `SandboxExecutor` for ephemeral, credential-free runners with
  no Docker daemon. Explicit enablement (`NEXUS_SOFTWARE_ENGINEER_SANDBOX=local_process`),
  immutable operator commands only, no shell, `.git`-free worktrees, a scrubbed
  environment (no secrets; `PYTHONPATH`/`MYPYPATH` point at the worktree), timeout and
  output bounds. It has no container isolation and is documented as such.
- Ship policy: a run that produced no change but failed no gate is a plan and becomes an
  approval request; a ship decision without a publisher becomes an approval request.
- SentinelQA: only the targeted and full-suite runs must report pytest counts;
  reproduction may be any operator check (for example a formatter check).
- Settings: `NEXUS_SOFTWARE_ENGINEER_REPOSITORY_URL`, `NEXUS_SOFTWARE_ENGINEER_SANDBOX`;
  the scheduled workflow passes the sandbox variable through.
- Tests: the executor over fixture repositories (formatting and lint-fix recipes produce
  verified branches; a recipe that breaks a test is not proposed; a SentinelQA
  disagreement fails the review gate; categories without a recipe return a plan; a
  propose-mode cycle turns the verified branch into an approval request) and the local
  sandbox (enablement, scrubbed environment, interpreter mapping, bounds, Git refusal).

## 0.20.0 — Resident Software Engineer foundation

- New `nexus.software_engineer` package (ADR 0012), disabled by default
  (`NEXUS_SOFTWARE_ENGINEER_ENABLED=false`, `mode=dry_run`, no model, zero model-call and
  cost budgets):
  - strict contracts for signals, candidates, risk, gates, self-review, change summaries,
    approval requests, owner decisions, budgets, cycle records, and reports;
  - `classify_change`: category floor, path sensitivity, size, uncertainty raises the
    level, governing paths (own package, PatchForge/SentinelQA/Atlas boundaries, CI,
    `AGENTS.md`, frozen evidence, infrastructure, sensitive files) are always HIGH;
  - `ShipPolicy`: budget, dry-run, failed or missing gates, governing paths, risk,
    self-review, mode, and owner decisions decide ship / request approval / abandon /
    blocked; silence never approves;
  - `UntrustedText` and `OwnerCommand`: repository text and messages are data and are
    scanned for instruction-shaped content; only the configured human owner decides;
  - `EngineerMemoryStore`: private namespace `software_engineer.resident` with typed
    categories, epistemic status (observation, inference, owner decision, validated
    fact, failed hypothesis), provenance, confidence, validity, versioning, dedup,
    correction, invalidation, trust-ordered retrieval, secret and instruction refusal;
  - `RepositoryInspector` and `CandidateGenerator`: Git history and status, TODO markers,
    pytest/Ruff/mypy/gate/benchmark artifacts, deterministic ranking with cited evidence;
  - `SelfReviewer` over `DiffFacts`; `Notifier` with urgent-now / aggregate-daily
    behavior, retries, per-cycle caps, secret blocking, and a `SlackWebhookTransport`
    that reads the webhook only from the environment;
  - `EngineeringCycle`: the closed phase machine with budgets, a dry-run executor,
    persisted canonical records and reports, memory writes only from validated evidence
    or owner decisions, and failure handling that always leaves a record;
  - `python -m nexus.software_engineer {preflight,inspect,cycle}` and a disabled-by-default
    scheduled GitHub Actions workflow with a read-only token.
- Tests cover configuration defaults, risk tables, every policy refusal, owner decisions,
  injection detection, memory rules and isolation, notifier behavior and Slack transport,
  diff facts and review, inspection and ranking, and the cycle end to end (no-work,
  dry-run approval request, autonomous ship with scripted executor, propose mode, failed
  gate and blocked retry, governing paths, weakened tests, budget exhaustion, notifier
  outage, dirty tree, prompt injection, CLI).
- Runbook `docs/runbooks/software-engineer.md`.

## 0.19.0 — PatchForge Milestone J: SentinelQA-lite independent verification

- New `nexus.sentinelqa` package (ADR 0011): `SpecificationLock` captured from the Git
  objects of the source commit (tests under the operator prefixes, evaluation
  configuration anywhere, operator-declared paths; symlinks fail closed),
  `SentinelQAVerifier`, `SpecificationRunner`, a strict pytest summary parser, and
  runtime-attested `SentinelVerdict`s that convert to Atlas `ReviewResult`s for reviewer
  `sentinelqa.reviewer`.
- Verification happens on fresh `.git`-free trees of the source commit: baseline runs on
  the pristine tree, the patch applied to a second tree, every locked file compared with
  the lock, then the *verification tree* (candidate with the pristine specification
  restored and candidate-added tests/config removed) run through the operator's targeted
  and full-suite commands with tree fingerprints, count rules against the baseline, and
  a cross-check of PatchForge's attested checks.
- Verdicts fail closed: unverifiable evidence is `inconclusive`, any rejection finding is
  `failed`, only a clean review is `passed`. Closed finding vocabulary with categories.
- `python -m nexus.sentinelqa`: a 29-scenario adversarial gate (modified, weakened,
  deleted, renamed, moved tests; manipulated expected data; `conftest.py`,
  `pyproject.toml`, `sitecustomize.py` injection; skips and non-collection from code;
  a candidate passing only with its own tests; mutated verification trees; rewritten
  source history; tampered, incomplete, or foreign locks; corrupted artifacts; executor
  outages, timeouts, missing summaries, contradictory statuses) replayed byte-identically.
  CI runs it and the SentinelQA unit tests.
- Benchmark v0 reviews every proposal with SentinelQA (no ground truth) and records
  agreement with the hidden truth: `reference` 5/5 passed, `fix_and_edit_tests` 5/5
  failed with `specification_modified`, zero disagreements. The live run path evaluates
  through SentinelQA and prints the verdict.
- `E2ERun` carries the `EngineeringTask` it ran (additive).
- The Milestone H finding is closed: specification-weakening proposals are now rejected
  by an independent verifier rather than only by benchmark ground truth.

## 0.18.0 — PatchForge Milestone I: model engine boundary (`b613072`)

- `ModelBackedEngine` behind a provider-neutral `ModelClient`: one strictly parsed action
  per turn, only the phase's tools, JSON-mode argument parsing, escaped and bounded
  untrusted tool output, a model-call budget, and hash-only call audit records.
  Malformed or unauthorized output is an engine failure, with no retries.
- `python -m nexus.patchforge.live`:
  - `preflight` makes no network call and reports only whether the key is present;
  - `run` requires `NEXUS_PATCHFORGE_LIVE_ENABLED=true`, the key, and `--confirm-live`,
    runs one Benchmark v0 task with the content-oracle sandbox, evaluates it
    independently, writes artifacts under `.nexus/`, and stops.
- `OpenAIResponsesClient` requests a strict single-action JSON schema with no SDK retries.
- The E2E harness declares the engine identity; deterministic gates must be `scripted`.
- `.env.example` documents the opt-in `NEXUS_PATCHFORGE_LIVE_*` settings.
- No live model call has been made.

## 0.17.0 — PatchForge Milestone H: Benchmark v0 (`693047c`)

- Five synthetic defects with evaluator-only ground truth (`nexus.patchforge.benchmark`,
  `nexus.patchforge.benchmark_corpus`).
- `ContentOracleSandbox` scores worktree contents against the ground truth without
  executing repository code or revealing the truth.
- An independent evaluator re-applies each attested patch artifact to a clean clone; a
  task counts as resolved only if PatchForge proposed a patch and the evaluator confirms
  it.
- Deterministic engines: `reference` (5/5 resolved), `noop` (0/5), `test_editor` (0/5),
  and `fix_and_edit_tests` (5 proposals, all rejected as false proposals).
- `python -m nexus.patchforge.benchmark_corpus` is a new CI gate: expected counts, zero
  invariant violations, and byte-identical reports on replay.
- The E2E harness accepts a custom sandbox factory and separates universal invariants
  from scenario expectations.

## 0.16.2 — PatchForge finalization-reserve hardening (`1984b8e`)

- A finalize-phase call refused because it would take the report's reserved call or
  output no longer destroys the report. The gateway raises `GatewayReserveRefusal`;
  Runtime records one `finalize -> finalize` failure transition and a
  `ReserveRefusalRecord`, then accepts only `submit_report`. Any other action ends
  finalization.
- The run stays `partial` / `budget_exhausted` (fail closed). Budgets are not reset or
  expanded; the refused call leaves no ledger entry.
- Unrelated finalization failures (engine errors, policy denials, ordinary budget
  exhaustion) still end finalization directly.
- `RuntimeSnapshot.report_only` and `RuntimeCompletion.reserve_refusal` are additive
  fields, validated against the transcript.
- New deterministic E2E scenarios: `reserve_refusal_then_other_action` and
  `second_report_attempt`.

## 0.16.1 — PatchForge Milestone G completion (`9b05cdc`)

- The deterministic E2E catalog grows to 21 scenarios: success, inability to reproduce
  (already passing and skipped), targeted and full-suite validation failure, a
  successful bounded retry, budget exhaustion, cancellation, policy, workspace and
  lease-renewal failure, sandbox, engine, finalization and cleanup failure, in-run and
  post-final tamper, stale validation, unknown report evidence, and
  finalization-reserve use and refusal.
- Every E2E run is checked for universal invariants: a closed transcript through
  cleanup, workspace removal, runtime-owned evidence linkage, an untouched source
  repository, isolated sandbox requests, memory disabled, and a scripted engine.
- The harness can refuse the Nth lease renewal, and the gate reports the specific
  expectation a scenario violated.

## 0.16.0 — PatchForge Milestone G: Deterministic E2E (`915ea12`)

- `PatchForgeE2EHarness` runs scenarios through the real PatchForge path around a
  `ScriptedEngine` and `FakeSandbox`, with no model, network, Docker, or secrets.
- Fixture repositories are built as data and committed with a fixed Git identity and
  date; fixed clocks and content-derived IDs make each scenario replay to a
  byte-identical `PatchResult`.
- The scenario catalog covers success and one end-to-end scenario per
  `PatchForgeFailure`. `python -m nexus.patchforge.e2e_catalog` runs each twice and
  fails on an unexpected outcome or a non-identical replay; CI runs it as a new
  "Validate PatchForge deterministic E2E" step.
- Fixed an intermittent workspace test that compared proposal commits built on two
  separately created fixture repositories, whose wall-clock commit times could differ.

## 0.15.0 — PatchForge Milestone F: Attestor (`d6d39d3`)

- `PatchForgeAttestor` turns a closed `RuntimeCompletion` into a `PatchResult` from
  runtime-owned evidence only. Only a clean attestation is `patch_proposed`.
- Runtime fingerprints the worktree diff at start and around every write or sandbox
  execution, and commits the final tree before cleanup (`WorkspaceManager.propose_commit`:
  fixed runtime identity, private index, byte-identical diff).
- Validation counts only when it observed the exact final tree and left it unchanged.
  Reproduction requires the profile's `reproduction` command to run on the unmodified
  tree; without that command it is `not_practical`.
- Tamper checks: pristine start, no unexplained or post-final changes, fingerprints on
  every write or execution, command and sandbox-policy hashes, verifiable report
  evidence. Scope, protected, sensitive, test-policy, and size checks re-run on the
  final diff.
- The patch is stored in a content-addressed `LocalArtifactStore`.
- Additive contract changes: `RuntimeCompletion` gains `workspace_states`,
  `final_capture`, and `final_capture_error`; `GitRunner` accepts an allowlist of index
  and commit-identity environment overrides.
- `RuntimeGateway.workspace_manager` is a read-only protocol property, so the real
  `ToolGateway` satisfies the protocol under strict typing.

## 0.14.0 — PatchForge Milestone E: Runtime (`7222297`)

- Closed deterministic lifecycle from `created` through `closed`, with explicit
  runtime-owned transition records and only the bounded
  `implement <-> targeted_validate` retry edge.
- Single-action typed engine protocol and coordinator over ToolGateway. Phase/report
  requests remain request-only until Runtime accepts them; no parallel action shape or
  live engine exists.
- Deterministic partial, cancellation, policy, sandbox, workspace, validation,
  attestation, engine, budget-exhaustion, finalization, and cleanup-failure paths.
- Runtime renews leases only for workspace-bound tools and immediately refreshes
  ToolGateway after every renewal. The gateway and Runtime expose cross-checked
  runtime-attested phase and total budget usage.
- Cleanup runs on success, typed failure, and unexpected-defect paths. Verified workspace
  removal safely handles read-only Git objects on Windows without relaxing managed-path
  checks.
- A real deterministic ToolGateway/WorkspaceManager/FakeSandbox path proves reproduction,
  editing, targeted/full validation, diff inspection, reserved finalization, report
  submission, evidence retention, lease refresh, and cleanup.
- Successful patch attestation remains deferred to Milestone F. Runtime produces no
  merge, approval, GitHub mutation, network, secret, memory, or live-model behavior.

## 0.13.1 — PatchForge Milestone D hardening (`16a58f3`)

### Security

Fixes seven gateway defects from an independent adversarial review of Milestone D:

- A budget overrun after a sandbox execution raised an unhandled `ValidationError`; it
  now returns a typed budget failure and keeps the execution evidence.
- Non-report tools in `finalize` could consume the reserve `submit_report` needs; the
  last call and result envelope are now reserved for the report.
- A new or changed `.gitignore`/`.gitattributes` could hide worktree changes from status
  and diff. Those files and Git-ignored paths are no longer writable, and status/diff
  fail closed when ignore or attribute rules differ from the source commit (tool-cache
  `.gitignore` files excepted).
- Non-portable filenames such as `a:b.py` crashed tree, search, and diff; reads now omit
  them (counted in the additive `TreeOutput.omitted_unrepresentable` field) and diffs
  fail closed.
- File and execution tools did not re-verify the workspace handle or lease; every
  workspace-touching call now does. `refresh_workspace` adopts a renewed handle.
- `submit_report` accepted unknown evidence IDs; it now requires existing runtime records.
- `conftest.py`, `pytest.ini`, `tox.ini`, `setup.cfg`, `sitecustomize.py`,
  `usercustomize.py`, and `*.pth` bypassed the test-change policy; they now follow it
  and are reported as test changes.

### Changed

- Slimmed default agent context: a short `AGENTS.md` router (imported by `CLAUDE.md`)
  and a current-state-only `CODEX_HANDOFF.md`. Retired `NEXUS_MASTER_BUILD_PROMPT.md`,
  `NEXUS_PROJECT_INSTRUCTIONS.md`, and `PROJECT_STATE.md`; their still-current content
  moved to `AGENTS.md`, `ROADMAP.md`, `BRAIN.md`, the ADRs, and the Brain runbook.
- Trimmed `README.md` to a concise entry point and reorganized this changelog by version.
- The PatchForge policy test fixture protects `AGENTS.md` instead of the retired file.

## 0.13.0 — PatchForge Milestone D: ToolGateway (`bbb093e`)

- `ToolGateway`: strict typed read, compare-and-swap write, runtime-owned Git status/diff,
  fixed operator-profile execution, and request-only `advance_phase`/`submit_report`,
  bound to the Atlas job, agent, task, source SHA, repository profile, and workspace.
- Enforces capability, phase, path/symlink, protected-file, test-change, call, duration,
  output, and finalization-reserve policy. Shell executables are rejected.
- Append-only runtime evidence with contiguous call IDs, canonical hashes, typed
  operational failures, and bounded-output truncation attestation.
- Reproduction and targeted validation select distinct immutable profile commands.

## 0.12.0 — PatchForge Milestone C: SandboxExecutor (`72c34c6`)

- `DockerSandbox` and deterministic `FakeSandbox` behind one typed executor protocol with
  runtime-attested command/policy hashes and typed outcomes.
- Docker isolation: content-addressed local image, no pull/network/host environment,
  non-root, read-only root, dropped capabilities, no-new-privileges, bounded
  CPU/memory/PIDs/time/output, one `.git`-free worktree mount, forced cleanup.
- Profile commands must fit sandbox timeout/output ceilings; images accept only
  content-addressed IDs or repository digests.

## 0.11.0 — PatchForge Milestone B: WorkspaceManager (`c0bcc9b`)

- Exact-SHA disposable worktrees with runtime-owned bare Git metadata outside the
  execution tree, deterministic bounded diffs, durable leases, idempotent cleanup, and
  marker-verified orphan reaping.
- No-shell Git runner with sanitized configuration, disabled hooks and line-ending
  conversion, and bounded time/output.

## 0.10.0 — PatchForge Milestone A: Contracts (`dd91aba`)

- Contracts for engineering tasks, run identity, phase budgets with a finalization
  reserve, runtime-attested evidence, narrative-only agent reports, typed patch outcomes,
  and canonical hashes.
- Operator-owned repository profiles with argument-vector commands and a mandatory
  digest-pinned, networkless, secretless, non-root, resource-bounded sandbox policy.
- ADR 0010 records the accelerated PatchForge sequence.

## 0.9.0 — Phase 8: Atlas thin control plane (`c28fe5c`)

- Typed jobs, source revisions, budgets, capabilities, results, reviews, human approval,
  failures, leases, and runtime dispatch adapters (ADR 0009).
- Closed deterministic lifecycle, explicit agent policy registry, transactional SQLite
  persistence, optimistic revisions, idempotent command replay, explicit expired-lease
  recovery, and append-only audit events protected by database triggers.
- Local library only: no service, model call, scheduler, shell, deployment, or remediation.

## 0.8.1 — Phase 7 post-calibration hardening (`15eff37`)

- Legacy analysis-version-1 summaries report unavailable Phase 7 metrics as `null`, so
  comparisons cannot manufacture historical zeros or deltas.
- Brain configuration is explicit: constructors default to disabled, the legacy
  evaluation harness rejects writable mode, and the ad-hoc investigator refuses ambient
  writable Brain settings.
- Evaluator score/canary invariance covered end to end; lock-schema-2 artifact digests
  must match `run_count`.

## 0.8.0 — Phase 7: Brain v1 (`a7b6f5c`)

- Private episodic and procedural memory for `aegisops.investigator` with provenance,
  namespace isolation, lifecycle state, deterministic retrieval, untrusted-context
  framing, secret rejection, and SQLite storage (ADR 0008).
- Default-off `disabled`/`learn`/`frozen_eval` modes, fail-closed `brain_failure`,
  per-run audit metadata, frozen snapshots, a targeted benchmark command, and a committed
  pre-registration protocol.
- Portable baseline lock schema 2 (storage-relative paths, per-artifact digests); legacy
  locks resolve by session ID. Benchmark schema 3 is unchanged and backward compatible.
- SQLAlchemy restricted to `<2.1` for OpenTelemetry instrumentor support.

## 0.7.4 — Evaluator portability repair (`52bd5f4`)

- Clean-stack subprocess capture is byte-based and locale-independent; failures
  distinguish timeout, launch failure, and nonzero exit without persisting output.

## 0.7.3 — SDK tool validation repair (`5a2939e`)

- SDK-facing tool schemas match inner Pydantic constraints; Agents SDK pinned to
  `0.22.3`; the tool identity hash covers SDK parameter/output schemas.
- Payload-free SDK validation diagnostics. Benchmark schema advanced to 3.

## 0.7.2 — Live runtime accounting repair (`2c2603a`)

- Concurrency-safe diagnostic call permits, in-flight drain tracking, sanitized failure
  classification, and partial failed-run accounting.
- Benchmark schema 2: failed-run turns distinguish unknown from zero; runs carry
  accounting completeness and typed failures.

## 0.7.1 — Structured output repair (`e014a87`)

- Strict `DiagnosisOutputSchema` Responses API adapter; diagnosis identity hashes both
  the domain and provider-facing schemas.

## 0.7.0 — Phase 6: Reproducible benchmarking (`0c75809`)

- Versioned `aegisops-memoryless-v1` identity; secret-safe preflight; explicit smoke,
  baseline, resume, compare, and immutable lock commands (ADR 0007).
- Atomic per-run history with recovery verification, clean-stack isolation, and warm-up;
  aggregate, calibration, scenario, tool-use, evidence, usage, and latency analysis.
- Normalized model usage includes cached, cache-write, and reasoning-token detail.

## 0.6.0 — Phase 5: AegisOps investigator (`d6132bd`)

- One `aegisops.investigator` (OpenAI Agents SDK) with exactly the eleven read-only
  diagnostic tools, structured diagnoses, evidence provenance, hard tool/turn/time
  limits, and a ground-truth-isolated five-scenario evaluation harness (ADR 0006).
- Diagnostic results and audit events share a stable `tool_call_id`.

## 0.5.0 — Phase 4: Read-only diagnostics (`6d99c3d`)

- Typed diagnostics over fixed health, Prometheus, Loki, and Tempo adapters with strict
  inputs and no raw-query or arbitrary-URL fields (ADR 0005).
- Deterministic tool registry, investigator allow/deny policy, per-session call counting,
  evidence-free audit records, and a diagnostic CLI.
- Trace normalization redacts SQL-shaped operation names and non-allowlisted attributes.

## 0.4.0 — Phase 3: Operational observability (`88425d4`)

- Bounded-label Prometheus metrics, OpenTelemetry tracing via the Collector to Tempo,
  Loki logs via Grafana Alloy, and a provisioned Grafana dashboard (ADR 0004).
- Request logs include duration and real trace/span IDs inside active spans.

## 0.3.0 — Phase 2: Deterministic incident lab (`1e23b3a`)

- Five versioned scenarios with typed, isolated ground truth; disabled-by-default,
  concurrency-safe failure controls with idempotent reset; evaluator lifecycle CLI
  (ADR 0003).

## 0.2.0 — Phase 1: AegisOps lab (`f377535`)

- Gateway, Users, and Orders FastAPI services; PostgreSQL orders with Alembic; Docker
  Compose with health checks; correlation IDs, structured JSON logs, and error envelopes
  (ADR 0002).
- CI for static checks, unit tests, Docker builds, Compose startup, and integration tests.

## 0.1.0 — Phase 0: Foundation (`66ef536`)

- Repository bootstrap, packaging, and quality tooling.
