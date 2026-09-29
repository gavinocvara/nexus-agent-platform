# ADR 0010: PatchForge v1 Trustworthy Engineering Agent

## Status

Accepted

## Context

PatchForge must become a useful software-engineering agent without weakening the Atlas
control plane or treating model narration as execution evidence. The shortest trustworthy
path is a memoryless, single-agent workflow over synthetic fixture repositories before
GitHub, shared memory, Kubernetes, or a large benchmark.

Repository contents and issue text are untrusted data. The model must not receive an
unrestricted shell, ambient credentials, Git authority, or the ability to claim that a
test ran or passed without a corresponding runtime record.

## Decision

Build PatchForge v1 as small validated milestones:

1. strict contracts and operator policy;
2. disposable source-SHA workspaces and external Git authority;
3. Docker sandbox plus a fake deterministic test executor;
4. narrow typed tool gateway;
5. deterministic runtime state machine;
6. runtime attestation;
7. scripted end-to-end fixture evaluation;
8. small synthetic benchmark;
9. one explicitly authorized live engine task;
10. independent SentinelQA-lite verification;
11. approval-gated GitHub branch and draft-PR integration.

Atlas remains the outer job, identity, capability, persistence, review, and human-
approval boundary. PatchForge implements Atlas' `AgentRuntime` interface later; it does
not approve, merge, deploy, or push to `main`. Its only successful terminal engineering
outcome is `patch_proposed`.

## Contracts And Evidence Authority

`EngineeringTask` binds the Atlas job, immutable repository/SHA, operator-owned
repository-profile identity and hash, acceptance criteria, constraints, and task scope.
`RunIdentity` binds that task to one Atlas execution, one engine version, memory-disabled
mode, and `parallel_tool_calls=false`.

The model may author only `AgentReport`: a narrative summary, hypothesis,
implementation description, limitations, and references. It has no test-pass, lint-pass,
typecheck-pass, reproduction-success, or patch-approval fields.

`ToolCallRecord`, `TestExecution`, `ReproductionEvidence`, `CheckResult`, `DiffSummary`,
policy findings, budget usage, and the encompassing `PatchResult` are runtime-attested.
The result contract cross-checks run IDs, contiguous calls, execution references, check
kinds/statuses, budget counts, source SHA, policy findings, and required validation.
Canonical NFC-normalized JSON and SHA-256 provide deterministic task, profile, command,
diff, and result identities.

A `patch_proposed` result requires:

- a diff rooted at the run's source SHA;
- reproduction evidence, including an explicit `not_practical` state when applicable;
- runtime-attested passing targeted and full-suite checks;
- no failed recorded check;
- no blocking policy finding;
- completion of the finalization phase;
- no failure classification.

Other outcomes are complete partial reports with an explicit failure classification.
They cannot be converted to Atlas' `PatchResult`.

## Repository And Sandbox Authority

Git clone, source-SHA verification, runtime branch creation, diff generation, artifact
hashing, and cleanup remain runtime-owned outside the execution sandbox. Each run will
use a fresh disposable workspace. The sandbox receives the workspace content but never
the host `.git` directory.

Milestone B implements this as a bare Git control directory beside a `.git`-free
execution worktree. Provisioning verifies the task's repository-profile ID, canonical
profile hash, repository URL, and exact source commit before activating the workspace.
A durable expiring marker supports renewal, idempotent cleanup, and conservative orphan
reaping without deleting unmarked or identity-mismatched directories.

Repository commands are immutable argument vectors from an operator-owned
`RepositoryProfile`; repository text cannot introduce commands. Every execution sandbox
must use a digest-pinned image, no network, no secrets, a non-root UID/GID, a read-only
root filesystem, bounded CPU, memory, PIDs, time, and output. A fake executor may replace
Docker only in deterministic unit tests.

Milestone C implements `DockerSandbox` and `FakeSandbox` behind one typed protocol. The
Docker executor uses a content-addressed local image with pulls disabled, passes no host
environment, mounts only the `.git`-free worktree, drops all capabilities, enables
no-new-privileges, and force-removes named containers after success or interruption.
Real integration tests verify the isolation boundary plus timeout and output-limit
termination.

