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

Repository commands are immutable argument vectors from an operator-owned
`RepositoryProfile`; repository text cannot introduce commands. Every execution sandbox
must use a digest-pinned image, no network, no secrets, a non-root UID/GID, a read-only
root filesystem, bounded CPU, memory, PIDs, time, and output. A fake executor may replace
Docker only in deterministic unit tests.

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
partial result. Parallel tool calls are disabled in v1.

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
