# ADR 0010: Accelerate PatchForge in the Execution Order

## Status

Accepted. Owner-approved roadmap decision recorded on 2026-09-28 after Phase 8.

## Context

`NEXUS_MASTER_BUILD_PROMPT.md` section 24 defines the original default build order.
After Phase 8 (Atlas thin control plane) it continues:

```text
Phase 9  AegisOps multi-agent + approval-gated remediation
Phase 10 Kubernetes (kind lab, Kubernetes tools and failure benchmark)
Phase 11 PatchForge
Phase 12 SentinelQA
Phase 13 Engram v2
Phase 14 Integrated NEXUS
Phase 15 Portfolio polish
```

Phases 0-8 are complete. The remaining NEXUS work is mostly software engineering on
NEXUS itself. A bounded, evaluated software-engineering agent can help with that work
earlier, and it can do so without new production-facing capabilities. Remediation and
Kubernetes both add write authority over running infrastructure. That makes them the
highest-risk phases, and nothing currently depends on them.

## Decision

The project owner changes the execution order. The work that follows Phase 8 is:

1. PatchForge v1: bounded issue/task -> isolated workspace -> reproduce -> patch ->
   validate -> self-review -> structured `PatchResult` -> draft PR for human approval.
2. SentinelQA / independent validation: independent re-execution and review of
   PatchForge output through the existing Atlas `ReviewResult` boundary.
3. Bounded daily self-improvement capability: scheduled evaluation that may propose one
   measured change as a PR and never merges or deploys by itself.
4. The deferred master-plan phases, in an order chosen when each is authorized:
   AegisOps specialists and approval-gated remediation (original Phase 9), Kubernetes
   (original Phase 10), Engram evolution (original Phase 13), integrated NEXUS workflows
   (original Phase 14), and portfolio polish (original Phase 15).

Phase numbers do not change. Master-plan phase numbers stay stable identifiers, so
existing references (for example "Phase 6 baseline" or "Phase 7 Brain") keep their
meaning. PatchForge keeps the identifier Phase 11 and SentinelQA keeps Phase 12;
they are simply executed before Phases 9 and 10. Self-improvement is a named milestone,
not a numbered phase. `ROADMAP.md` records the current execution order.

## Prerequisite

Atlas v1 (ADR 0009) is the required dispatch substrate. PatchForge runs only through
`AgentRuntime` and accepts `JobDispatch` from a running, leased Atlas job. It returns the
typed `PatchResult`. SentinelQA returns `ReviewResult`. Human approval stays a separate
Atlas decision. PatchForge needs no Atlas capability beyond the Phase 8 vocabulary:
source read, worktree write, bounded test execution, and patch creation.

## Why Remediation and Kubernetes Can Wait

- Neither is a dependency of PatchForge or SentinelQA.
- Both add write authority over running systems. Deferring them keeps the platform's
  authority read-only toward infrastructure while the engineering loop matures.
- The frozen AegisOps investigator, the Phase 6 baseline, and the Phase 7 evidence stay
  valid comparison controls regardless of when remediation begins.

## Unchanged Constraints

- All governing principles in `NEXUS_MASTER_BUILD_PROMPT.md` sections 1-23 and `BRAIN.md`.
- Least privilege, no unrestricted shell, no autonomous merge or deployment, and
  approval gates for sensitive actions.
- Evaluation-first adoption. A proposed improvement, including a PatchForge
  self-improvement, must beat a contemporaneous baseline under pre-registered rules and
  receive human approval.
- Private per-agent memory. PatchForge must not reuse AegisOps Brain records, and it
  must not copy `bounded-lexical-v1` retrieval, whose generic-prompt limitation Phase 7
  demonstrated.
- The frozen Phase 5 behavior, the Phase 6 lock, and the Phase 7 artifacts are immutable.
- Each phase or milestone still requires explicit owner authorization.

## Consequences

- `ROADMAP.md` and `PROJECT_STATE.md` hold the current execution order. The master
  prompt's section 24 is the original default order and is read through this ADR.
- The end-to-end demonstration in the master prompt (AegisOps detects, PatchForge fixes,
  SentinelQA validates, AegisOps verifies recovery) still requires the deferred
  remediation work before it can be closed.
- If PatchForge evidence shows the engineering loop is not useful, the owner may return
  to the original order. That change would need a new ADR.