Milestone D implements a narrow typed `ToolGateway` over bounded repository reads,
compare-and-swap writes, runtime-owned Git status/diff, and immutable operator-profile
commands. The gateway binds every call to the Atlas job, agent, engineering task, source
revision, profile, and verified workspace; enforces phase, capability, path, budget, and
finalization-reserve policy; and records append-only canonical evidence. Shell execution
is rejected. `advance_phase` and `submit_report` return typed control requests and cannot
silently mutate runtime state. The closed phase machine is implemented by Milestone E
below; a live model adapter remains deferred.

Milestone D hardening (0.13.1) closes findings from an independent adversarial review:

- Every workspace-touching call re-verifies the durable handle and an unexpired lease;
  a renewed handle is adopted only through `refresh_workspace` for the same workspace.
- Git ignore and attribute rules are pinned to the source commit. The gateway never
  writes `.gitignore`/`.gitattributes` or Git-ignored paths, and status/diff fail closed
  if those rules change. The only exception is a tool cache's own `.gitignore`
  (`.pytest_cache`, `.mypy_cache`, `.ruff_cache`).
- Paths that are not portable `RepositoryPath` values are omitted from reads and make
  diffs fail closed instead of being silently dropped or crashing.
- Test-infrastructure files (`conftest.py`, `pytest.ini`, `tox.ini`, `setup.cfg`,
  `sitecustomize.py`, `usercustomize.py`, `*.pth`) follow the test-change policy and are
  reported as test changes. Operators protect other config, such as `pyproject.toml`,
  through `protected_paths`.
- In `finalize`, non-report tools cannot use the last call or last result envelope, so
  `submit_report` always has capacity. Report evidence must reference existing
  runtime tool-call or execution records.
- A budget overrun after sandbox execution returns a typed budget failure and keeps the
  execution evidence.

Milestone E (0.14.0) implements the closed Runtime lifecycle and a single-action engine
protocol over ToolGateway. Runtime owns every transition, accepts only typed gateway
phase/report requests, bounds implementation retries, renews workspace leases with an
immediate gateway refresh, preserves phase and total budget evidence, and guarantees a
cleanup attempt on normal, typed-failure, and unexpected-defect paths. Unexpected
programming defects propagate after cleanup rather than being mislabeled as agent
failures. There is no live engine implementation.

Runtime completion preserves the narrative report and runtime-owned gateway, sandbox,
transition, lease, and budget evidence. It deliberately does not claim
`patch_proposed`; Milestone F must attest reproduction, checks, diff, scope, and tamper
conditions before constructing that outcome.

Milestone F (0.15.0) adds that attestation:

- Runtime fingerprints the worktree diff at the start of the run and before and after
  every write or sandbox-execution call. At `reported`, before cleanup, it commits the
  final tree in the control repository with a fixed runtime identity and a private index;
  the commit's diff must equal the worktree diff byte for byte. The patch is kept as a
  content-addressed artifact because cleanup deletes the control repository;
  `proposed_head_sha` is reproducible from the base, the patch, and the recorded
  commit time.
- `PatchForgeAttestor` is a pure function of runtime-owned evidence. It never runs
  commands or reads model narrative as evidence.
- Validation counts only when its execution observed the exact final tree and left it
  unchanged. Validation before a later change, or validation that changes the tree,
  does not count.
- Reproduction is practical exactly when the operator profile defines a `reproduction`
  command. Then the command must have run on the unmodified tree: failure followed by
  passing targeted tests on the final tree is `fail_before_pass_after`; passing before
  any change is a warning. Otherwise reproduction is `not_practical`.
- Tamper checks: a pristine starting tree, no worktree change unexplained by a recorded
  call, no change after the last call, before/after fingerprints on every write or
  execution, operator command and sandbox-policy hashes, and verifiable report evidence.
- Scope, protected-path, sensitive-path, test-policy, file-count, and diff-size checks
  re-run on the final diff as defense in depth for changes made by code in the sandbox.
- Blocking findings choose the failure by precedence: tamper (`attestation_failed`),
  then policy (`policy_denied`), then validation (`validation_failed`). Runtime failures
  keep their own classification. Only a clean attestation is `patch_proposed`.

Milestone G (0.16.0) adds the deterministic end-to-end gate that live execution depends
on. `PatchForgeE2EHarness` runs the production path (WorkspaceManager, ToolGateway,
FakeSandbox, Runtime, Attestor) around a scripted engine over fixture repositories
committed with a fixed identity and date. Fixed clocks and content-derived identifiers
make every scenario replay to a byte-identical `PatchResult`. The catalog covers success
and one scenario per `PatchForgeFailure`, and `python -m nexus.patchforge.e2e_catalog`
(a CI step) fails on any unexpected outcome or non-identical replay. Fault injection is
limited to the harness: out-of-band worktree edits, in-sandbox edits, a refused lease
renewal, and a refused cleanup.

In 0.16.1 every E2E run must also satisfy universal invariants: a closed transcript
through cleanup, workspace removal, runtime-owned evidence linkage, an untouched source
repository (no push, merge, or approval), sandbox requests without network or secrets,
memory disabled, and a scripted engine.

0.16.2 makes the finalization reserve usable after it refuses a call. The gateway raises
`GatewayReserveRefusal` only when a non-report call would take the report's reserved call
or output. Runtime then records one runtime-attested `finalize -> finalize` failure
transition (`budget_exhausted`, or the earlier primary failure) and a
`ReserveRefusalRecord`, and allows exactly one further action: `submit_report`. Any other
action ends finalization. The refused call never executed, so no budget is consumed or
restored. Every other failure during `finalize` still ends finalization directly.

Milestone H (0.17.0) adds Benchmark v0. Ground truth (accepted file contents plus files
that must not change) is evaluator-only. A `ContentOracleSandbox` answers test commands
by comparing worktree contents with it, without executing repository code, and reveals
nothing about it. An independent evaluator re-applies the attested patch artifact to a
clean clone. A task is resolved only when PatchForge proposed a patch and the evaluator
confirms it; any other proposal is a false proposal. Content-hash ground truth is a v0
simplification: equivalent solutions count only if listed. The gate engines show that
allowing test changes lets PatchForge propose patches that weaken the specification
tests; independent re-validation (Milestone J) must use the original tests.

## Budgets And Runtime

PatchForge uses the closed phases:

```text
created -> provisioning -> recon -> hypothesis -> reproduce -> implement
-> targeted_validate -> full_validate -> self_review -> finalize -> reported
-> cleanup -> closed
```

Only bounded `implement <-> targeted_validate` loops are permitted. Every operational
phase has a visible call, duration, and output budget. Finalization capacity is a
structurally separate reserve so exhaustion in an earlier phase still yields a typed
partial result. Parallel tool calls are disabled in v1. The deterministic Runtime also
models cancellation, policy, sandbox, workspace, validation, attestation, engine,
budget, finalization, and cleanup failures without giving the engine evidence authority.

## Deferred Work

- PatchForge memory and all AegisOps-memory reuse
- live model execution until deterministic E2E and CI pass
- large or historical benchmark reconstruction
- advanced semantic indexing and tamper heuristics
- automatic self-improvement adoption
- GitHub issue intake, branch push, and draft PR until SentinelQA-lite exists
- merge, deployment, production remediation, Kubernetes, Engram, and free-form
  multi-agent planning

Daily self-improvement hooks may be designed later, but execution remains dry-run and
can produce at most one isolated proposal that stops before adoption.

## Consequences

- A model cannot turn narrative confidence into validation evidence.
- The runtime, not the sandboxed process, controls Git identity and patch derivation.
- The first useful engineering path can be validated against small synthetic fixtures.
- PatchForge remains composable with Atlas review and human approval without expanding
  Atlas into a large orchestrator.
